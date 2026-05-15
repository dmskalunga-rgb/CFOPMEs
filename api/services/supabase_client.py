#!/usr/bin/env python3
"""
api/services/supabase_client.py

Enterprise-grade Supabase service client.

Objetivo:
- Centralizar acesso ao Supabase/PostgREST/Auth/Storage com padrões enterprise.
- Evitar acoplamento dos routers com detalhes de HTTP, headers, retries, paginação e erros.
- Suportar CRUD genérico, upsert, RPC, paginação, filtros, storage básico e health check.
- Operar sem dependências obrigatórias além da biblioteca padrão.

Variáveis de ambiente:
    SUPABASE_URL=https://xxxx.supabase.co
    SUPABASE_ANON_KEY=...
    SUPABASE_SERVICE_ROLE_KEY=...       # preferencial para backend confiável
    SUPABASE_JWT=...                    # opcional para RLS por usuário
    SUPABASE_TIMEOUT_SECONDS=20
    SUPABASE_RETRY_ATTEMPTS=3
    SUPABASE_RETRY_BACKOFF_SECONDS=0.4

Uso:
    client = SupabaseClient.from_env()
    rows = client.select("profiles", filters={"id": "123"})

    created = client.insert("audit_logs", {"action": "login"})

    result = client.rpc("calculate_score", {"entity_id": "abc"})
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union


LOGGER = logging.getLogger(__name__)
SERVICE_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc


class SupabaseAuthMode(str, Enum):
    ANON = "anon"
    SERVICE_ROLE = "service_role"
    USER_JWT = "user_jwt"


class ConflictResolution(str, Enum):
    IGNORE_DUPLICATES = "ignore_duplicates"
    MERGE_DUPLICATES = "merge_duplicates"


class ReturnPreference(str, Enum):
    MINIMAL = "minimal"
    REPRESENTATION = "representation"


@dataclass(frozen=True)
class SupabaseSettings:
    url: str
    anon_key: Optional[str] = None
    service_role_key: Optional[str] = None
    user_jwt: Optional[str] = None
    timeout_seconds: float = 20.0
    retry_attempts: int = 3
    retry_backoff_seconds: float = 0.4
    default_schema: str = "public"

    @staticmethod
    def from_env() -> "SupabaseSettings":
        url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        if not url:
            raise SupabaseConfigError("SUPABASE_URL não configurado")
        return SupabaseSettings(
            url=url,
            anon_key=os.getenv("SUPABASE_ANON_KEY"),
            service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
            user_jwt=os.getenv("SUPABASE_JWT"),
            timeout_seconds=float(os.getenv("SUPABASE_TIMEOUT_SECONDS", "20")),
            retry_attempts=int(os.getenv("SUPABASE_RETRY_ATTEMPTS", "3")),
            retry_backoff_seconds=float(os.getenv("SUPABASE_RETRY_BACKOFF_SECONDS", "0.4")),
            default_schema=os.getenv("SUPABASE_SCHEMA", "public"),
        )

    def resolve_key(self, mode: SupabaseAuthMode) -> str:
        if mode == SupabaseAuthMode.SERVICE_ROLE and self.service_role_key:
            return self.service_role_key
        if mode == SupabaseAuthMode.USER_JWT and self.user_jwt:
            return self.user_jwt
        if self.anon_key:
            return self.anon_key
        raise SupabaseConfigError("Nenhuma chave Supabase disponível para autenticação")

    def public_metadata(self) -> Dict[str, Any]:
        return {
            "url_configured": bool(self.url),
            "anon_key_configured": bool(self.anon_key),
            "service_role_key_configured": bool(self.service_role_key),
            "user_jwt_configured": bool(self.user_jwt),
            "timeout_seconds": self.timeout_seconds,
            "retry_attempts": self.retry_attempts,
            "retry_backoff_seconds": self.retry_backoff_seconds,
            "default_schema": self.default_schema,
        }


@dataclass(frozen=True)
class SupabaseResponse:
    status_code: int
    data: Any
    headers: Dict[str, str]
    request_id: str
    latency_ms: float

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


@dataclass(frozen=True)
class PageResult:
    data: List[Dict[str, Any]]
    count: Optional[int]
    limit: int
    offset: int
    has_next: bool
    request_id: str


class SupabaseError(Exception):
    """Base Supabase client error."""


class SupabaseConfigError(SupabaseError):
    """Invalid Supabase configuration."""


class SupabaseHttpError(SupabaseError):
    def __init__(self, message: str, status_code: Optional[int] = None, payload: Any = None, request_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
        self.request_id = request_id


class SupabaseClient:
    def __init__(self, settings: SupabaseSettings, auth_mode: SupabaseAuthMode = SupabaseAuthMode.SERVICE_ROLE) -> None:
        self.settings = settings
        self.auth_mode = auth_mode

    @staticmethod
    def from_env(auth_mode: SupabaseAuthMode = SupabaseAuthMode.SERVICE_ROLE) -> "SupabaseClient":
        return SupabaseClient(SupabaseSettings.from_env(), auth_mode=auth_mode)

    def with_user_jwt(self, jwt: str) -> "SupabaseClient":
        settings = dataclasses.replace(self.settings, user_jwt=jwt)
        return SupabaseClient(settings, auth_mode=SupabaseAuthMode.USER_JWT)

    def health(self) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            response = self.select("_health_check_missing_table", limit=1, maybe_missing_ok=True)
            status = "ok"
            message = "supabase_reachable"
        except SupabaseHttpError as exc:
            if exc.status_code in {404, 406}:
                status = "ok"
                message = "supabase_reachable"
            else:
                status = "fail"
                message = str(exc)
        except Exception as exc:  # noqa: BLE001
            status = "fail"
            message = str(exc)
        return {
            "status": status,
            "message": message,
            "latency_ms": elapsed_ms(started),
            "settings": self.settings.public_metadata(),
            "checked_at": utc_now_iso(),
        }

    def select(
        self,
        table: str,
        columns: str = "*",
        filters: Optional[Mapping[str, Any]] = None,
        order: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        count: Optional[str] = None,
        schema: Optional[str] = None,
        maybe_missing_ok: bool = False,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"select": columns}
        params.update(encode_filters(filters or {}))
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        headers: Dict[str, str] = {}
        if count:
            headers["Prefer"] = f"count={count}"
        try:
            response = self._request("GET", self._rest_path(table), params=params, headers=headers, schema=schema)
            return ensure_list_of_dicts(response.data)
        except SupabaseHttpError:
            if maybe_missing_ok:
                return []
            raise

    def page(
        self,
        table: str,
        columns: str = "*",
        filters: Optional[Mapping[str, Any]] = None,
        order: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        schema: Optional[str] = None,
    ) -> PageResult:
        headers = {"Prefer": "count=exact", "Range-Unit": "items", "Range": f"{offset}-{offset + limit - 1}"}
        params: Dict[str, Any] = {"select": columns}
        params.update(encode_filters(filters or {}))
        if order:
            params["order"] = order
        response = self._request("GET", self._rest_path(table), params=params, headers=headers, schema=schema)
        count = parse_content_range_count(response.headers.get("content-range"))
        rows = ensure_list_of_dicts(response.data)
        return PageResult(
            data=rows,
            count=count,
            limit=limit,
            offset=offset,
            has_next=False if count is None else offset + len(rows) < count,
            request_id=response.request_id,
        )

    def insert(
        self,
        table: str,
        payload: Union[Mapping[str, Any], Sequence[Mapping[str, Any]]],
        return_preference: ReturnPreference = ReturnPreference.REPRESENTATION,
        schema: Optional[str] = None,
    ) -> Any:
        headers = {"Prefer": f"return={return_preference.value}"}
        response = self._request("POST", self._rest_path(table), json_body=payload, headers=headers, schema=schema)
        return response.data

    def update(
        self,
        table: str,
        payload: Mapping[str, Any],
        filters: Mapping[str, Any],
        return_preference: ReturnPreference = ReturnPreference.REPRESENTATION,
        schema: Optional[str] = None,
    ) -> Any:
        if not filters:
            raise SupabaseError("update requer filters para evitar alteração massiva acidental")
        headers = {"Prefer": f"return={return_preference.value}"}
        response = self._request("PATCH", self._rest_path(table), params=encode_filters(filters), json_body=payload, headers=headers, schema=schema)
        return response.data

    def delete(
        self,
        table: str,
        filters: Mapping[str, Any],
        return_preference: ReturnPreference = ReturnPreference.REPRESENTATION,
        schema: Optional[str] = None,
    ) -> Any:
        if not filters:
            raise SupabaseError("delete requer filters para evitar exclusão massiva acidental")
        headers = {"Prefer": f"return={return_preference.value}"}
        response = self._request("DELETE", self._rest_path(table), params=encode_filters(filters), headers=headers, schema=schema)
        return response.data

    def upsert(
        self,
        table: str,
        payload: Union[Mapping[str, Any], Sequence[Mapping[str, Any]]],
        on_conflict: Optional[str] = None,
        resolution: ConflictResolution = ConflictResolution.MERGE_DUPLICATES,
        return_preference: ReturnPreference = ReturnPreference.REPRESENTATION,
        schema: Optional[str] = None,
    ) -> Any:
        params: Dict[str, Any] = {}
        if on_conflict:
            params["on_conflict"] = on_conflict
        prefer = [f"return={return_preference.value}", f"resolution={resolution.value}"]
        response = self._request("POST", self._rest_path(table), params=params, json_body=payload, headers={"Prefer": ",".join(prefer)}, schema=schema)
        return response.data

    def rpc(self, function_name: str, payload: Optional[Mapping[str, Any]] = None, schema: Optional[str] = None) -> Any:
        response = self._request("POST", f"/rest/v1/rpc/{safe_identifier(function_name)}", json_body=dict(payload or {}), schema=schema)
        return response.data

    def storage_upload(
        self,
        bucket: str,
        path: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        upsert: bool = False,
    ) -> SupabaseResponse:
        headers = {"Content-Type": content_type}
        if upsert:
            headers["x-upsert"] = "true"
        return self._request_raw("POST", f"/storage/v1/object/{safe_path(bucket)}/{safe_object_path(path)}", body=content, headers=headers)

    def storage_download(self, bucket: str, path: str) -> bytes:
        response = self._request_raw("GET", f"/storage/v1/object/{safe_path(bucket)}/{safe_object_path(path)}", parse_json=False)
        return response.data if isinstance(response.data, bytes) else bytes(str(response.data), "utf-8")

    def storage_delete(self, bucket: str, paths: Sequence[str]) -> Any:
        payload = {"prefixes": list(paths)}
        response = self._request("DELETE", f"/storage/v1/object/{safe_path(bucket)}", json_body=payload)
        return response.data

    def _rest_path(self, table: str) -> str:
        return f"/rest/v1/{safe_identifier(table)}"

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json_body: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        schema: Optional[str] = None,
    ) -> SupabaseResponse:
        body: Optional[bytes] = None
        request_headers = dict(headers or {})
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False, default=str).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        return self._request_raw(method, path, params=params, body=body, headers=request_headers, schema=schema)

    def _request_raw(
        self,
        method: str,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        body: Optional[bytes] = None,
        headers: Optional[Mapping[str, str]] = None,
        schema: Optional[str] = None,
        parse_json: bool = True,
    ) -> SupabaseResponse:
        request_id = f"sb_{uuid.uuid4().hex[:16]}"
        url = self._build_url(path, params)
        merged_headers = self._headers(headers or {}, schema=schema, request_id=request_id)
        attempts = max(self.settings.retry_attempts, 1)
        last_error: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                request = urllib.request.Request(url=url, data=body, headers=merged_headers, method=method.upper())
                with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as response:
                    raw = response.read()
                    parsed = parse_response_body(raw, response.headers.get("content-type", ""), parse_json=parse_json)
                    return SupabaseResponse(
                        status_code=response.status,
                        data=parsed,
                        headers={key.lower(): value for key, value in response.headers.items()},
                        request_id=request_id,
                        latency_ms=elapsed_ms(started),
                    )
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                payload = parse_response_body(raw, exc.headers.get("content-type", ""), parse_json=True)
                if not is_retryable_status(exc.code) or attempt >= attempts:
                    raise SupabaseHttpError(
                        message=f"Supabase HTTP {exc.code}: {extract_error_message(payload)}",
                        status_code=exc.code,
                        payload=payload,
                        request_id=request_id,
                    ) from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt >= attempts:
                    raise SupabaseHttpError(
                        message=f"Falha de conexão com Supabase: {exc}",
                        status_code=None,
                        payload=None,
                        request_id=request_id,
                    ) from exc
                last_error = exc
            sleep_for_retry(self.settings.retry_backoff_seconds, attempt)

        raise SupabaseHttpError(f"Falha Supabase após retries: {last_error}", request_id=request_id)

    def _build_url(self, path: str, params: Optional[Mapping[str, Any]] = None) -> str:
        query = urllib.parse.urlencode(flatten_query(params or {}), doseq=True)
        return f"{self.settings.url}{path}{'?' + query if query else ''}"

    def _headers(self, extra: Mapping[str, str], schema: Optional[str], request_id: str) -> Dict[str, str]:
        key = self.settings.resolve_key(self.auth_mode)
        headers = {
            "apikey": self.settings.anon_key or key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": f"enterprise-ai-supabase-client/{SERVICE_VERSION}",
            "x-client-info": f"enterprise-ai-supabase-client/{SERVICE_VERSION}",
            "x-request-id": request_id,
        }
        resolved_schema = schema or self.settings.default_schema
        if resolved_schema:
            headers.setdefault("Accept-Profile", resolved_schema)
            headers.setdefault("Content-Profile", resolved_schema)
        headers.update(dict(extra))
        return headers


def encode_filters(filters: Mapping[str, Any]) -> Dict[str, Any]:
    encoded: Dict[str, Any] = {}
    for field, value in filters.items():
        if field.endswith("__eq"):
            encoded[field[:-4]] = f"eq.{value}"
        elif field.endswith("__neq"):
            encoded[field[:-5]] = f"neq.{value}"
        elif field.endswith("__gt"):
            encoded[field[:-4]] = f"gt.{value}"
        elif field.endswith("__gte"):
            encoded[field[:-5]] = f"gte.{value}"
        elif field.endswith("__lt"):
            encoded[field[:-4]] = f"lt.{value}"
        elif field.endswith("__lte"):
            encoded[field[:-5]] = f"lte.{value}"
        elif field.endswith("__like"):
            encoded[field[:-6]] = f"like.{value}"
        elif field.endswith("__ilike"):
            encoded[field[:-7]] = f"ilike.{value}"
        elif field.endswith("__in"):
            values = ",".join(str(item) for item in value) if isinstance(value, (list, tuple, set)) else str(value)
            encoded[field[:-4]] = f"in.({values})"
        elif isinstance(value, str) and any(value.startswith(prefix) for prefix in ("eq.", "neq.", "gt.", "gte.", "lt.", "lte.", "like.", "ilike.", "in.", "is.")):
            encoded[field] = value
        else:
            encoded[field] = f"eq.{value}"
    return encoded


def flatten_query(params: Mapping[str, Any]) -> List[Tuple[str, Any]]:
    query: List[Tuple[str, Any]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                query.append((key, item))
        else:
            query.append((key, value))
    return query


def parse_response_body(raw: bytes, content_type: str, parse_json: bool = True) -> Any:
    if not raw:
        return None
    if not parse_json:
        return raw
    if "application/json" in content_type or raw[:1] in {b"[", b"{"}:
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace")
    return raw.decode("utf-8", errors="replace")


def ensure_list_of_dicts(data: Any) -> List[Dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [dict(item) if isinstance(item, Mapping) else {"value": item} for item in data]
    if isinstance(data, Mapping):
        return [dict(data)]
    return [{"value": data}]


def parse_content_range_count(value: Optional[str]) -> Optional[int]:
    if not value or "/" not in value:
        return None
    total = value.rsplit("/", 1)[-1]
    if total == "*":
        return None
    try:
        return int(total)
    except ValueError:
        return None


def safe_identifier(value: str) -> str:
    text = str(value).strip()
    if not text:
        raise SupabaseError("Identificador vazio")
    if not all(char.isalnum() or char in {"_", "-"} for char in text):
        raise SupabaseError(f"Identificador inválido: {value}")
    return urllib.parse.quote(text, safe="_")


def safe_path(value: str) -> str:
    text = str(value).strip().strip("/")
    if not text:
        raise SupabaseError("Path vazio")
    return urllib.parse.quote(text, safe="-_")


def safe_object_path(value: str) -> str:
    text = str(value).strip().lstrip("/")
    if not text:
        raise SupabaseError("Object path vazio")
    parts = [urllib.parse.quote(part, safe="-_.") for part in text.split("/") if part]
    return "/".join(parts)


def is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 425, 429, 500, 502, 503, 504}


def sleep_for_retry(backoff: float, attempt: int) -> None:
    delay = max(backoff, 0) * (2 ** max(attempt - 1, 0))
    if delay > 0:
        time.sleep(min(delay, 5.0))


def extract_error_message(payload: Any) -> str:
    if isinstance(payload, Mapping):
        for key in ("message", "error", "hint", "details"):
            if payload.get(key):
                return str(payload[key])
    return str(payload)[:500]


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 4)


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()


_default_client: Optional[SupabaseClient] = None


def get_supabase_client() -> SupabaseClient:
    global _default_client
    if _default_client is None:
        _default_client = SupabaseClient.from_env()
    return _default_client


def reset_supabase_client() -> None:
    global _default_client
    _default_client = None


__all__ = [
    "SERVICE_VERSION",
    "SupabaseAuthMode",
    "ConflictResolution",
    "ReturnPreference",
    "SupabaseSettings",
    "SupabaseResponse",
    "PageResult",
    "SupabaseError",
    "SupabaseConfigError",
    "SupabaseHttpError",
    "SupabaseClient",
    "encode_filters",
    "get_supabase_client",
    "reset_supabase_client",
]

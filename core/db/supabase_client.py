#!/usr/bin/env python3
"""
core/db/supabase_client.py

Enterprise-grade Supabase/PostgREST database client.

Objetivo:
- Fornecer um cliente Supabase robusto para a camada core/db.
- Centralizar HTTP, autenticação, retries, timeout, filtros PostgREST, paginação e erros.
- Suportar CRUD, upsert, RPC, health check, storage básico e execução via query builder.
- Manter dependências mínimas usando somente biblioteca padrão.

Variáveis de ambiente:
    SUPABASE_URL=https://xxxx.supabase.co
    SUPABASE_ANON_KEY=...
    SUPABASE_SERVICE_ROLE_KEY=...
    SUPABASE_SCHEMA=public
    SUPABASE_TIMEOUT_SECONDS=20
    SUPABASE_RETRY_ATTEMPTS=3
    SUPABASE_RETRY_BACKOFF_SECONDS=0.4

Uso:
    from core.db.supabase_client import get_supabase_client

    db = get_supabase_client()
    rows = db.select("users", filters={"id": "123"})
    db.insert("audit_logs", {"action": "login"})
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

try:
    from core.config.settings import get_settings
except Exception:  # pragma: no cover
    get_settings = None  # type: ignore

try:
    from core.db.queries import CompiledQuery
except Exception:  # pragma: no cover
    CompiledQuery = Any  # type: ignore


LOGGER = logging.getLogger(__name__)
CLIENT_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc


class SupabaseAuthMode(str, Enum):
    ANON = "anon"
    SERVICE_ROLE = "service_role"
    USER_JWT = "user_jwt"


class ReturnPreference(str, Enum):
    MINIMAL = "minimal"
    REPRESENTATION = "representation"


class UpsertResolution(str, Enum):
    MERGE_DUPLICATES = "merge-duplicates"
    IGNORE_DUPLICATES = "ignore-duplicates"


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    anon_key: Optional[str] = None
    service_role_key: Optional[str] = None
    user_jwt: Optional[str] = None
    schema: str = "public"
    timeout_seconds: float = 20.0
    retry_attempts: int = 3
    retry_backoff_seconds: float = 0.4

    @staticmethod
    def from_env() -> "SupabaseConfig":
        if get_settings:
            try:
                settings = get_settings()
                if settings.supabase.url:
                    return SupabaseConfig(
                        url=settings.supabase.url.rstrip("/"),
                        anon_key=settings.supabase.anon_key,
                        service_role_key=settings.supabase.service_role_key,
                        user_jwt=settings.supabase.user_jwt,
                        schema=settings.supabase.schema,
                        timeout_seconds=settings.supabase.timeout_seconds,
                        retry_attempts=settings.supabase.retry_attempts,
                        retry_backoff_seconds=settings.supabase.retry_backoff_seconds,
                    )
            except Exception:
                pass

        url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        if not url:
            raise SupabaseConfigError("SUPABASE_URL não configurado")
        return SupabaseConfig(
            url=url,
            anon_key=os.getenv("SUPABASE_ANON_KEY"),
            service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
            user_jwt=os.getenv("SUPABASE_JWT"),
            schema=os.getenv("SUPABASE_SCHEMA", "public"),
            timeout_seconds=float(os.getenv("SUPABASE_TIMEOUT_SECONDS", "20")),
            retry_attempts=int(os.getenv("SUPABASE_RETRY_ATTEMPTS", "3")),
            retry_backoff_seconds=float(os.getenv("SUPABASE_RETRY_BACKOFF_SECONDS", "0.4")),
        )

    def resolve_token(self, mode: SupabaseAuthMode) -> str:
        if mode == SupabaseAuthMode.SERVICE_ROLE and self.service_role_key:
            return self.service_role_key
        if mode == SupabaseAuthMode.USER_JWT and self.user_jwt:
            return self.user_jwt
        if self.anon_key:
            return self.anon_key
        raise SupabaseConfigError("Nenhum token Supabase disponível")

    def metadata(self) -> Dict[str, Any]:
        return {
            "url_configured": bool(self.url),
            "anon_key_configured": bool(self.anon_key),
            "service_role_key_configured": bool(self.service_role_key),
            "user_jwt_configured": bool(self.user_jwt),
            "schema": self.schema,
            "timeout_seconds": self.timeout_seconds,
            "retry_attempts": self.retry_attempts,
        }


@dataclass(frozen=True)
class SupabaseResult:
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
    """Base Supabase error."""


class SupabaseConfigError(SupabaseError):
    """Configuração inválida."""


class SupabaseHttpError(SupabaseError):
    def __init__(self, message: str, status_code: Optional[int] = None, payload: Any = None, request_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
        self.request_id = request_id


class SupabaseClient:
    def __init__(self, config: SupabaseConfig, auth_mode: SupabaseAuthMode = SupabaseAuthMode.SERVICE_ROLE) -> None:
        self.config = config
        self.auth_mode = auth_mode

    @staticmethod
    def from_env(auth_mode: SupabaseAuthMode = SupabaseAuthMode.SERVICE_ROLE) -> "SupabaseClient":
        return SupabaseClient(SupabaseConfig.from_env(), auth_mode=auth_mode)

    def with_user_jwt(self, jwt: str) -> "SupabaseClient":
        return SupabaseClient(
            SupabaseConfig(
                url=self.config.url,
                anon_key=self.config.anon_key,
                service_role_key=self.config.service_role_key,
                user_jwt=jwt,
                schema=self.config.schema,
                timeout_seconds=self.config.timeout_seconds,
                retry_attempts=self.config.retry_attempts,
                retry_backoff_seconds=self.config.retry_backoff_seconds,
            ),
            auth_mode=SupabaseAuthMode.USER_JWT,
        )

    def health(self) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            self.raw("GET", "/rest/v1/", parse_json=False)
            status = "ok"
            message = "supabase_reachable"
        except SupabaseHttpError as exc:
            status = "ok" if exc.status_code in {200, 401, 404, 406} else "fail"
            message = str(exc)
        except Exception as exc:  # noqa: BLE001
            status = "fail"
            message = str(exc)
        return {
            "status": status,
            "message": message,
            "latency_ms": elapsed_ms(started),
            "client_version": CLIENT_VERSION,
            "config": self.config.metadata(),
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
        schema: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"select": columns}
        params.update(postgrest_filters(filters or {}))
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        result = self.raw("GET", f"/rest/v1/{safe_identifier(table)}", params=params, schema=schema)
        return ensure_rows(result.data)

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
        params: Dict[str, Any] = {"select": columns}
        params.update(postgrest_filters(filters or {}))
        if order:
            params["order"] = order
        headers = {
            "Prefer": "count=exact",
            "Range-Unit": "items",
            "Range": f"{offset}-{offset + limit - 1}",
        }
        result = self.raw("GET", f"/rest/v1/{safe_identifier(table)}", params=params, headers=headers, schema=schema)
        rows = ensure_rows(result.data)
        count = parse_content_range_count(result.headers.get("content-range"))
        return PageResult(
            data=rows,
            count=count,
            limit=limit,
            offset=offset,
            has_next=False if count is None else offset + len(rows) < count,
            request_id=result.request_id,
        )

    def insert(
        self,
        table: str,
        payload: Union[Mapping[str, Any], Sequence[Mapping[str, Any]]],
        returning: ReturnPreference = ReturnPreference.REPRESENTATION,
        schema: Optional[str] = None,
    ) -> Any:
        result = self.raw(
            "POST",
            f"/rest/v1/{safe_identifier(table)}",
            json_body=payload,
            headers={"Prefer": f"return={returning.value}"},
            schema=schema,
        )
        return result.data

    def update(
        self,
        table: str,
        payload: Mapping[str, Any],
        filters: Mapping[str, Any],
        returning: ReturnPreference = ReturnPreference.REPRESENTATION,
        schema: Optional[str] = None,
    ) -> Any:
        if not filters:
            raise SupabaseError("update requer filtros")
        result = self.raw(
            "PATCH",
            f"/rest/v1/{safe_identifier(table)}",
            params=postgrest_filters(filters),
            json_body=payload,
            headers={"Prefer": f"return={returning.value}"},
            schema=schema,
        )
        return result.data

    def delete(
        self,
        table: str,
        filters: Mapping[str, Any],
        returning: ReturnPreference = ReturnPreference.REPRESENTATION,
        schema: Optional[str] = None,
    ) -> Any:
        if not filters:
            raise SupabaseError("delete requer filtros")
        result = self.raw(
            "DELETE",
            f"/rest/v1/{safe_identifier(table)}",
            params=postgrest_filters(filters),
            headers={"Prefer": f"return={returning.value}"},
            schema=schema,
        )
        return result.data

    def upsert(
        self,
        table: str,
        payload: Union[Mapping[str, Any], Sequence[Mapping[str, Any]]],
        on_conflict: Optional[str] = None,
        resolution: UpsertResolution = UpsertResolution.MERGE_DUPLICATES,
        returning: ReturnPreference = ReturnPreference.REPRESENTATION,
        schema: Optional[str] = None,
    ) -> Any:
        params: Dict[str, Any] = {}
        if on_conflict:
            params["on_conflict"] = on_conflict
        prefer = f"return={returning.value},resolution={resolution.value}"
        result = self.raw(
            "POST",
            f"/rest/v1/{safe_identifier(table)}",
            params=params,
            json_body=payload,
            headers={"Prefer": prefer},
            schema=schema,
        )
        return result.data

    def rpc(self, function_name: str, payload: Optional[Mapping[str, Any]] = None, schema: Optional[str] = None) -> Any:
        result = self.raw("POST", f"/rest/v1/rpc/{safe_identifier(function_name)}", json_body=dict(payload or {}), schema=schema)
        return result.data

    def execute_compiled(self, query: CompiledQuery) -> Any:
        """Executa query SQL compilada via RPC `execute_sql` se existir no Supabase."""
        return self.rpc("execute_sql", {"sql": query.sql, "params": query.params, "query_id": query.query_id})

    def storage_upload(self, bucket: str, path: str, content: bytes, content_type: str = "application/octet-stream", upsert: bool = False) -> SupabaseResult:
        headers = {"Content-Type": content_type}
        if upsert:
            headers["x-upsert"] = "true"
        return self.raw("POST", f"/storage/v1/object/{safe_path(bucket)}/{safe_object_path(path)}", body=content, headers=headers)

    def storage_download(self, bucket: str, path: str) -> bytes:
        result = self.raw("GET", f"/storage/v1/object/{safe_path(bucket)}/{safe_object_path(path)}", parse_json=False)
        return result.data if isinstance(result.data, bytes) else str(result.data).encode("utf-8")

    def storage_delete(self, bucket: str, paths: Sequence[str]) -> Any:
        result = self.raw("DELETE", f"/storage/v1/object/{safe_path(bucket)}", json_body={"prefixes": list(paths)})
        return result.data

    def raw(
        self,
        method: str,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json_body: Any = None,
        body: Optional[bytes] = None,
        headers: Optional[Mapping[str, str]] = None,
        schema: Optional[str] = None,
        parse_json: bool = True,
    ) -> SupabaseResult:
        if json_body is not None and body is not None:
            raise SupabaseError("Use json_body ou body, não ambos")
        request_body = body
        request_headers = dict(headers or {})
        if json_body is not None:
            request_body = json.dumps(json_body, ensure_ascii=False, default=str).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        return self._request(method, path, params=params, body=request_body, headers=request_headers, schema=schema, parse_json=parse_json)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Mapping[str, Any]],
        body: Optional[bytes],
        headers: Mapping[str, str],
        schema: Optional[str],
        parse_json: bool,
    ) -> SupabaseResult:
        request_id = f"sb_{uuid.uuid4().hex[:16]}"
        url = build_url(self.config.url, path, params or {})
        request_headers = self._headers(headers, schema=schema, request_id=request_id)
        attempts = max(1, self.config.retry_attempts)
        last_error: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                req = urllib.request.Request(url=url, data=body, headers=request_headers, method=method.upper())
                with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                    raw = resp.read()
                    return SupabaseResult(
                        status_code=resp.status,
                        data=parse_body(raw, resp.headers.get("content-type", ""), parse_json=parse_json),
                        headers={key.lower(): value for key, value in resp.headers.items()},
                        request_id=request_id,
                        latency_ms=elapsed_ms(started),
                    )
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                payload = parse_body(raw, exc.headers.get("content-type", ""), parse_json=True)
                if not retryable_status(exc.code) or attempt >= attempts:
                    raise SupabaseHttpError(
                        message=f"Supabase HTTP {exc.code}: {error_message(payload)}",
                        status_code=exc.code,
                        payload=payload,
                        request_id=request_id,
                    ) from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt >= attempts:
                    raise SupabaseHttpError(f"Falha de conexão Supabase: {exc}", request_id=request_id) from exc
                last_error = exc
            sleep_retry(self.config.retry_backoff_seconds, attempt)
        raise SupabaseHttpError(f"Falha Supabase após retries: {last_error}", request_id=request_id)

    def _headers(self, extra: Mapping[str, str], schema: Optional[str], request_id: str) -> Dict[str, str]:
        token = self.config.resolve_token(self.auth_mode)
        apikey = self.config.anon_key or token
        resolved_schema = schema or self.config.schema
        headers = {
            "apikey": apikey,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": f"core-db-supabase-client/{CLIENT_VERSION}",
            "x-client-info": f"core-db-supabase-client/{CLIENT_VERSION}",
            "x-request-id": request_id,
        }
        if resolved_schema:
            headers["Accept-Profile"] = resolved_schema
            headers["Content-Profile"] = resolved_schema
        headers.update(dict(extra))
        return headers


def postgrest_filters(filters: Mapping[str, Any]) -> Dict[str, Any]:
    encoded: Dict[str, Any] = {}
    for field, value in filters.items():
        if "__" in field:
            name, op = field.rsplit("__", 1)
            encoded[safe_filter_field(name)] = encode_filter_value(op, value)
        elif isinstance(value, str) and value.split(".", 1)[0] in {"eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike", "in", "is", "cs", "cd"}:
            encoded[safe_filter_field(field)] = value
        else:
            encoded[safe_filter_field(field)] = f"eq.{value}"
    return encoded


def encode_filter_value(op: str, value: Any) -> str:
    aliases = {"ne": "neq", "not": "neq", "contains": "ilike"}
    operator = aliases.get(op, op)
    if operator in {"in", "not_in"}:
        values = ",".join(str(item) for item in value) if isinstance(value, (list, tuple, set)) else str(value)
        return f"{'not.in' if operator == 'not_in' else 'in'}.({values})"
    if operator == "contains":
        return f"ilike.*{value}*"
    if operator == "isnull":
        return "is.null"
    if operator == "notnull":
        return "not.is.null"
    allowed = {"eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike", "is", "cs", "cd", "ov"}
    if operator not in allowed:
        raise SupabaseError(f"Operador PostgREST inválido: {op}")
    return f"{operator}.{value}"


def build_url(base_url: str, path: str, params: Mapping[str, Any]) -> str:
    clean_path = "/" + path.lstrip("/")
    query = urllib.parse.urlencode([(k, v) for k, v in params.items() if v is not None], doseq=True)
    return f"{base_url}{clean_path}{'?' + query if query else ''}"


def parse_body(raw: bytes, content_type: str, parse_json: bool = True) -> Any:
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


def ensure_rows(data: Any) -> List[Dict[str, Any]]:
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
    if not text or not all(char.isalnum() or char in {"_", "-"} for char in text):
        raise SupabaseError(f"Identificador inválido: {value}")
    return urllib.parse.quote(text, safe="_")


def safe_filter_field(value: str) -> str:
    text = str(value).strip()
    if not text or not all(char.isalnum() or char in {"_", ".", "-", ">"} for char in text):
        raise SupabaseError(f"Campo de filtro inválido: {value}")
    return text


def safe_path(value: str) -> str:
    text = str(value).strip().strip("/")
    if not text:
        raise SupabaseError("Path vazio")
    return urllib.parse.quote(text, safe="-_")


def safe_object_path(value: str) -> str:
    text = str(value).strip().lstrip("/")
    if not text:
        raise SupabaseError("Object path vazio")
    return "/".join(urllib.parse.quote(part, safe="-_.") for part in text.split("/") if part)


def retryable_status(status_code: int) -> bool:
    return status_code in {408, 425, 429, 500, 502, 503, 504}


def sleep_retry(backoff: float, attempt: int) -> None:
    delay = max(backoff, 0) * (2 ** max(attempt - 1, 0))
    if delay > 0:
        time.sleep(min(delay, 5.0))


def error_message(payload: Any) -> str:
    if isinstance(payload, Mapping):
        for key in ("message", "error", "hint", "details", "code"):
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
    "CLIENT_VERSION",
    "SupabaseAuthMode",
    "ReturnPreference",
    "UpsertResolution",
    "SupabaseConfig",
    "SupabaseResult",
    "PageResult",
    "SupabaseError",
    "SupabaseConfigError",
    "SupabaseHttpError",
    "SupabaseClient",
    "postgrest_filters",
    "get_supabase_client",
    "reset_supabase_client",
]

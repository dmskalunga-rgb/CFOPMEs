"""
kwanza-ai-core/services/supabase_client.py

Enterprise-grade Supabase client abstraction.

Purpose
-------
Provide a robust, framework-agnostic client for Supabase services used by
Kwanza AI Core: PostgREST database access, Auth admin/user endpoints, Storage,
Edge Functions, health checks and operational observability.

Design goals
------------
- Async-first HTTP client with dependency injection.
- Safe retries with exponential backoff and jitter.
- Timeout handling and circuit breaker protection.
- Tenant/request context propagation through headers.
- RLS-friendly Authorization support.
- Pagination helpers for PostgREST.
- Idempotency headers for write operations.
- Structured errors and response envelopes.
- Metrics and audit hooks.
- No hard dependency on the official Supabase SDK.

Environment variables commonly used
-----------------------------------
SUPABASE_URL
SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY
SUPABASE_JWT

Security note
-------------
Never expose the service-role key to browsers, mobile apps or untrusted clients.
Use it only in trusted server-side services and workers.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import mimetypes
import os
import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple
from urllib.parse import quote, urlencode

try:  # pragma: no cover - optional runtime dependency
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
MetricTags = Mapping[str, str]


# =============================================================================
# Exceptions
# =============================================================================


class SupabaseClientError(RuntimeError):
    """Base exception for Supabase client failures."""


class SupabaseConfigError(SupabaseClientError):
    """Raised when the client configuration is invalid."""


class SupabaseHTTPError(SupabaseClientError):
    """Raised for non-success HTTP responses."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        method: str,
        url: str,
        response_body: Any = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.method = method
        self.url = url
        self.response_body = response_body
        self.request_id = request_id


class SupabaseTimeoutError(SupabaseClientError):
    """Raised when a request times out."""


class SupabaseCircuitOpenError(SupabaseClientError):
    """Raised when the circuit breaker is open."""


class SupabaseDependencyMissingError(SupabaseClientError):
    """Raised when httpx is not installed."""


# =============================================================================
# Enums and data models
# =============================================================================


class SupabaseAuthMode(str, Enum):
    ANON = "anon"
    SERVICE_ROLE = "service_role"
    JWT = "jwt"


class PostgrestPrefer(str, Enum):
    RETURN_MINIMAL = "return=minimal"
    RETURN_REPRESENTATION = "return=representation"
    RESOLUTION_MERGE_DUPLICATES = "resolution=merge-duplicates"
    COUNT_EXACT = "count=exact"
    COUNT_PLANNED = "count=planned"
    COUNT_ESTIMATED = "count=estimated"


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


class StorageUpsertMode(str, Enum):
    ERROR = "error"
    UPSERT = "upsert"


@dataclass(frozen=True)
class SupabaseClientConfig:
    url: str
    anon_key: Optional[str] = None
    service_role_key: Optional[str] = None
    jwt: Optional[str] = None
    auth_mode: SupabaseAuthMode = SupabaseAuthMode.SERVICE_ROLE
    timeout_seconds: float = 20.0
    connect_timeout_seconds: float = 8.0
    retries: int = 3
    retry_base_delay_ms: int = 120
    retry_jitter_ms: int = 80
    circuit_failure_threshold: int = 6
    circuit_recovery_seconds: int = 45
    user_agent: str = "kwanza-ai-core/1.0"
    audit_enabled: bool = True
    privacy_hash_salt: str = "change-me-in-production"
    default_schema: str = "public"

    def validate(self) -> None:
        if not self.url:
            raise SupabaseConfigError("Supabase URL is required.")
        if not self.url.startswith(("http://", "https://")):
            raise SupabaseConfigError("Supabase URL must start with http:// or https://.")
        if self.timeout_seconds <= 0 or self.connect_timeout_seconds <= 0:
            raise SupabaseConfigError("Timeouts must be positive.")
        if self.retries < 0:
            raise SupabaseConfigError("retries cannot be negative.")
        if self.circuit_failure_threshold <= 0:
            raise SupabaseConfigError("circuit_failure_threshold must be positive.")
        if self.auth_mode == SupabaseAuthMode.ANON and not self.anon_key:
            raise SupabaseConfigError("anon_key is required for ANON auth mode.")
        if self.auth_mode == SupabaseAuthMode.SERVICE_ROLE and not self.service_role_key:
            raise SupabaseConfigError("service_role_key is required for SERVICE_ROLE auth mode.")
        if self.auth_mode == SupabaseAuthMode.JWT and not self.jwt:
            raise SupabaseConfigError("jwt is required for JWT auth mode.")

    @classmethod
    def from_env(cls, auth_mode: SupabaseAuthMode = SupabaseAuthMode.SERVICE_ROLE) -> "SupabaseClientConfig":
        return cls(
            url=os.environ.get("SUPABASE_URL", "").rstrip("/"),
            anon_key=os.environ.get("SUPABASE_ANON_KEY"),
            service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
            jwt=os.environ.get("SUPABASE_JWT"),
            auth_mode=auth_mode,
        )


@dataclass(frozen=True)
class SupabaseRequestContext:
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: Optional[str] = None
    role: Optional[str] = None
    jwt: Optional[str] = None
    idempotency_key: Optional[str] = None
    headers: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SupabaseResponse:
    status_code: int
    data: Any
    headers: Mapping[str, str]
    request_id: str
    url: str
    method: str
    latency_ms: float
    count: Optional[int] = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


@dataclass(frozen=True)
class PostgrestQuery:
    table: str
    select: str = "*"
    filters: Mapping[str, Any] = field(default_factory=dict)
    order_by: Optional[str] = None
    order_direction: SortDirection = SortDirection.ASC
    limit: Optional[int] = None
    offset: Optional[int] = None
    range_from: Optional[int] = None
    range_to: Optional[int] = None
    schema: Optional[str] = None
    count: Optional[str] = None


@dataclass(frozen=True)
class StorageObject:
    bucket: str
    path: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    etag: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


# =============================================================================
# Protocols / hooks
# =============================================================================


class MetricsClient(Protocol):
    def increment(self, name: str, value: int = 1, tags: Optional[MetricTags] = None) -> None: ...

    def timing(self, name: str, value_ms: float, tags: Optional[MetricTags] = None) -> None: ...

    def gauge(self, name: str, value: float, tags: Optional[MetricTags] = None) -> None: ...


class AuditSink(Protocol):
    async def write(self, event_name: str, payload: Mapping[str, Any]) -> None: ...


class NoopMetricsClient:
    def increment(self, name: str, value: int = 1, tags: Optional[MetricTags] = None) -> None:
        return None

    def timing(self, name: str, value_ms: float, tags: Optional[MetricTags] = None) -> None:
        return None

    def gauge(self, name: str, value: float, tags: Optional[MetricTags] = None) -> None:
        return None


class NoopAuditSink:
    async def write(self, event_name: str, payload: Mapping[str, Any]) -> None:
        return None


# =============================================================================
# Circuit breaker
# =============================================================================


@dataclass
class CircuitState:
    failures: int = 0
    opened_at: Optional[float] = None


class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_seconds: int) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._state = CircuitState()
        self._lock = asyncio.Lock()

    async def before_call(self) -> None:
        async with self._lock:
            if self._state.opened_at is None:
                return
            if time.monotonic() - self._state.opened_at >= self.recovery_seconds:
                self._state = CircuitState()
                return
            raise SupabaseCircuitOpenError("Supabase circuit breaker is open.")

    async def record_success(self) -> None:
        async with self._lock:
            self._state = CircuitState()

    async def record_failure(self) -> None:
        async with self._lock:
            self._state.failures += 1
            if self._state.failures >= self.failure_threshold:
                self._state.opened_at = time.monotonic()


# =============================================================================
# Utility functions
# =============================================================================


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _join_url(base: str, *parts: str) -> str:
    clean = [base.rstrip("/")]
    clean.extend(str(part).strip("/") for part in parts if part is not None and str(part) != "")
    return "/".join(clean)


def _hash_value(value: Optional[str], salt: str) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()[:20]


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_json_or_text(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _extract_count(headers: Mapping[str, str]) -> Optional[int]:
    content_range = headers.get("content-range") or headers.get("Content-Range")
    if not content_range or "/" not in content_range:
        return None
    _, total = content_range.rsplit("/", 1)
    if total == "*":
        return None
    try:
        return int(total)
    except ValueError:
        return None


def _content_type_for_path(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


# =============================================================================
# Main client
# =============================================================================


class SupabaseClient:
    def __init__(
        self,
        config: SupabaseClientConfig,
        metrics: Optional[MetricsClient] = None,
        audit_sink: Optional[AuditSink] = None,
        client: Optional[Any] = None,
    ) -> None:
        if httpx is None and client is None:
            raise SupabaseDependencyMissingError("httpx is required. Install with: pip install httpx")
        self.config = config
        self.config.validate()
        self.metrics = metrics or NoopMetricsClient()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.circuit_breaker = CircuitBreaker(config.circuit_failure_threshold, config.circuit_recovery_seconds)
        self._external_client = client is not None
        self._client = client or httpx.AsyncClient(  # type: ignore[union-attr]
            timeout=httpx.Timeout(config.timeout_seconds, connect=config.connect_timeout_seconds),  # type: ignore[union-attr]
            headers={"User-Agent": config.user_agent},
        )

    async def close(self) -> None:
        if not self._external_client and self._client:
            await self._client.aclose()

    async def __aenter__(self) -> "SupabaseClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    # -------------------------------------------------------------------------
    # Core HTTP
    # -------------------------------------------------------------------------

    async def request(
        self,
        method: str,
        path_or_url: str,
        *,
        context: Optional[SupabaseRequestContext] = None,
        headers: Optional[Mapping[str, str]] = None,
        params: Optional[Mapping[str, Any]] = None,
        json_body: Any = None,
        content: Optional[bytes] = None,
        expected_status: Sequence[int] = (200, 201, 202, 204, 206),
        service: str = "rest",
    ) -> SupabaseResponse:
        context = context or SupabaseRequestContext()
        url = path_or_url if path_or_url.startswith(("http://", "https://")) else _join_url(self.config.url, path_or_url)
        request_headers = self._build_headers(context, headers=headers, service=service)
        started = time.perf_counter()
        tags = {"service": service, "method": method.upper()}

        await self.circuit_breaker.before_call()
        self.metrics.increment("supabase.request.started", tags=tags)

        last_exc: Optional[Exception] = None
        for attempt in range(self.config.retries + 1):
            try:
                response = await self._client.request(
                    method.upper(),
                    url,
                    headers=request_headers,
                    params=self._clean_params(params),
                    json=json_body,
                    content=content,
                )
                latency_ms = round((time.perf_counter() - started) * 1000, 4)
                data = _parse_json_or_text(response.text)
                envelope = SupabaseResponse(
                    status_code=response.status_code,
                    data=data,
                    headers=dict(response.headers),
                    request_id=context.request_id,
                    url=str(response.url),
                    method=method.upper(),
                    latency_ms=latency_ms,
                    count=_extract_count(response.headers),
                )

                if response.status_code not in expected_status:
                    error = SupabaseHTTPError(
                        f"Supabase request failed with status {response.status_code}",
                        status_code=response.status_code,
                        method=method.upper(),
                        url=str(response.url),
                        response_body=data,
                        request_id=context.request_id,
                    )
                    if self._is_retryable_status(response.status_code) and attempt < self.config.retries:
                        last_exc = error
                        await self._sleep_before_retry(attempt)
                        continue
                    await self.circuit_breaker.record_failure()
                    self.metrics.increment("supabase.request.failed", tags={**tags, "status": str(response.status_code)})
                    await self._audit_request("supabase.request.failed", context, method, str(response.url), latency_ms, response.status_code)
                    raise error

                await self.circuit_breaker.record_success()
                self.metrics.increment("supabase.request.completed", tags={**tags, "status": str(response.status_code)})
                self.metrics.timing("supabase.request.latency_ms", latency_ms, tags=tags)
                await self._audit_request("supabase.request.completed", context, method, str(response.url), latency_ms, response.status_code)
                return envelope
            except SupabaseHTTPError:
                raise
            except Exception as exc:
                last_exc = exc
                if self._is_timeout_exception(exc):
                    last_exc = SupabaseTimeoutError(f"Supabase request timed out: {method.upper()} {url}")
                if attempt < self.config.retries:
                    await self._sleep_before_retry(attempt)
                    continue
                await self.circuit_breaker.record_failure()
                self.metrics.increment("supabase.request.failed", tags={**tags, "error": exc.__class__.__name__})
                latency_ms = round((time.perf_counter() - started) * 1000, 4)
                await self._audit_request("supabase.request.failed", context, method, url, latency_ms, None)
                raise last_exc

        assert last_exc is not None
        raise last_exc

    # -------------------------------------------------------------------------
    # PostgREST helpers
    # -------------------------------------------------------------------------

    async def select(
        self,
        query: PostgrestQuery,
        *,
        context: Optional[SupabaseRequestContext] = None,
        single: bool = False,
    ) -> Any:
        params = self._build_postgrest_params(query)
        headers: Dict[str, str] = {}
        if query.range_from is not None and query.range_to is not None:
            headers["Range-Unit"] = "items"
            headers["Range"] = f"{query.range_from}-{query.range_to}"
        if query.count:
            headers["Prefer"] = f"count={query.count}"
        if single:
            headers["Accept"] = "application/vnd.pgrst.object+json"
        response = await self.request(
            "GET",
            f"/rest/v1/{quote(query.table)}",
            context=context,
            headers=self._schema_headers(query.schema, headers),
            params=params,
            expected_status=(200, 206),
            service="postgrest",
        )
        return response.data

    async def select_all_pages(
        self,
        query: PostgrestQuery,
        *,
        context: Optional[SupabaseRequestContext] = None,
        page_size: int = 1000,
        max_pages: int = 1000,
    ) -> List[Any]:
        rows: List[Any] = []
        for page in range(max_pages):
            paged = PostgrestQuery(
                table=query.table,
                select=query.select,
                filters=query.filters,
                order_by=query.order_by,
                order_direction=query.order_direction,
                limit=None,
                offset=None,
                range_from=page * page_size,
                range_to=(page + 1) * page_size - 1,
                schema=query.schema,
                count=query.count,
            )
            batch = await self.select(paged, context=context)
            if not isinstance(batch, list):
                return rows
            rows.extend(batch)
            if len(batch) < page_size:
                break
        return rows

    async def insert(
        self,
        table: str,
        rows: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        context: Optional[SupabaseRequestContext] = None,
        schema: Optional[str] = None,
        returning: bool = True,
    ) -> Any:
        prefer = PostgrestPrefer.RETURN_REPRESENTATION.value if returning else PostgrestPrefer.RETURN_MINIMAL.value
        response = await self.request(
            "POST",
            f"/rest/v1/{quote(table)}",
            context=context,
            headers=self._schema_headers(schema, {"Prefer": prefer}),
            json_body=rows,
            expected_status=(200, 201, 204),
            service="postgrest",
        )
        return response.data

    async def upsert(
        self,
        table: str,
        rows: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        on_conflict: Optional[str] = None,
        context: Optional[SupabaseRequestContext] = None,
        schema: Optional[str] = None,
        returning: bool = True,
    ) -> Any:
        prefer = [PostgrestPrefer.RESOLUTION_MERGE_DUPLICATES.value]
        prefer.append(PostgrestPrefer.RETURN_REPRESENTATION.value if returning else PostgrestPrefer.RETURN_MINIMAL.value)
        params = {"on_conflict": on_conflict} if on_conflict else None
        response = await self.request(
            "POST",
            f"/rest/v1/{quote(table)}",
            context=context,
            headers=self._schema_headers(schema, {"Prefer": ",".join(prefer)}),
            params=params,
            json_body=rows,
            expected_status=(200, 201, 204),
            service="postgrest",
        )
        return response.data

    async def update(
        self,
        table: str,
        values: Mapping[str, Any],
        filters: Mapping[str, Any],
        *,
        context: Optional[SupabaseRequestContext] = None,
        schema: Optional[str] = None,
        returning: bool = True,
    ) -> Any:
        prefer = PostgrestPrefer.RETURN_REPRESENTATION.value if returning else PostgrestPrefer.RETURN_MINIMAL.value
        response = await self.request(
            "PATCH",
            f"/rest/v1/{quote(table)}",
            context=context,
            headers=self._schema_headers(schema, {"Prefer": prefer}),
            params=self._filters_to_params(filters),
            json_body=values,
            expected_status=(200, 204),
            service="postgrest",
        )
        return response.data

    async def delete(
        self,
        table: str,
        filters: Mapping[str, Any],
        *,
        context: Optional[SupabaseRequestContext] = None,
        schema: Optional[str] = None,
        returning: bool = True,
    ) -> Any:
        prefer = PostgrestPrefer.RETURN_REPRESENTATION.value if returning else PostgrestPrefer.RETURN_MINIMAL.value
        response = await self.request(
            "DELETE",
            f"/rest/v1/{quote(table)}",
            context=context,
            headers=self._schema_headers(schema, {"Prefer": prefer}),
            params=self._filters_to_params(filters),
            expected_status=(200, 204),
            service="postgrest",
        )
        return response.data

    async def rpc(
        self,
        function_name: str,
        params: Optional[Mapping[str, Any]] = None,
        *,
        context: Optional[SupabaseRequestContext] = None,
        schema: Optional[str] = None,
    ) -> Any:
        response = await self.request(
            "POST",
            f"/rest/v1/rpc/{quote(function_name)}",
            context=context,
            headers=self._schema_headers(schema),
            json_body=params or {},
            expected_status=(200, 201, 204),
            service="postgrest-rpc",
        )
        return response.data

    # -------------------------------------------------------------------------
    # Auth helpers
    # -------------------------------------------------------------------------

    async def auth_admin_create_user(
        self,
        email: str,
        *,
        password: Optional[str] = None,
        email_confirm: bool = True,
        user_metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[SupabaseRequestContext] = None,
    ) -> Any:
        payload: Dict[str, Any] = {"email": email, "email_confirm": email_confirm, "user_metadata": user_metadata or {}}
        if password:
            payload["password"] = password
        response = await self.request(
            "POST",
            "/auth/v1/admin/users",
            context=context,
            json_body=payload,
            expected_status=(200, 201),
            service="auth-admin",
        )
        return response.data

    async def auth_admin_get_user(self, user_id: str, *, context: Optional[SupabaseRequestContext] = None) -> Any:
        response = await self.request(
            "GET",
            f"/auth/v1/admin/users/{quote(user_id)}",
            context=context,
            expected_status=(200,),
            service="auth-admin",
        )
        return response.data

    async def auth_admin_delete_user(self, user_id: str, *, context: Optional[SupabaseRequestContext] = None) -> Any:
        response = await self.request(
            "DELETE",
            f"/auth/v1/admin/users/{quote(user_id)}",
            context=context,
            expected_status=(200, 204),
            service="auth-admin",
        )
        return response.data

    async def auth_sign_in_with_password(
        self,
        email: str,
        password: str,
        *,
        context: Optional[SupabaseRequestContext] = None,
    ) -> Any:
        response = await self.request(
            "POST",
            "/auth/v1/token?grant_type=password",
            context=context,
            json_body={"email": email, "password": password},
            expected_status=(200,),
            service="auth",
        )
        return response.data

    # -------------------------------------------------------------------------
    # Storage helpers
    # -------------------------------------------------------------------------

    async def storage_upload(
        self,
        bucket: str,
        path: str,
        content: bytes,
        *,
        context: Optional[SupabaseRequestContext] = None,
        content_type: Optional[str] = None,
        upsert: StorageUpsertMode = StorageUpsertMode.ERROR,
        cache_control: Optional[str] = None,
    ) -> StorageObject:
        headers = {
            "Content-Type": content_type or _content_type_for_path(path),
            "x-upsert": "true" if upsert == StorageUpsertMode.UPSERT else "false",
        }
        if cache_control:
            headers["Cache-Control"] = cache_control
        response = await self.request(
            "POST",
            f"/storage/v1/object/{quote(bucket)}/{quote(path, safe='/')}",
            context=context,
            headers=headers,
            content=content,
            expected_status=(200, 201),
            service="storage",
        )
        return StorageObject(
            bucket=bucket,
            path=path,
            content_type=headers["Content-Type"],
            size_bytes=len(content),
            etag=response.headers.get("etag"),
            metadata={"response": response.data},
        )

    async def storage_download(self, bucket: str, path: str, *, context: Optional[SupabaseRequestContext] = None) -> bytes:
        response = await self.request(
            "GET",
            f"/storage/v1/object/{quote(bucket)}/{quote(path, safe='/')}",
            context=context,
            expected_status=(200,),
            service="storage",
        )
        if isinstance(response.data, str):
            return response.data.encode("utf-8")
        if response.data is None:
            return b""
        return json.dumps(response.data).encode("utf-8")

    async def storage_delete(
        self,
        bucket: str,
        paths: Sequence[str],
        *,
        context: Optional[SupabaseRequestContext] = None,
    ) -> Any:
        response = await self.request(
            "DELETE",
            f"/storage/v1/object/{quote(bucket)}",
            context=context,
            json_body={"prefixes": list(paths)},
            expected_status=(200,),
            service="storage",
        )
        return response.data

    async def storage_create_signed_url(
        self,
        bucket: str,
        path: str,
        expires_in_seconds: int = 3600,
        *,
        context: Optional[SupabaseRequestContext] = None,
    ) -> Any:
        response = await self.request(
            "POST",
            f"/storage/v1/object/sign/{quote(bucket)}/{quote(path, safe='/')}",
            context=context,
            json_body={"expiresIn": expires_in_seconds},
            expected_status=(200,),
            service="storage",
        )
        return response.data

    # -------------------------------------------------------------------------
    # Edge Functions
    # -------------------------------------------------------------------------

    async def invoke_function(
        self,
        function_name: str,
        payload: Optional[Mapping[str, Any]] = None,
        *,
        context: Optional[SupabaseRequestContext] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Any:
        response = await self.request(
            "POST",
            f"/functions/v1/{quote(function_name)}",
            context=context,
            headers=headers,
            json_body=payload or {},
            expected_status=(200, 201, 202, 204),
            service="edge-functions",
        )
        return response.data

    # -------------------------------------------------------------------------
    # Health
    # -------------------------------------------------------------------------

    async def health_check(self, *, context: Optional[SupabaseRequestContext] = None) -> Mapping[str, Any]:
        started = time.perf_counter()
        try:
            response = await self.request(
                "GET",
                "/rest/v1/",
                context=context,
                expected_status=(200, 404),
                service="health",
            )
            return {
                "ok": True,
                "status_code": response.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 4),
                "checked_at": _utc_now().isoformat(),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": exc.__class__.__name__,
                "message": str(exc),
                "latency_ms": round((time.perf_counter() - started) * 1000, 4),
                "checked_at": _utc_now().isoformat(),
            }

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _auth_token(self, context: SupabaseRequestContext) -> str:
        if context.jwt:
            return context.jwt
        if self.config.auth_mode == SupabaseAuthMode.JWT:
            assert self.config.jwt is not None
            return self.config.jwt
        if self.config.auth_mode == SupabaseAuthMode.SERVICE_ROLE:
            assert self.config.service_role_key is not None
            return self.config.service_role_key
        assert self.config.anon_key is not None
        return self.config.anon_key

    def _api_key(self) -> str:
        if self.config.auth_mode == SupabaseAuthMode.SERVICE_ROLE and self.config.service_role_key:
            return self.config.service_role_key
        if self.config.anon_key:
            return self.config.anon_key
        if self.config.jwt:
            return self.config.jwt
        raise SupabaseConfigError("No Supabase API key available.")

    def _build_headers(
        self,
        context: SupabaseRequestContext,
        *,
        headers: Optional[Mapping[str, str]],
        service: str,
    ) -> Dict[str, str]:
        token = self._auth_token(context)
        built = {
            "apikey": self._api_key(),
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Request-ID": context.request_id,
            "X-Client-Info": self.config.user_agent,
        }
        if context.tenant_id:
            built["X-Tenant-ID"] = context.tenant_id
        if context.trace_id:
            built["X-Trace-ID"] = context.trace_id
        if context.role:
            built["X-Supabase-Role"] = context.role
        if context.idempotency_key:
            built["Idempotency-Key"] = context.idempotency_key
        built.update(context.headers)
        if headers:
            built.update(headers)
        return built

    def _schema_headers(self, schema: Optional[str], extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        schema_name = schema or self.config.default_schema
        headers = {
            "Accept-Profile": schema_name,
            "Content-Profile": schema_name,
        }
        if extra:
            headers.update(extra)
        return headers

    def _build_postgrest_params(self, query: PostgrestQuery) -> Dict[str, Any]:
        params: Dict[str, Any] = {"select": query.select}
        params.update(self._filters_to_params(query.filters))
        if query.order_by:
            params["order"] = f"{query.order_by}.{query.order_direction.value}"
        if query.limit is not None:
            params["limit"] = query.limit
        if query.offset is not None:
            params["offset"] = query.offset
        return params

    def _filters_to_params(self, filters: Mapping[str, Any]) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        for key, value in filters.items():
            if isinstance(value, str) and self._looks_like_postgrest_operator(value):
                params[key] = value
            elif isinstance(value, (list, tuple, set)):
                encoded = ",".join(str(v) for v in value)
                params[key] = f"in.({encoded})"
            elif value is None:
                params[key] = "is.null"
            else:
                params[key] = f"eq.{value}"
        return params

    def _looks_like_postgrest_operator(self, value: str) -> bool:
        prefixes = (
            "eq.", "neq.", "gt.", "gte.", "lt.", "lte.", "like.", "ilike.", "is.", "in.",
            "cs.", "cd.", "ov.", "sl.", "sr.", "nxr.", "nxl.", "fts.", "plfts.", "phfts.", "wfts.",
        )
        return value.startswith(prefixes)

    def _clean_params(self, params: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
        if not params:
            return None
        return {key: value for key, value in params.items() if value is not None}

    def _is_retryable_status(self, status_code: int) -> bool:
        return status_code in {408, 425, 429, 500, 502, 503, 504}

    def _is_timeout_exception(self, exc: Exception) -> bool:
        if httpx is None:
            return False
        return isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError))  # type: ignore[union-attr]

    async def _sleep_before_retry(self, attempt: int) -> None:
        delay_ms = self.config.retry_base_delay_ms * (2**attempt) + random.randint(0, self.config.retry_jitter_ms)
        await asyncio.sleep(delay_ms / 1000)

    async def _audit_request(
        self,
        event_name: str,
        context: SupabaseRequestContext,
        method: str,
        url: str,
        latency_ms: float,
        status_code: Optional[int],
    ) -> None:
        if not self.config.audit_enabled:
            return
        try:
            await self.audit_sink.write(
                event_name,
                {
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.tenant_id,
                    "user_hash": _hash_value(context.user_id, self.config.privacy_hash_salt),
                    "method": method.upper(),
                    "url_hash": _stable_hash({"url": url}),
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                    "created_at": _utc_now().isoformat(),
                },
            )
        except Exception:
            logger.exception("Failed to write Supabase audit event", extra={"event_name": event_name})


# =============================================================================
# Factory
# =============================================================================


def build_supabase_client(
    config: Optional[SupabaseClientConfig] = None,
    metrics: Optional[MetricsClient] = None,
    audit_sink: Optional[AuditSink] = None,
    client: Optional[Any] = None,
) -> SupabaseClient:
    return SupabaseClient(
        config=config or SupabaseClientConfig.from_env(),
        metrics=metrics,
        audit_sink=audit_sink,
        client=client,
    )


# =============================================================================
# Manual smoke example
# =============================================================================


async def _demo() -> None:
    logging.basicConfig(level=logging.INFO)
    config = SupabaseClientConfig.from_env(auth_mode=SupabaseAuthMode.SERVICE_ROLE)
    async with build_supabase_client(config=config) as client:
        health = await client.health_check(context=SupabaseRequestContext(tenant_id="tenant-demo"))
        print(json.dumps(health, indent=2, ensure_ascii=False, default=str))

        # Example PostgREST query:
        # rows = await client.select(PostgrestQuery(table="config", limit=5))
        # print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(_demo())

"""
data/integrations/supabase_connector.py

Enterprise-grade Supabase connector.

This module provides a production-ready integration layer for Supabase services:
- PostgREST database API
- Supabase Storage API
- Edge Functions invocation
- Authenticated HTTP session with service role / anon key support
- Retries with exponential backoff and jitter
- Circuit breaker protection
- Structured request/response models
- Pagination helpers
- Bulk insert/upsert/delete helpers
- Storage upload/download/list/delete helpers
- Audit and metrics sink integration
- Safe logging with secret redaction
- Dependency-light implementation based on requests

Environment variables commonly used:
- SUPABASE_URL
- SUPABASE_ANON_KEY
- SUPABASE_SERVICE_ROLE_KEY
- SUPABASE_JWT
- SUPABASE_TIMEOUT_SECONDS
- SUPABASE_MAX_RETRIES
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple
from urllib.parse import quote, urlencode

try:
    import requests
    from requests import Response, Session
except Exception:  # pragma: no cover
    requests = None  # type: ignore
    Response = Any  # type: ignore
    Session = Any  # type: ignore


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# =============================================================================
# Exceptions
# =============================================================================


class SupabaseConnectorError(Exception):
    """Base exception for Supabase connector failures."""


class SupabaseConfigurationError(SupabaseConnectorError):
    """Raised when connector configuration is invalid."""


class SupabaseDependencyError(SupabaseConnectorError):
    """Raised when required dependencies are missing."""


class SupabaseRequestError(SupabaseConnectorError):
    """Raised when Supabase returns an unsuccessful response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        response_body: Optional[Any] = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.request_id = request_id


class SupabaseCircuitOpenError(SupabaseConnectorError):
    """Raised when circuit breaker is open."""


# =============================================================================
# Protocols
# =============================================================================


class MetricsSink(Protocol):
    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def timing(self, metric_name: str, value_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...


class AuditSink(Protocol):
    def write_event(self, event: Mapping[str, Any]) -> None:
        ...


# =============================================================================
# Enums / config
# =============================================================================


class SupabaseAuthMode(str, Enum):
    ANON_KEY = "anon_key"
    SERVICE_ROLE = "service_role"
    JWT = "jwt"


class ConflictResolution(str, Enum):
    IGNORE_DUPLICATES = "ignore_duplicates"
    MERGE_DUPLICATES = "merge_duplicates"


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 10.0
    backoff_multiplier: float = 2.0
    jitter: bool = True
    retry_status_codes: Sequence[int] = (408, 409, 425, 429, 500, 502, 503, 504)

    def validate(self) -> None:
        if self.max_attempts < 1:
            raise SupabaseConfigurationError("max_attempts must be at least 1.")
        if self.base_delay_seconds < 0:
            raise SupabaseConfigurationError("base_delay_seconds cannot be negative.")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise SupabaseConfigurationError("max_delay_seconds must be >= base_delay_seconds.")
        if self.backoff_multiplier < 1:
            raise SupabaseConfigurationError("backoff_multiplier must be >= 1.")


@dataclass(frozen=True)
class CircuitBreakerConfig:
    enabled: bool = True
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0
    half_open_max_calls: int = 1

    def validate(self) -> None:
        if self.failure_threshold < 1:
            raise SupabaseConfigurationError("failure_threshold must be at least 1.")
        if self.recovery_timeout_seconds <= 0:
            raise SupabaseConfigurationError("recovery_timeout_seconds must be positive.")
        if self.half_open_max_calls < 1:
            raise SupabaseConfigurationError("half_open_max_calls must be at least 1.")


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    anon_key: Optional[str] = None
    service_role_key: Optional[str] = None
    jwt: Optional[str] = None
    auth_mode: SupabaseAuthMode = SupabaseAuthMode.SERVICE_ROLE
    schema: str = "public"
    timeout_seconds: float = 30.0
    verify_ssl: bool = True
    user_agent: str = "data-platform-supabase-connector/1.0"
    retry: RetryConfig = field(default_factory=RetryConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    default_page_size: int = 1000
    max_page_size: int = 10000

    @staticmethod
    def from_env(prefix: str = "SUPABASE") -> "SupabaseConfig":
        url = os.getenv(f"{prefix}_URL", "").strip()
        auth_mode = SupabaseAuthMode(os.getenv(f"{prefix}_AUTH_MODE", SupabaseAuthMode.SERVICE_ROLE.value))
        return SupabaseConfig(
            url=url,
            anon_key=os.getenv(f"{prefix}_ANON_KEY"),
            service_role_key=os.getenv(f"{prefix}_SERVICE_ROLE_KEY"),
            jwt=os.getenv(f"{prefix}_JWT"),
            auth_mode=auth_mode,
            schema=os.getenv(f"{prefix}_SCHEMA", "public"),
            timeout_seconds=float(os.getenv(f"{prefix}_TIMEOUT_SECONDS", "30")),
            verify_ssl=os.getenv(f"{prefix}_VERIFY_SSL", "true").lower() in {"1", "true", "yes", "y"},
            retry=RetryConfig(max_attempts=int(os.getenv(f"{prefix}_MAX_RETRIES", "3"))),
            default_page_size=int(os.getenv(f"{prefix}_DEFAULT_PAGE_SIZE", "1000")),
            max_page_size=int(os.getenv(f"{prefix}_MAX_PAGE_SIZE", "10000")),
        )

    def validate(self) -> None:
        if not self.url.strip():
            raise SupabaseConfigurationError("Supabase URL is required.")
        if not self.url.startswith(("http://", "https://")):
            raise SupabaseConfigurationError("Supabase URL must start with http:// or https://.")
        if self.auth_mode == SupabaseAuthMode.ANON_KEY and not self.anon_key:
            raise SupabaseConfigurationError("anon_key is required for ANON_KEY auth mode.")
        if self.auth_mode == SupabaseAuthMode.SERVICE_ROLE and not self.service_role_key:
            raise SupabaseConfigurationError("service_role_key is required for SERVICE_ROLE auth mode.")
        if self.auth_mode == SupabaseAuthMode.JWT and not self.jwt:
            raise SupabaseConfigurationError("jwt is required for JWT auth mode.")
        if self.timeout_seconds <= 0:
            raise SupabaseConfigurationError("timeout_seconds must be positive.")
        if self.default_page_size < 1:
            raise SupabaseConfigurationError("default_page_size must be positive.")
        if self.max_page_size < self.default_page_size:
            raise SupabaseConfigurationError("max_page_size must be >= default_page_size.")
        self.retry.validate()
        self.circuit_breaker.validate()


# =============================================================================
# Models
# =============================================================================


@dataclass
class SupabaseResponse:
    request_id: str
    status_code: int
    data: Any
    headers: Dict[str, str]
    duration_ms: float
    count: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class StorageObject:
    name: str
    bucket: str
    path: str
    id: Optional[str] = None
    updated_at: Optional[str] = None
    created_at: Optional[str] = None
    last_accessed_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class CircuitBreakerState:
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    opened_at: Optional[float] = None
    half_open_calls: int = 0


# =============================================================================
# Helpers
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_requests() -> None:
    if requests is None:
        raise SupabaseDependencyError("requests is required. Install with: pip install requests")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    return value


def _redact(value: Any) -> Any:
    if value is None:
        return None
    text = str(value)
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}***{text[-4:]}"


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(_json_safe(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _clean_base_url(url: str) -> str:
    return url.rstrip("/")


def _quote_path(path: str) -> str:
    return "/".join(quote(part, safe="") for part in path.strip("/").split("/") if part)


def _content_type_for_path(path: str) -> str:
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


# =============================================================================
# Sinks
# =============================================================================


class NoopMetricsSink:
    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        return None

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        return None

    def timing(self, metric_name: str, value_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        return None


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.events: List[Mapping[str, Any]] = []

    def write_event(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))


# =============================================================================
# Connector
# =============================================================================


class SupabaseConnector:
    """Enterprise Supabase connector."""

    def __init__(
        self,
        config: SupabaseConfig,
        *,
        session: Optional[Session] = None,
        metrics_sink: Optional[MetricsSink] = None,
        audit_sink: Optional[AuditSink] = None,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        _require_requests()
        self.config = config
        self.config.validate()
        self.base_url = _clean_base_url(config.url)
        self.session: Session = session or requests.Session()  # type: ignore[union-attr]
        self.metrics_sink = metrics_sink or NoopMetricsSink()
        self.audit_sink = audit_sink
        self.logger = logger_ or logger
        self.circuit = CircuitBreakerState()
        self._configure_session()

    @classmethod
    def from_env(
        cls,
        prefix: str = "SUPABASE",
        *,
        metrics_sink: Optional[MetricsSink] = None,
        audit_sink: Optional[AuditSink] = None,
    ) -> "SupabaseConnector":
        return cls(SupabaseConfig.from_env(prefix), metrics_sink=metrics_sink, audit_sink=audit_sink)

    def _configure_session(self) -> None:
        token = self._auth_token()
        api_key = self.config.service_role_key or self.config.anon_key or token
        self.session.headers.update(
            {
                "apikey": api_key or "",
                "Authorization": f"Bearer {token}",
                "User-Agent": self.config.user_agent,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Client-Info": self.config.user_agent,
            }
        )

    def _auth_token(self) -> str:
        if self.config.auth_mode == SupabaseAuthMode.JWT:
            return self.config.jwt or ""
        if self.config.auth_mode == SupabaseAuthMode.SERVICE_ROLE:
            return self.config.service_role_key or ""
        return self.config.anon_key or ""

    # ------------------------------------------------------------------
    # Low-level HTTP
    # ------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        base: str = "rest",
        params: Optional[Mapping[str, Any]] = None,
        json_body: Optional[Any] = None,
        data: Optional[Any] = None,
        headers: Optional[Mapping[str, str]] = None,
        expected_status: Sequence[int] = (200, 201, 202, 204),
        return_binary: bool = False,
        timeout_seconds: Optional[float] = None,
    ) -> SupabaseResponse:
        request_id = str(uuid.uuid4())
        url = self._url(base, path)
        final_headers = dict(headers or {})
        if base == "rest":
            final_headers.setdefault("Accept-Profile", self.config.schema)
            final_headers.setdefault("Content-Profile", self.config.schema)

        self._before_request()
        started = time.perf_counter()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.config.retry.max_attempts + 1):
            try:
                response = self.session.request(
                    method=method.upper(),
                    url=url,
                    params={k: v for k, v in (params or {}).items() if v is not None},
                    json=json_body,
                    data=data,
                    headers=final_headers,
                    timeout=timeout_seconds or self.config.timeout_seconds,
                    verify=self.config.verify_ssl,
                )
                duration_ms = (time.perf_counter() - started) * 1000
                if response.status_code in expected_status:
                    self._record_success(base, method, response.status_code, duration_ms)
                    parsed = self._parse_response(response, return_binary=return_binary)
                    result = SupabaseResponse(
                        request_id=request_id,
                        status_code=response.status_code,
                        data=parsed,
                        headers=dict(response.headers),
                        duration_ms=duration_ms,
                        count=self._parse_count(response),
                        metadata={"attempt": attempt, "base": base, "path": path},
                    )
                    self._audit("supabase_request_succeeded", request_id, method, base, path, response.status_code, duration_ms)
                    return result

                if response.status_code in self.config.retry.retry_status_codes and attempt < self.config.retry.max_attempts:
                    self._sleep_before_retry(attempt)
                    continue

                body = self._parse_response(response, return_binary=False)
                self._record_failure(base, method, response.status_code)
                self._audit("supabase_request_failed", request_id, method, base, path, response.status_code, duration_ms, body)
                raise SupabaseRequestError(
                    f"Supabase request failed with HTTP {response.status_code}: {body}",
                    status_code=response.status_code,
                    response_body=body,
                    request_id=request_id,
                )
            except SupabaseRequestError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self._record_failure(base, method, None)
                if attempt < self.config.retry.max_attempts:
                    self._sleep_before_retry(attempt)
                    continue
                duration_ms = (time.perf_counter() - started) * 1000
                self._audit("supabase_request_exception", request_id, method, base, path, None, duration_ms, {"error": str(exc)})
                raise SupabaseRequestError(
                    f"Supabase request exception after {attempt} attempts: {exc}",
                    request_id=request_id,
                ) from exc

        raise SupabaseRequestError(f"Supabase request failed: {last_error}", request_id=request_id)

    def _url(self, base: str, path: str) -> str:
        clean_path = path.lstrip("/")
        if base == "rest":
            return f"{self.base_url}/rest/v1/{clean_path}"
        if base == "storage":
            return f"{self.base_url}/storage/v1/{clean_path}"
        if base == "functions":
            return f"{self.base_url}/functions/v1/{clean_path}"
        if base == "auth":
            return f"{self.base_url}/auth/v1/{clean_path}"
        raise SupabaseConfigurationError(f"Unsupported Supabase API base: {base}")

    def _parse_response(self, response: Response, *, return_binary: bool) -> Any:
        if return_binary:
            return response.content
        if response.status_code == 204 or not response.content:
            return None
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            try:
                return response.json()
            except Exception:
                return response.text
        try:
            return response.json()
        except Exception:
            return response.text

    def _parse_count(self, response: Response) -> Optional[int]:
        content_range = response.headers.get("Content-Range")
        if not content_range or "/" not in content_range:
            return None
        total = content_range.rsplit("/", 1)[-1]
        if total == "*":
            return None
        try:
            return int(total)
        except ValueError:
            return None

    def _sleep_before_retry(self, attempt: int) -> None:
        delay = min(
            self.config.retry.max_delay_seconds,
            self.config.retry.base_delay_seconds * (self.config.retry.backoff_multiplier ** (attempt - 1)),
        )
        if self.config.retry.jitter:
            delay *= random.uniform(0.5, 1.5)
        time.sleep(delay)

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    def _before_request(self) -> None:
        cfg = self.config.circuit_breaker
        if not cfg.enabled:
            return
        if self.circuit.state == CircuitState.OPEN:
            elapsed = time.time() - (self.circuit.opened_at or 0)
            if elapsed >= cfg.recovery_timeout_seconds:
                self.circuit.state = CircuitState.HALF_OPEN
                self.circuit.half_open_calls = 0
            else:
                raise SupabaseCircuitOpenError("Supabase circuit breaker is open.")
        if self.circuit.state == CircuitState.HALF_OPEN:
            if self.circuit.half_open_calls >= cfg.half_open_max_calls:
                raise SupabaseCircuitOpenError("Supabase circuit breaker is half-open and call limit was reached.")
            self.circuit.half_open_calls += 1

    def _record_success(self, base: str, method: str, status_code: int, duration_ms: float) -> None:
        if self.config.circuit_breaker.enabled:
            self.circuit.state = CircuitState.CLOSED
            self.circuit.failure_count = 0
            self.circuit.opened_at = None
            self.circuit.half_open_calls = 0
        tags = {"base": base, "method": method.upper(), "status_code": str(status_code)}
        self.metrics_sink.increment("supabase.request.success", tags=tags)
        self.metrics_sink.timing("supabase.request.duration_ms", duration_ms, tags=tags)

    def _record_failure(self, base: str, method: str, status_code: Optional[int]) -> None:
        if self.config.circuit_breaker.enabled:
            self.circuit.failure_count += 1
            if self.circuit.failure_count >= self.config.circuit_breaker.failure_threshold:
                self.circuit.state = CircuitState.OPEN
                self.circuit.opened_at = time.time()
        tags = {"base": base, "method": method.upper(), "status_code": str(status_code or "exception")}
        self.metrics_sink.increment("supabase.request.failure", tags=tags)
        self.metrics_sink.gauge("supabase.circuit.failure_count", self.circuit.failure_count, tags={"state": self.circuit.state.value})

    def _audit(
        self,
        event_type: str,
        request_id: str,
        method: str,
        base: str,
        path: str,
        status_code: Optional[int],
        duration_ms: float,
        payload: Optional[Any] = None,
    ) -> None:
        if not self.audit_sink:
            return
        self.audit_sink.write_event(
            {
                "event_type": event_type,
                "request_id": request_id,
                "timestamp": utc_now_iso(),
                "method": method.upper(),
                "base": base,
                "path": path,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "payload_hash": _stable_hash(payload) if payload is not None else None,
            }
        )

    # ------------------------------------------------------------------
    # PostgREST database API
    # ------------------------------------------------------------------

    def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: Optional[Mapping[str, Any]] = None,
        order: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        count: Optional[str] = "exact",
    ) -> SupabaseResponse:
        params: Dict[str, Any] = {"select": columns}
        params.update(self._filters_to_params(filters or {}))
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        headers = {"Prefer": f"count={count}"} if count else None
        return self.request("GET", table, params=params, headers=headers)

    def select_all(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: Optional[Mapping[str, Any]] = None,
        order: Optional[str] = None,
        page_size: Optional[int] = None,
        max_rows: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        page_size = min(page_size or self.config.default_page_size, self.config.max_page_size)
        offset = 0
        rows: List[Dict[str, Any]] = []
        while True:
            response = self.select(
                table,
                columns=columns,
                filters=filters,
                order=order,
                limit=page_size,
                offset=offset,
            )
            batch = response.data or []
            if not isinstance(batch, list):
                raise SupabaseRequestError("Expected list response for select_all.")
            rows.extend(batch)
            if len(batch) < page_size:
                break
            if max_rows is not None and len(rows) >= max_rows:
                rows = rows[:max_rows]
                break
            offset += page_size
        return rows

    def insert(
        self,
        table: str,
        rows: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        returning: str = "representation",
    ) -> SupabaseResponse:
        return self.request(
            "POST",
            table,
            json_body=_json_safe(rows),
            headers={"Prefer": f"return={returning}"},
            expected_status=(200, 201),
        )

    def upsert(
        self,
        table: str,
        rows: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        on_conflict: Optional[Sequence[str] | str] = None,
        resolution: ConflictResolution = ConflictResolution.MERGE_DUPLICATES,
        returning: str = "representation",
    ) -> SupabaseResponse:
        params: Dict[str, Any] = {}
        if on_conflict:
            params["on_conflict"] = ",".join(on_conflict) if isinstance(on_conflict, (list, tuple)) else on_conflict
        prefer = [f"return={returning}"]
        if resolution == ConflictResolution.IGNORE_DUPLICATES:
            prefer.append("resolution=ignore-duplicates")
        else:
            prefer.append("resolution=merge-duplicates")
        return self.request(
            "POST",
            table,
            params=params,
            json_body=_json_safe(rows),
            headers={"Prefer": ",".join(prefer)},
            expected_status=(200, 201),
        )

    def update(
        self,
        table: str,
        values: Mapping[str, Any],
        *,
        filters: Mapping[str, Any],
        returning: str = "representation",
    ) -> SupabaseResponse:
        params = self._filters_to_params(filters)
        return self.request(
            "PATCH",
            table,
            params=params,
            json_body=_json_safe(values),
            headers={"Prefer": f"return={returning}"},
        )

    def delete(
        self,
        table: str,
        *,
        filters: Mapping[str, Any],
        returning: str = "representation",
    ) -> SupabaseResponse:
        params = self._filters_to_params(filters)
        return self.request(
            "DELETE",
            table,
            params=params,
            headers={"Prefer": f"return={returning}"},
        )

    def rpc(self, function_name: str, params: Optional[Mapping[str, Any]] = None) -> SupabaseResponse:
        return self.request("POST", f"rpc/{function_name}", json_body=_json_safe(params or {}))

    def bulk_insert(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        batch_size: int = 1000,
        returning: str = "minimal",
    ) -> List[SupabaseResponse]:
        return [
            self.insert(table, rows[i : i + batch_size], returning=returning)
            for i in range(0, len(rows), batch_size)
        ]

    def bulk_upsert(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        on_conflict: Optional[Sequence[str] | str] = None,
        batch_size: int = 1000,
        returning: str = "minimal",
    ) -> List[SupabaseResponse]:
        return [
            self.upsert(table, rows[i : i + batch_size], on_conflict=on_conflict, returning=returning)
            for i in range(0, len(rows), batch_size)
        ]

    def _filters_to_params(self, filters: Mapping[str, Any]) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        for key, value in filters.items():
            if isinstance(value, str) and re.match(r"^(eq|neq|gt|gte|lt|lte|like|ilike|in|is|cs|cd|ov|fts|plfts|phfts|wfts)\.", value):
                params[key] = value
            elif isinstance(value, (list, tuple, set)):
                params[key] = f"in.({','.join(str(v) for v in value)})"
            elif value is None:
                params[key] = "is.null"
            else:
                params[key] = f"eq.{value}"
        return params

    # ------------------------------------------------------------------
    # Storage API
    # ------------------------------------------------------------------

    def create_bucket(self, bucket: str, *, public: bool = False, file_size_limit: Optional[int] = None) -> SupabaseResponse:
        body: Dict[str, Any] = {"id": bucket, "name": bucket, "public": public}
        if file_size_limit is not None:
            body["file_size_limit"] = file_size_limit
        return self.request("POST", "bucket", base="storage", json_body=body, expected_status=(200, 201))

    def list_buckets(self) -> List[Dict[str, Any]]:
        response = self.request("GET", "bucket", base="storage")
        return response.data or []

    def delete_bucket(self, bucket: str) -> SupabaseResponse:
        return self.request("DELETE", f"bucket/{quote(bucket, safe='')}", base="storage")

    def empty_bucket(self, bucket: str) -> SupabaseResponse:
        return self.request("POST", f"bucket/{quote(bucket, safe='')}/empty", base="storage")

    def upload_file(
        self,
        bucket: str,
        object_path: str,
        file_path: str | Path,
        *,
        upsert: bool = True,
        content_type: Optional[str] = None,
        cache_control: str = "3600",
    ) -> SupabaseResponse:
        path = Path(file_path)
        content = path.read_bytes()
        return self.upload_bytes(
            bucket,
            object_path,
            content,
            upsert=upsert,
            content_type=content_type or _content_type_for_path(str(path)),
            cache_control=cache_control,
        )

    def upload_bytes(
        self,
        bucket: str,
        object_path: str,
        content: bytes,
        *,
        upsert: bool = True,
        content_type: str = "application/octet-stream",
        cache_control: str = "3600",
    ) -> SupabaseResponse:
        headers = {
            "Content-Type": content_type,
            "Cache-Control": cache_control,
            "x-upsert": "true" if upsert else "false",
        }
        return self.request(
            "POST",
            f"object/{quote(bucket, safe='')}/{_quote_path(object_path)}",
            base="storage",
            data=content,
            headers=headers,
            expected_status=(200, 201),
        )

    def download_bytes(self, bucket: str, object_path: str) -> bytes:
        response = self.request(
            "GET",
            f"object/{quote(bucket, safe='')}/{_quote_path(object_path)}",
            base="storage",
            return_binary=True,
        )
        return response.data or b""

    def download_file(self, bucket: str, object_path: str, destination: str | Path) -> Path:
        output = Path(destination)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(self.download_bytes(bucket, object_path))
        return output

    def list_objects(
        self,
        bucket: str,
        *,
        prefix: str = "",
        limit: int = 100,
        offset: int = 0,
        sort_by: Optional[Mapping[str, str]] = None,
    ) -> List[StorageObject]:
        body = {
            "prefix": prefix,
            "limit": limit,
            "offset": offset,
            "sortBy": sort_by or {"column": "name", "order": "asc"},
        }
        response = self.request("POST", f"object/list/{quote(bucket, safe='')}", base="storage", json_body=body)
        objects = []
        for item in response.data or []:
            name = item.get("name", "")
            objects.append(
                StorageObject(
                    name=name,
                    bucket=bucket,
                    path=f"{prefix.rstrip('/')}/{name}".strip("/"),
                    id=item.get("id"),
                    updated_at=item.get("updated_at"),
                    created_at=item.get("created_at"),
                    last_accessed_at=item.get("last_accessed_at"),
                    metadata=item.get("metadata") or {},
                )
            )
        return objects

    def delete_objects(self, bucket: str, object_paths: Sequence[str]) -> SupabaseResponse:
        return self.request(
            "DELETE",
            f"object/{quote(bucket, safe='')}",
            base="storage",
            json_body={"prefixes": list(object_paths)},
        )

    def signed_url(self, bucket: str, object_path: str, *, expires_in_seconds: int = 3600) -> str:
        response = self.request(
            "POST",
            f"object/sign/{quote(bucket, safe='')}/{_quote_path(object_path)}",
            base="storage",
            json_body={"expiresIn": expires_in_seconds},
        )
        signed = (response.data or {}).get("signedURL") or (response.data or {}).get("signedUrl")
        if not signed:
            raise SupabaseRequestError("Supabase did not return a signed URL.")
        if signed.startswith("http"):
            return signed
        return f"{self.base_url}/storage/v1{signed}"

    def public_url(self, bucket: str, object_path: str) -> str:
        return f"{self.base_url}/storage/v1/object/public/{quote(bucket, safe='')}/{_quote_path(object_path)}"

    # ------------------------------------------------------------------
    # Edge Functions
    # ------------------------------------------------------------------

    def invoke_function(
        self,
        function_name: str,
        *,
        payload: Optional[Any] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> SupabaseResponse:
        return self.request(
            "POST",
            function_name,
            base="functions",
            json_body=_json_safe(payload or {}),
            headers=headers,
            expected_status=(200, 201, 202),
        )

    # ------------------------------------------------------------------
    # Health / diagnostics
    # ------------------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            response = self.request("GET", "", base="rest", params={"select": "*"}, expected_status=(200, 404))
            ok = response.status_code in {200, 404}
            error = None
        except Exception as exc:  # noqa: BLE001
            ok = False
            error = str(exc)
        return {
            "ok": ok,
            "url": self.base_url,
            "schema": self.config.schema,
            "auth_mode": self.config.auth_mode.value,
            "service_role_key": _redact(self.config.service_role_key),
            "anon_key": _redact(self.config.anon_key),
            "circuit_state": self.circuit.state.value,
            "duration_ms": round((time.perf_counter() - started) * 1000, 4),
            "error": error,
            "checked_at": utc_now_iso(),
        }

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "SupabaseConnector":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self.close()
        return False


# =============================================================================
# Convenience API
# =============================================================================


def create_supabase_connector_from_env(prefix: str = "SUPABASE") -> SupabaseConnector:
    return SupabaseConnector.from_env(prefix)


# =============================================================================
# Local smoke example
# =============================================================================


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    connector = SupabaseConnector.from_env(audit_sink=InMemoryAuditSink())
    print(json.dumps(connector.health_check(), indent=2, ensure_ascii=False))

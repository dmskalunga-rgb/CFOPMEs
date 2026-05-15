#!/usr/bin/env python3
"""
api/core/dependencies.py

Enterprise-grade FastAPI dependency layer.

Objetivo:
- Centralizar dependências reutilizáveis da API.
- Fornecer providers para settings, request context, autenticação, autorização, paginação,
  idempotência, rate limit, correlação, serviços singleton e validações comuns.
- Reduzir acoplamento entre routers e implementações concretas.
- Padronizar erros, auditoria e metadados por request.

Uso típico:
    from fastapi import APIRouter, Depends
    from api.core.dependencies import (
        get_settings,
        get_request_context,
        get_current_principal,
        require_permission,
        get_pagination,
    )

    router = APIRouter()

    @router.get('/items')
    async def list_items(
        ctx = Depends(get_request_context),
        pagination = Depends(get_pagination),
        user = Depends(require_permission('items:read')),
    ):
        return {'request_id': ctx.request_id, 'limit': pagination.limit}
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

try:
    from fastapi import Depends, Header, HTTPException, Query, Request, Response, status
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Dependências ausentes. Instale com: pip install fastapi") from exc

try:
    from api.auth.service import AuthService, AuthSettings, Principal, AuthenticationError, AuthorizationError
except Exception:  # pragma: no cover
    AuthService = None  # type: ignore
    AuthSettings = None  # type: ignore
    Principal = Any  # type: ignore
    AuthenticationError = Exception  # type: ignore
    AuthorizationError = Exception  # type: ignore


logger = logging.getLogger(__name__)
UTC = timezone.utc


@dataclass(frozen=True)
class ApiSettings:
    app_name: str = "Enterprise AI API"
    app_version: str = "1.0.0"
    environment: str = "development"
    debug: bool = False
    api_key: Optional[str] = None
    cors_origins: List[str] = field(default_factory=lambda: ["*"])
    request_timeout_seconds: int = 30
    default_page_size: int = 50
    max_page_size: int = 500
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_max_requests: int = 300
    idempotency_ttl_seconds: int = 3600
    require_idempotency_for_writes: bool = False
    enable_auth: bool = True
    enable_audit: bool = True

    @staticmethod
    def from_env() -> "ApiSettings":
        return ApiSettings(
            app_name=os.getenv("API_NAME", "Enterprise AI API"),
            app_version=os.getenv("API_VERSION", "1.0.0"),
            environment=os.getenv("API_ENV", "development"),
            debug=parse_bool(os.getenv("API_DEBUG"), False),
            api_key=os.getenv("API_KEY"),
            cors_origins=parse_csv(os.getenv("API_CORS_ORIGINS", "*")),
            request_timeout_seconds=int(os.getenv("API_REQUEST_TIMEOUT_SECONDS", "30")),
            default_page_size=int(os.getenv("API_DEFAULT_PAGE_SIZE", "50")),
            max_page_size=int(os.getenv("API_MAX_PAGE_SIZE", "500")),
            rate_limit_enabled=parse_bool(os.getenv("API_RATE_LIMIT_ENABLED"), True),
            rate_limit_window_seconds=int(os.getenv("API_RATE_LIMIT_WINDOW_SECONDS", "60")),
            rate_limit_max_requests=int(os.getenv("API_RATE_LIMIT_MAX_REQUESTS", "300")),
            idempotency_ttl_seconds=int(os.getenv("API_IDEMPOTENCY_TTL_SECONDS", "3600")),
            require_idempotency_for_writes=parse_bool(os.getenv("API_REQUIRE_IDEMPOTENCY_FOR_WRITES"), False),
            enable_auth=parse_bool(os.getenv("API_ENABLE_AUTH"), True),
            enable_audit=parse_bool(os.getenv("API_ENABLE_AUDIT"), True),
        )


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    correlation_id: str
    idempotency_key: Optional[str]
    method: str
    path: str
    client_ip: Optional[str]
    user_agent: Optional[str]
    started_at: float
    received_at: str
    tenant_id: Optional[str] = None
    traceparent: Optional[str] = None

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.started_at) * 1000, 4)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
            "method": self.method,
            "path": self.path,
            "client_ip": self.client_ip,
            "user_agent": self.user_agent,
            "received_at": self.received_at,
            "elapsed_ms": self.elapsed_ms(),
            "tenant_id": self.tenant_id,
            "traceparent": self.traceparent,
        }


@dataclass(frozen=True)
class PaginationParams:
    page: int
    limit: int
    offset: int
    sort: Optional[str]
    order: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page": self.page,
            "limit": self.limit,
            "offset": self.offset,
            "sort": self.sort,
            "order": self.order,
        }


@dataclass(frozen=True)
class IdempotencyRecord:
    key: str
    request_hash: str
    created_at_epoch: int
    response_payload: Optional[Dict[str, Any]] = None
    status_code: Optional[int] = None

    def expired(self, ttl_seconds: int) -> bool:
        return now_epoch() - self.created_at_epoch > ttl_seconds


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._hits: Dict[str, List[int]] = {}

    def check(self, key: str, max_requests: int, window_seconds: int) -> None:
        current = now_epoch()
        window_start = current - window_seconds
        values = [item for item in self._hits.get(key, []) if item >= window_start]
        if len(values) >= max_requests:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")
        values.append(current)
        self._hits[key] = values


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._records: Dict[str, IdempotencyRecord] = {}

    def get(self, key: str, ttl_seconds: int) -> Optional[IdempotencyRecord]:
        record = self._records.get(key)
        if not record:
            return None
        if record.expired(ttl_seconds):
            self._records.pop(key, None)
            return None
        return record

    def put(self, record: IdempotencyRecord) -> None:
        self._records[record.key] = record

    def cleanup(self, ttl_seconds: int) -> None:
        for key, record in list(self._records.items()):
            if record.expired(ttl_seconds):
                self._records.pop(key, None)


@dataclass
class ServiceContainer:
    settings: ApiSettings
    auth_service: Optional[Any] = None
    rate_limiter: InMemoryRateLimiter = field(default_factory=InMemoryRateLimiter)
    idempotency_store: InMemoryIdempotencyStore = field(default_factory=InMemoryIdempotencyStore)
    services: Dict[str, Any] = field(default_factory=dict)

    def register(self, name: str, service: Any) -> None:
        self.services[name] = service

    def get(self, name: str) -> Any:
        if name not in self.services:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Service unavailable: {name}")
        return self.services[name]


@lru_cache(maxsize=1)
def get_settings() -> ApiSettings:
    return ApiSettings.from_env()


@lru_cache(maxsize=1)
def get_container() -> ServiceContainer:
    settings = get_settings()
    auth_service = None
    if settings.enable_auth and AuthService is not None and AuthSettings is not None:
        auth_service = AuthService(AuthSettings.from_env())
    return ServiceContainer(settings=settings, auth_service=auth_service)


def get_request_context(
    request: Request,
    x_request_id: Optional[str] = Header(default=None),
    x_correlation_id: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
    traceparent: Optional[str] = Header(default=None),
) -> RequestContext:
    request_id = x_request_id or getattr(request.state, "request_id", None) or f"req_{uuid.uuid4().hex}"
    correlation_id = x_correlation_id or request_id
    started_at = getattr(request.state, "started_at", None) or time.perf_counter()
    ctx = RequestContext(
        request_id=request_id,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        client_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        started_at=started_at,
        received_at=utc_now_iso(),
        tenant_id=x_tenant_id,
        traceparent=traceparent,
    )
    request.state.request_context = ctx
    return ctx


def attach_response_headers(response: Response, ctx: RequestContext = Depends(get_request_context)) -> None:
    response.headers["x-request-id"] = ctx.request_id
    response.headers["x-correlation-id"] = ctx.correlation_id


def get_pagination(
    page: int = Query(default=1, ge=1),
    limit: Optional[int] = Query(default=None, ge=1),
    sort: Optional[str] = Query(default=None),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    settings: ApiSettings = Depends(get_settings),
) -> PaginationParams:
    effective_limit = limit or settings.default_page_size
    if effective_limit > settings.max_page_size:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"limit cannot exceed {settings.max_page_size}",
        )
    return PaginationParams(
        page=page,
        limit=effective_limit,
        offset=(page - 1) * effective_limit,
        sort=sort,
        order=order,
    )


def get_rate_limiter(container: ServiceContainer = Depends(get_container)) -> InMemoryRateLimiter:
    return container.rate_limiter


def rate_limit(
    ctx: RequestContext = Depends(get_request_context),
    settings: ApiSettings = Depends(get_settings),
    limiter: InMemoryRateLimiter = Depends(get_rate_limiter),
) -> None:
    if not settings.rate_limit_enabled:
        return
    key = ctx.client_ip or ctx.correlation_id
    limiter.check(key, settings.rate_limit_max_requests, settings.rate_limit_window_seconds)


def require_api_key(
    x_api_key: Optional[str] = Header(default=None),
    settings: ApiSettings = Depends(get_settings),
) -> None:
    if not settings.api_key:
        return
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")


def get_auth_service(container: ServiceContainer = Depends(get_container)) -> Any:
    if container.auth_service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth service unavailable")
    return container.auth_service


def get_current_principal(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
    x_session_id: Optional[str] = Header(default=None),
    ctx: RequestContext = Depends(get_request_context),
    settings: ApiSettings = Depends(get_settings),
    auth_service: Any = Depends(get_auth_service),
    _: None = Depends(rate_limit),
) -> Any:
    if not settings.enable_auth:
        return anonymous_principal()
    try:
        return auth_service.authenticate_request(
            authorization=authorization,
            api_key=x_api_key,
            session_id=x_session_id,
            request_context=ctx.to_dict(),
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except Exception as exc:
        if exc.__class__.__name__ == "RateLimitError":
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed") from exc


def require_permission(permission: str) -> Callable[..., Any]:
    def dependency(
        principal: Any = Depends(get_current_principal),
        auth_service: Any = Depends(get_auth_service),
    ) -> Any:
        try:
            auth_service.authorize(principal, [permission], require_all=True)
            return principal
        except AuthorizationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return dependency


def require_permissions(permissions: Sequence[str], require_all: bool = True) -> Callable[..., Any]:
    def dependency(
        principal: Any = Depends(get_current_principal),
        auth_service: Any = Depends(get_auth_service),
    ) -> Any:
        try:
            auth_service.authorize(principal, list(permissions), require_all=require_all)
            return principal
        except AuthorizationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return dependency


def require_role(role: str) -> Callable[..., Any]:
    def dependency(
        principal: Any = Depends(get_current_principal),
        auth_service: Any = Depends(get_auth_service),
    ) -> Any:
        try:
            auth_service.authorize_roles(principal, [role], require_all=False)
            return principal
        except AuthorizationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return dependency


def require_idempotency_key(
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
    settings: ApiSettings = Depends(get_settings),
    container: ServiceContainer = Depends(get_container),
) -> Optional[IdempotencyRecord]:
    if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if settings.require_idempotency_for_writes and not ctx.idempotency_key:
        raise HTTPException(status_code=status.HTTP_428_PRECONDITION_REQUIRED, detail="Idempotency-Key header is required")
    if not ctx.idempotency_key:
        return None
    request_hash = hash_request_signature(request.method, request.url.path, ctx.tenant_id)
    existing = container.idempotency_store.get(ctx.idempotency_key, settings.idempotency_ttl_seconds)
    if existing and existing.request_hash != request_hash:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Idempotency key reused with different request signature")
    if existing:
        return existing
    record = IdempotencyRecord(
        key=ctx.idempotency_key,
        request_hash=request_hash,
        created_at_epoch=now_epoch(),
    )
    container.idempotency_store.put(record)
    return record


def get_service(name: str) -> Callable[..., Any]:
    def dependency(container: ServiceContainer = Depends(get_container)) -> Any:
        return container.get(name)
    return dependency


def get_json_body_size_guard(max_bytes: int) -> Callable[..., None]:
    async def dependency(request: Request) -> None:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > max_bytes:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Payload too large")
    return dependency


def validate_tenant_access(
    tenant_id: Optional[str],
    principal: Any,
) -> None:
    principal_tenant = getattr(principal, "tenant_id", None)
    if principal_tenant and tenant_id and principal_tenant != tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant access denied")


def audit_dependency(action: str) -> Callable[..., None]:
    def dependency(
        ctx: RequestContext = Depends(get_request_context),
        principal: Any = Depends(get_current_principal),
        settings: ApiSettings = Depends(get_settings),
    ) -> None:
        if not settings.enable_audit:
            return
        logger.info(
            "api_audit",
            extra={
                "action": action,
                "request_id": ctx.request_id,
                "correlation_id": ctx.correlation_id,
                "subject": getattr(principal, "subject", None),
                "path": ctx.path,
                "method": ctx.method,
                "tenant_id": ctx.tenant_id,
            },
        )
    return dependency


def anonymous_principal() -> Dict[str, Any]:
    return {
        "subject": "anonymous",
        "display_name": "Anonymous",
        "roles": ["anonymous"],
        "permissions": ["*"],
        "auth_method": "none",
    }


def parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "sim", "s"}


def parse_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def now_epoch() -> int:
    return int(time.time())


def hash_identifier(value: str, length: int = 32) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def hash_request_signature(method: str, path: str, tenant_id: Optional[str]) -> str:
    raw = json.dumps({"method": method.upper(), "path": path, "tenant_id": tenant_id}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_cache_key(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def reset_dependency_caches() -> None:
    get_settings.cache_clear()
    get_container.cache_clear()


__all__ = [
    "ApiSettings",
    "RequestContext",
    "PaginationParams",
    "IdempotencyRecord",
    "ServiceContainer",
    "get_settings",
    "get_container",
    "get_request_context",
    "attach_response_headers",
    "get_pagination",
    "rate_limit",
    "require_api_key",
    "get_auth_service",
    "get_current_principal",
    "require_permission",
    "require_permissions",
    "require_role",
    "require_idempotency_key",
    "get_service",
    "get_json_body_size_guard",
    "validate_tenant_access",
    "audit_dependency",
    "reset_dependency_caches",
]

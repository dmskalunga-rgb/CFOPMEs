#!/usr/bin/env python3
"""
api/middleware/tenant.py

Enterprise-grade multi-tenant middleware for FastAPI/Starlette.

Objetivo:
- Resolver tenant por header, subdomínio, path, querystring ou claim de autenticação.
- Validar tenant contra registry em memória ou provider externo adaptável.
- Anexar TenantContext ao request.state para uso em routers, services, logs e auditoria.
- Aplicar isolamento multi-tenant e bloquear acesso cross-tenant.
- Suportar allowlist, status do tenant, plano, região, limites e metadados.

Uso:
    from fastapi import FastAPI, Request
    from api.middleware.tenant import TenantMiddleware, TenantSettings, InMemoryTenantProvider, TenantRecord

    provider = InMemoryTenantProvider()
    provider.add(TenantRecord(tenant_id="default", name="Default Tenant"))

    app = FastAPI()
    app.add_middleware(
        TenantMiddleware,
        settings=TenantSettings.from_env(),
        provider=provider,
    )

    @app.get('/whoami')
    async def whoami(request: Request):
        return request.state.tenant.to_dict()

Variáveis de ambiente:
    TENANT_ENABLED=true|false
    TENANT_REQUIRED=true|false
    TENANT_HEADER=X-Tenant-ID
    TENANT_ALLOWED=default,tenant-a
    TENANT_DEFAULT=default
    TENANT_RESOLUTION_ORDER=header,jwt,subdomain,path,query,default
    TENANT_BASE_DOMAIN=example.com
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Set

try:
    from fastapi import HTTPException, Request, Response, status
    from starlette.middleware.base import BaseHTTPMiddleware
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Dependências ausentes. Instale com: pip install fastapi starlette") from exc


TENANT_MIDDLEWARE_VERSION = "1.0.0"
UTC = timezone.utc
logger = logging.getLogger(__name__)


class TenantStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISABLED = "disabled"
    TRIAL = "trial"
    ARCHIVED = "archived"


class TenantResolutionSource(str, Enum):
    HEADER = "header"
    JWT = "jwt"
    SUBDOMAIN = "subdomain"
    PATH = "path"
    QUERY = "query"
    DEFAULT = "default"
    NONE = "none"


@dataclass(frozen=True)
class TenantSettings:
    enabled: bool = True
    required: bool = True
    tenant_header: str = "X-Tenant-ID"
    tenant_query_param: str = "tenant_id"
    tenant_path_prefix: str = "/t/"
    base_domain: Optional[str] = None
    default_tenant_id: Optional[str] = None
    allowed_tenants: List[str] = field(default_factory=list)
    resolution_order: List[TenantResolutionSource] = field(
        default_factory=lambda: [
            TenantResolutionSource.HEADER,
            TenantResolutionSource.JWT,
            TenantResolutionSource.SUBDOMAIN,
            TenantResolutionSource.PATH,
            TenantResolutionSource.QUERY,
            TenantResolutionSource.DEFAULT,
        ]
    )
    public_paths: List[str] = field(default_factory=lambda: ["/health", "/ready", "/metadata", "/docs", "/openapi.json"])
    expose_response_headers: bool = True
    hash_tenant_in_logs: bool = False
    strict_principal_tenant_match: bool = True
    cache_ttl_seconds: int = 300

    @staticmethod
    def from_env() -> "TenantSettings":
        return TenantSettings(
            enabled=parse_bool(os.getenv("TENANT_ENABLED"), True),
            required=parse_bool(os.getenv("TENANT_REQUIRED"), True),
            tenant_header=os.getenv("TENANT_HEADER", "X-Tenant-ID"),
            tenant_query_param=os.getenv("TENANT_QUERY_PARAM", "tenant_id"),
            tenant_path_prefix=os.getenv("TENANT_PATH_PREFIX", "/t/"),
            base_domain=os.getenv("TENANT_BASE_DOMAIN") or None,
            default_tenant_id=os.getenv("TENANT_DEFAULT") or None,
            allowed_tenants=parse_csv(os.getenv("TENANT_ALLOWED", "")),
            resolution_order=parse_resolution_order(os.getenv("TENANT_RESOLUTION_ORDER", "header,jwt,subdomain,path,query,default")),
            public_paths=parse_csv(os.getenv("TENANT_PUBLIC_PATHS", "/health,/ready,/metadata,/docs,/openapi.json")),
            expose_response_headers=parse_bool(os.getenv("TENANT_EXPOSE_RESPONSE_HEADERS"), True),
            hash_tenant_in_logs=parse_bool(os.getenv("TENANT_HASH_IN_LOGS"), False),
            strict_principal_tenant_match=parse_bool(os.getenv("TENANT_STRICT_PRINCIPAL_MATCH"), True),
            cache_ttl_seconds=int(os.getenv("TENANT_CACHE_TTL_SECONDS", "300")),
        )


@dataclass(frozen=True)
class TenantRecord:
    tenant_id: str
    name: str
    status: TenantStatus = TenantStatus.ACTIVE
    plan: str = "standard"
    region: Optional[str] = None
    allowed_domains: List[str] = field(default_factory=list)
    features: List[str] = field(default_factory=list)
    limits: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())

    def __post_init__(self) -> None:
        if not self.tenant_id or not is_valid_tenant_id(self.tenant_id):
            raise ValueError("tenant_id inválido")
        if isinstance(self.status, str):
            object.__setattr__(self, "status", TenantStatus(self.status))

    def active(self) -> bool:
        return self.status in {TenantStatus.ACTIVE, TenantStatus.TRIAL}

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "tenant_id": self.tenant_id,
            "name": self.name,
            "status": self.status.value,
            "plan": self.plan,
            "region": self.region,
            "allowed_domains": self.allowed_domains,
            "features": self.features,
            "limits": self.limits,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }
        return payload


@dataclass(frozen=True)
class TenantContext:
    tenant_id: Optional[str]
    tenant: Optional[TenantRecord]
    source: TenantResolutionSource
    request_id: str
    resolved_at: str
    valid: bool
    reason: Optional[str] = None

    @property
    def tenant_id_hash(self) -> Optional[str]:
        return hash_identifier(self.tenant_id) if self.tenant_id else None

    def require_valid(self) -> None:
        if not self.valid:
            raise TenantAccessError(self.reason or "tenant inválido")

    def to_dict(self, mask: bool = False) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id_hash if mask else self.tenant_id,
            "tenant": None if self.tenant is None else self.tenant.to_dict(),
            "source": self.source.value,
            "request_id": self.request_id,
            "resolved_at": self.resolved_at,
            "valid": self.valid,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TenantResolutionResult:
    tenant_id: Optional[str]
    source: TenantResolutionSource
    reason: Optional[str] = None


class TenantProvider(Protocol):
    def get_tenant(self, tenant_id: str) -> Optional[TenantRecord]:
        ...

    def list_tenants(self) -> List[TenantRecord]:
        ...


class TenantError(Exception):
    """Base tenant exception."""


class TenantAccessError(TenantError):
    """Tenant access denied."""


class TenantResolutionError(TenantError):
    """Tenant could not be resolved."""


class InMemoryTenantProvider:
    def __init__(self, tenants: Optional[Iterable[TenantRecord]] = None) -> None:
        self._tenants: Dict[str, TenantRecord] = {}
        for tenant in tenants or []:
            self.add(tenant)

    def add(self, tenant: TenantRecord) -> None:
        self._tenants[tenant.tenant_id] = tenant

    def remove(self, tenant_id: str) -> bool:
        return self._tenants.pop(tenant_id, None) is not None

    def get_tenant(self, tenant_id: str) -> Optional[TenantRecord]:
        return self._tenants.get(tenant_id)

    def list_tenants(self) -> List[TenantRecord]:
        return sorted(self._tenants.values(), key=lambda item: item.tenant_id)


class CachedTenantProvider:
    def __init__(self, provider: TenantProvider, ttl_seconds: int) -> None:
        self.provider = provider
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, tuple[float, Optional[TenantRecord]]] = {}

    def get_tenant(self, tenant_id: str) -> Optional[TenantRecord]:
        now = time.time()
        cached = self._cache.get(tenant_id)
        if cached and now - cached[0] <= self.ttl_seconds:
            return cached[1]
        tenant = self.provider.get_tenant(tenant_id)
        self._cache[tenant_id] = (now, tenant)
        return tenant

    def list_tenants(self) -> List[TenantRecord]:
        return self.provider.list_tenants()

    def clear(self) -> None:
        self._cache.clear()


class TenantResolver:
    def __init__(self, settings: TenantSettings) -> None:
        self.settings = settings

    def resolve(self, request: Request) -> TenantResolutionResult:
        for source in self.settings.resolution_order:
            tenant_id = self._resolve_by_source(request, source)
            if tenant_id:
                normalized = normalize_tenant_id(tenant_id)
                if not is_valid_tenant_id(normalized):
                    return TenantResolutionResult(None, source, "invalid_tenant_id_format")
                return TenantResolutionResult(normalized, source)
        return TenantResolutionResult(None, TenantResolutionSource.NONE, "tenant_not_found")

    def _resolve_by_source(self, request: Request, source: TenantResolutionSource) -> Optional[str]:
        if source == TenantResolutionSource.HEADER:
            return request.headers.get(self.settings.tenant_header)
        if source == TenantResolutionSource.JWT:
            return self._resolve_from_principal(request)
        if source == TenantResolutionSource.SUBDOMAIN:
            return self._resolve_from_subdomain(request)
        if source == TenantResolutionSource.PATH:
            return self._resolve_from_path(request)
        if source == TenantResolutionSource.QUERY:
            return request.query_params.get(self.settings.tenant_query_param)
        if source == TenantResolutionSource.DEFAULT:
            return self.settings.default_tenant_id
        return None

    @staticmethod
    def _resolve_from_principal(request: Request) -> Optional[str]:
        principal = getattr(request.state, "principal", None)
        if principal is None:
            return None
        if isinstance(principal, Mapping):
            return principal.get("tenant_id")
        return getattr(principal, "tenant_id", None)

    def _resolve_from_subdomain(self, request: Request) -> Optional[str]:
        host = request.headers.get("host", "").split(":")[0].lower()
        if not host:
            return None
        if self.settings.base_domain:
            base = self.settings.base_domain.lower().lstrip(".")
            if host.endswith(base):
                prefix = host[: -len(base)].rstrip(".")
                if prefix and prefix not in {"www", "api"}:
                    return prefix.split(".")[-1]
            return None
        parts = host.split(".")
        if len(parts) >= 3 and parts[0] not in {"www", "api"}:
            return parts[0]
        return None

    def _resolve_from_path(self, request: Request) -> Optional[str]:
        path = request.url.path
        prefix = self.settings.tenant_path_prefix
        if not path.startswith(prefix):
            return None
        remaining = path[len(prefix) :]
        return remaining.split("/", 1)[0] if remaining else None


class TenantMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: Any,
        settings: Optional[TenantSettings] = None,
        provider: Optional[TenantProvider] = None,
    ) -> None:
        super().__init__(app)
        self.settings = settings or TenantSettings.from_env()
        base_provider = provider or default_provider_from_settings(self.settings)
        self.provider = CachedTenantProvider(base_provider, self.settings.cache_ttl_seconds)
        self.resolver = TenantResolver(self.settings)

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        request_id = request.headers.get("x-request-id") or getattr(request.state, "request_id", None) or f"req_{uuid.uuid4().hex}"

        if not self.settings.enabled or is_public_path(request.url.path, self.settings.public_paths):
            context = TenantContext(
                tenant_id=None,
                tenant=None,
                source=TenantResolutionSource.NONE,
                request_id=request_id,
                resolved_at=utc_now_iso(),
                valid=True,
                reason="tenant_resolution_skipped",
            )
            request.state.tenant = context
            response = await call_next(request)
            return response

        try:
            context = self.resolve_context(request, request_id)
            request.state.tenant = context
            if self.settings.strict_principal_tenant_match:
                validate_principal_tenant_match(request, context)
        except TenantError as exc:
            logger.warning(
                "tenant_denied",
                extra={"request_id": request_id, "path": request.url.path, "reason": str(exc)},
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

        response = await call_next(request)
        if self.settings.expose_response_headers and context.tenant_id:
            response.headers["x-tenant-id"] = context.tenant_id
            response.headers["x-tenant-source"] = context.source.value
        return response

    def resolve_context(self, request: Request, request_id: str) -> TenantContext:
        result = self.resolver.resolve(request)
        if not result.tenant_id:
            if self.settings.required:
                raise TenantResolutionError(result.reason or "tenant_required")
            return TenantContext(None, None, result.source, request_id, utc_now_iso(), True, result.reason)

        if self.settings.allowed_tenants and result.tenant_id not in self.settings.allowed_tenants:
            raise TenantAccessError("tenant_not_allowed")

        tenant = self.provider.get_tenant(result.tenant_id)
        if tenant is None:
            raise TenantAccessError("tenant_not_registered")
        if not tenant.active():
            raise TenantAccessError(f"tenant_status_{tenant.status.value}")
        if tenant.allowed_domains and not request_domain_allowed(request, tenant.allowed_domains):
            raise TenantAccessError("tenant_domain_not_allowed")

        context = TenantContext(
            tenant_id=result.tenant_id,
            tenant=tenant,
            source=result.source,
            request_id=request_id,
            resolved_at=utc_now_iso(),
            valid=True,
        )
        logger.info(
            "tenant_resolved",
            extra={
                "request_id": request_id,
                "tenant_id": context.tenant_id_hash if self.settings.hash_tenant_in_logs else context.tenant_id,
                "source": result.source.value,
            },
        )
        return context


def default_provider_from_settings(settings: TenantSettings) -> InMemoryTenantProvider:
    tenants: List[TenantRecord] = []
    for tenant_id in settings.allowed_tenants:
        tenants.append(TenantRecord(tenant_id=normalize_tenant_id(tenant_id), name=tenant_id))
    if settings.default_tenant_id and settings.default_tenant_id not in {tenant.tenant_id for tenant in tenants}:
        tenants.append(TenantRecord(tenant_id=normalize_tenant_id(settings.default_tenant_id), name=settings.default_tenant_id))
    if not tenants and not settings.required:
        tenants.append(TenantRecord(tenant_id="default", name="Default Tenant"))
    return InMemoryTenantProvider(tenants)


def get_tenant_context(request: Request) -> TenantContext:
    context = getattr(request.state, "tenant", None)
    if context is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Tenant context missing")
    return context


def require_tenant(request: Request) -> TenantContext:
    context = get_tenant_context(request)
    if not context.tenant_id or not context.valid:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=context.reason or "Tenant required")
    return context


def require_tenant_feature(feature: str) -> Callable[[Request], TenantContext]:
    def dependency(request: Request) -> TenantContext:
        context = require_tenant(request)
        if context.tenant and feature not in context.tenant.features:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Tenant feature not enabled: {feature}")
        return context

    return dependency


def require_tenant_plan(plans: Sequence[str]) -> Callable[[Request], TenantContext]:
    allowed = set(plans)

    def dependency(request: Request) -> TenantContext:
        context = require_tenant(request)
        if context.tenant and context.tenant.plan not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant plan not allowed")
        return context

    return dependency


def validate_principal_tenant_match(request: Request, context: TenantContext) -> None:
    principal = getattr(request.state, "principal", None)
    if principal is None or not context.tenant_id:
        return
    principal_tenant = None
    if isinstance(principal, Mapping):
        principal_tenant = principal.get("tenant_id")
    else:
        principal_tenant = getattr(principal, "tenant_id", None)
    if principal_tenant and normalize_tenant_id(str(principal_tenant)) != context.tenant_id:
        raise TenantAccessError("principal_tenant_mismatch")


def request_domain_allowed(request: Request, allowed_domains: Sequence[str]) -> bool:
    host = request.headers.get("host", "").split(":")[0].lower()
    if not host:
        return False
    allowed = {item.lower().lstrip(".") for item in allowed_domains}
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed)


def is_public_path(path: str, public_paths: Sequence[str]) -> bool:
    for public_path in public_paths:
        if path == public_path or path.startswith(public_path.rstrip("/") + "/"):
            return True
    return False


def normalize_tenant_id(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def is_valid_tenant_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}[a-z0-9]", value.strip().lower()))


def hash_identifier(value: Optional[str], length: int = 32) -> Optional[str]:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "sim", "s"}


def parse_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_resolution_order(value: str) -> List[TenantResolutionSource]:
    result: List[TenantResolutionSource] = []
    aliases = {
        "header": TenantResolutionSource.HEADER,
        "jwt": TenantResolutionSource.JWT,
        "subdomain": TenantResolutionSource.SUBDOMAIN,
        "path": TenantResolutionSource.PATH,
        "query": TenantResolutionSource.QUERY,
        "default": TenantResolutionSource.DEFAULT,
    }
    for item in parse_csv(value):
        source = aliases.get(item.lower())
        if source and source not in result:
            result.append(source)
    return result or [TenantResolutionSource.HEADER, TenantResolutionSource.DEFAULT]


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


__all__ = [
    "TenantStatus",
    "TenantResolutionSource",
    "TenantSettings",
    "TenantRecord",
    "TenantContext",
    "TenantProvider",
    "InMemoryTenantProvider",
    "CachedTenantProvider",
    "TenantResolver",
    "TenantMiddleware",
    "get_tenant_context",
    "require_tenant",
    "require_tenant_feature",
    "require_tenant_plan",
    "TenantError",
    "TenantAccessError",
    "TenantResolutionError",
]

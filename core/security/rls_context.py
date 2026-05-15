#!/usr/bin/env python3
"""
core/security/rls_context.py

Enterprise-grade Row Level Security context utilities.

Objetivo:
- Centralizar contexto multi-tenant para RLS em Postgres/Supabase.
- Transportar tenant_id, user_id, roles, scopes, correlation_id e claims de forma segura.
- Gerar claims JWT/RLS, headers Supabase/PostgREST e comandos SQL `set_config`.
- Evitar vazamento entre tenants em services, repositories e jobs.

Uso:
    from core.security.rls_context import RLSContext, bind_rls_context, get_rls_context

    ctx = RLSContext.from_principal(principal)
    bind_rls_context(ctx)

    headers = ctx.to_supabase_headers()
    sql, params = ctx.to_postgres_set_config_sql()
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

try:
    from core.security.auth import Principal, PrincipalType, AuthMethod
except Exception:  # pragma: no cover
    Principal = Any  # type: ignore
    PrincipalType = Any  # type: ignore
    AuthMethod = Any  # type: ignore


RLS_CONTEXT_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc

_rls_context: contextvars.ContextVar[Optional["RLSContext"]] = contextvars.ContextVar("rls_context", default=None)


class RLSMode(str, Enum):
    STRICT = "strict"
    PERMISSIVE = "permissive"
    DISABLED = "disabled"


class TenantScope(str, Enum):
    TENANT = "tenant"
    ORGANIZATION = "organization"
    GLOBAL = "global"


@dataclass(frozen=True)
class RLSContext:
    tenant_id: Optional[str]
    subject: str
    user_id: Optional[str] = None
    organization_id: Optional[str] = None
    principal_type: str = "user"
    auth_method: str = "unknown"
    roles: List[str] = field(default_factory=list)
    scopes: List[str] = field(default_factory=list)
    tenant_scope: TenantScope = TenantScope.TENANT
    request_id: str = field(default_factory=lambda: f"req_{uuid.uuid4().hex[:16]}")
    correlation_id: str = field(default_factory=lambda: f"corr_{uuid.uuid4().hex[:16]}")
    session_id: Optional[str] = None
    service_account: bool = False
    bypass_rls: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(tz=DEFAULT_TIMEZONE).isoformat())

    @staticmethod
    def anonymous(tenant_id: Optional[str] = None) -> "RLSContext":
        return RLSContext(
            tenant_id=tenant_id,
            subject="anonymous",
            principal_type="anonymous",
            auth_method="none",
            roles=[],
            scopes=[],
            tenant_scope=TenantScope.TENANT,
            service_account=False,
            bypass_rls=False,
        )

    @staticmethod
    def system(reason: str = "system_job") -> "RLSContext":
        return RLSContext(
            tenant_id=None,
            subject="system",
            principal_type="system",
            auth_method="internal",
            roles=["system", "admin"],
            scopes=["*", "admin"],
            tenant_scope=TenantScope.GLOBAL,
            service_account=True,
            bypass_rls=True,
            metadata={"reason": reason},
        )

    @staticmethod
    def from_principal(
        principal: Principal,
        tenant_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        session_id: Optional[str] = None,
        allow_admin_bypass: bool = True,
    ) -> "RLSContext":
        principal_type = getattr(principal, "principal_type", "user")
        auth_method = getattr(principal, "auth_method", "unknown")
        roles = list(getattr(principal, "roles", []) or [])
        scopes = list(getattr(principal, "scopes", []) or [])
        resolved_tenant = tenant_id or getattr(principal, "tenant_id", None)
        subject = str(getattr(principal, "subject", "unknown"))
        principal_type_value = getattr(principal_type, "value", str(principal_type))
        auth_method_value = getattr(auth_method, "value", str(auth_method))
        is_service = principal_type_value in {"service", "system"}
        is_admin = "admin" in roles or "*" in scopes or "admin" in scopes or "system:admin" in scopes
        bypass = bool(allow_admin_bypass and is_service and is_admin)
        return RLSContext(
            tenant_id=resolved_tenant,
            subject=subject,
            user_id=subject if principal_type_value == "user" else None,
            organization_id=organization_id,
            principal_type=principal_type_value,
            auth_method=auth_method_value,
            roles=normalize_list(roles),
            scopes=normalize_list(scopes),
            tenant_scope=TenantScope.GLOBAL if bypass else TenantScope.TENANT,
            request_id=request_id or f"req_{uuid.uuid4().hex[:16]}",
            correlation_id=correlation_id or f"corr_{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            service_account=is_service,
            bypass_rls=bypass,
            metadata=sanitize_metadata(getattr(principal, "metadata", {}) or {}),
        )

    def require_tenant(self) -> None:
        if self.bypass_rls or self.tenant_scope == TenantScope.GLOBAL:
            return
        if not self.tenant_id:
            raise RLSContextError("tenant_id obrigatório para RLS")

    def assert_tenant_access(self, tenant_id: Optional[str]) -> None:
        if self.bypass_rls or self.tenant_scope == TenantScope.GLOBAL:
            return
        if not tenant_id:
            raise RLSContextError("tenant_id de destino obrigatório")
        if not self.tenant_id:
            raise RLSContextError("tenant_id ausente no contexto")
        if tenant_id != self.tenant_id:
            raise RLSContextError("Acesso cross-tenant negado")

    def with_tenant(self, tenant_id: str) -> "RLSContext":
        if self.tenant_id and self.tenant_id != tenant_id and not self.bypass_rls:
            raise RLSContextError("Não é permitido trocar tenant sem bypass RLS")
        return replace(self, tenant_id=tenant_id)

    def with_request(self, request_id: Optional[str] = None, correlation_id: Optional[str] = None) -> "RLSContext":
        return replace(
            self,
            request_id=request_id or self.request_id or f"req_{uuid.uuid4().hex[:16]}",
            correlation_id=correlation_id or self.correlation_id or f"corr_{uuid.uuid4().hex[:16]}",
        )

    def to_claims(self) -> Dict[str, Any]:
        return {
            "sub": self.subject,
            "tenant_id": self.tenant_id,
            "organization_id": self.organization_id,
            "user_id": self.user_id,
            "principal_type": self.principal_type,
            "auth_method": self.auth_method,
            "roles": list(self.roles),
            "scopes": list(self.scopes),
            "tenant_scope": self.tenant_scope.value,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "session_id": self.session_id,
            "service_account": self.service_account,
            "bypass_rls": self.bypass_rls,
            "metadata": sanitize_metadata(self.metadata),
        }

    def to_postgrest_claims_header(self) -> Dict[str, str]:
        claims = self.to_claims()
        return {
            "x-rls-context": json.dumps(claims, ensure_ascii=False, separators=(",", ":")),
            "x-tenant-id": self.tenant_id or "",
            "x-request-id": self.request_id,
            "x-correlation-id": self.correlation_id,
        }

    def to_supabase_headers(self, jwt: Optional[str] = None) -> Dict[str, str]:
        headers = self.to_postgrest_claims_header()
        if jwt:
            headers["Authorization"] = f"Bearer {jwt}"
        return headers

    def to_postgres_set_config_sql(self, local: bool = True) -> Tuple[str, Dict[str, Any]]:
        claims = self.to_claims()
        local_text = "true" if local else "false"
        params = {
            "tenant_id": self.tenant_id or "",
            "user_id": self.user_id or self.subject,
            "subject": self.subject,
            "roles": json.dumps(self.roles, ensure_ascii=False),
            "scopes": json.dumps(self.scopes, ensure_ascii=False),
            "claims": json.dumps(claims, ensure_ascii=False, separators=(",", ":")),
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "bypass_rls": "true" if self.bypass_rls else "false",
        }
        sql = "\n".join(
            [
                f"SELECT set_config('app.tenant_id', %(tenant_id)s, {local_text});",
                f"SELECT set_config('app.user_id', %(user_id)s, {local_text});",
                f"SELECT set_config('app.subject', %(subject)s, {local_text});",
                f"SELECT set_config('app.roles', %(roles)s, {local_text});",
                f"SELECT set_config('app.scopes', %(scopes)s, {local_text});",
                f"SELECT set_config('app.jwt.claims', %(claims)s, {local_text});",
                f"SELECT set_config('app.request_id', %(request_id)s, {local_text});",
                f"SELECT set_config('app.correlation_id', %(correlation_id)s, {local_text});",
                f"SELECT set_config('app.bypass_rls', %(bypass_rls)s, {local_text});",
            ]
        )
        return sql, params

    def to_audit_payload(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "subject": self.subject,
            "subject_hash": hash_text(self.subject)[:16],
            "principal_type": self.principal_type,
            "auth_method": self.auth_method,
            "roles": list(self.roles),
            "scopes": list(self.scopes),
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "service_account": self.service_account,
            "bypass_rls": self.bypass_rls,
            "created_at": self.created_at,
        }

    def safe_dict(self) -> Dict[str, Any]:
        payload = self.to_claims()
        payload["subject_hash"] = hash_text(self.subject)[:16]
        payload.pop("sub", None)
        payload["metadata"] = sanitize_metadata(payload.get("metadata", {}))
        return payload


class RLSContextError(Exception):
    """RLS context error."""


class RLSPolicyError(RLSContextError):
    """RLS policy validation error."""


def bind_rls_context(context: RLSContext) -> RLSContext:
    _rls_context.set(context)
    return context


def get_rls_context(default: Optional[RLSContext] = None) -> RLSContext:
    context = _rls_context.get()
    if context is not None:
        return context
    if default is not None:
        return default
    return RLSContext.anonymous()


def clear_rls_context() -> None:
    _rls_context.set(None)


def require_rls_context() -> RLSContext:
    context = _rls_context.get()
    if context is None:
        raise RLSContextError("RLS context não inicializado")
    return context


def require_tenant_context() -> RLSContext:
    context = require_rls_context()
    context.require_tenant()
    return context


def assert_same_tenant(target_tenant_id: Optional[str]) -> None:
    require_rls_context().assert_tenant_access(target_tenant_id)


def build_rls_where_clause(
    table_alias: Optional[str] = None,
    tenant_field: str = "tenant_id",
    context: Optional[RLSContext] = None,
) -> Tuple[str, Dict[str, Any]]:
    ctx = context or get_rls_context()
    if ctx.bypass_rls or ctx.tenant_scope == TenantScope.GLOBAL:
        return "1 = 1", {}
    ctx.require_tenant()
    field = f"{table_alias}.{tenant_field}" if table_alias else tenant_field
    return f"{field} = %(rls_tenant_id)s", {"rls_tenant_id": ctx.tenant_id}


def apply_rls_filters(filters: Optional[Mapping[str, Any]] = None, context: Optional[RLSContext] = None, tenant_field: str = "tenant_id") -> Dict[str, Any]:
    ctx = context or get_rls_context()
    result = dict(filters or {})
    if not (ctx.bypass_rls or ctx.tenant_scope == TenantScope.GLOBAL):
        ctx.require_tenant()
        existing = result.get(tenant_field) or result.get(f"{tenant_field}__eq")
        if existing and existing != ctx.tenant_id:
            raise RLSContextError("Filtro tenant_id divergente do contexto RLS")
        result[tenant_field] = ctx.tenant_id
    return result


def build_rls_jwt_claims(context: Optional[RLSContext] = None) -> Dict[str, Any]:
    return (context or get_rls_context()).to_claims()


def sql_policy_examples(schema: str = "public") -> Dict[str, str]:
    return {
        "enable_rls": f"ALTER TABLE {schema}.your_table ENABLE ROW LEVEL SECURITY;",
        "tenant_policy": (
            f"CREATE POLICY tenant_isolation ON {schema}.your_table "
            "USING (tenant_id = current_setting('app.tenant_id', true));"
        ),
        "bypass_policy": (
            f"CREATE POLICY service_bypass ON {schema}.your_table "
            "USING (current_setting('app.bypass_rls', true) = 'true');"
        ),
        "claims_policy": (
            f"CREATE POLICY jwt_tenant_claim ON {schema}.your_table "
            "USING (tenant_id = ((current_setting('app.jwt.claims', true)::jsonb)->>'tenant_id'));"
        ),
    }


def normalize_list(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        item = str(value).strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def sanitize_metadata(metadata: Mapping[str, Any], max_items: int = 50) -> Dict[str, Any]:
    sensitive = {"password", "secret", "token", "api_key", "apikey", "authorization", "cookie"}
    result: Dict[str, Any] = {}
    for index, (key, value) in enumerate(metadata.items()):
        if index >= max_items:
            break
        key_text = str(key)
        lower = key_text.lower()
        if any(item in lower for item in sensitive):
            result[key_text] = "[REDACTED]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key_text] = value
        else:
            result[key_text] = str(value)[:500]
    return result


def hash_text(value: Optional[str]) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def rls_context_health() -> Dict[str, Any]:
    ctx = _rls_context.get()
    return {
        "status": "ok",
        "version": RLS_CONTEXT_VERSION,
        "bound": ctx is not None,
        "context": None if ctx is None else ctx.safe_dict(),
        "checked_at": datetime.now(tz=DEFAULT_TIMEZONE).isoformat(),
    }


__all__ = [
    "RLS_CONTEXT_VERSION",
    "RLSMode",
    "TenantScope",
    "RLSContext",
    "RLSContextError",
    "RLSPolicyError",
    "bind_rls_context",
    "get_rls_context",
    "clear_rls_context",
    "require_rls_context",
    "require_tenant_context",
    "assert_same_tenant",
    "build_rls_where_clause",
    "apply_rls_filters",
    "build_rls_jwt_claims",
    "sql_policy_examples",
    "rls_context_health",
]

"""
data/security/tenant_isolation.py

Enterprise-grade tenant isolation module for Python services, APIs, data
platforms, workers and internal tools.

Core capabilities:
- Tenant context resolution and propagation
- Tenant boundary enforcement
- Row-level/data-filter policy generation
- Cross-tenant access policies
- Tenant-aware resource descriptors
- Isolation validation with explainable decisions
- Repository/query guard helpers
- Decorators for service/API methods
- Structured audit events
- Decision cache with TTL
- In-memory tenant registry for local development/tests

Security posture:
- Deny by default
- Fail closed by default
- Explicit cross-tenant access only through policy
- Strong auditability for tenant boundary violations

This module is framework-agnostic and can be integrated with FastAPI, Flask,
Django, SQLAlchemy, repository classes, DataFrame pipelines, data lake access
layers, queue consumers or admin services.
"""

from __future__ import annotations

import contextvars
import dataclasses
import functools
import hashlib
import json
import logging
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple, TypeVar, Union, cast

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
F = TypeVar("F", bound=Callable[..., Any])


# =============================================================================
# Exceptions
# =============================================================================


class TenantIsolationError(Exception):
    """Base tenant isolation error."""


class TenantBoundaryViolationError(TenantIsolationError):
    """Raised when a tenant boundary is violated."""


class TenantNotFoundError(TenantIsolationError):
    """Raised when a tenant cannot be found."""


class TenantContextError(TenantIsolationError):
    """Raised when tenant context is missing or invalid."""


class TenantPolicyError(TenantIsolationError):
    """Raised when a tenant isolation policy is invalid or denies access."""


class TenantRepositoryError(TenantIsolationError):
    """Raised when a tenant repository fails."""


# =============================================================================
# Enums and config
# =============================================================================


class TenantStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISABLED = "disabled"
    PENDING = "pending"
    DELETED = "deleted"


class IsolationDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    NOT_APPLICABLE = "not_applicable"
    INDETERMINATE = "indeterminate"


class IsolationMode(str, Enum):
    STRICT = "strict"
    SHARED_SERVICES = "shared_services"
    ADMIN_CROSS_TENANT = "admin_cross_tenant"
    SYSTEM = "system"


class TenantAccessOperation(str, Enum):
    READ = "read"
    WRITE = "write"
    UPDATE = "update"
    DELETE = "delete"
    LIST = "list"
    ADMIN = "admin"
    EXPORT = "export"
    IMPORT = "import"
    EXECUTE = "execute"


class DataFilterDialect(str, Enum):
    SQL = "sql"
    SQLALCHEMY_LIKE = "sqlalchemy_like"
    PANDAS_QUERY = "pandas_query"
    DICT = "dict"


@dataclass(frozen=True)
class TenantIsolationConfig:
    """Runtime configuration for tenant isolation."""

    deny_by_default: bool = True
    fail_closed: bool = True
    require_tenant_context: bool = True
    allow_system_bypass: bool = True
    system_principals: Tuple[str, ...] = ("system", "scheduler", "migration-service")
    super_admin_roles: Tuple[str, ...] = ("super_admin", "platform_admin")
    allow_super_admin_cross_tenant: bool = True
    enable_cache: bool = True
    cache_ttl_seconds: int = 60
    max_cache_entries: int = 10_000
    enable_audit: bool = True
    redact_sensitive_audit_fields: bool = True
    tenant_field_name: str = "tenant_id"
    organization_field_name: str = "organization_id"
    default_isolation_mode: IsolationMode = IsolationMode.STRICT


# =============================================================================
# Domain models
# =============================================================================


@dataclass(frozen=True)
class Tenant:
    """Tenant metadata."""

    tenant_id: str
    name: str
    status: TenantStatus = TenantStatus.ACTIVE
    organization_id: Optional[str] = None
    region: Optional[str] = None
    tier: Optional[str] = None
    data_residency: Optional[str] = None
    parent_tenant_id: Optional[str] = None
    allowed_child_tenant_ids: Tuple[str, ...] = ()
    attributes: JsonDict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_active(self) -> bool:
        return self.status == TenantStatus.ACTIVE


@dataclass(frozen=True)
class TenantPrincipal:
    """Principal requesting access in tenant context."""

    principal_id: str
    tenant_id: Optional[str] = None
    username: Optional[str] = None
    roles: Tuple[str, ...] = ()
    groups: Tuple[str, ...] = ()
    scopes: Tuple[str, ...] = ()
    allowed_tenant_ids: Tuple[str, ...] = ()
    attributes: JsonDict = field(default_factory=dict)
    authenticated: bool = True

    def is_system(self, config: TenantIsolationConfig) -> bool:
        return self.principal_id in config.system_principals or "system" in self.roles

    def is_super_admin(self, config: TenantIsolationConfig) -> bool:
        return bool(set(self.roles).intersection(config.super_admin_roles))


@dataclass(frozen=True)
class TenantContext:
    """Resolved tenant execution context."""

    tenant_id: str
    principal: TenantPrincipal
    organization_id: Optional[str] = None
    isolation_mode: IsolationMode = IsolationMode.STRICT
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    source: str = "unknown"
    environment: JsonDict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def validate(self) -> None:
        if not self.tenant_id:
            raise TenantContextError("tenant_id is required in TenantContext.")
        if not self.principal.principal_id:
            raise TenantContextError("principal_id is required in TenantContext.")


@dataclass(frozen=True)
class TenantResource:
    """Resource with tenant ownership/boundary metadata."""

    resource_type: str
    resource_id: Optional[str] = None
    tenant_id: Optional[str] = None
    organization_id: Optional[str] = None
    owner_id: Optional[str] = None
    shared: bool = False
    shared_with_tenant_ids: Tuple[str, ...] = ()
    attributes: JsonDict = field(default_factory=dict)

    def canonical(self) -> str:
        return f"{self.resource_type}:{self.resource_id or '*'}"


@dataclass(frozen=True)
class CrossTenantPolicy:
    """Explicit cross-tenant access policy."""

    policy_id: str
    name: str
    source_tenant_id: str
    target_tenant_ids: Tuple[str, ...]
    operations: Tuple[TenantAccessOperation, ...]
    principal_ids: Tuple[str, ...] = ()
    roles: Tuple[str, ...] = ()
    resource_types: Tuple[str, ...] = ("*",)
    enabled: bool = True
    expires_at: Optional[datetime] = None
    reason: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def is_active(self) -> bool:
        return self.enabled and (self.expires_at is None or self.expires_at > datetime.now(timezone.utc))

    def applies(self, request: "TenantIsolationRequest") -> bool:
        if not self.is_active():
            return False
        if self.source_tenant_id != request.context.tenant_id:
            return False
        target_tenant = request.resource.tenant_id or request.target_tenant_id
        if not target_tenant or target_tenant not in self.target_tenant_ids:
            return False
        if request.operation not in self.operations and TenantAccessOperation.ADMIN not in self.operations:
            return False
        if "*" not in self.resource_types and request.resource.resource_type not in self.resource_types:
            return False
        principal = request.context.principal
        principal_match = not self.principal_ids or principal.principal_id in self.principal_ids
        role_match = not self.roles or bool(set(principal.roles).intersection(self.roles))
        return principal_match and role_match


@dataclass(frozen=True)
class TenantIsolationRequest:
    """Tenant isolation access request."""

    context: TenantContext
    resource: TenantResource
    operation: TenantAccessOperation
    target_tenant_id: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def validate(self) -> None:
        self.context.validate()
        if not self.resource.resource_type:
            raise TenantContextError("resource_type is required.")


@dataclass(frozen=True)
class IsolationEvaluation:
    """Single evaluation detail."""

    source: str
    decision: IsolationDecision
    reason: str
    applicable: bool = True
    policy_id: Optional[str] = None
    diagnostics: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class TenantIsolationResult:
    """Final tenant isolation decision."""

    decision: IsolationDecision
    allowed: bool
    reason: str
    tenant_id: str
    resource_tenant_id: Optional[str]
    operation: TenantAccessOperation
    evaluations: Tuple[IsolationEvaluation, ...] = ()
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cache_hit: bool = False
    diagnostics: JsonDict = field(default_factory=dict)

    def require_allowed(self) -> None:
        if not self.allowed:
            raise TenantBoundaryViolationError(self.reason)

    def to_dict(self) -> JsonDict:
        return {
            "decision": self.decision.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "tenant_id": self.tenant_id,
            "resource_tenant_id": self.resource_tenant_id,
            "operation": self.operation.value,
            "evaluations": [dataclasses.asdict(e) | {"decision": e.decision.value} for e in self.evaluations],
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "evaluated_at": self.evaluated_at.isoformat(),
            "cache_hit": self.cache_hit,
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class TenantAuditEvent:
    """Structured tenant isolation audit event."""

    event_type: str
    decision: IsolationDecision
    allowed: bool
    reason: str
    tenant_id: str
    resource_tenant_id: Optional[str]
    principal_id: str
    operation: TenantAccessOperation
    resource: str
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self, redact: bool = True) -> JsonDict:
        data = {
            "event_type": self.event_type,
            "decision": self.decision.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "tenant_id": self.tenant_id,
            "resource_tenant_id": self.resource_tenant_id,
            "principal_id": self.principal_id,
            "operation": self.operation.value,
            "resource": self.resource,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "metadata": dict(self.metadata),
            "timestamp": self.timestamp.isoformat(),
        }
        return redact_sensitive(data) if redact else data


# =============================================================================
# Context variable helpers
# =============================================================================


_CURRENT_TENANT_CONTEXT: contextvars.ContextVar[Optional[TenantContext]] = contextvars.ContextVar("current_tenant_context", default=None)


def set_current_tenant_context(context: TenantContext) -> contextvars.Token[Optional[TenantContext]]:
    context.validate()
    return _CURRENT_TENANT_CONTEXT.set(context)


def get_current_tenant_context(required: bool = True) -> Optional[TenantContext]:
    context = _CURRENT_TENANT_CONTEXT.get()
    if required and context is None:
        raise TenantContextError("Tenant context is not set.")
    return context


def reset_current_tenant_context(token: contextvars.Token[Optional[TenantContext]]) -> None:
    _CURRENT_TENANT_CONTEXT.reset(token)


class tenant_context_scope:
    """Context manager for setting tenant context."""

    def __init__(self, context: TenantContext) -> None:
        self.context = context
        self.token: Optional[contextvars.Token[Optional[TenantContext]]] = None

    def __enter__(self) -> TenantContext:
        self.token = set_current_tenant_context(self.context)
        return self.context

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.token is not None:
            reset_current_tenant_context(self.token)


# =============================================================================
# Repositories and audit
# =============================================================================


class TenantRepository(ABC):
    """Tenant registry abstraction."""

    @abstractmethod
    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """Return tenant by ID."""

    @abstractmethod
    def upsert_tenant(self, tenant: Tenant) -> None:
        """Create or update tenant."""

    @abstractmethod
    def list_tenants(self) -> Sequence[Tenant]:
        """List tenants."""


class CrossTenantPolicyRepository(ABC):
    """Cross-tenant policy repository abstraction."""

    @abstractmethod
    def list_policies_for_source(self, source_tenant_id: str) -> Sequence[CrossTenantPolicy]:
        """Return policies for source tenant."""

    @abstractmethod
    def upsert_policy(self, policy: CrossTenantPolicy) -> None:
        """Create or update policy."""


class InMemoryTenantRepository(TenantRepository):
    """Thread-safe in-memory tenant repository."""

    def __init__(self, tenants: Optional[Iterable[Tenant]] = None) -> None:
        self._tenants: Dict[str, Tenant] = {}
        self._lock = threading.RLock()
        for tenant in tenants or ():
            self.upsert_tenant(tenant)

    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        with self._lock:
            return self._tenants.get(tenant_id)

    def upsert_tenant(self, tenant: Tenant) -> None:
        if not tenant.tenant_id:
            raise TenantContextError("tenant_id is required.")
        with self._lock:
            self._tenants[tenant.tenant_id] = dataclasses.replace(tenant, updated_at=datetime.now(timezone.utc))

    def list_tenants(self) -> Sequence[Tenant]:
        with self._lock:
            return tuple(sorted(self._tenants.values(), key=lambda t: t.tenant_id))


class InMemoryCrossTenantPolicyRepository(CrossTenantPolicyRepository):
    """Thread-safe in-memory cross-tenant policy repository."""

    def __init__(self, policies: Optional[Iterable[CrossTenantPolicy]] = None) -> None:
        self._policies: Dict[str, CrossTenantPolicy] = {}
        self._lock = threading.RLock()
        for policy in policies or ():
            self.upsert_policy(policy)

    def list_policies_for_source(self, source_tenant_id: str) -> Sequence[CrossTenantPolicy]:
        with self._lock:
            return tuple(p for p in self._policies.values() if p.source_tenant_id == source_tenant_id and p.is_active())

    def upsert_policy(self, policy: CrossTenantPolicy) -> None:
        if not policy.policy_id:
            raise TenantPolicyError("policy_id is required.")
        with self._lock:
            self._policies[policy.policy_id] = policy


class TenantAuditSink(ABC):
    """Tenant isolation audit sink abstraction."""

    @abstractmethod
    def emit(self, event: TenantAuditEvent) -> None:
        """Emit tenant audit event."""


class LoggingTenantAuditSink(TenantAuditSink):
    """Logging-backed tenant audit sink."""

    def __init__(self, audit_logger: Optional[logging.Logger] = None, redact: bool = True) -> None:
        self.audit_logger = audit_logger or logging.getLogger("security.tenant_isolation.audit")
        self.redact = redact

    def emit(self, event: TenantAuditEvent) -> None:
        level = logging.INFO if event.allowed else logging.WARNING
        self.audit_logger.log(level, "tenant_isolation_event=%s", json.dumps(event.to_dict(redact=self.redact), sort_keys=True, default=str))


# =============================================================================
# Cache
# =============================================================================


@dataclass
class _DecisionCacheEntry:
    result: TenantIsolationResult
    expires_at: float


class TenantDecisionCache:
    """TTL cache for tenant isolation decisions."""

    def __init__(self, ttl_seconds: int = 60, max_entries: int = 10_000) -> None:
        self.ttl_seconds = max(0, ttl_seconds)
        self.max_entries = max(1, max_entries)
        self._cache: Dict[str, _DecisionCacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[TenantIsolationResult]:
        now = time.time()
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            if entry.expires_at <= now:
                self._cache.pop(key, None)
                return None
            return dataclasses.replace(entry.result, cache_hit=True)

    def set(self, key: str, result: TenantIsolationResult) -> None:
        if self.ttl_seconds <= 0:
            return
        with self._lock:
            if len(self._cache) >= self.max_entries:
                self._evict()
            self._cache[key] = _DecisionCacheEntry(result=result, expires_at=time.time() + self.ttl_seconds)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _evict(self) -> None:
        now = time.time()
        expired = [k for k, v in self._cache.items() if v.expires_at <= now]
        for key in expired:
            self._cache.pop(key, None)
        if len(self._cache) >= self.max_entries and self._cache:
            oldest = min(self._cache.items(), key=lambda item: item[1].expires_at)[0]
            self._cache.pop(oldest, None)


# =============================================================================
# Tenant isolation service
# =============================================================================


class TenantIsolationService:
    """Enterprise tenant isolation enforcement service."""

    def __init__(
        self,
        tenant_repository: Optional[TenantRepository] = None,
        policy_repository: Optional[CrossTenantPolicyRepository] = None,
        audit_sink: Optional[TenantAuditSink] = None,
        config: Optional[TenantIsolationConfig] = None,
        cache: Optional[TenantDecisionCache] = None,
    ) -> None:
        self.config = config or TenantIsolationConfig()
        self.tenant_repository = tenant_repository or InMemoryTenantRepository()
        self.policy_repository = policy_repository or InMemoryCrossTenantPolicyRepository()
        self.audit_sink = audit_sink or LoggingTenantAuditSink(redact=self.config.redact_sensitive_audit_fields)
        self.cache = cache or TenantDecisionCache(self.config.cache_ttl_seconds, self.config.max_cache_entries)

    def authorize(self, request: TenantIsolationRequest) -> TenantIsolationResult:
        """Evaluate tenant isolation request."""
        request.validate()
        cache_key = self._cache_key(request)
        if self.config.enable_cache:
            cached = self.cache.get(cache_key)
            if cached:
                return cached

        try:
            result = self._evaluate(request)
        except Exception as exc:
            logger.exception("Tenant isolation evaluation failed. request_id=%s", request.context.request_id)
            if self.config.fail_closed:
                result = self._deny(request, "Tenant isolation evaluation failed; fail-closed deny applied.", (
                    IsolationEvaluation("tenant_isolation_service", IsolationDecision.DENY, str(exc), diagnostics={"error_type": type(exc).__name__}),
                ))
            else:
                result = TenantIsolationResult(
                    decision=IsolationDecision.INDETERMINATE,
                    allowed=False,
                    reason="Tenant isolation evaluation failed.",
                    tenant_id=request.context.tenant_id,
                    resource_tenant_id=request.resource.tenant_id,
                    operation=request.operation,
                    request_id=request.context.request_id,
                    correlation_id=request.context.correlation_id,
                    diagnostics={"error": str(exc), "error_type": type(exc).__name__},
                )

        if self.config.enable_cache:
            self.cache.set(cache_key, result)
        if self.config.enable_audit:
            self._audit(request, result)
        return result

    def require(self, request: TenantIsolationRequest) -> None:
        self.authorize(request).require_allowed()

    def can_access(self, request: TenantIsolationRequest) -> bool:
        return self.authorize(request).allowed

    def enforce_resource_access(
        self,
        resource: TenantResource,
        operation: TenantAccessOperation,
        context: Optional[TenantContext] = None,
    ) -> TenantIsolationResult:
        ctx = context or get_current_tenant_context(required=True)
        assert ctx is not None
        return self.authorize(TenantIsolationRequest(context=ctx, resource=resource, operation=operation))

    def build_data_filter(
        self,
        context: Optional[TenantContext] = None,
        dialect: DataFilterDialect = DataFilterDialect.DICT,
        tenant_field: Optional[str] = None,
    ) -> Union[str, JsonDict]:
        """Build a tenant filter for repository/query layers."""
        ctx = context or get_current_tenant_context(required=True)
        assert ctx is not None
        field = tenant_field or self.config.tenant_field_name
        if dialect == DataFilterDialect.DICT:
            return {field: ctx.tenant_id}
        if dialect == DataFilterDialect.SQL:
            return f"{field} = :tenant_id"
        if dialect == DataFilterDialect.SQLALCHEMY_LIKE:
            return f"{field} == tenant_id"
        if dialect == DataFilterDialect.PANDAS_QUERY:
            return f"{field} == '{_escape_query_value(ctx.tenant_id)}'"
        raise TenantPolicyError(f"Unsupported filter dialect: {dialect.value}")

    def filter_records(self, records: Iterable[Mapping[str, Any]], context: Optional[TenantContext] = None, tenant_field: Optional[str] = None) -> Tuple[Mapping[str, Any], ...]:
        """Filter mapping records to current tenant."""
        ctx = context or get_current_tenant_context(required=True)
        assert ctx is not None
        field = tenant_field or self.config.tenant_field_name
        return tuple(record for record in records if str(record.get(field, "")) == ctx.tenant_id)

    def _evaluate(self, request: TenantIsolationRequest) -> TenantIsolationResult:
        evaluations: list[IsolationEvaluation] = []
        context = request.context
        principal = context.principal
        resource_tenant_id = request.resource.tenant_id or request.target_tenant_id

        if not principal.authenticated:
            return self._deny(request, "Principal is not authenticated.")

        context_tenant = self._get_active_tenant(context.tenant_id)
        if not context_tenant:
            return self._deny(request, "Context tenant is not active or does not exist.")

        if principal.tenant_id and principal.tenant_id != context.tenant_id and not principal.is_system(self.config):
            evaluations.append(IsolationEvaluation(
                source="principal_context_match",
                decision=IsolationDecision.DENY,
                reason="Principal tenant does not match execution tenant.",
            ))
            return self._deny(request, "Principal tenant mismatch.", tuple(evaluations))

        if principal.is_system(self.config) and self.config.allow_system_bypass and context.isolation_mode == IsolationMode.SYSTEM:
            evaluations.append(IsolationEvaluation("system_bypass", IsolationDecision.ALLOW, "System principal bypass allowed."))
            return self._allow(request, "System tenant bypass allowed.", tuple(evaluations))

        if principal.is_super_admin(self.config) and self.config.allow_super_admin_cross_tenant and context.isolation_mode == IsolationMode.ADMIN_CROSS_TENANT:
            evaluations.append(IsolationEvaluation("super_admin_cross_tenant", IsolationDecision.ALLOW, "Super admin cross-tenant access allowed by mode."))
            return self._allow(request, "Super admin cross-tenant access allowed.", tuple(evaluations))

        if not resource_tenant_id:
            if request.resource.shared:
                evaluations.append(IsolationEvaluation("shared_resource", IsolationDecision.ALLOW, "Shared resource without tenant owner allowed."))
                return self._allow(request, "Shared resource access allowed.", tuple(evaluations))
            if self.config.deny_by_default:
                return self._deny(request, "Resource tenant is missing; deny-by-default applied.")
            return TenantIsolationResult(
                decision=IsolationDecision.NOT_APPLICABLE,
                allowed=False,
                reason="Resource tenant is missing.",
                tenant_id=context.tenant_id,
                resource_tenant_id=None,
                operation=request.operation,
                request_id=context.request_id,
                correlation_id=context.correlation_id,
            )

        resource_tenant = self._get_active_tenant(resource_tenant_id)
        if not resource_tenant:
            return self._deny(request, "Resource tenant is not active or does not exist.")

        if context.tenant_id == resource_tenant_id:
            evaluations.append(IsolationEvaluation("same_tenant", IsolationDecision.ALLOW, "Context tenant matches resource tenant."))
            return self._allow(request, "Same-tenant access allowed.", tuple(evaluations))

        if request.resource.shared and context.tenant_id in request.resource.shared_with_tenant_ids:
            evaluations.append(IsolationEvaluation("resource_share", IsolationDecision.ALLOW, "Resource explicitly shared with tenant."))
            return self._allow(request, "Resource share allows tenant access.", tuple(evaluations))

        if resource_tenant_id in principal.allowed_tenant_ids:
            evaluations.append(IsolationEvaluation("principal_allowed_tenants", IsolationDecision.ALLOW, "Principal has explicit allowed tenant."))
            return self._allow(request, "Principal allowed tenant access.", tuple(evaluations))

        if resource_tenant_id in context_tenant.allowed_child_tenant_ids or resource_tenant.parent_tenant_id == context.tenant_id:
            evaluations.append(IsolationEvaluation("tenant_hierarchy", IsolationDecision.ALLOW, "Tenant hierarchy allows access."))
            return self._allow(request, "Tenant hierarchy allows access.", tuple(evaluations))

        for policy in self.policy_repository.list_policies_for_source(context.tenant_id):
            if policy.applies(request):
                evaluations.append(IsolationEvaluation("cross_tenant_policy", IsolationDecision.ALLOW, "Cross-tenant policy allows access.", policy_id=policy.policy_id))
                return self._allow(request, "Cross-tenant policy allows access.", tuple(evaluations))

        evaluations.append(IsolationEvaluation("tenant_boundary", IsolationDecision.DENY, "No allowed tenant boundary rule matched."))
        return self._deny(request, "Cross-tenant access denied.", tuple(evaluations))

    def _get_active_tenant(self, tenant_id: str) -> Optional[Tenant]:
        tenant = self.tenant_repository.get_tenant(tenant_id)
        return tenant if tenant and tenant.is_active() else None

    def _allow(self, request: TenantIsolationRequest, reason: str, evaluations: Tuple[IsolationEvaluation, ...] = ()) -> TenantIsolationResult:
        return TenantIsolationResult(
            decision=IsolationDecision.ALLOW,
            allowed=True,
            reason=reason,
            tenant_id=request.context.tenant_id,
            resource_tenant_id=request.resource.tenant_id or request.target_tenant_id,
            operation=request.operation,
            evaluations=evaluations,
            request_id=request.context.request_id,
            correlation_id=request.context.correlation_id,
            diagnostics={"evaluation_count": len(evaluations)},
        )

    def _deny(self, request: TenantIsolationRequest, reason: str, evaluations: Tuple[IsolationEvaluation, ...] = ()) -> TenantIsolationResult:
        return TenantIsolationResult(
            decision=IsolationDecision.DENY,
            allowed=False,
            reason=reason,
            tenant_id=request.context.tenant_id,
            resource_tenant_id=request.resource.tenant_id or request.target_tenant_id,
            operation=request.operation,
            evaluations=evaluations,
            request_id=request.context.request_id,
            correlation_id=request.context.correlation_id,
            diagnostics={"evaluation_count": len(evaluations)},
        )

    def _cache_key(self, request: TenantIsolationRequest) -> str:
        payload = {
            "tenant_id": request.context.tenant_id,
            "principal": dataclasses.asdict(request.context.principal),
            "resource": dataclasses.asdict(request.resource),
            "operation": request.operation.value,
            "target_tenant_id": request.target_tenant_id,
            "isolation_mode": request.context.isolation_mode.value,
        }
        return hashlib.sha256(json.dumps(_canonicalize(payload), sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _audit(self, request: TenantIsolationRequest, result: TenantIsolationResult) -> None:
        try:
            event = TenantAuditEvent(
                event_type="tenant_isolation.evaluated",
                decision=result.decision,
                allowed=result.allowed,
                reason=result.reason,
                tenant_id=request.context.tenant_id,
                resource_tenant_id=result.resource_tenant_id,
                principal_id=request.context.principal.principal_id,
                operation=request.operation,
                resource=request.resource.canonical(),
                request_id=request.context.request_id,
                correlation_id=request.context.correlation_id,
                metadata={
                    "resource_type": request.resource.resource_type,
                    "resource_id": request.resource.resource_id,
                    "isolation_mode": request.context.isolation_mode.value,
                    "environment": dict(request.context.environment),
                    "request_metadata": dict(request.metadata),
                    "diagnostics": dict(result.diagnostics),
                },
            )
            self.audit_sink.emit(event)
        except Exception:
            logger.exception("Failed to emit tenant isolation audit event.")


# =============================================================================
# Context resolution and decorators
# =============================================================================


class TenantContextResolver(ABC):
    """Resolve tenant context from external inputs."""

    @abstractmethod
    def resolve(self, source: Mapping[str, Any]) -> TenantContext:
        """Resolve tenant context."""


class HeaderTenantContextResolver(TenantContextResolver):
    """Resolve tenant context from a headers/user-claims-like mapping."""

    def __init__(self, tenant_header: str = "x-tenant-id") -> None:
        self.tenant_header = tenant_header

    def resolve(self, source: Mapping[str, Any]) -> TenantContext:
        headers = {str(k).lower(): v for k, v in dict(source.get("headers", {})).items()}
        claims = dict(source.get("claims", {}))
        tenant_id = str(headers.get(self.tenant_header.lower()) or claims.get("tenant_id") or "")
        if not tenant_id:
            raise TenantContextError("Tenant id not found in headers or claims.")
        principal = TenantPrincipal(
            principal_id=str(claims.get("sub") or claims.get("user_id") or source.get("principal_id") or ""),
            tenant_id=claims.get("tenant_id") or tenant_id,
            username=claims.get("username") or claims.get("email"),
            roles=tuple(claims.get("roles") or ()),
            groups=tuple(claims.get("groups") or ()),
            scopes=tuple(_split_scopes(claims.get("scope") or claims.get("scopes") or ())),
            allowed_tenant_ids=tuple(claims.get("allowed_tenant_ids") or ()),
            attributes=dict(claims.get("attributes") or {}),
        )
        return TenantContext(
            tenant_id=tenant_id,
            principal=principal,
            organization_id=claims.get("organization_id"),
            isolation_mode=IsolationMode(str(source.get("isolation_mode") or IsolationMode.STRICT.value)),
            request_id=source.get("request_id"),
            correlation_id=source.get("correlation_id"),
            source="headers",
            environment=dict(source.get("environment") or {}),
        )


def requires_tenant_access(
    service: TenantIsolationService,
    operation: TenantAccessOperation,
    resource_arg: str = "resource",
    context_arg: str = "tenant_context",
) -> Callable[[F], F]:
    """Decorator enforcing tenant isolation before function execution."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            context = kwargs.get(context_arg) or get_current_tenant_context(required=True)
            resource = kwargs.get(resource_arg)
            if not isinstance(context, TenantContext):
                raise TenantContextError(f"{context_arg} must be TenantContext.")
            if not isinstance(resource, TenantResource):
                raise TenantContextError(f"{resource_arg} must be TenantResource.")
            service.require(TenantIsolationRequest(context=context, resource=resource, operation=operation))
            return func(*args, **kwargs)

        return cast(F, wrapper)

    return decorator


# =============================================================================
# Utility functions and factory
# =============================================================================


def redact_sensitive(data: Mapping[str, Any]) -> JsonDict:
    sensitive_terms = (
        "password", "secret", "token", "api_key", "apikey", "authorization",
        "credential", "private_key", "session", "cookie",
    )

    def walk(value: Any) -> Any:
        if isinstance(value, Mapping):
            output: JsonDict = {}
            for key, item in value.items():
                if any(term in str(key).lower() for term in sensitive_terms):
                    output[str(key)] = "***REDACTED***"
                else:
                    output[str(key)] = walk(item)
            return output
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, tuple):
            return tuple(walk(item) for item in value)
        return value

    return walk(dict(data))


def _split_scopes(raw: Any) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return tuple(part.strip() for part in raw.split() if part.strip())
    if isinstance(raw, Iterable):
        return tuple(str(item) for item in raw)
    return (str(raw),)


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(value[key]) for key in sorted(value.keys(), key=str)}
    if isinstance(value, (list, tuple, set)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _escape_query_value(value: str) -> str:
    return value.replace("'", "\\'")


def create_default_tenant_isolation_service() -> TenantIsolationService:
    tenants = [
        Tenant(tenant_id="default", name="Default Tenant", status=TenantStatus.ACTIVE),
        Tenant(tenant_id="platform", name="Platform Tenant", status=TenantStatus.ACTIVE, allowed_child_tenant_ids=("default",)),
    ]
    return TenantIsolationService(tenant_repository=InMemoryTenantRepository(tenants))


__all__ = [
    "CrossTenantPolicy",
    "CrossTenantPolicyRepository",
    "DataFilterDialect",
    "HeaderTenantContextResolver",
    "InMemoryCrossTenantPolicyRepository",
    "InMemoryTenantRepository",
    "IsolationDecision",
    "IsolationEvaluation",
    "IsolationMode",
    "LoggingTenantAuditSink",
    "Tenant",
    "TenantAccessOperation",
    "TenantAuditEvent",
    "TenantAuditSink",
    "TenantBoundaryViolationError",
    "TenantContext",
    "TenantContextError",
    "TenantContextResolver",
    "TenantDecisionCache",
    "TenantIsolationConfig",
    "TenantIsolationError",
    "TenantIsolationRequest",
    "TenantIsolationResult",
    "TenantIsolationService",
    "TenantNotFoundError",
    "TenantPolicyError",
    "TenantPrincipal",
    "TenantRepository",
    "TenantRepositoryError",
    "TenantResource",
    "TenantStatus",
    "create_default_tenant_isolation_service",
    "get_current_tenant_context",
    "redact_sensitive",
    "requires_tenant_access",
    "reset_current_tenant_context",
    "set_current_tenant_context",
    "tenant_context_scope",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    service = create_default_tenant_isolation_service()
    principal = TenantPrincipal(principal_id="user-001", tenant_id="default", username="user@example.com", roles=("analyst",))
    context = TenantContext(tenant_id="default", principal=principal, request_id="req-demo", correlation_id="corr-demo")
    resource = TenantResource(resource_type="dataset", resource_id="ds-001", tenant_id="default")

    result = service.authorize(TenantIsolationRequest(context=context, resource=resource, operation=TenantAccessOperation.READ))
    print(json.dumps(result.to_dict(), indent=2, default=str))
    print(service.build_data_filter(context, DataFilterDialect.SQL))

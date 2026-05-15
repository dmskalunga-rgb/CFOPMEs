"""
data/security/rbac_engine.py

Enterprise-grade Role-Based Access Control (RBAC) engine.

Designed for APIs, data platforms, microservices, workers, admin panels and
internal enterprise systems.

Core capabilities:
- Users/principals, roles, permissions and scopes
- Role hierarchy and inherited permissions
- Tenant-aware RBAC
- Resource/action permission matching
- Wildcard permissions, e.g. data:read, data:*, *
- Explicit deny grants
- Static and dynamic separation-of-duty constraints
- Time-bound assignments
- Explainable decisions
- Deny-by-default posture
- Policy/decision cache with TTL
- Structured audit events
- Repository abstractions and in-memory implementation
- Decorator helpers for service/API methods

This module is framework-agnostic and can be integrated with FastAPI, Flask,
Django, Celery, Kafka consumers, data pipelines or internal services.
"""

from __future__ import annotations

import dataclasses
import fnmatch
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
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Set, Tuple, TypeVar, Union, cast

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
F = TypeVar("F", bound=Callable[..., Any])


# =============================================================================
# Exceptions
# =============================================================================


class RBACError(Exception):
    """Base RBAC error."""


class RBACAccessDeniedError(RBACError):
    """Raised when an RBAC authorization request is denied."""


class RBACValidationError(RBACError):
    """Raised when RBAC configuration or input is invalid."""


class RBACRepositoryError(RBACError):
    """Raised when a repository operation fails."""


class RBACConstraintViolationError(RBACError):
    """Raised when a separation-of-duty or policy constraint is violated."""


class RBACPrincipalResolutionError(RBACError):
    """Raised when a principal cannot be resolved."""


# =============================================================================
# Enums and config
# =============================================================================


class RBACDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    NOT_APPLICABLE = "not_applicable"
    INDETERMINATE = "indeterminate"


class RBACEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class AssignmentStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    EXPIRED = "expired"
    PENDING = "pending"


class ConstraintType(str, Enum):
    STATIC_SEPARATION_OF_DUTY = "static_separation_of_duty"
    DYNAMIC_SEPARATION_OF_DUTY = "dynamic_separation_of_duty"
    MUTUALLY_EXCLUSIVE_ROLES = "mutually_exclusive_roles"
    MAX_ACTIVE_ROLES = "max_active_roles"
    TENANT_BOUNDARY = "tenant_boundary"


class CombiningStrategy(str, Enum):
    DENY_OVERRIDES = "deny_overrides"
    ALLOW_OVERRIDES = "allow_overrides"
    MOST_SPECIFIC = "most_specific"


@dataclass(frozen=True)
class RBACConfig:
    """Runtime configuration for RBAC evaluation."""

    deny_by_default: bool = True
    fail_closed: bool = True
    enable_role_hierarchy: bool = True
    enable_permission_wildcards: bool = True
    enable_scope_checks: bool = True
    enable_constraints: bool = True
    enable_cache: bool = True
    cache_ttl_seconds: int = 60
    max_cache_entries: int = 10_000
    enable_audit: bool = True
    enable_explanations: bool = True
    combining_strategy: CombiningStrategy = CombiningStrategy.DENY_OVERRIDES
    allow_super_admin_bypass: bool = True
    super_admin_roles: Tuple[str, ...] = ("super_admin", "platform_admin")
    redact_sensitive_audit_fields: bool = True


# =============================================================================
# Domain models
# =============================================================================


@dataclass(frozen=True)
class Principal:
    """Subject requesting access."""

    principal_id: str
    username: Optional[str] = None
    tenant_id: Optional[str] = None
    roles: Tuple[str, ...] = ()
    scopes: Tuple[str, ...] = ()
    attributes: JsonDict = field(default_factory=dict)
    authenticated: bool = True

    def validate(self) -> None:
        if not self.principal_id:
            raise RBACValidationError("principal_id is required.")


@dataclass(frozen=True)
class Permission:
    """Permission definition."""

    permission_id: str
    name: str
    resource: str
    action: str
    effect: RBACEffect = RBACEffect.ALLOW
    description: Optional[str] = None
    scopes: Tuple[str, ...] = ()
    tenant_id: Optional[str] = None
    conditions: JsonDict = field(default_factory=dict)
    metadata: JsonDict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.permission_id:
            raise RBACValidationError("permission_id is required.")
        if not self.name:
            raise RBACValidationError("permission name is required.")
        if not self.resource:
            raise RBACValidationError("permission resource is required.")
        if not self.action:
            raise RBACValidationError("permission action is required.")

    def canonical(self) -> str:
        return f"{self.resource}:{self.action}"


@dataclass(frozen=True)
class Role:
    """Role definition with direct permissions and inherited roles."""

    role_id: str
    name: str
    description: Optional[str] = None
    permission_ids: Tuple[str, ...] = ()
    parent_role_ids: Tuple[str, ...] = ()
    tenant_id: Optional[str] = None
    active: bool = True
    metadata: JsonDict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def validate(self) -> None:
        if not self.role_id:
            raise RBACValidationError("role_id is required.")
        if not self.name:
            raise RBACValidationError("role name is required.")


@dataclass(frozen=True)
class RoleAssignment:
    """Assignment of a role to a principal."""

    assignment_id: str
    principal_id: str
    role_id: str
    tenant_id: Optional[str] = None
    status: AssignmentStatus = AssignmentStatus.ACTIVE
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    assigned_by: Optional[str] = None
    reason: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_active(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if self.status != AssignmentStatus.ACTIVE:
            return False
        if self.valid_from and self.valid_from > now:
            return False
        if self.valid_until and self.valid_until <= now:
            return False
        return True


@dataclass(frozen=True)
class RBACConstraint:
    """RBAC constraint such as separation of duty."""

    constraint_id: str
    constraint_type: ConstraintType
    name: str
    role_ids: Tuple[str, ...] = ()
    max_active_roles: Optional[int] = None
    tenant_id: Optional[str] = None
    enabled: bool = True
    description: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.constraint_id:
            raise RBACValidationError("constraint_id is required.")
        if self.constraint_type in {
            ConstraintType.STATIC_SEPARATION_OF_DUTY,
            ConstraintType.DYNAMIC_SEPARATION_OF_DUTY,
            ConstraintType.MUTUALLY_EXCLUSIVE_ROLES,
        } and len(self.role_ids) < 2:
            raise RBACValidationError("separation-of-duty constraints require at least two roles.")
        if self.constraint_type == ConstraintType.MAX_ACTIVE_ROLES and not self.max_active_roles:
            raise RBACValidationError("max_active_roles is required for MAX_ACTIVE_ROLES constraint.")


@dataclass(frozen=True)
class RBACRequest:
    """Authorization request evaluated by the RBAC engine."""

    principal: Principal
    resource: str
    action: str
    required_permissions: Tuple[str, ...] = ()
    required_roles: Tuple[str, ...] = ()
    required_scopes: Tuple[str, ...] = ()
    tenant_id: Optional[str] = None
    active_roles: Tuple[str, ...] = ()
    environment: JsonDict = field(default_factory=dict)
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def validate(self) -> None:
        self.principal.validate()
        if not self.resource:
            raise RBACValidationError("resource is required.")
        if not self.action:
            raise RBACValidationError("action is required.")

    def requested_permission(self) -> str:
        return f"{self.resource}:{self.action}"


@dataclass(frozen=True)
class RBACEvaluation:
    """Single RBAC evaluation detail."""

    source: str
    source_id: str
    decision: RBACDecision
    effect: Optional[RBACEffect]
    applicable: bool
    reason: str
    priority: int = 100
    matched_permissions: Tuple[str, ...] = ()
    matched_roles: Tuple[str, ...] = ()
    diagnostics: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class RBACResult:
    """Final RBAC authorization result."""

    decision: RBACDecision
    allowed: bool
    reason: str
    request_id: Optional[str]
    correlation_id: Optional[str]
    principal_id: str
    requested_permission: str
    evaluations: Tuple[RBACEvaluation, ...] = ()
    effective_roles: Tuple[str, ...] = ()
    effective_permissions: Tuple[str, ...] = ()
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cache_hit: bool = False
    diagnostics: JsonDict = field(default_factory=dict)

    def require_allowed(self) -> None:
        if not self.allowed:
            raise RBACAccessDeniedError(self.reason)

    def to_dict(self) -> JsonDict:
        return {
            "decision": self.decision.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "principal_id": self.principal_id,
            "requested_permission": self.requested_permission,
            "evaluations": [_evaluation_to_dict(item) for item in self.evaluations],
            "effective_roles": list(self.effective_roles),
            "effective_permissions": list(self.effective_permissions),
            "evaluated_at": self.evaluated_at.isoformat(),
            "cache_hit": self.cache_hit,
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class RBACAuditEvent:
    """Structured RBAC audit event."""

    event_type: str
    decision: RBACDecision
    allowed: bool
    reason: str
    principal_id: str
    username: Optional[str]
    tenant_id: Optional[str]
    resource: str
    action: str
    requested_permission: str
    request_id: Optional[str]
    correlation_id: Optional[str]
    matched_roles: Tuple[str, ...] = ()
    matched_permissions: Tuple[str, ...] = ()
    metadata: JsonDict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self, redact: bool = True) -> JsonDict:
        data = {
            "event_type": self.event_type,
            "decision": self.decision.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "principal_id": self.principal_id,
            "username": self.username,
            "tenant_id": self.tenant_id,
            "resource": self.resource,
            "action": self.action,
            "requested_permission": self.requested_permission,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "matched_roles": list(self.matched_roles),
            "matched_permissions": list(self.matched_permissions),
            "metadata": dict(self.metadata),
            "timestamp": self.timestamp.isoformat(),
        }
        return redact_sensitive(data) if redact else data


# =============================================================================
# Repositories
# =============================================================================


class RBACRepository(ABC):
    """RBAC repository abstraction."""

    @abstractmethod
    def get_role(self, role_id: str) -> Optional[Role]:
        """Return a role by ID."""

    @abstractmethod
    def get_permission(self, permission_id: str) -> Optional[Permission]:
        """Return a permission by ID."""

    @abstractmethod
    def list_roles(self, tenant_id: Optional[str] = None) -> Sequence[Role]:
        """List roles."""

    @abstractmethod
    def list_permissions(self, tenant_id: Optional[str] = None) -> Sequence[Permission]:
        """List permissions."""

    @abstractmethod
    def list_assignments_for_principal(self, principal_id: str, tenant_id: Optional[str] = None) -> Sequence[RoleAssignment]:
        """List role assignments for principal."""

    @abstractmethod
    def list_constraints(self, tenant_id: Optional[str] = None) -> Sequence[RBACConstraint]:
        """List RBAC constraints."""

    @abstractmethod
    def upsert_role(self, role: Role) -> None:
        """Create or update a role."""

    @abstractmethod
    def upsert_permission(self, permission: Permission) -> None:
        """Create or update a permission."""

    @abstractmethod
    def upsert_assignment(self, assignment: RoleAssignment) -> None:
        """Create or update a role assignment."""

    @abstractmethod
    def upsert_constraint(self, constraint: RBACConstraint) -> None:
        """Create or update a constraint."""


class InMemoryRBACRepository(RBACRepository):
    """Thread-safe in-memory RBAC repository."""

    def __init__(
        self,
        roles: Optional[Iterable[Role]] = None,
        permissions: Optional[Iterable[Permission]] = None,
        assignments: Optional[Iterable[RoleAssignment]] = None,
        constraints: Optional[Iterable[RBACConstraint]] = None,
    ) -> None:
        self._roles: Dict[str, Role] = {}
        self._permissions: Dict[str, Permission] = {}
        self._assignments: Dict[str, RoleAssignment] = {}
        self._constraints: Dict[str, RBACConstraint] = {}
        self._lock = threading.RLock()

        for permission in permissions or ():
            self.upsert_permission(permission)
        for role in roles or ():
            self.upsert_role(role)
        for assignment in assignments or ():
            self.upsert_assignment(assignment)
        for constraint in constraints or ():
            self.upsert_constraint(constraint)

    def get_role(self, role_id: str) -> Optional[Role]:
        with self._lock:
            return self._roles.get(role_id)

    def get_permission(self, permission_id: str) -> Optional[Permission]:
        with self._lock:
            return self._permissions.get(permission_id)

    def list_roles(self, tenant_id: Optional[str] = None) -> Sequence[Role]:
        with self._lock:
            roles = tuple(self._roles.values())
            if tenant_id is not None:
                roles = tuple(role for role in roles if role.tenant_id in {None, tenant_id})
            return tuple(sorted(roles, key=lambda item: item.role_id))

    def list_permissions(self, tenant_id: Optional[str] = None) -> Sequence[Permission]:
        with self._lock:
            permissions = tuple(self._permissions.values())
            if tenant_id is not None:
                permissions = tuple(permission for permission in permissions if permission.tenant_id in {None, tenant_id})
            return tuple(sorted(permissions, key=lambda item: item.permission_id))

    def list_assignments_for_principal(self, principal_id: str, tenant_id: Optional[str] = None) -> Sequence[RoleAssignment]:
        with self._lock:
            assignments = tuple(item for item in self._assignments.values() if item.principal_id == principal_id)
            if tenant_id is not None:
                assignments = tuple(item for item in assignments if item.tenant_id in {None, tenant_id})
            return tuple(sorted(assignments, key=lambda item: item.created_at))

    def list_constraints(self, tenant_id: Optional[str] = None) -> Sequence[RBACConstraint]:
        with self._lock:
            constraints = tuple(self._constraints.values())
            if tenant_id is not None:
                constraints = tuple(item for item in constraints if item.tenant_id in {None, tenant_id})
            return tuple(sorted(constraints, key=lambda item: item.constraint_id))

    def upsert_role(self, role: Role) -> None:
        role.validate()
        with self._lock:
            self._roles[role.role_id] = dataclasses.replace(role, updated_at=datetime.now(timezone.utc))

    def upsert_permission(self, permission: Permission) -> None:
        permission.validate()
        with self._lock:
            self._permissions[permission.permission_id] = permission

    def upsert_assignment(self, assignment: RoleAssignment) -> None:
        if not assignment.assignment_id or not assignment.principal_id or not assignment.role_id:
            raise RBACValidationError("assignment_id, principal_id and role_id are required.")
        with self._lock:
            self._assignments[assignment.assignment_id] = assignment

    def upsert_constraint(self, constraint: RBACConstraint) -> None:
        constraint.validate()
        with self._lock:
            self._constraints[constraint.constraint_id] = constraint


# =============================================================================
# Audit and cache
# =============================================================================


class RBACAuditSink(ABC):
    """RBAC audit sink abstraction."""

    @abstractmethod
    def emit(self, event: RBACAuditEvent) -> None:
        """Emit RBAC audit event."""


class LoggingRBACAuditSink(RBACAuditSink):
    """Logging-backed RBAC audit sink."""

    def __init__(self, audit_logger: Optional[logging.Logger] = None, redact: bool = True) -> None:
        self.audit_logger = audit_logger or logging.getLogger("security.rbac.audit")
        self.redact = redact

    def emit(self, event: RBACAuditEvent) -> None:
        self.audit_logger.info("rbac_event=%s", json.dumps(event.to_dict(redact=self.redact), sort_keys=True, default=str))


@dataclass
class _CacheEntry:
    result: RBACResult
    expires_at: float


class RBACDecisionCache:
    """TTL cache for RBAC decisions."""

    def __init__(self, ttl_seconds: int = 60, max_entries: int = 10_000) -> None:
        self.ttl_seconds = max(0, ttl_seconds)
        self.max_entries = max(1, max_entries)
        self._cache: Dict[str, _CacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[RBACResult]:
        now = time.time()
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            if entry.expires_at <= now:
                self._cache.pop(key, None)
                return None
            return dataclasses.replace(entry.result, cache_hit=True)

    def set(self, key: str, result: RBACResult) -> None:
        if self.ttl_seconds <= 0:
            return
        with self._lock:
            if len(self._cache) >= self.max_entries:
                self._evict()
            self._cache[key] = _CacheEntry(result=result, expires_at=time.time() + self.ttl_seconds)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _evict(self) -> None:
        now = time.time()
        expired = [key for key, entry in self._cache.items() if entry.expires_at <= now]
        for key in expired:
            self._cache.pop(key, None)
        if len(self._cache) >= self.max_entries and self._cache:
            oldest = min(self._cache.items(), key=lambda item: item[1].expires_at)[0]
            self._cache.pop(oldest, None)


# =============================================================================
# RBAC engine
# =============================================================================


class RBACEngine:
    """Enterprise RBAC authorization engine."""

    def __init__(
        self,
        repository: RBACRepository,
        config: Optional[RBACConfig] = None,
        audit_sink: Optional[RBACAuditSink] = None,
        cache: Optional[RBACDecisionCache] = None,
    ) -> None:
        self.repository = repository
        self.config = config or RBACConfig()
        self.audit_sink = audit_sink or LoggingRBACAuditSink(redact=self.config.redact_sensitive_audit_fields)
        self.cache = cache or RBACDecisionCache(self.config.cache_ttl_seconds, self.config.max_cache_entries)

    def authorize(self, request: RBACRequest) -> RBACResult:
        """Evaluate RBAC request and return an explainable decision."""
        request.validate()
        cache_key = self._cache_key(request)

        if self.config.enable_cache:
            cached = self.cache.get(cache_key)
            if cached:
                return cached

        try:
            result = self._evaluate(request)
        except Exception as exc:
            logger.exception("RBAC evaluation failed. request_id=%s", request.request_id)
            if self.config.fail_closed:
                result = self._deny(
                    request,
                    "RBAC evaluation failed; fail-closed deny applied.",
                    evaluations=(RBACEvaluation(
                        source="rbac_engine",
                        source_id="fail_closed",
                        decision=RBACDecision.DENY,
                        effect=RBACEffect.DENY,
                        applicable=True,
                        reason=str(exc),
                        priority=0,
                        diagnostics={"error_type": type(exc).__name__},
                    ),),
                )
            else:
                result = RBACResult(
                    decision=RBACDecision.INDETERMINATE,
                    allowed=False,
                    reason="RBAC evaluation failed and fail_closed is disabled.",
                    request_id=request.request_id,
                    correlation_id=request.correlation_id,
                    principal_id=request.principal.principal_id,
                    requested_permission=request.requested_permission(),
                    diagnostics={"error": str(exc), "error_type": type(exc).__name__},
                )

        if not self.config.enable_explanations:
            result = dataclasses.replace(result, evaluations=(), diagnostics={})

        if self.config.enable_cache:
            self.cache.set(cache_key, result)

        if self.config.enable_audit:
            self._audit(request, result)

        return result

    def require(self, request: RBACRequest) -> None:
        self.authorize(request).require_allowed()

    def can(self, request: RBACRequest) -> bool:
        return self.authorize(request).allowed

    def explain(self, request: RBACRequest) -> JsonDict:
        return self.authorize(request).to_dict()

    def clear_cache(self) -> None:
        self.cache.clear()

    def _evaluate(self, request: RBACRequest) -> RBACResult:
        if not request.principal.authenticated:
            return self._deny(request, "Principal is not authenticated.")

        if self.config.allow_super_admin_bypass and set(request.principal.roles).intersection(self.config.super_admin_roles):
            evaluation = RBACEvaluation(
                source="super_admin_bypass",
                source_id="super_admin",
                decision=RBACDecision.ALLOW,
                effect=RBACEffect.ALLOW,
                applicable=True,
                reason="Super admin bypass allowed by configuration.",
                priority=0,
                matched_roles=tuple(set(request.principal.roles).intersection(self.config.super_admin_roles)),
            )
            return self._allow(request, "Super admin bypass applied.", (evaluation,), effective_roles=request.principal.roles)

        tenant_id = request.tenant_id or request.principal.tenant_id
        effective_role_ids = self._effective_roles(request.principal, tenant_id)
        if request.active_roles:
            active_set = set(request.active_roles)
            effective_role_ids = tuple(role_id for role_id in effective_role_ids if role_id in active_set)

        constraint_evaluations = self._evaluate_constraints(request, effective_role_ids, tenant_id)
        if any(item.effect == RBACEffect.DENY and item.applicable for item in constraint_evaluations):
            return self._deny(
                request,
                "RBAC constraint denied the request.",
                constraint_evaluations,
                effective_roles=effective_role_ids,
                effective_permissions=self._effective_permission_names(effective_role_ids, tenant_id),
            )

        evaluations = list(constraint_evaluations)
        effective_permissions = self._effective_permissions(effective_role_ids, tenant_id)
        effective_permission_names = tuple(sorted({permission.canonical() for permission in effective_permissions}))

        if request.required_roles:
            has_roles = set(request.required_roles).issubset(set(effective_role_ids))
            evaluations.append(RBACEvaluation(
                source="required_roles",
                source_id="required_roles",
                decision=RBACDecision.ALLOW if has_roles else RBACDecision.DENY,
                effect=RBACEffect.ALLOW if has_roles else RBACEffect.DENY,
                applicable=True,
                reason="Required roles satisfied." if has_roles else "Required roles missing.",
                priority=20,
                matched_roles=tuple(sorted(set(request.required_roles).intersection(effective_role_ids))),
                diagnostics={"required_roles": list(request.required_roles), "effective_roles": list(effective_role_ids)},
            ))

        if self.config.enable_scope_checks and request.required_scopes:
            has_scopes = set(request.required_scopes).issubset(set(request.principal.scopes))
            evaluations.append(RBACEvaluation(
                source="required_scopes",
                source_id="required_scopes",
                decision=RBACDecision.ALLOW if has_scopes else RBACDecision.DENY,
                effect=RBACEffect.ALLOW if has_scopes else RBACEffect.DENY,
                applicable=True,
                reason="Required scopes satisfied." if has_scopes else "Required scopes missing.",
                priority=30,
                diagnostics={"required_scopes": list(request.required_scopes), "principal_scopes": list(request.principal.scopes)},
            ))

        permission_evaluation = self._evaluate_permissions(request, effective_permissions)
        evaluations.append(permission_evaluation)

        if request.required_permissions:
            required_eval = self._evaluate_required_permissions(request, effective_permissions)
            evaluations.append(required_eval)

        return self._combine(request, tuple(evaluations), effective_role_ids, effective_permission_names)

    def _effective_roles(self, principal: Principal, tenant_id: Optional[str]) -> Tuple[str, ...]:
        assigned = {
            assignment.role_id
            for assignment in self.repository.list_assignments_for_principal(principal.principal_id, tenant_id)
            if assignment.is_active()
        }
        direct = set(principal.roles)
        role_ids = assigned.union(direct)

        if not self.config.enable_role_hierarchy:
            return tuple(sorted(role_ids))

        expanded = set(role_ids)
        visiting: Set[str] = set()

        def visit(role_id: str) -> None:
            if role_id in visiting:
                raise RBACValidationError(f"Role hierarchy cycle detected at role: {role_id}")
            visiting.add(role_id)
            role = self.repository.get_role(role_id)
            if role and role.active:
                if tenant_id and role.tenant_id not in {None, tenant_id}:
                    visiting.remove(role_id)
                    return
                for parent_id in role.parent_role_ids:
                    if parent_id not in expanded:
                        expanded.add(parent_id)
                    visit(parent_id)
            visiting.remove(role_id)

        for role_id in tuple(role_ids):
            visit(role_id)
        return tuple(sorted(expanded))

    def _effective_permissions(self, role_ids: Sequence[str], tenant_id: Optional[str]) -> Tuple[Permission, ...]:
        permissions: Dict[str, Permission] = {}
        for role_id in role_ids:
            role = self.repository.get_role(role_id)
            if not role or not role.active:
                continue
            if tenant_id and role.tenant_id not in {None, tenant_id}:
                continue
            for permission_id in role.permission_ids:
                permission = self.repository.get_permission(permission_id)
                if not permission:
                    continue
                if tenant_id and permission.tenant_id not in {None, tenant_id}:
                    continue
                permissions[permission.permission_id] = permission
        return tuple(permissions.values())

    def _effective_permission_names(self, role_ids: Sequence[str], tenant_id: Optional[str]) -> Tuple[str, ...]:
        return tuple(sorted({permission.canonical() for permission in self._effective_permissions(role_ids, tenant_id)}))

    def _evaluate_permissions(self, request: RBACRequest, permissions: Sequence[Permission]) -> RBACEvaluation:
        requested = request.requested_permission()
        matched_allow: list[Permission] = []
        matched_deny: list[Permission] = []

        for permission in permissions:
            if not self._permission_matches(permission, request):
                continue
            if not self._permission_conditions_match(permission, request):
                continue
            if permission.effect == RBACEffect.DENY:
                matched_deny.append(permission)
            else:
                matched_allow.append(permission)

        if matched_deny:
            return RBACEvaluation(
                source="permissions",
                source_id="permission_match",
                decision=RBACDecision.DENY,
                effect=RBACEffect.DENY,
                applicable=True,
                reason="Explicit deny permission matched.",
                priority=10,
                matched_permissions=tuple(permission.canonical() for permission in matched_deny),
            )

        if matched_allow:
            return RBACEvaluation(
                source="permissions",
                source_id="permission_match",
                decision=RBACDecision.ALLOW,
                effect=RBACEffect.ALLOW,
                applicable=True,
                reason="Allow permission matched.",
                priority=50,
                matched_permissions=tuple(permission.canonical() for permission in matched_allow),
            )

        return RBACEvaluation(
            source="permissions",
            source_id="permission_match",
            decision=RBACDecision.NOT_APPLICABLE,
            effect=None,
            applicable=False,
            reason=f"No permission matched requested permission '{requested}'.",
            priority=100,
        )

    def _evaluate_required_permissions(self, request: RBACRequest, permissions: Sequence[Permission]) -> RBACEvaluation:
        effective_allow = {
            permission.canonical()
            for permission in permissions
            if permission.effect == RBACEffect.ALLOW and self._permission_conditions_match(permission, request)
        }
        required = set(request.required_permissions)
        missing = required.difference(effective_allow)
        return RBACEvaluation(
            source="required_permissions",
            source_id="required_permissions",
            decision=RBACDecision.DENY if missing else RBACDecision.ALLOW,
            effect=RBACEffect.DENY if missing else RBACEffect.ALLOW,
            applicable=True,
            reason="Required permissions missing." if missing else "Required permissions satisfied.",
            priority=40,
            matched_permissions=tuple(sorted(required.intersection(effective_allow))),
            diagnostics={"required_permissions": sorted(required), "missing_permissions": sorted(missing)},
        )

    def _permission_matches(self, permission: Permission, request: RBACRequest) -> bool:
        resource_match = _match_value(request.resource, permission.resource, self.config.enable_permission_wildcards)
        action_match = _match_value(request.action, permission.action, self.config.enable_permission_wildcards)
        if not resource_match or not action_match:
            return False
        tenant_id = request.tenant_id or request.principal.tenant_id
        if permission.tenant_id and tenant_id and permission.tenant_id != tenant_id:
            return False
        if permission.scopes and not set(permission.scopes).issubset(set(request.principal.scopes)):
            return False
        return True

    def _permission_conditions_match(self, permission: Permission, request: RBACRequest) -> bool:
        if not permission.conditions:
            return True
        for key, expected in permission.conditions.items():
            actual = resolve_path({"principal": dataclasses.asdict(request.principal), "environment": request.environment, "metadata": request.metadata}, key)
            if actual != expected:
                return False
        return True

    def _evaluate_constraints(self, request: RBACRequest, role_ids: Sequence[str], tenant_id: Optional[str]) -> Tuple[RBACEvaluation, ...]:
        if not self.config.enable_constraints:
            return ()

        evaluations: list[RBACEvaluation] = []
        role_set = set(role_ids)

        for constraint in self.repository.list_constraints(tenant_id):
            if not constraint.enabled:
                continue
            constrained_roles = set(constraint.role_ids)
            intersection = role_set.intersection(constrained_roles)

            if constraint.constraint_type in {
                ConstraintType.STATIC_SEPARATION_OF_DUTY,
                ConstraintType.DYNAMIC_SEPARATION_OF_DUTY,
                ConstraintType.MUTUALLY_EXCLUSIVE_ROLES,
            }:
                if len(intersection) >= 2:
                    evaluations.append(RBACEvaluation(
                        source="constraint",
                        source_id=constraint.constraint_id,
                        decision=RBACDecision.DENY,
                        effect=RBACEffect.DENY,
                        applicable=True,
                        reason=f"Constraint violated: {constraint.name}.",
                        priority=5,
                        matched_roles=tuple(sorted(intersection)),
                    ))

            elif constraint.constraint_type == ConstraintType.MAX_ACTIVE_ROLES:
                max_roles = constraint.max_active_roles or 0
                if len(role_set) > max_roles:
                    evaluations.append(RBACEvaluation(
                        source="constraint",
                        source_id=constraint.constraint_id,
                        decision=RBACDecision.DENY,
                        effect=RBACEffect.DENY,
                        applicable=True,
                        reason=f"Maximum active roles exceeded: {len(role_set)} > {max_roles}.",
                        priority=5,
                        matched_roles=tuple(sorted(role_set)),
                    ))

            elif constraint.constraint_type == ConstraintType.TENANT_BOUNDARY:
                if tenant_id and request.principal.tenant_id and tenant_id != request.principal.tenant_id:
                    evaluations.append(RBACEvaluation(
                        source="constraint",
                        source_id=constraint.constraint_id,
                        decision=RBACDecision.DENY,
                        effect=RBACEffect.DENY,
                        applicable=True,
                        reason="Tenant boundary constraint violated.",
                        priority=5,
                    ))

        return tuple(evaluations)

    def _combine(
        self,
        request: RBACRequest,
        evaluations: Tuple[RBACEvaluation, ...],
        effective_roles: Tuple[str, ...],
        effective_permissions: Tuple[str, ...],
    ) -> RBACResult:
        applicable = tuple(item for item in evaluations if item.applicable and item.effect is not None)
        deny_items = tuple(item for item in applicable if item.effect == RBACEffect.DENY)
        allow_items = tuple(item for item in applicable if item.effect == RBACEffect.ALLOW)

        if not applicable:
            if self.config.deny_by_default:
                return self._deny(request, "No applicable RBAC permission found; deny-by-default applied.", evaluations, effective_roles, effective_permissions)
            return RBACResult(
                decision=RBACDecision.NOT_APPLICABLE,
                allowed=False,
                reason="No applicable RBAC permission found.",
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                principal_id=request.principal.principal_id,
                requested_permission=request.requested_permission(),
                evaluations=evaluations,
                effective_roles=effective_roles,
                effective_permissions=effective_permissions,
            )

        if self.config.combining_strategy == CombiningStrategy.DENY_OVERRIDES:
            if deny_items:
                return self._deny(request, f"Deny-overrides selected deny from {deny_items[0].source_id}.", evaluations, effective_roles, effective_permissions)
            if allow_items:
                return self._allow(request, f"Deny-overrides selected allow from {allow_items[0].source_id}.", evaluations, effective_roles, effective_permissions)

        if self.config.combining_strategy == CombiningStrategy.ALLOW_OVERRIDES:
            if allow_items:
                return self._allow(request, f"Allow-overrides selected allow from {allow_items[0].source_id}.", evaluations, effective_roles, effective_permissions)
            if deny_items:
                return self._deny(request, f"Allow-overrides selected deny from {deny_items[0].source_id}.", evaluations, effective_roles, effective_permissions)

        if self.config.combining_strategy == CombiningStrategy.MOST_SPECIFIC:
            selected = sorted(applicable, key=lambda item: (item.priority, item.source_id))[0]
            if selected.effect == RBACEffect.ALLOW:
                return self._allow(request, f"Most-specific selected allow from {selected.source_id}.", evaluations, effective_roles, effective_permissions)
            return self._deny(request, f"Most-specific selected deny from {selected.source_id}.", evaluations, effective_roles, effective_permissions)

        return self._deny(request, "No RBAC allow decision produced; deny applied.", evaluations, effective_roles, effective_permissions)

    def _allow(
        self,
        request: RBACRequest,
        reason: str,
        evaluations: Tuple[RBACEvaluation, ...],
        effective_roles: Tuple[str, ...] = (),
        effective_permissions: Tuple[str, ...] = (),
    ) -> RBACResult:
        return RBACResult(
            decision=RBACDecision.ALLOW,
            allowed=True,
            reason=reason,
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            principal_id=request.principal.principal_id,
            requested_permission=request.requested_permission(),
            evaluations=evaluations,
            effective_roles=effective_roles,
            effective_permissions=effective_permissions,
            diagnostics={"evaluation_count": len(evaluations)},
        )

    def _deny(
        self,
        request: RBACRequest,
        reason: str,
        evaluations: Tuple[RBACEvaluation, ...] = (),
        effective_roles: Tuple[str, ...] = (),
        effective_permissions: Tuple[str, ...] = (),
    ) -> RBACResult:
        return RBACResult(
            decision=RBACDecision.DENY,
            allowed=False,
            reason=reason,
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            principal_id=request.principal.principal_id,
            requested_permission=request.requested_permission(),
            evaluations=evaluations,
            effective_roles=effective_roles,
            effective_permissions=effective_permissions,
            diagnostics={"evaluation_count": len(evaluations)},
        )

    def _cache_key(self, request: RBACRequest) -> str:
        payload = {
            "principal": _canonicalize(dataclasses.asdict(request.principal)),
            "resource": request.resource,
            "action": request.action,
            "required_permissions": list(request.required_permissions),
            "required_roles": list(request.required_roles),
            "required_scopes": list(request.required_scopes),
            "tenant_id": request.tenant_id,
            "active_roles": list(request.active_roles),
            "environment": _canonicalize(request.environment),
            "metadata": _canonicalize(request.metadata),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _audit(self, request: RBACRequest, result: RBACResult) -> None:
        try:
            matched_roles = tuple(sorted({role for item in result.evaluations for role in item.matched_roles}))
            matched_permissions = tuple(sorted({permission for item in result.evaluations for permission in item.matched_permissions}))
            event = RBACAuditEvent(
                event_type="rbac.authorization.evaluated",
                decision=result.decision,
                allowed=result.allowed,
                reason=result.reason,
                principal_id=request.principal.principal_id,
                username=request.principal.username,
                tenant_id=request.tenant_id or request.principal.tenant_id,
                resource=request.resource,
                action=request.action,
                requested_permission=request.requested_permission(),
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                matched_roles=matched_roles,
                matched_permissions=matched_permissions,
                metadata={
                    "effective_roles": list(result.effective_roles),
                    "effective_permissions": list(result.effective_permissions),
                    "environment": dict(request.environment),
                    "request_metadata": dict(request.metadata),
                },
            )
            self.audit_sink.emit(event)
        except Exception:
            logger.exception("Failed to emit RBAC audit event. request_id=%s", request.request_id)


# =============================================================================
# Decorators/helpers
# =============================================================================


class RBACContextProvider(ABC):
    """Extract RBAC context from decorated function arguments."""

    @abstractmethod
    def get_principal(self, args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> Principal:
        """Return principal."""

    def get_environment(self, args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> JsonDict:
        return {}


class KeywordRBACContextProvider(RBACContextProvider):
    """Context provider that expects a `principal` keyword argument."""

    def __init__(self, principal_key: str = "principal", environment_key: str = "environment") -> None:
        self.principal_key = principal_key
        self.environment_key = environment_key

    def get_principal(self, args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> Principal:
        principal = kwargs.get(self.principal_key)
        if not isinstance(principal, Principal):
            raise RBACPrincipalResolutionError(f"Keyword '{self.principal_key}' must contain a Principal.")
        return principal

    def get_environment(self, args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> JsonDict:
        raw = kwargs.get(self.environment_key) or {}
        if not isinstance(raw, Mapping):
            raise RBACValidationError(f"Keyword '{self.environment_key}' must be a mapping.")
        return dict(raw)


def requires_rbac(
    engine: RBACEngine,
    resource: str,
    action: str,
    required_permissions: Sequence[str] = (),
    required_roles: Sequence[str] = (),
    required_scopes: Sequence[str] = (),
    context_provider: Optional[RBACContextProvider] = None,
) -> Callable[[F], F]:
    """Decorator enforcing RBAC before function execution."""

    provider = context_provider or KeywordRBACContextProvider()

    def decorator(func: F) -> F:
        @dataclasses.dataclass
        class _WrapperState:
            pass

        @functools_wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            principal = provider.get_principal(args, kwargs)
            environment = provider.get_environment(args, kwargs)
            request = RBACRequest(
                principal=principal,
                resource=resource,
                action=action,
                required_permissions=tuple(required_permissions),
                required_roles=tuple(required_roles),
                required_scopes=tuple(required_scopes),
                tenant_id=principal.tenant_id,
                environment=environment,
                request_id=str(uuid.uuid4()),
                correlation_id=str(environment.get("correlation_id")) if environment.get("correlation_id") else None,
            )
            engine.require(request)
            return func(*args, **kwargs)

        return cast(F, wrapper)

    return decorator


def functools_wraps(func: F) -> Callable[[F], F]:
    """Small indirection to keep imports minimal and explicit."""
    import functools

    return functools.wraps(func)


# =============================================================================
# Factory and utilities
# =============================================================================


def create_default_rbac_engine() -> RBACEngine:
    """Create a practical default RBAC engine for development/tests."""
    permissions = [
        Permission("perm-datasets-read", "Read datasets", "datasets", "read", scopes=("datasets:read",)),
        Permission("perm-datasets-write", "Write datasets", "datasets", "write", scopes=("datasets:write",)),
        Permission("perm-datasets-delete", "Delete datasets", "datasets", "delete", effect=RBACEffect.ALLOW),
        Permission("perm-users-manage", "Manage users", "users", "*"),
        Permission("perm-admin-all", "Admin all", "*", "*"),
        Permission("perm-deny-secret-delete", "Deny secret deletion", "secrets", "delete", effect=RBACEffect.DENY),
    ]
    roles = [
        Role("role-viewer", "Viewer", permission_ids=("perm-datasets-read",)),
        Role("role-editor", "Editor", permission_ids=("perm-datasets-write",), parent_role_ids=("role-viewer",)),
        Role("role-admin", "Admin", permission_ids=("perm-users-manage",), parent_role_ids=("role-editor",)),
        Role("super_admin", "Super Admin", permission_ids=("perm-admin-all",)),
    ]
    constraints = [
        RBACConstraint(
            constraint_id="sod-admin-viewer-example",
            constraint_type=ConstraintType.MUTUALLY_EXCLUSIVE_ROLES,
            name="Example mutually exclusive role constraint",
            role_ids=("role-auditor", "role-payment-approver"),
            enabled=True,
        )
    ]
    repository = InMemoryRBACRepository(roles=roles, permissions=permissions, constraints=constraints)
    return RBACEngine(repository=repository)


def resolve_path(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current


def redact_sensitive(data: Mapping[str, Any]) -> JsonDict:
    sensitive_terms = (
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "private_key",
        "session",
    )

    def walk(value: Any) -> Any:
        if isinstance(value, Mapping):
            output: JsonDict = {}
            for key, item in value.items():
                key_text = str(key)
                if any(term in key_text.lower() for term in sensitive_terms):
                    output[key_text] = "***REDACTED***"
                else:
                    output[key_text] = walk(item)
            return output
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, tuple):
            return tuple(walk(item) for item in value)
        return value

    return walk(dict(data))


def _match_value(value: str, pattern: str, wildcard: bool) -> bool:
    if pattern == "*":
        return True
    if wildcard:
        return fnmatch.fnmatchcase(value, pattern)
    return value == pattern


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


def _evaluation_to_dict(item: RBACEvaluation) -> JsonDict:
    return {
        "source": item.source,
        "source_id": item.source_id,
        "decision": item.decision.value,
        "effect": item.effect.value if item.effect else None,
        "applicable": item.applicable,
        "reason": item.reason,
        "priority": item.priority,
        "matched_permissions": list(item.matched_permissions),
        "matched_roles": list(item.matched_roles),
        "diagnostics": dict(item.diagnostics),
    }


__all__ = [
    "AssignmentStatus",
    "CombiningStrategy",
    "ConstraintType",
    "InMemoryRBACRepository",
    "KeywordRBACContextProvider",
    "LoggingRBACAuditSink",
    "Permission",
    "Principal",
    "RBACAccessDeniedError",
    "RBACAuditEvent",
    "RBACAuditSink",
    "RBACConfig",
    "RBACConstraint",
    "RBACConstraintViolationError",
    "RBACDecision",
    "RBACDecisionCache",
    "RBACEffect",
    "RBACEngine",
    "RBACError",
    "RBACEvaluation",
    "RBACPrincipalResolutionError",
    "RBACRepository",
    "RBACRepositoryError",
    "RBACRequest",
    "RBACResult",
    "RBACValidationError",
    "Role",
    "RoleAssignment",
    "create_default_rbac_engine",
    "redact_sensitive",
    "requires_rbac",
    "resolve_path",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    engine = create_default_rbac_engine()
    principal = Principal(
        principal_id="user-001",
        username="analyst@example.com",
        tenant_id="tenant-a",
        roles=("role-editor",),
        scopes=("datasets:read", "datasets:write"),
    )

    request = RBACRequest(
        principal=principal,
        resource="datasets",
        action="write",
        required_scopes=("datasets:write",),
        request_id="req-demo",
        correlation_id="corr-demo",
    )

    result = engine.authorize(request)
    print(json.dumps(result.to_dict(), indent=2, default=str))

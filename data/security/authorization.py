"""
data/security/authorization.py

Enterprise-grade authorization module for Python services, APIs, data platforms,
workers and internal tools.

This module complements authentication.py and abac_engine.py by providing a
high-level authorization orchestration layer with:

- RBAC permission checks
- Scope-based authorization
- Resource/action authorization
- Optional ABAC adapter integration
- Deny-by-default behavior
- Explainable authorization decisions
- Policy decision caching
- Structured audit events
- Decorators for service/API methods
- Repository abstractions for database-backed permission stores
- Thread-safe in-memory repository for local development and tests

The module is intentionally framework-agnostic and does not depend on FastAPI,
Flask, Django or any specific database. It is designed so infrastructure teams
can plug in SQL/NoSQL repositories, centralized policy engines, OPA, Cedar,
custom ABAC engines, IAM systems or enterprise governance platforms.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import logging
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, TypeVar, Union, cast

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
F = TypeVar("F", bound=Callable[..., Any])


# =============================================================================
# Exceptions
# =============================================================================


class AuthorizationError(Exception):
    """Base authorization error."""


class AccessDeniedError(AuthorizationError):
    """Raised when authorization denies the request."""


class AuthorizationConfigurationError(AuthorizationError):
    """Raised when the authorization layer is misconfigured."""


class PermissionRepositoryError(AuthorizationError):
    """Raised when permission repository operations fail."""


class PrincipalResolutionError(AuthorizationError):
    """Raised when a principal cannot be resolved."""


class InvalidAuthorizationRequestError(AuthorizationError):
    """Raised when an authorization request is invalid."""


# =============================================================================
# Enums/config
# =============================================================================


class AuthorizationDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    NOT_APPLICABLE = "not_applicable"
    INDETERMINATE = "indeterminate"


class AuthorizationEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class AuthorizationMode(str, Enum):
    RBAC = "rbac"
    SCOPE = "scope"
    ABAC = "abac"
    HYBRID = "hybrid"


class CombiningStrategy(str, Enum):
    DENY_OVERRIDES = "deny_overrides"
    ALLOW_OVERRIDES = "allow_overrides"
    ALL_MUST_ALLOW = "all_must_allow"
    ANY_ALLOW = "any_allow"


class ResourceSensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    SECRET = "secret"


@dataclass(frozen=True)
class AuthorizationConfig:
    """Runtime configuration for the authorization service."""

    mode: AuthorizationMode = AuthorizationMode.HYBRID
    combining_strategy: CombiningStrategy = CombiningStrategy.DENY_OVERRIDES
    deny_by_default: bool = True
    fail_closed: bool = True
    enable_cache: bool = True
    cache_ttl_seconds: int = 60
    max_cache_entries: int = 10_000
    enable_audit: bool = True
    enable_explanations: bool = True
    allow_super_admin_bypass: bool = True
    super_admin_roles: Tuple[str, ...] = ("super_admin", "platform_admin")
    redact_sensitive_audit_fields: bool = True


# =============================================================================
# Domain models
# =============================================================================


@dataclass(frozen=True)
class Principal:
    """Authenticated principal used for authorization."""

    principal_id: str
    username: Optional[str] = None
    tenant_id: Optional[str] = None
    roles: Tuple[str, ...] = ()
    groups: Tuple[str, ...] = ()
    scopes: Tuple[str, ...] = ()
    permissions: Tuple[str, ...] = ()
    attributes: JsonDict = field(default_factory=dict)
    authenticated: bool = True

    def has_role(self, role: str) -> bool:
        return role in set(self.roles)

    def has_any_role(self, roles: Iterable[str]) -> bool:
        return bool(set(self.roles).intersection(set(roles)))

    def has_scope(self, scope: str) -> bool:
        return scope in set(self.scopes)

    def has_permission(self, permission: str) -> bool:
        return permission in set(self.permissions)


@dataclass(frozen=True)
class ResourceDescriptor:
    """Resource being accessed."""

    resource_type: str
    resource_id: Optional[str] = None
    tenant_id: Optional[str] = None
    owner_id: Optional[str] = None
    sensitivity: ResourceSensitivity = ResourceSensitivity.INTERNAL
    classification_level: int = 1
    attributes: JsonDict = field(default_factory=dict)

    def canonical_name(self) -> str:
        return f"{self.resource_type}:{self.resource_id or '*'}"


@dataclass(frozen=True)
class AuthorizationRequest:
    """Authorization request payload."""

    principal: Principal
    action: str
    resource: ResourceDescriptor
    required_permissions: Tuple[str, ...] = ()
    required_roles: Tuple[str, ...] = ()
    required_scopes: Tuple[str, ...] = ()
    environment: JsonDict = field(default_factory=dict)
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.principal.principal_id:
            raise InvalidAuthorizationRequestError("principal_id is required.")
        if not self.action:
            raise InvalidAuthorizationRequestError("action is required.")
        if not self.resource.resource_type:
            raise InvalidAuthorizationRequestError("resource_type is required.")


@dataclass(frozen=True)
class PermissionGrant:
    """Permission grant assigned to a role, group, user or tenant."""

    grant_id: str
    permission: str
    effect: AuthorizationEffect = AuthorizationEffect.ALLOW
    role: Optional[str] = None
    group: Optional[str] = None
    principal_id: Optional[str] = None
    tenant_id: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    actions: Tuple[str, ...] = ("*",)
    conditions: JsonDict = field(default_factory=dict)
    priority: int = 100
    active: bool = True
    description: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def applies_to(self, request: AuthorizationRequest) -> bool:
        if not self.active:
            return False

        principal = request.principal
        resource = request.resource

        if self.tenant_id and self.tenant_id not in {principal.tenant_id, resource.tenant_id}:
            return False
        if self.principal_id and self.principal_id != principal.principal_id:
            return False
        if self.role and self.role not in principal.roles:
            return False
        if self.group and self.group not in principal.groups:
            return False
        if self.resource_type and self.resource_type != resource.resource_type:
            return False
        if self.resource_id and self.resource_id != resource.resource_id:
            return False
        if "*" not in self.actions and request.action not in self.actions:
            return False
        return True


@dataclass(frozen=True)
class AuthorizationRule:
    """Static authorization rule for common enterprise use cases."""

    rule_id: str
    name: str
    effect: AuthorizationEffect
    actions: Tuple[str, ...] = ("*",)
    resource_types: Tuple[str, ...] = ("*",)
    roles: Tuple[str, ...] = ()
    scopes: Tuple[str, ...] = ()
    permissions: Tuple[str, ...] = ()
    tenant_match_required: bool = True
    owner_match_allowed: bool = False
    min_clearance_level: Optional[int] = None
    max_resource_sensitivity: Optional[ResourceSensitivity] = None
    priority: int = 100
    active: bool = True
    description: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def applies_to(self, request: AuthorizationRequest) -> bool:
        if not self.active:
            return False
        if "*" not in self.actions and request.action not in self.actions:
            return False
        if "*" not in self.resource_types and request.resource.resource_type not in self.resource_types:
            return False
        if self.tenant_match_required:
            principal_tenant = request.principal.tenant_id
            resource_tenant = request.resource.tenant_id
            if principal_tenant and resource_tenant and principal_tenant != resource_tenant:
                return False
        if self.owner_match_allowed and request.resource.owner_id == request.principal.principal_id:
            return True
        if self.roles and not request.principal.has_any_role(self.roles):
            return False
        if self.scopes and not set(self.scopes).issubset(set(request.principal.scopes)):
            return False
        if self.permissions and not set(self.permissions).issubset(set(request.principal.permissions)):
            return False
        if self.min_clearance_level is not None:
            principal_clearance = int(request.principal.attributes.get("clearance_level", 0))
            if principal_clearance < self.min_clearance_level:
                return False
        if self.max_resource_sensitivity is not None:
            if _sensitivity_rank(request.resource.sensitivity) > _sensitivity_rank(self.max_resource_sensitivity):
                return False
        return True


@dataclass(frozen=True)
class AuthorizationEvaluation:
    """Single evaluator/rule/grant result."""

    source: str
    source_id: str
    decision: AuthorizationDecision
    effect: Optional[AuthorizationEffect]
    applicable: bool
    reason: str
    priority: int = 100
    obligations: Tuple[str, ...] = ()
    advices: Tuple[str, ...] = ()
    diagnostics: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class AuthorizationResult:
    """Final authorization result."""

    decision: AuthorizationDecision
    allowed: bool
    reason: str
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    principal_id: Optional[str] = None
    action: Optional[str] = None
    resource: Optional[str] = None
    evaluations: Tuple[AuthorizationEvaluation, ...] = ()
    obligations: Tuple[str, ...] = ()
    advices: Tuple[str, ...] = ()
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cache_hit: bool = False
    diagnostics: JsonDict = field(default_factory=dict)

    def require_allowed(self) -> None:
        if not self.allowed:
            raise AccessDeniedError(self.reason)

    def to_dict(self) -> JsonDict:
        return {
            "decision": self.decision.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "principal_id": self.principal_id,
            "action": self.action,
            "resource": self.resource,
            "evaluations": [_evaluation_to_dict(item) for item in self.evaluations],
            "obligations": list(self.obligations),
            "advices": list(self.advices),
            "evaluated_at": self.evaluated_at.isoformat(),
            "cache_hit": self.cache_hit,
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class AuthorizationAuditEvent:
    """Structured authorization audit event."""

    event_type: str
    decision: AuthorizationDecision
    allowed: bool
    reason: str
    principal_id: str
    username: Optional[str]
    tenant_id: Optional[str]
    action: str
    resource_type: str
    resource_id: Optional[str]
    request_id: Optional[str]
    correlation_id: Optional[str]
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    matched_sources: Tuple[str, ...] = ()
    metadata: JsonDict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self, redact: bool = True) -> JsonDict:
        metadata = redact_sensitive(self.metadata) if redact else dict(self.metadata)
        return {
            "event_type": self.event_type,
            "decision": self.decision.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "principal_id": self.principal_id,
            "username": self.username,
            "tenant_id": self.tenant_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "matched_sources": list(self.matched_sources),
            "metadata": metadata,
            "timestamp": self.timestamp.isoformat(),
        }


# =============================================================================
# Repositories
# =============================================================================


class PermissionRepository(ABC):
    """Permission repository abstraction."""

    @abstractmethod
    def list_grants_for_principal(self, principal: Principal) -> Sequence[PermissionGrant]:
        """Return grants relevant to a principal."""

    @abstractmethod
    def upsert_grant(self, grant: PermissionGrant) -> None:
        """Create or update a permission grant."""

    @abstractmethod
    def delete_grant(self, grant_id: str) -> bool:
        """Delete a grant."""


class RuleRepository(ABC):
    """Authorization rule repository abstraction."""

    @abstractmethod
    def list_rules(self) -> Sequence[AuthorizationRule]:
        """Return static authorization rules."""

    @abstractmethod
    def upsert_rule(self, rule: AuthorizationRule) -> None:
        """Create or update a rule."""

    @abstractmethod
    def delete_rule(self, rule_id: str) -> bool:
        """Delete a rule."""


class InMemoryPermissionRepository(PermissionRepository):
    """Thread-safe in-memory permission repository."""

    def __init__(self, grants: Optional[Iterable[PermissionGrant]] = None) -> None:
        self._grants: Dict[str, PermissionGrant] = {}
        self._lock = threading.RLock()
        for grant in grants or []:
            self.upsert_grant(grant)

    def list_grants_for_principal(self, principal: Principal) -> Sequence[PermissionGrant]:
        with self._lock:
            result = []
            for grant in self._grants.values():
                if grant.principal_id == principal.principal_id or grant.role in principal.roles or grant.group in principal.groups or grant.tenant_id == principal.tenant_id:
                    result.append(grant)
            return tuple(sorted(result, key=lambda item: (item.priority, item.grant_id)))

    def upsert_grant(self, grant: PermissionGrant) -> None:
        with self._lock:
            self._grants[grant.grant_id] = dataclasses.replace(grant, updated_at=datetime.now(timezone.utc))

    def delete_grant(self, grant_id: str) -> bool:
        with self._lock:
            return self._grants.pop(grant_id, None) is not None


class InMemoryRuleRepository(RuleRepository):
    """Thread-safe in-memory authorization rule repository."""

    def __init__(self, rules: Optional[Iterable[AuthorizationRule]] = None) -> None:
        self._rules: Dict[str, AuthorizationRule] = {}
        self._lock = threading.RLock()
        for rule in rules or []:
            self.upsert_rule(rule)

    def list_rules(self) -> Sequence[AuthorizationRule]:
        with self._lock:
            return tuple(sorted(self._rules.values(), key=lambda item: (item.priority, item.rule_id)))

    def upsert_rule(self, rule: AuthorizationRule) -> None:
        with self._lock:
            self._rules[rule.rule_id] = rule

    def delete_rule(self, rule_id: str) -> bool:
        with self._lock:
            return self._rules.pop(rule_id, None) is not None


# =============================================================================
# Audit
# =============================================================================


class AuthorizationAuditSink(ABC):
    """Authorization audit sink abstraction."""

    @abstractmethod
    def emit(self, event: AuthorizationAuditEvent) -> None:
        """Emit an authorization audit event."""


class LoggingAuthorizationAuditSink(AuthorizationAuditSink):
    """Logging-based authorization audit sink."""

    def __init__(self, audit_logger: Optional[logging.Logger] = None, redact: bool = True) -> None:
        self.audit_logger = audit_logger or logging.getLogger("security.authorization.audit")
        self.redact = redact

    def emit(self, event: AuthorizationAuditEvent) -> None:
        self.audit_logger.info(
            "authorization_event=%s",
            json.dumps(event.to_dict(redact=self.redact), sort_keys=True, default=str),
        )


# =============================================================================
# Cache
# =============================================================================


@dataclass
class _AuthorizationCacheEntry:
    result: AuthorizationResult
    expires_at: float


class AuthorizationDecisionCache:
    """TTL decision cache."""

    def __init__(self, ttl_seconds: int = 60, max_entries: int = 10_000) -> None:
        self.ttl_seconds = max(0, ttl_seconds)
        self.max_entries = max(1, max_entries)
        self._cache: Dict[str, _AuthorizationCacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[AuthorizationResult]:
        now = time.time()
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            if entry.expires_at <= now:
                self._cache.pop(key, None)
                return None
            return dataclasses.replace(entry.result, cache_hit=True)

    def set(self, key: str, result: AuthorizationResult) -> None:
        if self.ttl_seconds <= 0:
            return
        with self._lock:
            if len(self._cache) >= self.max_entries:
                self._evict()
            self._cache[key] = _AuthorizationCacheEntry(result=result, expires_at=time.time() + self.ttl_seconds)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _evict(self) -> None:
        now = time.time()
        expired = [key for key, entry in self._cache.items() if entry.expires_at <= now]
        for key in expired:
            self._cache.pop(key, None)
        if len(self._cache) >= self.max_entries and self._cache:
            oldest_key = min(self._cache.items(), key=lambda item: item[1].expires_at)[0]
            self._cache.pop(oldest_key, None)


# =============================================================================
# ABAC adapter
# =============================================================================


class ABACAdapter(ABC):
    """Adapter abstraction for plugging in ABAC engines."""

    @abstractmethod
    def authorize(self, request: AuthorizationRequest) -> AuthorizationEvaluation:
        """Return an ABAC authorization evaluation."""


class NullABACAdapter(ABACAdapter):
    """Default ABAC adapter that returns not-applicable."""

    def authorize(self, request: AuthorizationRequest) -> AuthorizationEvaluation:
        return AuthorizationEvaluation(
            source="abac",
            source_id="null-abac-adapter",
            decision=AuthorizationDecision.NOT_APPLICABLE,
            effect=None,
            applicable=False,
            reason="No ABAC adapter configured.",
            priority=1000,
        )


class CallableABACAdapter(ABACAdapter):
    """Adapter around a callable for easy integration with an existing ABAC engine."""

    def __init__(self, fn: Callable[[AuthorizationRequest], Union[bool, AuthorizationEvaluation, Mapping[str, Any]]]) -> None:
        self.fn = fn

    def authorize(self, request: AuthorizationRequest) -> AuthorizationEvaluation:
        try:
            raw = self.fn(request)
            if isinstance(raw, AuthorizationEvaluation):
                return raw
            if isinstance(raw, bool):
                return AuthorizationEvaluation(
                    source="abac",
                    source_id="callable-abac-adapter",
                    decision=AuthorizationDecision.ALLOW if raw else AuthorizationDecision.DENY,
                    effect=AuthorizationEffect.ALLOW if raw else AuthorizationEffect.DENY,
                    applicable=True,
                    reason="Callable ABAC adapter returned boolean decision.",
                    priority=50,
                )
            if isinstance(raw, Mapping):
                allowed = bool(raw.get("allowed", False))
                return AuthorizationEvaluation(
                    source="abac",
                    source_id=str(raw.get("source_id", "callable-abac-adapter")),
                    decision=AuthorizationDecision.ALLOW if allowed else AuthorizationDecision.DENY,
                    effect=AuthorizationEffect.ALLOW if allowed else AuthorizationEffect.DENY,
                    applicable=bool(raw.get("applicable", True)),
                    reason=str(raw.get("reason", "Callable ABAC adapter returned mapping decision.")),
                    priority=int(raw.get("priority", 50)),
                    obligations=tuple(raw.get("obligations", ())),
                    advices=tuple(raw.get("advices", ())),
                    diagnostics=dict(raw.get("diagnostics", {})),
                )
            raise AuthorizationConfigurationError("ABAC callable returned unsupported result type.")
        except Exception as exc:
            logger.exception("ABAC adapter failed.")
            return AuthorizationEvaluation(
                source="abac",
                source_id="callable-abac-adapter",
                decision=AuthorizationDecision.INDETERMINATE,
                effect=AuthorizationEffect.DENY,
                applicable=True,
                reason="ABAC adapter failed; fail-closed evaluation returned.",
                priority=1,
                diagnostics={"error": str(exc), "error_type": type(exc).__name__},
            )


# =============================================================================
# Authorization service
# =============================================================================


class AuthorizationService:
    """High-level enterprise authorization service."""

    def __init__(
        self,
        permission_repository: Optional[PermissionRepository] = None,
        rule_repository: Optional[RuleRepository] = None,
        abac_adapter: Optional[ABACAdapter] = None,
        config: Optional[AuthorizationConfig] = None,
        audit_sink: Optional[AuthorizationAuditSink] = None,
        cache: Optional[AuthorizationDecisionCache] = None,
    ) -> None:
        self.config = config or AuthorizationConfig()
        self.permission_repository = permission_repository or InMemoryPermissionRepository()
        self.rule_repository = rule_repository or InMemoryRuleRepository()
        self.abac_adapter = abac_adapter or NullABACAdapter()
        self.audit_sink = audit_sink or LoggingAuthorizationAuditSink(redact=self.config.redact_sensitive_audit_fields)
        self.cache = cache or AuthorizationDecisionCache(self.config.cache_ttl_seconds, self.config.max_cache_entries)

    def authorize(self, request: AuthorizationRequest) -> AuthorizationResult:
        """Authorize a request and return an explainable decision."""
        request.validate()

        cache_key = self._cache_key(request)
        if self.config.enable_cache:
            cached = self.cache.get(cache_key)
            if cached:
                return cached

        try:
            result = self._evaluate(request)
        except Exception as exc:
            logger.exception("Authorization evaluation failed. request_id=%s", request.request_id)
            if self.config.fail_closed:
                result = self._deny(
                    request,
                    reason="Authorization evaluation failed; fail-closed deny applied.",
                    evaluations=(AuthorizationEvaluation(
                        source="authorization_service",
                        source_id="fail-closed",
                        decision=AuthorizationDecision.DENY,
                        effect=AuthorizationEffect.DENY,
                        applicable=True,
                        reason=str(exc),
                        priority=0,
                        diagnostics={"error_type": type(exc).__name__},
                    ),),
                )
            else:
                result = AuthorizationResult(
                    decision=AuthorizationDecision.INDETERMINATE,
                    allowed=False,
                    reason="Authorization evaluation failed and fail_closed is disabled.",
                    request_id=request.request_id,
                    correlation_id=request.correlation_id,
                    principal_id=request.principal.principal_id,
                    action=request.action,
                    resource=request.resource.canonical_name(),
                    diagnostics={"error": str(exc), "error_type": type(exc).__name__},
                )

        if not self.config.enable_explanations:
            result = dataclasses.replace(result, evaluations=(), diagnostics={})

        if self.config.enable_cache:
            self.cache.set(cache_key, result)

        if self.config.enable_audit:
            self._audit(request, result)

        return result

    def require(self, request: AuthorizationRequest) -> None:
        """Authorize and raise AccessDeniedError if denied."""
        self.authorize(request).require_allowed()

    def is_allowed(self, request: AuthorizationRequest) -> bool:
        """Convenience boolean authorization check."""
        return self.authorize(request).allowed

    def explain(self, request: AuthorizationRequest) -> JsonDict:
        """Return authorization result as a serializable dictionary."""
        return self.authorize(request).to_dict()

    def clear_cache(self) -> None:
        self.cache.clear()

    def _evaluate(self, request: AuthorizationRequest) -> AuthorizationResult:
        principal = request.principal

        if not principal.authenticated:
            return self._deny(request, "Principal is not authenticated.")

        if self.config.allow_super_admin_bypass and principal.has_any_role(self.config.super_admin_roles):
            evaluation = AuthorizationEvaluation(
                source="super_admin_bypass",
                source_id="super-admin",
                decision=AuthorizationDecision.ALLOW,
                effect=AuthorizationEffect.ALLOW,
                applicable=True,
                reason="Super admin bypass allowed by configuration.",
                priority=0,
            )
            return self._allow(request, "Super admin bypass applied.", (evaluation,))

        evaluations: List[AuthorizationEvaluation] = []

        if self.config.mode in {AuthorizationMode.RBAC, AuthorizationMode.HYBRID}:
            evaluations.extend(self._evaluate_rbac(request))

        if self.config.mode in {AuthorizationMode.SCOPE, AuthorizationMode.HYBRID}:
            evaluations.extend(self._evaluate_scopes(request))

        if self.config.mode in {AuthorizationMode.ABAC, AuthorizationMode.HYBRID}:
            evaluations.append(self.abac_adapter.authorize(request))

        evaluations.extend(self._evaluate_static_rules(request))

        return self._combine(request, tuple(evaluations))

    def _evaluate_rbac(self, request: AuthorizationRequest) -> List[AuthorizationEvaluation]:
        evaluations: List[AuthorizationEvaluation] = []

        if request.required_roles:
            has_required_roles = set(request.required_roles).issubset(set(request.principal.roles))
            evaluations.append(AuthorizationEvaluation(
                source="rbac.required_roles",
                source_id="required_roles",
                decision=AuthorizationDecision.ALLOW if has_required_roles else AuthorizationDecision.DENY,
                effect=AuthorizationEffect.ALLOW if has_required_roles else AuthorizationEffect.DENY,
                applicable=True,
                reason="Required roles satisfied." if has_required_roles else "Required roles missing.",
                priority=20,
                diagnostics={"required_roles": list(request.required_roles), "principal_roles": list(request.principal.roles)},
            ))

        if request.required_permissions:
            principal_permissions = set(request.principal.permissions)
            repository_grants = self.permission_repository.list_grants_for_principal(request.principal)
            grant_evaluations: List[AuthorizationEvaluation] = []

            derived_permissions: Set[str] = set(principal_permissions)
            for grant in repository_grants:
                if not grant.applies_to(request):
                    continue
                if grant.effect == AuthorizationEffect.DENY:
                    grant_evaluations.append(AuthorizationEvaluation(
                        source="permission_grant",
                        source_id=grant.grant_id,
                        decision=AuthorizationDecision.DENY,
                        effect=AuthorizationEffect.DENY,
                        applicable=True,
                        reason=f"Explicit deny grant matched permission '{grant.permission}'.",
                        priority=grant.priority,
                    ))
                else:
                    derived_permissions.add(grant.permission)
                    grant_evaluations.append(AuthorizationEvaluation(
                        source="permission_grant",
                        source_id=grant.grant_id,
                        decision=AuthorizationDecision.ALLOW,
                        effect=AuthorizationEffect.ALLOW,
                        applicable=True,
                        reason=f"Allow grant matched permission '{grant.permission}'.",
                        priority=grant.priority,
                    ))

            evaluations.extend(grant_evaluations)
            has_permissions = set(request.required_permissions).issubset(derived_permissions)
            evaluations.append(AuthorizationEvaluation(
                source="rbac.required_permissions",
                source_id="required_permissions",
                decision=AuthorizationDecision.ALLOW if has_permissions else AuthorizationDecision.DENY,
                effect=AuthorizationEffect.ALLOW if has_permissions else AuthorizationEffect.DENY,
                applicable=True,
                reason="Required permissions satisfied." if has_permissions else "Required permissions missing.",
                priority=25,
                diagnostics={
                    "required_permissions": list(request.required_permissions),
                    "derived_permissions": sorted(derived_permissions),
                },
            ))

        return evaluations

    def _evaluate_scopes(self, request: AuthorizationRequest) -> List[AuthorizationEvaluation]:
        if not request.required_scopes:
            return []
        has_scopes = set(request.required_scopes).issubset(set(request.principal.scopes))
        return [AuthorizationEvaluation(
            source="scope.required_scopes",
            source_id="required_scopes",
            decision=AuthorizationDecision.ALLOW if has_scopes else AuthorizationDecision.DENY,
            effect=AuthorizationEffect.ALLOW if has_scopes else AuthorizationEffect.DENY,
            applicable=True,
            reason="Required scopes satisfied." if has_scopes else "Required scopes missing.",
            priority=30,
            diagnostics={"required_scopes": list(request.required_scopes), "principal_scopes": list(request.principal.scopes)},
        )]

    def _evaluate_static_rules(self, request: AuthorizationRequest) -> List[AuthorizationEvaluation]:
        evaluations: List[AuthorizationEvaluation] = []
        for rule in self.rule_repository.list_rules():
            try:
                applicable = rule.applies_to(request)
                if not applicable:
                    evaluations.append(AuthorizationEvaluation(
                        source="authorization_rule",
                        source_id=rule.rule_id,
                        decision=AuthorizationDecision.NOT_APPLICABLE,
                        effect=None,
                        applicable=False,
                        reason="Rule not applicable.",
                        priority=rule.priority,
                    ))
                    continue
                evaluations.append(AuthorizationEvaluation(
                    source="authorization_rule",
                    source_id=rule.rule_id,
                    decision=AuthorizationDecision.ALLOW if rule.effect == AuthorizationEffect.ALLOW else AuthorizationDecision.DENY,
                    effect=rule.effect,
                    applicable=True,
                    reason=f"Rule '{rule.name}' applied.",
                    priority=rule.priority,
                    diagnostics=dict(rule.metadata),
                ))
            except Exception as exc:
                evaluations.append(AuthorizationEvaluation(
                    source="authorization_rule",
                    source_id=rule.rule_id,
                    decision=AuthorizationDecision.INDETERMINATE,
                    effect=AuthorizationEffect.DENY if self.config.fail_closed else None,
                    applicable=self.config.fail_closed,
                    reason="Rule evaluation failed.",
                    priority=1,
                    diagnostics={"error": str(exc), "error_type": type(exc).__name__},
                ))
        return evaluations

    def _combine(self, request: AuthorizationRequest, evaluations: Tuple[AuthorizationEvaluation, ...]) -> AuthorizationResult:
        applicable = tuple(item for item in evaluations if item.applicable and item.effect is not None)

        if not applicable:
            if self.config.deny_by_default:
                return self._deny(
                    request,
                    "No applicable authorization rule, grant, scope, role or ABAC decision found; deny-by-default applied.",
                    evaluations,
                    diagnostics={"applicable_evaluation_count": 0},
                )
            return AuthorizationResult(
                decision=AuthorizationDecision.NOT_APPLICABLE,
                allowed=False,
                reason="No applicable authorization evaluation found.",
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                principal_id=request.principal.principal_id,
                action=request.action,
                resource=request.resource.canonical_name(),
                evaluations=evaluations,
                diagnostics={"applicable_evaluation_count": 0},
            )

        sorted_applicable = tuple(sorted(applicable, key=lambda item: (item.priority, item.source, item.source_id)))
        deny_items = tuple(item for item in sorted_applicable if item.effect == AuthorizationEffect.DENY)
        allow_items = tuple(item for item in sorted_applicable if item.effect == AuthorizationEffect.ALLOW)

        strategy = self.config.combining_strategy

        if strategy == CombiningStrategy.DENY_OVERRIDES:
            if deny_items:
                return self._deny(request, f"Deny-overrides strategy selected deny from {deny_items[0].source_id}.", evaluations)
            if allow_items:
                return self._allow(request, f"Deny-overrides strategy selected allow from {allow_items[0].source_id}.", evaluations)
        elif strategy == CombiningStrategy.ALLOW_OVERRIDES:
            if allow_items:
                return self._allow(request, f"Allow-overrides strategy selected allow from {allow_items[0].source_id}.", evaluations)
            if deny_items:
                return self._deny(request, f"Allow-overrides strategy selected deny from {deny_items[0].source_id}.", evaluations)
        elif strategy == CombiningStrategy.ALL_MUST_ALLOW:
            if deny_items:
                return self._deny(request, "All-must-allow strategy found a deny evaluation.", evaluations)
            if len(allow_items) == len(applicable):
                return self._allow(request, "All applicable evaluations allowed.", evaluations)
        elif strategy == CombiningStrategy.ANY_ALLOW:
            if allow_items:
                return self._allow(request, "Any-allow strategy found an allow evaluation.", evaluations)
            if deny_items:
                return self._deny(request, "Any-allow strategy found no allow and at least one deny.", evaluations)

        return self._deny(request, "Combining strategy produced no allow decision; deny applied.", evaluations)

    def _allow(
        self,
        request: AuthorizationRequest,
        reason: str,
        evaluations: Tuple[AuthorizationEvaluation, ...] = (),
        diagnostics: Optional[JsonDict] = None,
    ) -> AuthorizationResult:
        return AuthorizationResult(
            decision=AuthorizationDecision.ALLOW,
            allowed=True,
            reason=reason,
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            principal_id=request.principal.principal_id,
            action=request.action,
            resource=request.resource.canonical_name(),
            evaluations=evaluations,
            obligations=_collect_obligations(evaluations),
            advices=_collect_advices(evaluations),
            diagnostics=diagnostics or {"evaluation_count": len(evaluations)},
        )

    def _deny(
        self,
        request: AuthorizationRequest,
        reason: str,
        evaluations: Tuple[AuthorizationEvaluation, ...] = (),
        diagnostics: Optional[JsonDict] = None,
    ) -> AuthorizationResult:
        return AuthorizationResult(
            decision=AuthorizationDecision.DENY,
            allowed=False,
            reason=reason,
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            principal_id=request.principal.principal_id,
            action=request.action,
            resource=request.resource.canonical_name(),
            evaluations=evaluations,
            obligations=_collect_obligations(evaluations),
            advices=_collect_advices(evaluations),
            diagnostics=diagnostics or {"evaluation_count": len(evaluations)},
        )

    def _cache_key(self, request: AuthorizationRequest) -> str:
        payload = {
            "principal": _canonicalize(dataclasses.asdict(request.principal)),
            "action": request.action,
            "resource": _canonicalize(dataclasses.asdict(request.resource)),
            "required_permissions": list(request.required_permissions),
            "required_roles": list(request.required_roles),
            "required_scopes": list(request.required_scopes),
            "environment": _canonicalize(request.environment),
        }
        raw = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _audit(self, request: AuthorizationRequest, result: AuthorizationResult) -> None:
        try:
            event = AuthorizationAuditEvent(
                event_type="authorization.evaluated",
                decision=result.decision,
                allowed=result.allowed,
                reason=result.reason,
                principal_id=request.principal.principal_id,
                username=request.principal.username,
                tenant_id=request.principal.tenant_id or request.resource.tenant_id,
                action=request.action,
                resource_type=request.resource.resource_type,
                resource_id=request.resource.resource_id,
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                ip_address=str(request.environment.get("ip_address")) if request.environment.get("ip_address") else None,
                user_agent=str(request.environment.get("user_agent")) if request.environment.get("user_agent") else None,
                matched_sources=tuple(
                    f"{item.source}:{item.source_id}"
                    for item in result.evaluations
                    if item.applicable and item.effect is not None
                ),
                metadata={
                    "resource_sensitivity": request.resource.sensitivity.value,
                    "required_permissions": list(request.required_permissions),
                    "required_roles": list(request.required_roles),
                    "required_scopes": list(request.required_scopes),
                    "request_metadata": dict(request.metadata),
                    "diagnostics": dict(result.diagnostics),
                },
            )
            self.audit_sink.emit(event)
        except Exception:
            logger.exception("Failed to emit authorization audit event. request_id=%s", request.request_id)


# =============================================================================
# Decorators / helpers
# =============================================================================


class AuthorizationContextProvider(ABC):
    """Extract authorization request pieces from decorated function calls."""

    @abstractmethod
    def get_principal(self, args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> Principal:
        """Return the current principal."""

    @abstractmethod
    def get_resource(self, args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> ResourceDescriptor:
        """Return the target resource."""

    def get_environment(self, args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> JsonDict:
        return {}


class KeywordAuthorizationContextProvider(AuthorizationContextProvider):
    """
    Context provider that expects principal/resource keyword arguments.

    Example decorated function signature:
        def read_dataset(*, principal: Principal, resource: ResourceDescriptor): ...
    """

    def __init__(self, principal_key: str = "principal", resource_key: str = "resource", environment_key: str = "environment") -> None:
        self.principal_key = principal_key
        self.resource_key = resource_key
        self.environment_key = environment_key

    def get_principal(self, args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> Principal:
        principal = kwargs.get(self.principal_key)
        if not isinstance(principal, Principal):
            raise PrincipalResolutionError(f"Keyword '{self.principal_key}' must contain a Principal.")
        return principal

    def get_resource(self, args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> ResourceDescriptor:
        resource = kwargs.get(self.resource_key)
        if not isinstance(resource, ResourceDescriptor):
            raise InvalidAuthorizationRequestError(f"Keyword '{self.resource_key}' must contain a ResourceDescriptor.")
        return resource

    def get_environment(self, args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> JsonDict:
        environment = kwargs.get(self.environment_key) or {}
        if not isinstance(environment, Mapping):
            raise InvalidAuthorizationRequestError(f"Keyword '{self.environment_key}' must contain a mapping.")
        return dict(environment)


def requires_authorization(
    service: AuthorizationService,
    action: str,
    required_permissions: Sequence[str] = (),
    required_roles: Sequence[str] = (),
    required_scopes: Sequence[str] = (),
    context_provider: Optional[AuthorizationContextProvider] = None,
) -> Callable[[F], F]:
    """Decorator that enforces authorization before function execution."""

    provider = context_provider or KeywordAuthorizationContextProvider()

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            principal = provider.get_principal(args, kwargs)
            resource = provider.get_resource(args, kwargs)
            environment = provider.get_environment(args, kwargs)
            request = AuthorizationRequest(
                principal=principal,
                action=action,
                resource=resource,
                required_permissions=tuple(required_permissions),
                required_roles=tuple(required_roles),
                required_scopes=tuple(required_scopes),
                environment=environment,
                request_id=str(uuid.uuid4()),
                correlation_id=str(environment.get("correlation_id")) if environment.get("correlation_id") else None,
            )
            service.require(request)
            return func(*args, **kwargs)

        return cast(F, wrapper)

    return decorator


# =============================================================================
# Factories
# =============================================================================


def create_default_authorization_service() -> AuthorizationService:
    """Create a practical default authorization service for development/tests."""
    rules = [
        AuthorizationRule(
            rule_id="allow-admin-all",
            name="Allow admins to perform all actions",
            effect=AuthorizationEffect.ALLOW,
            actions=("*",),
            resource_types=("*",),
            roles=("admin",),
            tenant_match_required=True,
            priority=50,
        ),
        AuthorizationRule(
            rule_id="allow-owner-read-write",
            name="Allow owners to read and write their own resources",
            effect=AuthorizationEffect.ALLOW,
            actions=("read", "write", "update"),
            resource_types=("*",),
            owner_match_allowed=True,
            tenant_match_required=True,
            priority=70,
        ),
        AuthorizationRule(
            rule_id="deny-delete-without-admin",
            name="Deny delete unless admin rule already allowed",
            effect=AuthorizationEffect.DENY,
            actions=("delete",),
            resource_types=("*",),
            tenant_match_required=True,
            priority=80,
        ),
        AuthorizationRule(
            rule_id="allow-analyst-read-internal",
            name="Allow analysts to read internal resources",
            effect=AuthorizationEffect.ALLOW,
            actions=("read", "query"),
            resource_types=("dataset", "report", "document"),
            roles=("analyst",),
            tenant_match_required=True,
            max_resource_sensitivity=ResourceSensitivity.INTERNAL,
            priority=90,
        ),
    ]

    grants = [
        PermissionGrant(
            grant_id="grant-admin-manage-users",
            permission="users:manage",
            role="admin",
            actions=("create", "read", "update", "delete"),
            resource_type="user",
            priority=40,
            description="Admins can manage users.",
        ),
        PermissionGrant(
            grant_id="grant-analyst-read-datasets",
            permission="datasets:read",
            role="analyst",
            actions=("read", "query"),
            resource_type="dataset",
            priority=60,
            description="Analysts can read/query datasets.",
        ),
    ]

    return AuthorizationService(
        permission_repository=InMemoryPermissionRepository(grants),
        rule_repository=InMemoryRuleRepository(rules),
        config=AuthorizationConfig(mode=AuthorizationMode.HYBRID, combining_strategy=CombiningStrategy.DENY_OVERRIDES),
    )


# =============================================================================
# Utility functions
# =============================================================================


def principal_from_token_claims(claims: Mapping[str, Any]) -> Principal:
    """Build a Principal from JWT/OIDC-like claims."""
    return Principal(
        principal_id=str(claims.get("sub") or claims.get("user_id") or ""),
        username=claims.get("username") or claims.get("preferred_username") or claims.get("email"),
        tenant_id=claims.get("tenant_id") or claims.get("tid"),
        roles=tuple(claims.get("roles") or ()),
        groups=tuple(claims.get("groups") or ()),
        scopes=tuple(_split_scopes(claims.get("scope") or claims.get("scopes") or ())),
        permissions=tuple(claims.get("permissions") or ()),
        attributes=dict(claims.get("attributes") or {}),
        authenticated=True,
    )


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


def _split_scopes(raw: Any) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return tuple(item.strip() for item in raw.split() if item.strip())
    if isinstance(raw, Iterable):
        return tuple(str(item) for item in raw)
    return (str(raw),)


def _sensitivity_rank(value: ResourceSensitivity) -> int:
    order = {
        ResourceSensitivity.PUBLIC: 0,
        ResourceSensitivity.INTERNAL: 1,
        ResourceSensitivity.CONFIDENTIAL: 2,
        ResourceSensitivity.RESTRICTED: 3,
        ResourceSensitivity.SECRET: 4,
    }
    return order[value]


def _collect_obligations(evaluations: Sequence[AuthorizationEvaluation]) -> Tuple[str, ...]:
    seen: Dict[str, None] = {}
    for evaluation in evaluations:
        for item in evaluation.obligations:
            seen[item] = None
    return tuple(seen.keys())


def _collect_advices(evaluations: Sequence[AuthorizationEvaluation]) -> Tuple[str, ...]:
    seen: Dict[str, None] = {}
    for evaluation in evaluations:
        for item in evaluation.advices:
            seen[item] = None
    return tuple(seen.keys())


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


def _evaluation_to_dict(item: AuthorizationEvaluation) -> JsonDict:
    return {
        "source": item.source,
        "source_id": item.source_id,
        "decision": item.decision.value,
        "effect": item.effect.value if item.effect else None,
        "applicable": item.applicable,
        "reason": item.reason,
        "priority": item.priority,
        "obligations": list(item.obligations),
        "advices": list(item.advices),
        "diagnostics": dict(item.diagnostics),
    }


__all__ = [
    "ABACAdapter",
    "AccessDeniedError",
    "AuthorizationAuditEvent",
    "AuthorizationAuditSink",
    "AuthorizationConfigurationError",
    "AuthorizationConfig",
    "AuthorizationContextProvider",
    "AuthorizationDecision",
    "AuthorizationDecisionCache",
    "AuthorizationEffect",
    "AuthorizationError",
    "AuthorizationEvaluation",
    "AuthorizationMode",
    "AuthorizationRequest",
    "AuthorizationResult",
    "AuthorizationRule",
    "AuthorizationService",
    "CallableABACAdapter",
    "CombiningStrategy",
    "InMemoryPermissionRepository",
    "InMemoryRuleRepository",
    "InvalidAuthorizationRequestError",
    "KeywordAuthorizationContextProvider",
    "LoggingAuthorizationAuditSink",
    "NullABACAdapter",
    "PermissionGrant",
    "PermissionRepository",
    "PermissionRepositoryError",
    "Principal",
    "PrincipalResolutionError",
    "ResourceDescriptor",
    "ResourceSensitivity",
    "RuleRepository",
    "create_default_authorization_service",
    "principal_from_token_claims",
    "redact_sensitive",
    "requires_authorization",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    service = create_default_authorization_service()

    principal = Principal(
        principal_id="user-001",
        username="analyst@example.com",
        tenant_id="tenant-a",
        roles=("analyst",),
        scopes=("datasets:read",),
        permissions=(),
        attributes={"clearance_level": 2},
    )

    resource = ResourceDescriptor(
        resource_type="dataset",
        resource_id="dataset-001",
        tenant_id="tenant-a",
        owner_id="user-999",
        sensitivity=ResourceSensitivity.INTERNAL,
        classification_level=1,
    )

    request = AuthorizationRequest(
        principal=principal,
        action="read",
        resource=resource,
        required_permissions=("datasets:read",),
        required_scopes=("datasets:read",),
        environment={"ip_address": "127.0.0.1", "user_agent": "local-dev"},
        request_id="req-demo",
        correlation_id="corr-demo",
    )

    result = service.authorize(request)
    print(json.dumps(result.to_dict(), indent=2, default=str))

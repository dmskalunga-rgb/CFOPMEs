"""
data/security/abac_engine.py

Enterprise-grade Attribute-Based Access Control (ABAC) engine.

This module provides a production-ready ABAC authorization engine designed for
large-scale data platforms, APIs, microservices, and governance-heavy enterprise
systems.

Core capabilities:
- Attribute-Based Access Control with subject/resource/action/environment context
- Deny-by-default security posture
- Policy priority, effect, versioning, tags, metadata and lifecycle status
- Composable conditions with operators, dotted-path resolution and type-safe evaluation
- Policy obligations and advices
- Explainable authorization decisions
- Structured audit events
- Optional decision caching with TTL
- Policy repository abstraction
- In-memory repository implementation
- JSON policy loading/exporting
- Safe error handling with fail-closed behavior

This file intentionally avoids hard dependencies on any specific web framework,
database, or observability stack. It can be integrated into FastAPI, Flask,
Django, Celery, Kafka consumers, data pipelines, or internal services.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import operator as py_operator
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
AttributeMap = Dict[str, Any]
OperatorFn = Callable[[Any, Any], bool]


class ABACError(Exception):
    """Base ABAC exception."""


class PolicyValidationError(ABACError):
    """Raised when a policy definition is invalid."""


class PolicyRepositoryError(ABACError):
    """Raised when the policy repository cannot complete an operation."""


class AttributeResolutionError(ABACError):
    """Raised when an attribute path cannot be resolved in strict mode."""


class ConditionEvaluationError(ABACError):
    """Raised when a condition cannot be safely evaluated."""


class Effect(str, Enum):
    """Policy effect."""

    ALLOW = "allow"
    DENY = "deny"


class PolicyStatus(str, Enum):
    """Policy lifecycle status."""

    ACTIVE = "active"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"
    DRAFT = "draft"


class Decision(str, Enum):
    """Final authorization decision."""

    ALLOW = "allow"
    DENY = "deny"
    NOT_APPLICABLE = "not_applicable"
    INDETERMINATE = "indeterminate"


class CombiningAlgorithm(str, Enum):
    """Supported policy combining algorithms."""

    DENY_OVERRIDES = "deny_overrides"
    ALLOW_OVERRIDES = "allow_overrides"
    FIRST_APPLICABLE = "first_applicable"
    PRIORITY = "priority"


class MatchStrategy(str, Enum):
    """String matching strategy for target matching."""

    EXACT = "exact"
    WILDCARD = "wildcard"
    REGEX = "regex"
    ANY = "any"


@dataclass(frozen=True)
class EvaluationConfig:
    """Runtime configuration for the ABAC engine."""

    combining_algorithm: CombiningAlgorithm = CombiningAlgorithm.DENY_OVERRIDES
    deny_by_default: bool = True
    fail_closed: bool = True
    strict_attribute_resolution: bool = False
    enable_explanations: bool = True
    enable_audit: bool = True
    enable_cache: bool = True
    cache_ttl_seconds: int = 60
    max_cache_entries: int = 10_000
    redact_sensitive_attributes: bool = True
    sensitive_attribute_names: Tuple[str, ...] = (
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "private_key",
    )


@dataclass(frozen=True)
class ABACContext:
    """
    Authorization request context.

    Attributes are separated into four ABAC categories:
    - subject: user, service account, role, tenant, department, clearance, groups
    - resource: dataset, table, document, record, owner, classification, tenant
    - action: operation being requested, such as read, write, delete, approve
    - environment: request time, IP, region, device posture, risk score, session
    """

    subject: AttributeMap
    resource: AttributeMap
    action: AttributeMap
    environment: AttributeMap = field(default_factory=dict)
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    tenant_id: Optional[str] = None
    trace_id: Optional[str] = None

    def as_mapping(self) -> JsonDict:
        return {
            "subject": self.subject,
            "resource": self.resource,
            "action": self.action,
            "environment": self.environment,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "tenant_id": self.tenant_id,
            "trace_id": self.trace_id,
        }


@dataclass(frozen=True)
class PolicyTarget:
    """Fast target pre-filter for policies before full condition evaluation."""

    subjects: Tuple[str, ...] = ("*",)
    resources: Tuple[str, ...] = ("*",)
    actions: Tuple[str, ...] = ("*",)
    tenants: Tuple[str, ...] = ("*",)
    match_strategy: MatchStrategy = MatchStrategy.WILDCARD

    def matches(self, context: ABACContext) -> bool:
        subject_id = _stringify(context.subject.get("id") or context.subject.get("user_id") or "")
        resource_id = _stringify(context.resource.get("id") or context.resource.get("resource_id") or "")
        action_name = _stringify(context.action.get("name") or context.action.get("operation") or "")
        tenant_id = _stringify(context.tenant_id or context.subject.get("tenant_id") or context.resource.get("tenant_id") or "")

        return (
            _matches_any(subject_id, self.subjects, self.match_strategy)
            and _matches_any(resource_id, self.resources, self.match_strategy)
            and _matches_any(action_name, self.actions, self.match_strategy)
            and _matches_any(tenant_id, self.tenants, self.match_strategy)
        )


@dataclass(frozen=True)
class Condition:
    """
    A single ABAC condition.

    Example:
        Condition(
            left="subject.department",
            operator="eq",
            right="resource.department",
            right_is_attribute=True,
        )
    """

    left: str
    operator: str
    right: Any = None
    right_is_attribute: bool = False
    negate: bool = False
    description: Optional[str] = None

    def evaluate(self, context: ABACContext, registry: OperatorRegistry, strict: bool = False) -> bool:
        left_value = resolve_attribute(context.as_mapping(), self.left, strict=strict)
        right_value = (
            resolve_attribute(context.as_mapping(), str(self.right), strict=strict)
            if self.right_is_attribute
            else self.right
        )

        result = registry.evaluate(self.operator, left_value, right_value)
        return not result if self.negate else result


@dataclass(frozen=True)
class ConditionGroup:
    """Composable condition group using all/any/not logic."""

    all: Tuple[Union[Condition, "ConditionGroup"], ...] = ()
    any: Tuple[Union[Condition, "ConditionGroup"], ...] = ()
    not_: Tuple[Union[Condition, "ConditionGroup"], ...] = ()

    def evaluate(self, context: ABACContext, registry: OperatorRegistry, strict: bool = False) -> bool:
        if self.all and not all(_evaluate_node(node, context, registry, strict) for node in self.all):
            return False

        if self.any and not any(_evaluate_node(node, context, registry, strict) for node in self.any):
            return False

        if self.not_ and any(_evaluate_node(node, context, registry, strict) for node in self.not_):
            return False

        return True


@dataclass(frozen=True)
class Policy:
    """Enterprise ABAC policy definition."""

    policy_id: str
    name: str
    effect: Effect
    target: PolicyTarget = field(default_factory=PolicyTarget)
    conditions: Optional[ConditionGroup] = None
    priority: int = 100
    status: PolicyStatus = PolicyStatus.ACTIVE
    version: str = "1.0.0"
    description: Optional[str] = None
    obligations: Tuple[str, ...] = ()
    advices: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()
    metadata: JsonDict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def validate(self) -> None:
        if not self.policy_id or not self.policy_id.strip():
            raise PolicyValidationError("Policy policy_id is required.")
        if not self.name or not self.name.strip():
            raise PolicyValidationError(f"Policy {self.policy_id} name is required.")
        if not isinstance(self.effect, Effect):
            raise PolicyValidationError(f"Policy {self.policy_id} has invalid effect.")
        if not isinstance(self.priority, int):
            raise PolicyValidationError(f"Policy {self.policy_id} priority must be an integer.")

    def is_applicable(self, context: ABACContext, registry: OperatorRegistry, strict: bool = False) -> bool:
        if self.status != PolicyStatus.ACTIVE:
            return False
        if not self.target.matches(context):
            return False
        if self.conditions is None:
            return True
        return self.conditions.evaluate(context, registry, strict=strict)


@dataclass(frozen=True)
class PolicyEvaluationResult:
    """Result of evaluating a single policy."""

    policy_id: str
    policy_name: str
    effect: Optional[Effect]
    applicable: bool
    priority: int
    reason: str
    obligations: Tuple[str, ...] = ()
    advices: Tuple[str, ...] = ()
    error: Optional[str] = None


@dataclass(frozen=True)
class AuthorizationResult:
    """Final ABAC decision result."""

    decision: Decision
    allowed: bool
    reason: str
    request_id: Optional[str]
    matched_policies: Tuple[PolicyEvaluationResult, ...] = ()
    obligations: Tuple[str, ...] = ()
    advices: Tuple[str, ...] = ()
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cache_hit: bool = False
    diagnostics: JsonDict = field(default_factory=dict)

    def require_allowed(self) -> None:
        if not self.allowed:
            raise PermissionError(self.reason)

    def to_dict(self) -> JsonDict:
        data = asdict(self)
        data["decision"] = self.decision.value
        data["evaluated_at"] = self.evaluated_at.isoformat()
        for item in data.get("matched_policies", []):
            if item.get("effect") is not None and isinstance(item["effect"], Effect):
                item["effect"] = item["effect"].value
        return data


@dataclass(frozen=True)
class AuditEvent:
    """Structured audit event emitted after authorization evaluation."""

    event_type: str
    decision: Decision
    allowed: bool
    reason: str
    request_id: Optional[str]
    correlation_id: Optional[str]
    tenant_id: Optional[str]
    subject: JsonDict
    resource: JsonDict
    action: JsonDict
    environment: JsonDict
    matched_policy_ids: Tuple[str, ...]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> JsonDict:
        return {
            "event_type": self.event_type,
            "decision": self.decision.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "tenant_id": self.tenant_id,
            "subject": self.subject,
            "resource": self.resource,
            "action": self.action,
            "environment": self.environment,
            "matched_policy_ids": list(self.matched_policy_ids),
            "timestamp": self.timestamp.isoformat(),
        }


class OperatorRegistry:
    """Registry for ABAC condition operators."""

    def __init__(self) -> None:
        self._operators: Dict[str, OperatorFn] = {}
        self._lock = threading.RLock()
        self._register_defaults()

    def register(self, name: str, fn: OperatorFn, overwrite: bool = False) -> None:
        normalized = _normalize_operator_name(name)
        if not callable(fn):
            raise ValueError("Operator function must be callable.")
        with self._lock:
            if normalized in self._operators and not overwrite:
                raise ValueError(f"Operator already registered: {normalized}")
            self._operators[normalized] = fn

    def evaluate(self, name: str, left: Any, right: Any) -> bool:
        normalized = _normalize_operator_name(name)
        with self._lock:
            fn = self._operators.get(normalized)
        if fn is None:
            raise ConditionEvaluationError(f"Unsupported operator: {name}")
        try:
            return bool(fn(left, right))
        except Exception as exc:
            raise ConditionEvaluationError(
                f"Failed to evaluate operator '{name}' with left={type(left).__name__}, right={type(right).__name__}."
            ) from exc

    def available(self) -> Tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._operators.keys()))

    def _register_defaults(self) -> None:
        self.register("eq", lambda left, right: left == right)
        self.register("ne", lambda left, right: left != right)
        self.register("gt", lambda left, right: _coerce_comparable(left) > _coerce_comparable(right))
        self.register("gte", lambda left, right: _coerce_comparable(left) >= _coerce_comparable(right))
        self.register("lt", lambda left, right: _coerce_comparable(left) < _coerce_comparable(right))
        self.register("lte", lambda left, right: _coerce_comparable(left) <= _coerce_comparable(right))
        self.register("in", lambda left, right: left in _ensure_iterable(right))
        self.register("not_in", lambda left, right: left not in _ensure_iterable(right))
        self.register("contains", lambda left, right: right in _ensure_iterable(left))
        self.register("contains_any", lambda left, right: bool(set(_ensure_iterable(left)).intersection(set(_ensure_iterable(right)))))
        self.register("contains_all", lambda left, right: set(_ensure_iterable(right)).issubset(set(_ensure_iterable(left))))
        self.register("starts_with", lambda left, right: str(left).startswith(str(right)))
        self.register("ends_with", lambda left, right: str(left).endswith(str(right)))
        self.register("matches", lambda left, right: re.fullmatch(str(right), str(left)) is not None)
        self.register("wildcard", lambda left, right: fnmatch.fnmatchcase(str(left), str(right)))
        self.register("exists", lambda left, right: left is not None)
        self.register("not_exists", lambda left, right: left is None)
        self.register("truthy", lambda left, right: bool(left) is True)
        self.register("falsy", lambda left, right: bool(left) is False)
        self.register("between", _operator_between)
        self.register("date_before", lambda left, right: _parse_datetime(left) < _parse_datetime(right))
        self.register("date_after", lambda left, right: _parse_datetime(left) > _parse_datetime(right))
        self.register("date_between", _operator_date_between)


class PolicyRepository(ABC):
    """Policy repository abstraction."""

    @abstractmethod
    def list_policies(self) -> Sequence[Policy]:
        """Return all known policies."""

    @abstractmethod
    def get_policy(self, policy_id: str) -> Optional[Policy]:
        """Return a policy by ID."""

    @abstractmethod
    def upsert_policy(self, policy: Policy) -> None:
        """Create or update a policy."""

    @abstractmethod
    def delete_policy(self, policy_id: str) -> bool:
        """Delete a policy by ID."""


class InMemoryPolicyRepository(PolicyRepository):
    """Thread-safe in-memory policy repository."""

    def __init__(self, policies: Optional[Iterable[Policy]] = None) -> None:
        self._policies: Dict[str, Policy] = {}
        self._lock = threading.RLock()
        for policy in policies or []:
            self.upsert_policy(policy)

    def list_policies(self) -> Sequence[Policy]:
        with self._lock:
            return tuple(sorted(self._policies.values(), key=lambda p: (p.priority, p.policy_id)))

    def get_policy(self, policy_id: str) -> Optional[Policy]:
        with self._lock:
            return self._policies.get(policy_id)

    def upsert_policy(self, policy: Policy) -> None:
        policy.validate()
        with self._lock:
            self._policies[policy.policy_id] = policy

    def delete_policy(self, policy_id: str) -> bool:
        with self._lock:
            return self._policies.pop(policy_id, None) is not None


@dataclass
class _CacheEntry:
    result: AuthorizationResult
    expires_at: float


class DecisionCache:
    """Small TTL cache for authorization decisions."""

    def __init__(self, ttl_seconds: int = 60, max_entries: int = 10_000) -> None:
        self.ttl_seconds = max(0, ttl_seconds)
        self.max_entries = max(1, max_entries)
        self._cache: MutableMapping[str, _CacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[AuthorizationResult]:
        now = time.time()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._cache.pop(key, None)
                return None
            return AuthorizationResult(
                decision=entry.result.decision,
                allowed=entry.result.allowed,
                reason=entry.result.reason,
                request_id=entry.result.request_id,
                matched_policies=entry.result.matched_policies,
                obligations=entry.result.obligations,
                advices=entry.result.advices,
                evaluated_at=entry.result.evaluated_at,
                cache_hit=True,
                diagnostics=entry.result.diagnostics,
            )

    def set(self, key: str, result: AuthorizationResult) -> None:
        if self.ttl_seconds <= 0:
            return
        with self._lock:
            if len(self._cache) >= self.max_entries:
                self._evict_expired_or_oldest()
            self._cache[key] = _CacheEntry(result=result, expires_at=time.time() + self.ttl_seconds)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _evict_expired_or_oldest(self) -> None:
        now = time.time()
        expired = [key for key, entry in self._cache.items() if entry.expires_at <= now]
        for key in expired:
            self._cache.pop(key, None)
        if len(self._cache) >= self.max_entries and self._cache:
            oldest_key = min(self._cache.items(), key=lambda item: item[1].expires_at)[0]
            self._cache.pop(oldest_key, None)


class AuditSink(ABC):
    """Audit event sink abstraction."""

    @abstractmethod
    def emit(self, event: AuditEvent) -> None:
        """Emit an audit event."""


class LoggingAuditSink(AuditSink):
    """Audit sink backed by Python logging."""

    def __init__(self, audit_logger: Optional[logging.Logger] = None) -> None:
        self.audit_logger = audit_logger or logging.getLogger("security.abac.audit")

    def emit(self, event: AuditEvent) -> None:
        self.audit_logger.info("abac_authorization_event=%s", json.dumps(event.to_dict(), sort_keys=True))


class ABACEngine:
    """Enterprise ABAC authorization engine."""

    def __init__(
        self,
        repository: PolicyRepository,
        config: Optional[EvaluationConfig] = None,
        operator_registry: Optional[OperatorRegistry] = None,
        audit_sink: Optional[AuditSink] = None,
        cache: Optional[DecisionCache] = None,
    ) -> None:
        self.repository = repository
        self.config = config or EvaluationConfig()
        self.operator_registry = operator_registry or OperatorRegistry()
        self.audit_sink = audit_sink or LoggingAuditSink()
        self.cache = cache or DecisionCache(
            ttl_seconds=self.config.cache_ttl_seconds,
            max_entries=self.config.max_cache_entries,
        )

    def authorize(self, context: ABACContext) -> AuthorizationResult:
        """Evaluate the authorization request and return a final decision."""
        cache_key = self._cache_key(context)

        if self.config.enable_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        try:
            result = self._evaluate(context)
        except Exception as exc:
            logger.exception("ABAC evaluation failed. request_id=%s", context.request_id)
            if self.config.fail_closed:
                result = AuthorizationResult(
                    decision=Decision.DENY,
                    allowed=False,
                    reason="ABAC evaluation failed; fail-closed deny applied.",
                    request_id=context.request_id,
                    diagnostics={"error": str(exc), "error_type": type(exc).__name__},
                )
            else:
                result = AuthorizationResult(
                    decision=Decision.INDETERMINATE,
                    allowed=False,
                    reason="ABAC evaluation failed and fail_closed is disabled.",
                    request_id=context.request_id,
                    diagnostics={"error": str(exc), "error_type": type(exc).__name__},
                )

        if self.config.enable_cache:
            self.cache.set(cache_key, result)

        if self.config.enable_audit:
            self._emit_audit(context, result)

        return result

    def can(self, context: ABACContext) -> bool:
        """Convenience boolean authorization check."""
        return self.authorize(context).allowed

    def explain(self, context: ABACContext) -> JsonDict:
        """Return a human-readable explanation payload for an authorization request."""
        result = self.authorize(context)
        return result.to_dict()

    def reload_cache(self) -> None:
        """Clear the decision cache, usually after policy updates."""
        self.cache.clear()

    def _evaluate(self, context: ABACContext) -> AuthorizationResult:
        policies = list(self.repository.list_policies())
        evaluation_results: List[PolicyEvaluationResult] = []
        applicable: List[PolicyEvaluationResult] = []

        for policy in policies:
            try:
                is_applicable = policy.is_applicable(
                    context,
                    self.operator_registry,
                    strict=self.config.strict_attribute_resolution,
                )
                result = PolicyEvaluationResult(
                    policy_id=policy.policy_id,
                    policy_name=policy.name,
                    effect=policy.effect if is_applicable else None,
                    applicable=is_applicable,
                    priority=policy.priority,
                    reason="Policy applicable." if is_applicable else "Policy not applicable.",
                    obligations=policy.obligations if is_applicable else (),
                    advices=policy.advices if is_applicable else (),
                )
                evaluation_results.append(result)
                if is_applicable:
                    applicable.append(result)
            except Exception as exc:
                error_result = PolicyEvaluationResult(
                    policy_id=policy.policy_id,
                    policy_name=policy.name,
                    effect=Effect.DENY if self.config.fail_closed else None,
                    applicable=self.config.fail_closed,
                    priority=policy.priority,
                    reason="Policy evaluation error.",
                    error=f"{type(exc).__name__}: {exc}",
                )
                evaluation_results.append(error_result)
                if self.config.fail_closed:
                    applicable.append(error_result)

        decision_result = self._combine(applicable, evaluation_results)
        if not self.config.enable_explanations:
            decision_result = AuthorizationResult(
                decision=decision_result.decision,
                allowed=decision_result.allowed,
                reason=decision_result.reason,
                request_id=decision_result.request_id,
                matched_policies=(),
                obligations=decision_result.obligations,
                advices=decision_result.advices,
                evaluated_at=decision_result.evaluated_at,
                cache_hit=decision_result.cache_hit,
                diagnostics={},
            )
        return decision_result

    def _combine(
        self,
        applicable: Sequence[PolicyEvaluationResult],
        all_results: Sequence[PolicyEvaluationResult],
    ) -> AuthorizationResult:
        if not applicable:
            if self.config.deny_by_default:
                return AuthorizationResult(
                    decision=Decision.DENY,
                    allowed=False,
                    reason="No applicable allow policy found; deny-by-default applied.",
                    request_id=self._request_id_from_results(all_results),
                    matched_policies=tuple(all_results),
                    diagnostics={"applicable_policy_count": 0},
                )
            return AuthorizationResult(
                decision=Decision.NOT_APPLICABLE,
                allowed=False,
                reason="No applicable policy found.",
                request_id=self._request_id_from_results(all_results),
                matched_policies=tuple(all_results),
                diagnostics={"applicable_policy_count": 0},
            )

        algorithm = self.config.combining_algorithm
        sorted_applicable = sorted(applicable, key=lambda r: (r.priority, r.policy_id))

        selected: Optional[PolicyEvaluationResult]
        reason: str

        if algorithm == CombiningAlgorithm.DENY_OVERRIDES:
            selected = next((r for r in sorted_applicable if r.effect == Effect.DENY), None)
            if selected is None:
                selected = next((r for r in sorted_applicable if r.effect == Effect.ALLOW), None)
            reason = "Deny-overrides combining algorithm applied."
        elif algorithm == CombiningAlgorithm.ALLOW_OVERRIDES:
            selected = next((r for r in sorted_applicable if r.effect == Effect.ALLOW), None)
            if selected is None:
                selected = next((r for r in sorted_applicable if r.effect == Effect.DENY), None)
            reason = "Allow-overrides combining algorithm applied."
        elif algorithm == CombiningAlgorithm.FIRST_APPLICABLE:
            selected = sorted_applicable[0]
            reason = "First-applicable combining algorithm applied."
        elif algorithm == CombiningAlgorithm.PRIORITY:
            selected = sorted_applicable[0]
            reason = "Priority combining algorithm applied."
        else:
            selected = None
            reason = "Unknown combining algorithm; deny applied."

        if selected is None or selected.effect is None:
            return AuthorizationResult(
                decision=Decision.DENY,
                allowed=False,
                reason=reason,
                request_id=self._request_id_from_results(all_results),
                matched_policies=tuple(all_results),
                diagnostics={"applicable_policy_count": len(applicable)},
            )

        obligations = tuple(dict.fromkeys(item for result in sorted_applicable for item in result.obligations))
        advices = tuple(dict.fromkeys(item for result in sorted_applicable for item in result.advices))
        decision = Decision.ALLOW if selected.effect == Effect.ALLOW else Decision.DENY

        return AuthorizationResult(
            decision=decision,
            allowed=decision == Decision.ALLOW,
            reason=f"{reason} Selected policy: {selected.policy_id}.",
            request_id=self._request_id_from_results(all_results),
            matched_policies=tuple(all_results),
            obligations=obligations,
            advices=advices,
            diagnostics={
                "selected_policy_id": selected.policy_id,
                "combining_algorithm": algorithm.value,
                "applicable_policy_count": len(applicable),
                "evaluated_policy_count": len(all_results),
            },
        )

    def _cache_key(self, context: ABACContext) -> str:
        payload = json.dumps(_canonicalize(context.as_mapping()), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _emit_audit(self, context: ABACContext, result: AuthorizationResult) -> None:
        try:
            event = AuditEvent(
                event_type="abac.authorization.evaluated",
                decision=result.decision,
                allowed=result.allowed,
                reason=result.reason,
                request_id=context.request_id,
                correlation_id=context.correlation_id,
                tenant_id=context.tenant_id,
                subject=_redact(context.subject, self.config) if self.config.redact_sensitive_attributes else dict(context.subject),
                resource=_redact(context.resource, self.config) if self.config.redact_sensitive_attributes else dict(context.resource),
                action=_redact(context.action, self.config) if self.config.redact_sensitive_attributes else dict(context.action),
                environment=_redact(context.environment, self.config) if self.config.redact_sensitive_attributes else dict(context.environment),
                matched_policy_ids=tuple(
                    result.policy_id for result in result.matched_policies if result.applicable
                ),
            )
            self.audit_sink.emit(event)
        except Exception:
            logger.exception("Failed to emit ABAC audit event. request_id=%s", context.request_id)

    @staticmethod
    def _request_id_from_results(results: Sequence[PolicyEvaluationResult]) -> Optional[str]:
        return None


class PolicyJsonCodec:
    """Serialize and deserialize ABAC policies from JSON-compatible dictionaries."""

    @classmethod
    def policy_from_dict(cls, data: Mapping[str, Any]) -> Policy:
        try:
            target_data = data.get("target") or {}
            target = PolicyTarget(
                subjects=tuple(target_data.get("subjects", ("*",))),
                resources=tuple(target_data.get("resources", ("*",))),
                actions=tuple(target_data.get("actions", ("*",))),
                tenants=tuple(target_data.get("tenants", ("*",))),
                match_strategy=MatchStrategy(target_data.get("match_strategy", MatchStrategy.WILDCARD.value)),
            )

            policy = Policy(
                policy_id=str(data["policy_id"]),
                name=str(data["name"]),
                effect=Effect(data["effect"]),
                target=target,
                conditions=cls.condition_group_from_dict(data.get("conditions")) if data.get("conditions") else None,
                priority=int(data.get("priority", 100)),
                status=PolicyStatus(data.get("status", PolicyStatus.ACTIVE.value)),
                version=str(data.get("version", "1.0.0")),
                description=data.get("description"),
                obligations=tuple(data.get("obligations", ())),
                advices=tuple(data.get("advices", ())),
                tags=tuple(data.get("tags", ())),
                metadata=dict(data.get("metadata", {})),
                created_at=_parse_datetime_or_default(data.get("created_at")),
                updated_at=_parse_datetime_or_default(data.get("updated_at")),
            )
            policy.validate()
            return policy
        except Exception as exc:
            raise PolicyValidationError(f"Invalid policy JSON: {exc}") from exc

    @classmethod
    def condition_group_from_dict(cls, data: Optional[Mapping[str, Any]]) -> Optional[ConditionGroup]:
        if not data:
            return None
        return ConditionGroup(
            all=tuple(cls._condition_node(item) for item in data.get("all", ())),
            any=tuple(cls._condition_node(item) for item in data.get("any", ())),
            not_=tuple(cls._condition_node(item) for item in data.get("not", data.get("not_", ()))),
        )

    @classmethod
    def policy_to_dict(cls, policy: Policy) -> JsonDict:
        return {
            "policy_id": policy.policy_id,
            "name": policy.name,
            "effect": policy.effect.value,
            "target": {
                "subjects": list(policy.target.subjects),
                "resources": list(policy.target.resources),
                "actions": list(policy.target.actions),
                "tenants": list(policy.target.tenants),
                "match_strategy": policy.target.match_strategy.value,
            },
            "conditions": cls.condition_group_to_dict(policy.conditions) if policy.conditions else None,
            "priority": policy.priority,
            "status": policy.status.value,
            "version": policy.version,
            "description": policy.description,
            "obligations": list(policy.obligations),
            "advices": list(policy.advices),
            "tags": list(policy.tags),
            "metadata": dict(policy.metadata),
            "created_at": policy.created_at.isoformat(),
            "updated_at": policy.updated_at.isoformat(),
        }

    @classmethod
    def condition_group_to_dict(cls, group: ConditionGroup) -> JsonDict:
        return {
            "all": [cls._node_to_dict(item) for item in group.all],
            "any": [cls._node_to_dict(item) for item in group.any],
            "not": [cls._node_to_dict(item) for item in group.not_],
        }

    @classmethod
    def load_policies_from_json(cls, raw_json: Union[str, bytes]) -> Tuple[Policy, ...]:
        payload = json.loads(raw_json)
        if isinstance(payload, Mapping):
            payload = payload.get("policies", [])
        if not isinstance(payload, list):
            raise PolicyValidationError("Policy JSON must be a list or an object with a 'policies' list.")
        return tuple(cls.policy_from_dict(item) for item in payload)

    @classmethod
    def dump_policies_to_json(cls, policies: Iterable[Policy], indent: int = 2) -> str:
        return json.dumps(
            {"policies": [cls.policy_to_dict(policy) for policy in policies]},
            indent=indent,
            sort_keys=True,
            default=str,
        )

    @classmethod
    def _condition_node(cls, data: Mapping[str, Any]) -> Union[Condition, ConditionGroup]:
        if any(key in data for key in ("all", "any", "not", "not_")):
            group = cls.condition_group_from_dict(data)
            if group is None:
                raise PolicyValidationError("Invalid empty condition group.")
            return group
        return Condition(
            left=str(data["left"]),
            operator=str(data["operator"]),
            right=data.get("right"),
            right_is_attribute=bool(data.get("right_is_attribute", False)),
            negate=bool(data.get("negate", False)),
            description=data.get("description"),
        )

    @classmethod
    def _node_to_dict(cls, node: Union[Condition, ConditionGroup]) -> JsonDict:
        if isinstance(node, ConditionGroup):
            return cls.condition_group_to_dict(node)
        return {
            "left": node.left,
            "operator": node.operator,
            "right": node.right,
            "right_is_attribute": node.right_is_attribute,
            "negate": node.negate,
            "description": node.description,
        }


def resolve_attribute(source: Mapping[str, Any], path: str, strict: bool = False) -> Any:
    """
    Resolve dotted attribute path from nested dictionaries/objects/lists.

    Examples:
        subject.department
        resource.classification
        environment.geo.country
        subject.groups.0
    """
    if not path:
        if strict:
            raise AttributeResolutionError("Attribute path is empty.")
        return None

    current: Any = source
    for part in path.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                if strict:
                    raise AttributeResolutionError(f"Missing attribute path segment: {part} in {path}")
                return None
            current = current[part]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                if strict:
                    raise AttributeResolutionError(f"Invalid list index '{part}' in attribute path: {path}")
                return None
        else:
            if hasattr(current, part):
                current = getattr(current, part)
            else:
                if strict:
                    raise AttributeResolutionError(f"Cannot resolve segment '{part}' in attribute path: {path}")
                return None
    return current


def build_engine_from_json(
    raw_json: Union[str, bytes],
    config: Optional[EvaluationConfig] = None,
    audit_sink: Optional[AuditSink] = None,
) -> ABACEngine:
    """Build an ABAC engine using policies from JSON."""
    policies = PolicyJsonCodec.load_policies_from_json(raw_json)
    repository = InMemoryPolicyRepository(policies)
    return ABACEngine(repository=repository, config=config, audit_sink=audit_sink)


def create_default_data_policy_engine() -> ABACEngine:
    """
    Example factory for a data platform.

    Rules:
    - Explicitly deny inactive users.
    - Allow same-tenant reads when subject clearance >= resource classification_level.
    - Allow resource owners to write their own resource.
    - Deny deletes unless user has admin role.
    """
    policies = [
        Policy(
            policy_id="deny-inactive-subjects",
            name="Deny inactive subjects",
            effect=Effect.DENY,
            priority=1,
            conditions=ConditionGroup(
                all=(Condition("subject.active", "eq", False),),
            ),
            tags=("baseline", "identity"),
        ),
        Policy(
            policy_id="allow-same-tenant-clearance-read",
            name="Allow same-tenant read when clearance is sufficient",
            effect=Effect.ALLOW,
            priority=20,
            target=PolicyTarget(actions=("read", "query", "export"), tenants=("*",)),
            conditions=ConditionGroup(
                all=(
                    Condition("subject.tenant_id", "eq", "resource.tenant_id", right_is_attribute=True),
                    Condition("subject.clearance_level", "gte", "resource.classification_level", right_is_attribute=True),
                ),
            ),
            obligations=("mask_pii_when_required",),
            advices=("log_data_access",),
            tags=("data", "read"),
        ),
        Policy(
            policy_id="allow-owner-write",
            name="Allow owner write",
            effect=Effect.ALLOW,
            priority=30,
            target=PolicyTarget(actions=("write", "update"), resources=("*",)),
            conditions=ConditionGroup(
                all=(
                    Condition("subject.id", "eq", "resource.owner_id", right_is_attribute=True),
                    Condition("subject.tenant_id", "eq", "resource.tenant_id", right_is_attribute=True),
                ),
            ),
            tags=("data", "write", "ownership"),
        ),
        Policy(
            policy_id="deny-delete-non-admin",
            name="Deny delete for non-admin subjects",
            effect=Effect.DENY,
            priority=5,
            target=PolicyTarget(actions=("delete",)),
            conditions=ConditionGroup(
                not_=(Condition("subject.roles", "contains", "admin"),),
            ),
            tags=("destructive-action", "admin"),
        ),
        Policy(
            policy_id="allow-admin-delete",
            name="Allow admin delete",
            effect=Effect.ALLOW,
            priority=50,
            target=PolicyTarget(actions=("delete",)),
            conditions=ConditionGroup(
                all=(Condition("subject.roles", "contains", "admin"),),
            ),
            obligations=("require_mfa", "create_deletion_audit_record"),
            tags=("destructive-action", "admin"),
        ),
    ]
    return ABACEngine(repository=InMemoryPolicyRepository(policies))


def _evaluate_node(
    node: Union[Condition, ConditionGroup],
    context: ABACContext,
    registry: OperatorRegistry,
    strict: bool,
) -> bool:
    if isinstance(node, ConditionGroup):
        return node.evaluate(context, registry, strict=strict)
    return node.evaluate(context, registry, strict=strict)


def _matches_any(value: str, patterns: Sequence[str], strategy: MatchStrategy) -> bool:
    if strategy == MatchStrategy.ANY:
        return True
    if not patterns:
        return False
    for pattern in patterns:
        if pattern == "*":
            return True
        if strategy == MatchStrategy.EXACT and value == pattern:
            return True
        if strategy == MatchStrategy.WILDCARD and fnmatch.fnmatchcase(value, pattern):
            return True
        if strategy == MatchStrategy.REGEX and re.fullmatch(pattern, value):
            return True
    return False


def _operator_between(left: Any, right: Any) -> bool:
    values = list(_ensure_iterable(right))
    if len(values) != 2:
        raise ConditionEvaluationError("between operator expects exactly two boundary values.")
    comparable = _coerce_comparable(left)
    low = _coerce_comparable(values[0])
    high = _coerce_comparable(values[1])
    return low <= comparable <= high


def _operator_date_between(left: Any, right: Any) -> bool:
    values = list(_ensure_iterable(right))
    if len(values) != 2:
        raise ConditionEvaluationError("date_between operator expects exactly two boundary values.")
    value = _parse_datetime(left)
    start = _parse_datetime(values[0])
    end = _parse_datetime(values[1])
    return start <= value <= end


def _ensure_iterable(value: Any) -> Iterable[Any]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)):
        return (value,)
    if isinstance(value, Mapping):
        return value.keys()
    try:
        iter(value)
        return value
    except TypeError:
        return (value,)


def _coerce_comparable(value: Any) -> Any:
    if isinstance(value, (int, float, datetime, date)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        try:
            if "." in stripped:
                return float(stripped)
            return int(stripped)
        except ValueError:
            return stripped
    return value


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError as exc:
            raise ConditionEvaluationError(f"Invalid datetime value: {value}") from exc
    raise ConditionEvaluationError(f"Unsupported datetime value type: {type(value).__name__}")


def _parse_datetime_or_default(value: Any) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return _parse_datetime(value)


def _normalize_operator_name(name: str) -> str:
    return str(name).strip().lower().replace("-", "_")


def _stringify(value: Any) -> str:
    return "" if value is None else str(value)


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(value[key]) for key in sorted(value.keys(), key=str)}
    if isinstance(value, (list, tuple, set)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _redact(data: Mapping[str, Any], config: EvaluationConfig) -> JsonDict:
    sensitive = {item.lower() for item in config.sensitive_attribute_names}

    def walk(obj: Any) -> Any:
        if isinstance(obj, Mapping):
            result: JsonDict = {}
            for key, value in obj.items():
                key_str = str(key)
                if any(token in key_str.lower() for token in sensitive):
                    result[key_str] = "***REDACTED***"
                else:
                    result[key_str] = walk(value)
            return result
        if isinstance(obj, list):
            return [walk(item) for item in obj]
        if isinstance(obj, tuple):
            return tuple(walk(item) for item in obj)
        return obj

    return walk(dict(data))


__all__ = [
    "ABACContext",
    "ABACEngine",
    "ABACError",
    "AuditEvent",
    "AuditSink",
    "AuthorizationResult",
    "CombiningAlgorithm",
    "Condition",
    "ConditionEvaluationError",
    "ConditionGroup",
    "Decision",
    "DecisionCache",
    "Effect",
    "EvaluationConfig",
    "InMemoryPolicyRepository",
    "LoggingAuditSink",
    "MatchStrategy",
    "OperatorRegistry",
    "Policy",
    "PolicyEvaluationResult",
    "PolicyJsonCodec",
    "PolicyRepository",
    "PolicyRepositoryError",
    "PolicyStatus",
    "PolicyTarget",
    "PolicyValidationError",
    "build_engine_from_json",
    "create_default_data_policy_engine",
    "resolve_attribute",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    engine = create_default_data_policy_engine()

    request_context = ABACContext(
        subject={
            "id": "user-001",
            "tenant_id": "tenant-a",
            "active": True,
            "roles": ["analyst"],
            "clearance_level": 3,
        },
        resource={
            "id": "dataset-001",
            "tenant_id": "tenant-a",
            "owner_id": "user-999",
            "classification_level": 2,
        },
        action={"name": "read"},
        environment={"ip": "10.0.0.10", "risk_score": 12},
        request_id="req-123",
        correlation_id="corr-123",
        tenant_id="tenant-a",
    )

    auth_result = engine.authorize(request_context)
    print(json.dumps(auth_result.to_dict(), indent=2, default=str))

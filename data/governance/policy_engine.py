"""
policy_engine.py
================

Enterprise-grade policy engine for data governance platforms.

Core capabilities
-----------------
- Declarative policy model for governance, access, privacy, retention and compliance.
- RBAC/ABAC-style condition evaluation with nested context paths.
- Deny-overrides, allow-overrides, priority and first-match combining algorithms.
- Policy obligations, advice, remediation hints and control mappings.
- Versioned policy bundles with validation and conflict detection.
- Policy simulation, dry-run evaluation and batch evaluation.
- Decision audit trail with explainability and deterministic audit hashes.
- Pluggable policy repository and audit sink.
- Dependency-light and vendor-neutral architecture.

Typical usage
-------------
>>> engine = build_default_policy_engine()
>>> decision = engine.evaluate({
...     "subject": {"roles": ["analyst"], "department": "finance"},
...     "resource": {"sensitivity": "confidential", "domain": "finance"},
...     "action": "read",
...     "purpose": "analytics",
... })
>>> decision.effect.value
'allow'
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import hashlib
import json
import logging
import re
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Set, Tuple, Union, runtime_checkable

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
ConditionFunction = Callable[[Mapping[str, Any]], bool]


class PolicyEngineError(Exception):
    """Base exception for policy engine failures."""


class PolicyValidationError(PolicyEngineError):
    """Raised when a policy or bundle is invalid."""


class PolicyEvaluationError(PolicyEngineError):
    """Raised when policy evaluation fails."""


class PolicyNotFoundError(PolicyEngineError):
    """Raised when a requested policy is not found."""


class PolicyDomain(str, enum.Enum):
    ACCESS = "access"
    PRIVACY = "privacy"
    RETENTION = "retention"
    COMPLIANCE = "compliance"
    CLASSIFICATION = "classification"
    MASKING = "masking"
    ENCRYPTION = "encryption"
    QUALITY = "quality"
    CATALOG = "catalog"
    LINEAGE = "lineage"
    SECURITY = "security"
    STEWARDSHIP = "stewardship"
    GENERAL = "general"


class PolicyEffect(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"
    NOT_APPLICABLE = "not_applicable"


class DecisionEffect(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"
    NOT_APPLICABLE = "not_applicable"
    INDETERMINATE = "indeterminate"


class PolicyStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class CombiningAlgorithm(str, enum.Enum):
    DENY_OVERRIDES = "deny_overrides"
    ALLOW_OVERRIDES = "allow_overrides"
    FIRST_APPLICABLE = "first_applicable"
    PRIORITY = "priority"
    CONSENSUS = "consensus"


class ConditionOperator(str, enum.Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    INTERSECTS = "intersects"
    EXISTS = "exists"
    MISSING = "missing"
    REGEX = "regex"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    BETWEEN = "between"


class LogicalOperator(str, enum.Enum):
    ALL = "all"
    ANY = "any"
    NOT = "not"


class ObligationType(str, enum.Enum):
    REQUIRE_APPROVAL = "require_approval"
    REQUIRE_MFA = "require_mfa"
    MASK_FIELDS = "mask_fields"
    ENCRYPT_FIELDS = "encrypt_fields"
    AUDIT = "audit"
    NOTIFY = "notify"
    RETAIN_FOR = "retain_for"
    DELETE_AFTER = "delete_after"
    ESCALATE = "escalate"
    TAG_RESOURCE = "tag_resource"
    BLOCK_EXPORT = "block_export"
    REQUIRE_JUSTIFICATION = "require_justification"
    CUSTOM = "custom"


class PolicySeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class PolicyCondition:
    """Atomic policy condition over a nested evaluation context."""

    path: str
    operator: ConditionOperator
    value: Any = None
    case_sensitive: bool = False
    description: str = ""

    def evaluate(self, context: Mapping[str, Any]) -> bool:
        actual = get_path(context, self.path)
        expected = self.value
        op = self.operator

        if not self.case_sensitive:
            actual_cmp = normalize_value(actual)
            expected_cmp = normalize_value(expected)
        else:
            actual_cmp = actual
            expected_cmp = expected

        if op == ConditionOperator.EXISTS:
            return actual is not None
        if op == ConditionOperator.MISSING:
            return actual is None
        if op == ConditionOperator.EQ:
            return actual_cmp == expected_cmp
        if op == ConditionOperator.NE:
            return actual_cmp != expected_cmp
        if op == ConditionOperator.GT:
            return actual_cmp > expected_cmp
        if op == ConditionOperator.GTE:
            return actual_cmp >= expected_cmp
        if op == ConditionOperator.LT:
            return actual_cmp < expected_cmp
        if op == ConditionOperator.LTE:
            return actual_cmp <= expected_cmp
        if op == ConditionOperator.IN:
            return actual_cmp in ensure_collection(expected_cmp)
        if op == ConditionOperator.NOT_IN:
            return actual_cmp not in ensure_collection(expected_cmp)
        if op == ConditionOperator.CONTAINS:
            return contains(actual_cmp, expected_cmp)
        if op == ConditionOperator.NOT_CONTAINS:
            return not contains(actual_cmp, expected_cmp)
        if op == ConditionOperator.INTERSECTS:
            return bool(set(ensure_collection(actual_cmp)).intersection(set(ensure_collection(expected_cmp))))
        if op == ConditionOperator.REGEX:
            flags = 0 if self.case_sensitive else re.IGNORECASE
            return bool(re.search(str(expected), str(actual), flags=flags))
        if op == ConditionOperator.STARTS_WITH:
            return str(actual_cmp).startswith(str(expected_cmp))
        if op == ConditionOperator.ENDS_WITH:
            return str(actual_cmp).endswith(str(expected_cmp))
        if op == ConditionOperator.BETWEEN:
            bounds = list(ensure_collection(expected_cmp))
            if len(bounds) != 2:
                raise PolicyEvaluationError(f"BETWEEN condition requires exactly two bounds for path {self.path}")
            return bounds[0] <= actual_cmp <= bounds[1]
        raise PolicyEvaluationError(f"Unsupported condition operator: {op}")

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class ConditionGroup:
    """Nested logical condition group."""

    operator: LogicalOperator = LogicalOperator.ALL
    conditions: Tuple[Union[PolicyCondition, "ConditionGroup"], ...] = field(default_factory=tuple)
    description: str = ""

    def evaluate(self, context: Mapping[str, Any]) -> bool:
        if self.operator == LogicalOperator.NOT:
            if len(self.conditions) != 1:
                raise PolicyEvaluationError("NOT condition group requires exactly one child condition")
            return not self.conditions[0].evaluate(context)  # type: ignore[union-attr]
        outcomes = [condition.evaluate(context) for condition in self.conditions]  # type: ignore[union-attr]
        if self.operator == LogicalOperator.ALL:
            return all(outcomes)
        if self.operator == LogicalOperator.ANY:
            return any(outcomes)
        raise PolicyEvaluationError(f"Unsupported logical operator: {self.operator}")

    def to_dict(self) -> JsonDict:
        return {
            "operator": self.operator.value,
            "conditions": [condition.to_dict() for condition in self.conditions],
            "description": self.description,
        }


@dataclass(frozen=True)
class PolicyObligation:
    obligation_id: str
    obligation_type: ObligationType
    parameters: JsonDict = field(default_factory=dict)
    required: bool = True
    description: str = ""

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class PolicyAdvice:
    advice_id: str
    message: str
    severity: PolicySeverity = PolicySeverity.INFO
    remediation: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class GovernancePolicy:
    policy_id: str
    name: str
    domain: PolicyDomain
    effect: PolicyEffect
    conditions: ConditionGroup = field(default_factory=ConditionGroup)
    version: str = "1.0.0"
    status: PolicyStatus = PolicyStatus.ACTIVE
    priority: int = 100
    description: str = ""
    obligations: Tuple[PolicyObligation, ...] = field(default_factory=tuple)
    advice: Tuple[PolicyAdvice, ...] = field(default_factory=tuple)
    tags: Tuple[str, ...] = field(default_factory=tuple)
    control_ids: Tuple[str, ...] = field(default_factory=tuple)
    requirement_ids: Tuple[str, ...] = field(default_factory=tuple)
    owner_id: Optional[str] = None
    valid_from: Optional[dt.datetime] = None
    valid_until: Optional[dt.datetime] = None
    metadata: JsonDict = field(default_factory=dict)

    def is_active(self, as_of: Optional[dt.datetime] = None) -> bool:
        if self.status != PolicyStatus.ACTIVE:
            return False
        as_of = as_of or dt.datetime.now(dt.timezone.utc)
        if self.valid_from and self.valid_from > as_of:
            return False
        if self.valid_until and self.valid_until < as_of:
            return False
        return True

    def evaluate(self, context: Mapping[str, Any], *, as_of: Optional[dt.datetime] = None) -> Tuple[PolicyEffect, List[str]]:
        if not self.is_active(as_of):
            return PolicyEffect.NOT_APPLICABLE, ["policy_not_active"]
        try:
            matched = self.conditions.evaluate(context) if self.conditions.conditions else True
        except Exception as exc:
            raise PolicyEvaluationError(f"Policy {self.policy_id} failed: {exc}") from exc
        if not matched:
            return PolicyEffect.NOT_APPLICABLE, ["conditions_not_matched"]
        return self.effect, ["conditions_matched", f"effect_{self.effect.value}"]

    def to_dict(self) -> JsonDict:
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "domain": self.domain.value,
            "effect": self.effect.value,
            "conditions": self.conditions.to_dict(),
            "version": self.version,
            "status": self.status.value,
            "priority": self.priority,
            "description": self.description,
            "obligations": [obligation.to_dict() for obligation in self.obligations],
            "advice": [advice.to_dict() for advice in self.advice],
            "tags": list(self.tags),
            "control_ids": list(self.control_ids),
            "requirement_ids": list(self.requirement_ids),
            "owner_id": self.owner_id,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class PolicyBundle:
    bundle_id: str
    name: str
    policies: Tuple[GovernancePolicy, ...]
    combining_algorithm: CombiningAlgorithm = CombiningAlgorithm.DENY_OVERRIDES
    version: str = "1.0.0"
    status: PolicyStatus = PolicyStatus.ACTIVE
    description: str = ""
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    created_by: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def active_policies(self, as_of: Optional[dt.datetime] = None) -> List[GovernancePolicy]:
        if self.status != PolicyStatus.ACTIVE:
            return []
        return [policy for policy in self.policies if policy.is_active(as_of)]

    def to_dict(self) -> JsonDict:
        return {
            "bundle_id": self.bundle_id,
            "name": self.name,
            "version": self.version,
            "status": self.status.value,
            "combining_algorithm": self.combining_algorithm.value,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "metadata": dict(self.metadata),
            "policies": [policy.to_dict() for policy in self.policies],
        }


@dataclass
class PolicyMatch:
    policy_id: str
    policy_name: str
    effect: PolicyEffect
    reasons: List[str]
    priority: int
    obligations: List[PolicyObligation] = field(default_factory=list)
    advice: List[PolicyAdvice] = field(default_factory=list)
    control_ids: List[str] = field(default_factory=list)
    requirement_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return {
            "policy_id": self.policy_id,
            "policy_name": self.policy_name,
            "effect": self.effect.value,
            "reasons": list(self.reasons),
            "priority": self.priority,
            "obligations": [obligation.to_dict() for obligation in self.obligations],
            "advice": [advice.to_dict() for advice in self.advice],
            "control_ids": list(self.control_ids),
            "requirement_ids": list(self.requirement_ids),
        }


@dataclass
class PolicyDecision:
    decision_id: str
    effect: DecisionEffect
    matched_policies: List[PolicyMatch]
    evaluated_policy_count: int
    applicable_policy_count: int
    combining_algorithm: CombiningAlgorithm
    reasons: List[str] = field(default_factory=list)
    obligations: List[PolicyObligation] = field(default_factory=list)
    advice: List[PolicyAdvice] = field(default_factory=list)
    context_hash: Optional[str] = None
    audit_hash: Optional[str] = None
    evaluated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    duration_ms: Optional[float] = None
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "decision_id": self.decision_id,
            "effect": self.effect.value,
            "matched_policies": [match.to_dict() for match in self.matched_policies],
            "evaluated_policy_count": self.evaluated_policy_count,
            "applicable_policy_count": self.applicable_policy_count,
            "combining_algorithm": self.combining_algorithm.value,
            "reasons": list(self.reasons),
            "obligations": [obligation.to_dict() for obligation in self.obligations],
            "advice": [advice.to_dict() for advice in self.advice],
            "context_hash": self.context_hash,
            "audit_hash": self.audit_hash,
            "evaluated_at": self.evaluated_at.isoformat(),
            "duration_ms": self.duration_ms,
            "metadata": dict(self.metadata),
        }


@dataclass
class PolicyValidationIssue:
    code: str
    message: str
    policy_id: Optional[str] = None
    severity: PolicySeverity = PolicySeverity.ERROR
    context: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass
class PolicyValidationReport:
    valid: bool
    issues: List[PolicyValidationIssue]
    policy_count: int
    generated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def to_dict(self) -> JsonDict:
        return {
            "valid": self.valid,
            "policy_count": self.policy_count,
            "generated_at": self.generated_at.isoformat(),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass
class PolicySimulationResult:
    simulation_id: str
    contexts_evaluated: int
    decisions: List[PolicyDecision]
    effect_counts: JsonDict
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def duration_ms(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at) * 1000, 3)

    def finish(self) -> None:
        self.finished_at = time.time()

    def to_dict(self) -> JsonDict:
        return {
            "simulation_id": self.simulation_id,
            "contexts_evaluated": self.contexts_evaluated,
            "effect_counts": dict(self.effect_counts),
            "duration_ms": self.duration_ms,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


@runtime_checkable
class PolicyRepository(Protocol):
    def upsert_policy(self, policy: GovernancePolicy) -> None:
        ...

    def get_policy(self, policy_id: str) -> Optional[GovernancePolicy]:
        ...

    def list_policies(self, domain: Optional[PolicyDomain] = None, active_only: bool = True) -> List[GovernancePolicy]:
        ...

    def upsert_bundle(self, bundle: PolicyBundle) -> None:
        ...

    def get_bundle(self, bundle_id: str) -> Optional[PolicyBundle]:
        ...

    def list_bundles(self, active_only: bool = True) -> List[PolicyBundle]:
        ...

    def record_decision(self, decision: PolicyDecision) -> None:
        ...

    def list_decisions(self, limit: int = 1000) -> List[PolicyDecision]:
        ...


class InMemoryPolicyRepository(PolicyRepository):
    """In-memory policy repository for tests, local mode and fallback usage."""

    def __init__(self) -> None:
        self.policies: Dict[str, GovernancePolicy] = {}
        self.bundles: Dict[str, PolicyBundle] = {}
        self.decisions: Dict[str, PolicyDecision] = {}

    def upsert_policy(self, policy: GovernancePolicy) -> None:
        self.policies[policy.policy_id] = policy

    def get_policy(self, policy_id: str) -> Optional[GovernancePolicy]:
        return self.policies.get(policy_id)

    def list_policies(self, domain: Optional[PolicyDomain] = None, active_only: bool = True) -> List[GovernancePolicy]:
        policies = list(self.policies.values())
        if domain:
            policies = [policy for policy in policies if policy.domain == domain]
        if active_only:
            policies = [policy for policy in policies if policy.is_active()]
        return sorted(policies, key=lambda policy: policy.priority)

    def upsert_bundle(self, bundle: PolicyBundle) -> None:
        self.bundles[bundle.bundle_id] = bundle

    def get_bundle(self, bundle_id: str) -> Optional[PolicyBundle]:
        return self.bundles.get(bundle_id)

    def list_bundles(self, active_only: bool = True) -> List[PolicyBundle]:
        bundles = list(self.bundles.values())
        if active_only:
            bundles = [bundle for bundle in bundles if bundle.status == PolicyStatus.ACTIVE]
        return sorted(bundles, key=lambda bundle: bundle.name)

    def record_decision(self, decision: PolicyDecision) -> None:
        self.decisions[decision.decision_id] = decision

    def list_decisions(self, limit: int = 1000) -> List[PolicyDecision]:
        return sorted(self.decisions.values(), key=lambda d: d.evaluated_at, reverse=True)[:limit]


@runtime_checkable
class PolicyAuditSink(Protocol):
    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        ...


class LoggingPolicyAuditSink:
    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self.log.info("policy_engine_audit", extra={"event_type": event_type, "payload": dict(payload)})


@dataclass(frozen=True)
class PolicyEngineConfig:
    default_combining_algorithm: CombiningAlgorithm = CombiningAlgorithm.DENY_OVERRIDES
    fail_on_policy_error: bool = True
    record_decisions: bool = True
    enable_audit: bool = True
    include_context_hash: bool = True
    metadata: JsonDict = field(default_factory=dict)


class PolicyEngine:
    """Main enterprise governance policy engine."""

    def __init__(
        self,
        repository: Optional[PolicyRepository] = None,
        *,
        config: Optional[PolicyEngineConfig] = None,
        audit_sink: Optional[PolicyAuditSink] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.repository = repository or InMemoryPolicyRepository()
        self.config = config or PolicyEngineConfig()
        self.audit = audit_sink or LoggingPolicyAuditSink()
        self.log = log or logger

    def register_policy(self, policy: GovernancePolicy, *, validate: bool = True) -> GovernancePolicy:
        if validate:
            report = self.validate_policies([policy])
            if not report.valid:
                raise PolicyValidationError(json.dumps(report.to_dict(), ensure_ascii=False))
        self.repository.upsert_policy(policy)
        self._audit("policy_registered", policy.to_dict())
        return policy

    def register_bundle(self, bundle: PolicyBundle, *, validate: bool = True) -> PolicyBundle:
        if validate:
            report = self.validate_policies(bundle.policies)
            if not report.valid:
                raise PolicyValidationError(json.dumps(report.to_dict(), ensure_ascii=False))
        self.repository.upsert_bundle(bundle)
        for policy in bundle.policies:
            self.repository.upsert_policy(policy)
        self._audit("policy_bundle_registered", bundle.to_dict())
        return bundle

    def get_policy(self, policy_id: str) -> GovernancePolicy:
        policy = self.repository.get_policy(policy_id)
        if not policy:
            raise PolicyNotFoundError(f"Policy not found: {policy_id}")
        return policy

    def evaluate(
        self,
        context: Mapping[str, Any],
        *,
        domain: Optional[PolicyDomain] = None,
        bundle_id: Optional[str] = None,
        combining_algorithm: Optional[CombiningAlgorithm] = None,
        as_of: Optional[dt.datetime] = None,
        dry_run: bool = False,
    ) -> PolicyDecision:
        started = time.time()
        algorithm = combining_algorithm or self.config.default_combining_algorithm
        policies: List[GovernancePolicy]

        if bundle_id:
            bundle = self.repository.get_bundle(bundle_id)
            if not bundle:
                raise PolicyNotFoundError(f"Policy bundle not found: {bundle_id}")
            policies = bundle.active_policies(as_of)
            algorithm = combining_algorithm or bundle.combining_algorithm
            if domain:
                policies = [policy for policy in policies if policy.domain == domain]
        else:
            policies = self.repository.list_policies(domain=domain, active_only=True)

        matches: List[PolicyMatch] = []
        errors: List[str] = []
        for policy in sorted(policies, key=lambda p: p.priority):
            try:
                effect, reasons = policy.evaluate(context, as_of=as_of)
                if effect != PolicyEffect.NOT_APPLICABLE:
                    matches.append(
                        PolicyMatch(
                            policy_id=policy.policy_id,
                            policy_name=policy.name,
                            effect=effect,
                            reasons=reasons,
                            priority=policy.priority,
                            obligations=list(policy.obligations),
                            advice=list(policy.advice),
                            control_ids=list(policy.control_ids),
                            requirement_ids=list(policy.requirement_ids),
                        )
                    )
            except Exception as exc:
                errors.append(f"{policy.policy_id}: {exc}")
                if self.config.fail_on_policy_error:
                    raise

        effect, reasons = combine_policy_matches(matches, algorithm)
        obligations = dedupe_obligations([ob for match in matches for ob in match.obligations])
        advice = dedupe_advice([adv for match in matches for adv in match.advice])
        if errors:
            reasons.extend(["policy_errors_detected", *errors])
            if effect == DecisionEffect.NOT_APPLICABLE:
                effect = DecisionEffect.INDETERMINATE

        decision = PolicyDecision(
            decision_id=str(uuid.uuid4()),
            effect=effect,
            matched_policies=matches,
            evaluated_policy_count=len(policies),
            applicable_policy_count=len(matches),
            combining_algorithm=algorithm,
            reasons=reasons,
            obligations=obligations,
            advice=advice,
            context_hash=stable_hash(context) if self.config.include_context_hash else None,
            duration_ms=round((time.time() - started) * 1000, 3),
            metadata={"domain": domain.value if domain else None, "bundle_id": bundle_id, "dry_run": dry_run},
        )
        decision.audit_hash = stable_hash(decision.to_dict())

        if self.config.record_decisions and not dry_run:
            self.repository.record_decision(decision)
        self._audit("policy_evaluated", decision.to_dict())
        return decision

    def evaluate_batch(
        self,
        contexts: Sequence[Mapping[str, Any]],
        *,
        domain: Optional[PolicyDomain] = None,
        bundle_id: Optional[str] = None,
        combining_algorithm: Optional[CombiningAlgorithm] = None,
        dry_run: bool = False,
    ) -> List[PolicyDecision]:
        return [
            self.evaluate(
                context,
                domain=domain,
                bundle_id=bundle_id,
                combining_algorithm=combining_algorithm,
                dry_run=dry_run,
            )
            for context in contexts
        ]

    def simulate(
        self,
        contexts: Sequence[Mapping[str, Any]],
        *,
        domain: Optional[PolicyDomain] = None,
        bundle_id: Optional[str] = None,
        combining_algorithm: Optional[CombiningAlgorithm] = None,
    ) -> PolicySimulationResult:
        simulation = PolicySimulationResult(simulation_id=str(uuid.uuid4()), contexts_evaluated=len(contexts), decisions=[] , effect_counts={})
        decisions = self.evaluate_batch(contexts, domain=domain, bundle_id=bundle_id, combining_algorithm=combining_algorithm, dry_run=True)
        simulation.decisions.extend(decisions)
        simulation.effect_counts = dict(Counter(decision.effect.value for decision in decisions))
        simulation.finish()
        self._audit("policy_simulation_completed", simulation.to_dict())
        return simulation

    def validate_policies(self, policies: Sequence[GovernancePolicy]) -> PolicyValidationReport:
        issues: List[PolicyValidationIssue] = []
        ids = [policy.policy_id for policy in policies]
        duplicates = [policy_id for policy_id, count in Counter(ids).items() if count > 1]
        for policy_id in duplicates:
            issues.append(PolicyValidationIssue("DUPLICATE_POLICY_ID", f"Duplicate policy id: {policy_id}", policy_id=policy_id))

        for policy in policies:
            if not policy.policy_id:
                issues.append(PolicyValidationIssue("MISSING_POLICY_ID", "Policy id is required"))
            if not policy.name:
                issues.append(PolicyValidationIssue("MISSING_POLICY_NAME", "Policy name is required", policy_id=policy.policy_id))
            if policy.priority < 0:
                issues.append(PolicyValidationIssue("INVALID_PRIORITY", "Policy priority cannot be negative", policy_id=policy.policy_id))
            issues.extend(validate_condition_group(policy.conditions, policy.policy_id))
            if policy.effect == PolicyEffect.ALLOW and any(ob.obligation_type == ObligationType.BLOCK_EXPORT for ob in policy.obligations):
                issues.append(
                    PolicyValidationIssue(
                        "CONFLICTING_OBLIGATION",
                        "ALLOW policy contains BLOCK_EXPORT obligation; verify intended behavior.",
                        policy_id=policy.policy_id,
                        severity=PolicySeverity.WARNING,
                    )
                )

        conflicts = detect_policy_conflicts(policies)
        issues.extend(conflicts)
        valid = not any(issue.severity in {PolicySeverity.ERROR, PolicySeverity.CRITICAL} for issue in issues)
        return PolicyValidationReport(valid=valid, issues=issues, policy_count=len(policies))

    def explain(self, decision_id: str) -> JsonDict:
        decisions = self.repository.list_decisions(limit=100000)
        decision = next((item for item in decisions if item.decision_id == decision_id), None)
        if not decision:
            raise PolicyEvaluationError(f"Decision not found: {decision_id}")
        return {
            "decision": decision.to_dict(),
            "summary": {
                "effect": decision.effect.value,
                "matched_policy_ids": [match.policy_id for match in decision.matched_policies],
                "obligation_types": [ob.obligation_type.value for ob in decision.obligations],
                "advice_count": len(decision.advice),
                "reasons": decision.reasons,
            },
        }

    def list_policies(self, domain: Optional[PolicyDomain] = None, active_only: bool = True) -> List[GovernancePolicy]:
        return self.repository.list_policies(domain=domain, active_only=active_only)

    def decision_history(self, limit: int = 1000) -> List[PolicyDecision]:
        return self.repository.list_decisions(limit=limit)

    def export_policies(self, *, indent: int = 2) -> str:
        return json.dumps([policy.to_dict() for policy in self.repository.list_policies(active_only=False)], ensure_ascii=False, indent=indent, default=str)

    def import_policies(self, payload: Union[str, Sequence[Mapping[str, Any]]], *, created_by: Optional[str] = None) -> List[GovernancePolicy]:
        data = json.loads(payload) if isinstance(payload, str) else payload
        policies = [policy_from_dict(item) for item in data]
        report = self.validate_policies(policies)
        if not report.valid:
            raise PolicyValidationError(json.dumps(report.to_dict(), ensure_ascii=False))
        for policy in policies:
            self.repository.upsert_policy(policy)
        self._audit("policies_imported", {"count": len(policies), "created_by": created_by})
        return policies

    def _audit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if self.config.enable_audit:
            self.audit.emit(event_type, to_json_safe(payload))


# -----------------------------------------------------------------------------
# Combining algorithms
# -----------------------------------------------------------------------------


def combine_policy_matches(matches: Sequence[PolicyMatch], algorithm: CombiningAlgorithm) -> Tuple[DecisionEffect, List[str]]:
    if not matches:
        return DecisionEffect.NOT_APPLICABLE, ["no_applicable_policy"]

    if algorithm == CombiningAlgorithm.DENY_OVERRIDES:
        if any(match.effect == PolicyEffect.DENY for match in matches):
            return DecisionEffect.DENY, ["deny_overrides", "deny_policy_matched"]
        if any(match.effect == PolicyEffect.ALLOW for match in matches):
            return DecisionEffect.ALLOW, ["deny_overrides", "allow_policy_matched"]

    if algorithm == CombiningAlgorithm.ALLOW_OVERRIDES:
        if any(match.effect == PolicyEffect.ALLOW for match in matches):
            return DecisionEffect.ALLOW, ["allow_overrides", "allow_policy_matched"]
        if any(match.effect == PolicyEffect.DENY for match in matches):
            return DecisionEffect.DENY, ["allow_overrides", "deny_policy_matched"]

    if algorithm == CombiningAlgorithm.FIRST_APPLICABLE:
        first = sorted(matches, key=lambda match: match.priority)[0]
        return DecisionEffect(first.effect.value), ["first_applicable", f"selected_policy={first.policy_id}"]

    if algorithm == CombiningAlgorithm.PRIORITY:
        selected = sorted(matches, key=lambda match: match.priority)[0]
        return DecisionEffect(selected.effect.value), ["priority", f"selected_policy={selected.policy_id}"]

    if algorithm == CombiningAlgorithm.CONSENSUS:
        counts = Counter(match.effect for match in matches)
        if counts[PolicyEffect.DENY] > counts[PolicyEffect.ALLOW]:
            return DecisionEffect.DENY, ["consensus", "deny_majority"]
        if counts[PolicyEffect.ALLOW] > counts[PolicyEffect.DENY]:
            return DecisionEffect.ALLOW, ["consensus", "allow_majority"]
        return DecisionEffect.INDETERMINATE, ["consensus", "tie"]

    return DecisionEffect.INDETERMINATE, ["unsupported_combining_algorithm"]


# -----------------------------------------------------------------------------
# Validation and conflict detection
# -----------------------------------------------------------------------------


def validate_condition_group(group: ConditionGroup, policy_id: Optional[str]) -> List[PolicyValidationIssue]:
    issues: List[PolicyValidationIssue] = []
    if group.operator == LogicalOperator.NOT and len(group.conditions) != 1:
        issues.append(PolicyValidationIssue("INVALID_NOT_GROUP", "NOT group requires exactly one child", policy_id=policy_id))
    for condition in group.conditions:
        if isinstance(condition, PolicyCondition):
            if not condition.path and condition.operator not in {ConditionOperator.EXISTS, ConditionOperator.MISSING}:
                issues.append(PolicyValidationIssue("MISSING_CONDITION_PATH", "Condition path is required", policy_id=policy_id))
            if condition.operator == ConditionOperator.BETWEEN and len(ensure_collection(condition.value)) != 2:
                issues.append(PolicyValidationIssue("INVALID_BETWEEN", "BETWEEN operator requires two values", policy_id=policy_id))
        else:
            issues.extend(validate_condition_group(condition, policy_id))
    return issues


def detect_policy_conflicts(policies: Sequence[GovernancePolicy]) -> List[PolicyValidationIssue]:
    issues: List[PolicyValidationIssue] = []
    active = [policy for policy in policies if policy.status == PolicyStatus.ACTIVE]
    by_domain_priority: Dict[Tuple[PolicyDomain, int], List[GovernancePolicy]] = defaultdict(list)
    for policy in active:
        by_domain_priority[(policy.domain, policy.priority)].append(policy)
    for (domain, priority), grouped in by_domain_priority.items():
        effects = {policy.effect for policy in grouped}
        if PolicyEffect.ALLOW in effects and PolicyEffect.DENY in effects:
            issues.append(
                PolicyValidationIssue(
                    code="POTENTIAL_PRIORITY_CONFLICT",
                    message=f"ALLOW and DENY policies share same domain/priority: {domain.value}/{priority}",
                    policy_id=",".join(policy.policy_id for policy in grouped),
                    severity=PolicySeverity.WARNING,
                )
            )
    return issues


# -----------------------------------------------------------------------------
# Conversion helpers
# -----------------------------------------------------------------------------


def policy_from_dict(data: Mapping[str, Any]) -> GovernancePolicy:
    payload = dict(data)
    payload["domain"] = PolicyDomain(payload["domain"])
    payload["effect"] = PolicyEffect(payload["effect"])
    payload["status"] = PolicyStatus(payload.get("status", PolicyStatus.ACTIVE))
    payload["conditions"] = condition_group_from_dict(payload.get("conditions") or {"operator": "all", "conditions": []})
    payload["obligations"] = tuple(obligation_from_dict(item) for item in payload.get("obligations", []))
    payload["advice"] = tuple(advice_from_dict(item) for item in payload.get("advice", []))
    for key in ("tags", "control_ids", "requirement_ids"):
        payload[key] = tuple(payload.get(key, ()))
    for key in ("valid_from", "valid_until"):
        if payload.get(key) and isinstance(payload[key], str):
            payload[key] = dt.datetime.fromisoformat(payload[key].replace("Z", "+00:00"))
    return GovernancePolicy(**payload)


def condition_group_from_dict(data: Mapping[str, Any]) -> ConditionGroup:
    children = []
    for item in data.get("conditions", []):
        if "conditions" in item:
            children.append(condition_group_from_dict(item))
        else:
            payload = dict(item)
            payload["operator"] = ConditionOperator(payload["operator"])
            children.append(PolicyCondition(**payload))
    return ConditionGroup(operator=LogicalOperator(data.get("operator", "all")), conditions=tuple(children), description=data.get("description", ""))


def obligation_from_dict(data: Mapping[str, Any]) -> PolicyObligation:
    payload = dict(data)
    payload["obligation_type"] = ObligationType(payload["obligation_type"])
    return PolicyObligation(**payload)


def advice_from_dict(data: Mapping[str, Any]) -> PolicyAdvice:
    payload = dict(data)
    payload["severity"] = PolicySeverity(payload.get("severity", PolicySeverity.INFO))
    return PolicyAdvice(**payload)


# -----------------------------------------------------------------------------
# Utility helpers
# -----------------------------------------------------------------------------


def get_path(data: Mapping[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return default
            current = current[index]
        else:
            return default
    return current


def ensure_collection(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(normalize_value(item) for item in value)
    if isinstance(value, set):
        return {normalize_value(item) for item in value}
    return value


def contains(container: Any, value: Any) -> bool:
    if isinstance(container, Mapping):
        return value in container or value in container.values()
    if isinstance(container, (list, tuple, set)):
        return value in container
    if isinstance(container, str):
        return str(value) in container
    return False


def dedupe_obligations(obligations: Sequence[PolicyObligation]) -> List[PolicyObligation]:
    seen: Set[str] = set()
    output: List[PolicyObligation] = []
    for obligation in obligations:
        key = stable_hash(obligation.to_dict())
        if key not in seen:
            seen.add(key)
            output.append(obligation)
    return output


def dedupe_advice(advice: Sequence[PolicyAdvice]) -> List[PolicyAdvice]:
    seen: Set[str] = set()
    output: List[PolicyAdvice] = []
    for item in advice:
        key = stable_hash(item.to_dict())
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output


def stable_hash(value: Any) -> str:
    raw = json.dumps(to_json_safe(value), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def to_json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return to_json_safe(dataclasses.asdict(value))
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_safe(v) for v in value]
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return value


# -----------------------------------------------------------------------------
# Default policy factory
# -----------------------------------------------------------------------------


def build_default_policy_engine() -> PolicyEngine:
    engine = PolicyEngine()
    policies = [
        GovernancePolicy(
            policy_id="deny_inactive_subjects",
            name="Deny inactive subjects",
            domain=PolicyDomain.ACCESS,
            effect=PolicyEffect.DENY,
            priority=1,
            conditions=ConditionGroup(
                LogicalOperator.ALL,
                (PolicyCondition("subject.status", ConditionOperator.NE, "active"),),
            ),
            obligations=(PolicyObligation("audit-deny", ObligationType.AUDIT, {"level": "security"}),),
            advice=(PolicyAdvice("reactivate-or-remove", "Subject is not active; access must be denied.", PolicySeverity.ERROR),),
            control_ids=("AC-001",),
        ),
        GovernancePolicy(
            policy_id="allow_analyst_read_confidential_for_analytics",
            name="Allow analysts to read confidential data for analytics",
            domain=PolicyDomain.ACCESS,
            effect=PolicyEffect.ALLOW,
            priority=50,
            conditions=ConditionGroup(
                LogicalOperator.ALL,
                (
                    PolicyCondition("action", ConditionOperator.EQ, "read"),
                    PolicyCondition("subject.roles", ConditionOperator.CONTAINS, "analyst"),
                    PolicyCondition("resource.sensitivity", ConditionOperator.IN, ["internal", "confidential"]),
                    PolicyCondition("purpose", ConditionOperator.IN, ["analytics", "reporting"]),
                ),
            ),
            obligations=(
                PolicyObligation("mfa", ObligationType.REQUIRE_MFA, {"reason": "confidential_data_access"}),
                PolicyObligation("audit", ObligationType.AUDIT, {"event": "confidential_read"}),
            ),
            control_ids=("AC-002", "DG-ACCESS-REVIEW"),
            requirement_ids=("LGPD-ACCESS-01",),
        ),
        GovernancePolicy(
            policy_id="deny_export_restricted_data",
            name="Deny export of restricted data",
            domain=PolicyDomain.ACCESS,
            effect=PolicyEffect.DENY,
            priority=10,
            conditions=ConditionGroup(
                LogicalOperator.ALL,
                (
                    PolicyCondition("action", ConditionOperator.EQ, "export"),
                    PolicyCondition("resource.sensitivity", ConditionOperator.IN, ["restricted", "highly_restricted"]),
                ),
            ),
            obligations=(PolicyObligation("block-export", ObligationType.BLOCK_EXPORT, {"reason": "restricted_data"}),),
            advice=(PolicyAdvice("use-approved-export", "Use approved export workflow with explicit approval.", PolicySeverity.WARNING),),
            control_ids=("DLP-001",),
        ),
        GovernancePolicy(
            policy_id="mask_pii_in_nonprod",
            name="Mask PII in non-production environments",
            domain=PolicyDomain.MASKING,
            effect=PolicyEffect.ALLOW,
            priority=20,
            conditions=ConditionGroup(
                LogicalOperator.ALL,
                (
                    PolicyCondition("environment", ConditionOperator.NOT_IN, ["prod", "production"]),
                    PolicyCondition("resource.classifications", ConditionOperator.INTERSECTS, ["pii", "personal"]),
                ),
            ),
            obligations=(PolicyObligation("mask-pii", ObligationType.MASK_FIELDS, {"classification": "pii", "strategy": "partial"}),),
            control_ids=("PRIV-001",),
        ),
        GovernancePolicy(
            policy_id="encrypt_highly_restricted",
            name="Encrypt highly restricted data",
            domain=PolicyDomain.ENCRYPTION,
            effect=PolicyEffect.ALLOW,
            priority=20,
            conditions=ConditionGroup(
                LogicalOperator.ALL,
                (PolicyCondition("resource.sensitivity", ConditionOperator.EQ, "highly_restricted"),),
            ),
            obligations=(PolicyObligation("encrypt-fields", ObligationType.ENCRYPT_FIELDS, {"key_alias": "data-restricted"}),),
            control_ids=("ENC-001",),
        ),
    ]
    bundle = PolicyBundle(
        bundle_id="default_governance_bundle",
        name="Default Governance Policy Bundle",
        policies=tuple(policies),
        combining_algorithm=CombiningAlgorithm.DENY_OVERRIDES,
        created_by="system",
    )
    engine.register_bundle(bundle)
    return engine


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    engine = build_default_policy_engine()
    context = {
        "subject": {"id": "u-100", "roles": ["analyst"], "status": "active", "department": "finance"},
        "resource": {"id": "sales_daily", "sensitivity": "confidential", "domain": "finance", "classifications": ["financial_sensitive"]},
        "action": "read",
        "purpose": "analytics",
        "environment": "prod",
    }
    decision = engine.evaluate(context, domain=PolicyDomain.ACCESS, bundle_id="default_governance_bundle")
    print(json.dumps(decision.to_dict(), indent=2, ensure_ascii=False, default=str))

    simulation = engine.simulate(
        [
            context,
            {**context, "action": "export", "resource": {**context["resource"], "sensitivity": "restricted"}},
            {**context, "subject": {**context["subject"], "status": "inactive"}},
        ],
        domain=PolicyDomain.ACCESS,
        bundle_id="default_governance_bundle",
    )
    print(json.dumps(simulation.to_dict(), indent=2, ensure_ascii=False, default=str))

"""
access_governance.py
====================

Enterprise-grade access governance module for data platforms.

Core capabilities
-----------------
- RBAC and ABAC access policy evaluation.
- Data asset sensitivity-aware access decisions.
- Least-privilege and purpose-based access checks.
- Segregation of Duties (SoD) conflict detection.
- Access review / recertification workflow primitives.
- Entitlement inventory and risk scoring.
- Break-glass access with mandatory justification.
- Audit trail for policy decisions and reviewer actions.
- Pluggable identity, entitlement and policy repositories.

This module is intentionally vendor-neutral and can be integrated with IAM,
IdP, catalog, SIEM, ticketing and governance platforms.
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
AttributePredicate = Callable[[Mapping[str, Any]], bool]


class AccessGovernanceError(Exception):
    """Base exception for access governance failures."""


class PolicyEvaluationError(AccessGovernanceError):
    """Raised when a policy cannot be evaluated."""


class AccessReviewError(AccessGovernanceError):
    """Raised when access review workflow fails."""


class AccessDecision(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"
    CONDITIONAL_ALLOW = "conditional_allow"
    MANUAL_REVIEW = "manual_review"


class AccessEffect(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"


class AccessAction(str, enum.Enum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    EXPORT = "export"
    SHARE = "share"
    ADMIN = "admin"
    EXECUTE = "execute"
    APPROVE = "approve"


class SensitivityLevel(str, enum.Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    HIGHLY_RESTRICTED = "highly_restricted"


class ReviewDecision(str, enum.Enum):
    APPROVE = "approve"
    REVOKE = "revoke"
    MODIFY = "modify"
    ESCALATE = "escalate"
    DEFER = "defer"


class ReviewStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MatchMode(str, enum.Enum):
    ANY = "any"
    ALL = "all"


@dataclass(frozen=True)
class AccessSubject:
    """Identity requesting access."""

    subject_id: str
    display_name: str = ""
    email: Optional[str] = None
    roles: Set[str] = field(default_factory=set)
    groups: Set[str] = field(default_factory=set)
    department: Optional[str] = None
    manager_id: Optional[str] = None
    employment_status: str = "active"
    attributes: JsonDict = field(default_factory=dict)

    def to_context(self) -> JsonDict:
        return {
            "subject_id": self.subject_id,
            "display_name": self.display_name,
            "email": self.email,
            "roles": sorted(self.roles),
            "groups": sorted(self.groups),
            "department": self.department,
            "manager_id": self.manager_id,
            "employment_status": self.employment_status,
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class DataAsset:
    """Data resource being accessed."""

    asset_id: str
    name: str
    asset_type: str = "dataset"
    domain: Optional[str] = None
    owner_id: Optional[str] = None
    steward_id: Optional[str] = None
    sensitivity: SensitivityLevel = SensitivityLevel.INTERNAL
    classifications: Set[str] = field(default_factory=set)
    tags: Set[str] = field(default_factory=set)
    attributes: JsonDict = field(default_factory=dict)

    def to_context(self) -> JsonDict:
        return {
            "asset_id": self.asset_id,
            "name": self.name,
            "asset_type": self.asset_type,
            "domain": self.domain,
            "owner_id": self.owner_id,
            "steward_id": self.steward_id,
            "sensitivity": self.sensitivity.value,
            "classifications": sorted(self.classifications),
            "tags": sorted(self.tags),
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class AccessRequest:
    """Access evaluation request."""

    subject: AccessSubject
    asset: DataAsset
    action: AccessAction
    purpose: Optional[str] = None
    justification: Optional[str] = None
    environment: str = "prod"
    requested_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    break_glass: bool = False
    context: JsonDict = field(default_factory=dict)

    def to_context(self) -> JsonDict:
        return {
            "request_id": self.request_id,
            "subject": self.subject.to_context(),
            "asset": self.asset.to_context(),
            "action": self.action.value,
            "purpose": self.purpose,
            "justification": self.justification,
            "environment": self.environment,
            "requested_at": self.requested_at.isoformat(),
            "break_glass": self.break_glass,
            "context": dict(self.context),
        }


@dataclass(frozen=True)
class PolicyCondition:
    """Declarative ABAC-like condition."""

    path: str
    operator: str
    value: Any
    case_sensitive: bool = False

    def evaluate(self, context: Mapping[str, Any]) -> bool:
        actual = get_path(context, self.path)
        expected = self.value

        if not self.case_sensitive and isinstance(actual, str) and isinstance(expected, str):
            actual_cmp = actual.lower()
            expected_cmp = expected.lower()
        else:
            actual_cmp = actual
            expected_cmp = expected

        op = self.operator.lower()
        if op in {"eq", "=="}:
            return actual_cmp == expected_cmp
        if op in {"ne", "!="}:
            return actual_cmp != expected_cmp
        if op == "in":
            if isinstance(expected_cmp, (list, tuple, set)):
                return actual_cmp in expected_cmp
            return False
        if op == "not_in":
            if isinstance(expected_cmp, (list, tuple, set)):
                return actual_cmp not in expected_cmp
            return True
        if op == "contains":
            if isinstance(actual_cmp, (list, tuple, set)):
                return expected_cmp in actual_cmp
            if isinstance(actual_cmp, str):
                return str(expected_cmp) in actual_cmp
            return False
        if op == "intersects":
            if not isinstance(actual, (list, tuple, set)) or not isinstance(expected, (list, tuple, set)):
                return False
            return bool(set(actual).intersection(set(expected)))
        if op == "exists":
            return actual is not None
        if op == "missing":
            return actual is None
        if op in {"gt", ">"}:
            return actual_cmp > expected_cmp
        if op in {"gte", ">="}:
            return actual_cmp >= expected_cmp
        if op in {"lt", "<"}:
            return actual_cmp < expected_cmp
        if op in {"lte", "<="}:
            return actual_cmp <= expected_cmp
        if op == "regex":
            flags = 0 if self.case_sensitive else re.IGNORECASE
            return bool(re.search(str(expected), str(actual), flags=flags))
        raise PolicyEvaluationError(f"Unsupported condition operator: {self.operator}")


@dataclass(frozen=True)
class AccessPolicy:
    """Access governance policy."""

    policy_id: str
    name: str
    effect: AccessEffect
    actions: Set[AccessAction] = field(default_factory=set)
    roles: Set[str] = field(default_factory=set)
    groups: Set[str] = field(default_factory=set)
    asset_ids: Set[str] = field(default_factory=set)
    asset_tags: Set[str] = field(default_factory=set)
    asset_classifications: Set[str] = field(default_factory=set)
    sensitivity_levels: Set[SensitivityLevel] = field(default_factory=set)
    purposes: Set[str] = field(default_factory=set)
    conditions: Sequence[PolicyCondition] = field(default_factory=tuple)
    condition_match_mode: MatchMode = MatchMode.ALL
    priority: int = 100
    enabled: bool = True
    requires_mfa: bool = False
    requires_approval: bool = False
    max_duration_hours: Optional[int] = None
    description: str = ""
    metadata: JsonDict = field(default_factory=dict)

    def matches(self, request: AccessRequest) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        context = request.to_context()

        if not self.enabled:
            return False, ["policy_disabled"]

        if self.actions and request.action not in self.actions:
            return False, ["action_not_matched"]
        reasons.append("action_matched")

        if self.roles and not request.subject.roles.intersection(self.roles):
            return False, ["role_not_matched"]
        if self.roles:
            reasons.append("role_matched")

        if self.groups and not request.subject.groups.intersection(self.groups):
            return False, ["group_not_matched"]
        if self.groups:
            reasons.append("group_matched")

        if self.asset_ids and request.asset.asset_id not in self.asset_ids:
            return False, ["asset_id_not_matched"]
        if self.asset_ids:
            reasons.append("asset_id_matched")

        if self.asset_tags and not request.asset.tags.intersection(self.asset_tags):
            return False, ["asset_tag_not_matched"]
        if self.asset_tags:
            reasons.append("asset_tag_matched")

        if self.asset_classifications and not request.asset.classifications.intersection(self.asset_classifications):
            return False, ["classification_not_matched"]
        if self.asset_classifications:
            reasons.append("classification_matched")

        if self.sensitivity_levels and request.asset.sensitivity not in self.sensitivity_levels:
            return False, ["sensitivity_not_matched"]
        if self.sensitivity_levels:
            reasons.append("sensitivity_matched")

        if self.purposes and (request.purpose or "") not in self.purposes:
            return False, ["purpose_not_matched"]
        if self.purposes:
            reasons.append("purpose_matched")

        if self.conditions:
            outcomes = [condition.evaluate(context) for condition in self.conditions]
            matched = all(outcomes) if self.condition_match_mode == MatchMode.ALL else any(outcomes)
            if not matched:
                return False, ["conditions_not_matched"]
            reasons.append("conditions_matched")

        return True, reasons

    def to_dict(self) -> JsonDict:
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "effect": self.effect.value,
            "actions": sorted(action.value for action in self.actions),
            "roles": sorted(self.roles),
            "groups": sorted(self.groups),
            "asset_ids": sorted(self.asset_ids),
            "asset_tags": sorted(self.asset_tags),
            "asset_classifications": sorted(self.asset_classifications),
            "sensitivity_levels": sorted(level.value for level in self.sensitivity_levels),
            "purposes": sorted(self.purposes),
            "priority": self.priority,
            "enabled": self.enabled,
            "requires_mfa": self.requires_mfa,
            "requires_approval": self.requires_approval,
            "max_duration_hours": self.max_duration_hours,
            "description": self.description,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SoDRule:
    """Segregation of Duties rule."""

    rule_id: str
    name: str
    conflicting_roles: Set[str] = field(default_factory=set)
    conflicting_groups: Set[str] = field(default_factory=set)
    conflicting_actions: Set[AccessAction] = field(default_factory=set)
    risk_level: RiskLevel = RiskLevel.HIGH
    enabled: bool = True
    description: str = ""

    def evaluate(self, request: AccessRequest) -> Optional[str]:
        if not self.enabled:
            return None
        role_conflict = self.conflicting_roles and self.conflicting_roles.issubset(request.subject.roles)
        group_conflict = self.conflicting_groups and self.conflicting_groups.issubset(request.subject.groups)
        action_conflict = self.conflicting_actions and request.action in self.conflicting_actions
        if role_conflict or group_conflict or action_conflict:
            return self.name
        return None


@dataclass
class PolicyDecision:
    request_id: str
    decision: AccessDecision
    risk_level: RiskLevel
    matched_policies: List[str] = field(default_factory=list)
    denied_by_policies: List[str] = field(default_factory=list)
    conditions: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    obligations: List[str] = field(default_factory=list)
    expires_at: Optional[dt.datetime] = None
    evaluated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    audit_hash: Optional[str] = None

    def to_dict(self) -> JsonDict:
        return {
            "decision_id": self.decision_id,
            "request_id": self.request_id,
            "decision": self.decision.value,
            "risk_level": self.risk_level.value,
            "matched_policies": list(self.matched_policies),
            "denied_by_policies": list(self.denied_by_policies),
            "conditions": list(self.conditions),
            "reasons": list(self.reasons),
            "obligations": list(self.obligations),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "evaluated_at": self.evaluated_at.isoformat(),
            "audit_hash": self.audit_hash,
        }


@dataclass(frozen=True)
class Entitlement:
    entitlement_id: str
    subject_id: str
    asset_id: str
    actions: Set[AccessAction]
    granted_by: Optional[str] = None
    granted_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    expires_at: Optional[dt.datetime] = None
    purpose: Optional[str] = None
    source: str = "manual"
    metadata: JsonDict = field(default_factory=dict)

    def is_expired(self, now: Optional[dt.datetime] = None) -> bool:
        if self.expires_at is None:
            return False
        now = now or dt.datetime.now(dt.timezone.utc)
        return self.expires_at <= now

    def to_dict(self) -> JsonDict:
        return {
            "entitlement_id": self.entitlement_id,
            "subject_id": self.subject_id,
            "asset_id": self.asset_id,
            "actions": sorted(action.value for action in self.actions),
            "granted_by": self.granted_by,
            "granted_at": self.granted_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "purpose": self.purpose,
            "source": self.source,
            "metadata": dict(self.metadata),
        }


@dataclass
class AccessReviewItem:
    item_id: str
    entitlement: Entitlement
    subject: Optional[AccessSubject] = None
    asset: Optional[DataAsset] = None
    reviewer_id: Optional[str] = None
    decision: Optional[ReviewDecision] = None
    decision_reason: Optional[str] = None
    decided_at: Optional[dt.datetime] = None
    risk_level: RiskLevel = RiskLevel.LOW
    recommendation: Optional[ReviewDecision] = None
    recommendation_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return {
            "item_id": self.item_id,
            "entitlement": self.entitlement.to_dict(),
            "subject": self.subject.to_context() if self.subject else None,
            "asset": self.asset.to_context() if self.asset else None,
            "reviewer_id": self.reviewer_id,
            "decision": self.decision.value if self.decision else None,
            "decision_reason": self.decision_reason,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "risk_level": self.risk_level.value,
            "recommendation": self.recommendation.value if self.recommendation else None,
            "recommendation_reasons": list(self.recommendation_reasons),
        }


@dataclass
class AccessReviewCampaign:
    campaign_id: str
    name: str
    items: List[AccessReviewItem]
    owner_id: str
    due_at: dt.datetime
    status: ReviewStatus = ReviewStatus.OPEN
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    completed_at: Optional[dt.datetime] = None
    metadata: JsonDict = field(default_factory=dict)

    def completion_rate(self) -> float:
        if not self.items:
            return 0.0
        completed = sum(1 for item in self.items if item.decision is not None)
        return round(completed / len(self.items), 6)

    def update_status(self, now: Optional[dt.datetime] = None) -> None:
        now = now or dt.datetime.now(dt.timezone.utc)
        if self.status == ReviewStatus.CANCELLED:
            return
        if self.items and all(item.decision is not None for item in self.items):
            self.status = ReviewStatus.COMPLETED
            self.completed_at = self.completed_at or now
        elif now > self.due_at:
            self.status = ReviewStatus.OVERDUE
        elif any(item.decision is not None for item in self.items):
            self.status = ReviewStatus.IN_PROGRESS
        else:
            self.status = ReviewStatus.OPEN

    def summary(self) -> JsonDict:
        self.update_status()
        decisions = Counter(item.decision.value for item in self.items if item.decision)
        risks = Counter(item.risk_level.value for item in self.items)
        return {
            "campaign_id": self.campaign_id,
            "name": self.name,
            "owner_id": self.owner_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "due_at": self.due_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total_items": len(self.items),
            "completion_rate": self.completion_rate(),
            "decisions": dict(decisions),
            "risks": dict(risks),
        }


@dataclass
class AccessReviewResult:
    campaign: AccessReviewCampaign
    revoked: List[Entitlement] = field(default_factory=list)
    approved: List[Entitlement] = field(default_factory=list)
    modified: List[Entitlement] = field(default_factory=list)
    escalated: List[Entitlement] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return {
            "campaign": self.campaign.summary(),
            "revoked": [e.to_dict() for e in self.revoked],
            "approved": [e.to_dict() for e in self.approved],
            "modified": [e.to_dict() for e in self.modified],
            "escalated": [e.to_dict() for e in self.escalated],
        }


@runtime_checkable
class AuditSink(Protocol):
    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        ...


class LoggingAuditSink:
    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self.log.info("access_governance_audit", extra={"event_type": event_type, "payload": dict(payload)})


class InMemoryGovernanceRepository:
    """Simple in-memory repository for policies, identities, assets and entitlements."""

    def __init__(self) -> None:
        self.subjects: Dict[str, AccessSubject] = {}
        self.assets: Dict[str, DataAsset] = {}
        self.policies: Dict[str, AccessPolicy] = {}
        self.sod_rules: Dict[str, SoDRule] = {}
        self.entitlements: Dict[str, Entitlement] = {}
        self.campaigns: Dict[str, AccessReviewCampaign] = {}

    def upsert_subject(self, subject: AccessSubject) -> None:
        self.subjects[subject.subject_id] = subject

    def upsert_asset(self, asset: DataAsset) -> None:
        self.assets[asset.asset_id] = asset

    def upsert_policy(self, policy: AccessPolicy) -> None:
        self.policies[policy.policy_id] = policy

    def upsert_sod_rule(self, rule: SoDRule) -> None:
        self.sod_rules[rule.rule_id] = rule

    def upsert_entitlement(self, entitlement: Entitlement) -> None:
        self.entitlements[entitlement.entitlement_id] = entitlement

    def delete_entitlement(self, entitlement_id: str) -> bool:
        return self.entitlements.pop(entitlement_id, None) is not None

    def active_entitlements(self, now: Optional[dt.datetime] = None) -> List[Entitlement]:
        return [ent for ent in self.entitlements.values() if not ent.is_expired(now)]


class AccessRiskScorer:
    """Risk scoring logic for access decisions and recertification."""

    SENSITIVITY_WEIGHTS = {
        SensitivityLevel.PUBLIC: 0,
        SensitivityLevel.INTERNAL: 10,
        SensitivityLevel.CONFIDENTIAL: 30,
        SensitivityLevel.RESTRICTED: 55,
        SensitivityLevel.HIGHLY_RESTRICTED: 80,
    }
    ACTION_WEIGHTS = {
        AccessAction.READ: 5,
        AccessAction.WRITE: 20,
        AccessAction.DELETE: 40,
        AccessAction.EXPORT: 45,
        AccessAction.SHARE: 50,
        AccessAction.ADMIN: 65,
        AccessAction.EXECUTE: 25,
        AccessAction.APPROVE: 30,
    }

    def score_request(self, request: AccessRequest, sod_conflicts: Sequence[str] = ()) -> Tuple[int, RiskLevel, List[str]]:
        score = 0
        reasons: List[str] = []
        score += self.SENSITIVITY_WEIGHTS.get(request.asset.sensitivity, 10)
        score += self.ACTION_WEIGHTS.get(request.action, 10)

        if request.asset.classifications.intersection({"pii", "phi", "pci", "financial_sensitive"}):
            score += 20
            reasons.append("sensitive_classification")
        if request.break_glass:
            score += 25
            reasons.append("break_glass")
        if request.subject.employment_status.lower() != "active":
            score += 60
            reasons.append("inactive_subject")
        if request.action in {AccessAction.EXPORT, AccessAction.SHARE, AccessAction.ADMIN}:
            reasons.append("privileged_or_exfiltration_capable_action")
        if sod_conflicts:
            score += 40
            reasons.append("sod_conflict")

        score = min(score, 100)
        return score, self.to_risk_level(score), reasons

    def score_entitlement(self, entitlement: Entitlement, subject: Optional[AccessSubject], asset: Optional[DataAsset]) -> Tuple[int, RiskLevel, List[str]]:
        if asset and subject:
            action = max(entitlement.actions, key=lambda action_: self.ACTION_WEIGHTS.get(action_, 0))
            request = AccessRequest(subject=subject, asset=asset, action=action, purpose=entitlement.purpose)
            return self.score_request(request)
        score = 40
        reasons = ["missing_subject_or_asset_context"]
        return score, self.to_risk_level(score), reasons

    @staticmethod
    def to_risk_level(score: int) -> RiskLevel:
        if score >= 85:
            return RiskLevel.CRITICAL
        if score >= 60:
            return RiskLevel.HIGH
        if score >= 30:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW


class AccessGovernanceEngine:
    """Main access governance engine."""

    def __init__(
        self,
        repository: Optional[InMemoryGovernanceRepository] = None,
        *,
        risk_scorer: Optional[AccessRiskScorer] = None,
        audit_sink: Optional[AuditSink] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.repository = repository or InMemoryGovernanceRepository()
        self.risk_scorer = risk_scorer or AccessRiskScorer()
        self.audit = audit_sink or LoggingAuditSink()
        self.log = log or logger

    def evaluate_access(self, request: AccessRequest) -> PolicyDecision:
        """Evaluate access request using deny-overrides semantics."""
        context = request.to_context()
        matched_allow: List[AccessPolicy] = []
        matched_deny: List[AccessPolicy] = []
        reasons: List[str] = []
        obligations: List[str] = []
        conditions: List[str] = []

        policies = sorted(self.repository.policies.values(), key=lambda policy: policy.priority)
        for policy in policies:
            matched, policy_reasons = policy.matches(request)
            if not matched:
                continue
            reasons.extend(f"{policy.name}:{reason}" for reason in policy_reasons)
            if policy.effect == AccessEffect.DENY:
                matched_deny.append(policy)
            else:
                matched_allow.append(policy)
                if policy.requires_mfa:
                    obligations.append("mfa_required")
                if policy.requires_approval:
                    obligations.append("approval_required")
                if policy.max_duration_hours:
                    conditions.append(f"max_duration_hours={policy.max_duration_hours}")

        sod_conflicts = self.detect_sod_conflicts(request)
        risk_score, risk_level, risk_reasons = self.risk_scorer.score_request(request, sod_conflicts=sod_conflicts)
        reasons.extend(risk_reasons)

        if request.break_glass:
            if not request.justification or len(request.justification.strip()) < 10:
                decision = AccessDecision.DENY
                reasons.append("break_glass_requires_justification")
            else:
                decision = AccessDecision.CONDITIONAL_ALLOW
                obligations.extend(["break_glass_audit", "post_access_review_required"])
        elif matched_deny:
            decision = AccessDecision.DENY
        elif sod_conflicts:
            decision = AccessDecision.MANUAL_REVIEW
            obligations.append("sod_exception_approval_required")
            reasons.extend(f"sod_conflict:{conflict}" for conflict in sod_conflicts)
        elif matched_allow:
            if obligations or risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
                decision = AccessDecision.CONDITIONAL_ALLOW if "approval_required" not in obligations else AccessDecision.MANUAL_REVIEW
            else:
                decision = AccessDecision.ALLOW
        else:
            decision = AccessDecision.DENY
            reasons.append("no_allow_policy_matched")

        expires_at = self._compute_expiration(matched_allow)
        policy_decision = PolicyDecision(
            request_id=request.request_id,
            decision=decision,
            risk_level=risk_level,
            matched_policies=[policy.policy_id for policy in matched_allow],
            denied_by_policies=[policy.policy_id for policy in matched_deny],
            conditions=sorted(set(conditions)),
            reasons=sorted(set(reasons)),
            obligations=sorted(set(obligations)),
            expires_at=expires_at,
        )
        policy_decision.audit_hash = stable_hash({"request": context, "decision": policy_decision.to_dict(), "risk_score": risk_score})
        self.audit.emit("access_evaluated", {"request": context, "decision": policy_decision.to_dict(), "risk_score": risk_score})
        return policy_decision

    def grant_entitlement(
        self,
        *,
        subject_id: str,
        asset_id: str,
        actions: Set[AccessAction],
        granted_by: Optional[str],
        purpose: Optional[str] = None,
        duration_hours: Optional[int] = None,
        source: str = "governance_engine",
        metadata: Optional[JsonDict] = None,
    ) -> Entitlement:
        now = dt.datetime.now(dt.timezone.utc)
        entitlement = Entitlement(
            entitlement_id=str(uuid.uuid4()),
            subject_id=subject_id,
            asset_id=asset_id,
            actions=set(actions),
            granted_by=granted_by,
            granted_at=now,
            expires_at=now + dt.timedelta(hours=duration_hours) if duration_hours else None,
            purpose=purpose,
            source=source,
            metadata=metadata or {},
        )
        self.repository.upsert_entitlement(entitlement)
        self.audit.emit("entitlement_granted", entitlement.to_dict())
        return entitlement

    def revoke_entitlement(self, entitlement_id: str, *, revoked_by: Optional[str] = None, reason: Optional[str] = None) -> bool:
        entitlement = self.repository.entitlements.get(entitlement_id)
        deleted = self.repository.delete_entitlement(entitlement_id)
        if deleted:
            self.audit.emit(
                "entitlement_revoked",
                {
                    "entitlement": entitlement.to_dict() if entitlement else None,
                    "revoked_by": revoked_by,
                    "reason": reason,
                    "revoked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                },
            )
        return deleted

    def detect_sod_conflicts(self, request: AccessRequest) -> List[str]:
        conflicts: List[str] = []
        for rule in self.repository.sod_rules.values():
            conflict = rule.evaluate(request)
            if conflict:
                conflicts.append(conflict)
        return conflicts

    def create_access_review_campaign(
        self,
        *,
        name: str,
        owner_id: str,
        due_at: dt.datetime,
        entitlements: Optional[Sequence[Entitlement]] = None,
        reviewer_strategy: str = "asset_owner",
        metadata: Optional[JsonDict] = None,
    ) -> AccessReviewCampaign:
        entitlements = list(entitlements) if entitlements is not None else self.repository.active_entitlements()
        items: List[AccessReviewItem] = []

        for entitlement in entitlements:
            subject = self.repository.subjects.get(entitlement.subject_id)
            asset = self.repository.assets.get(entitlement.asset_id)
            score, risk, reasons = self.risk_scorer.score_entitlement(entitlement, subject, asset)
            recommendation = self._recommend_review_decision(entitlement, subject, asset, risk, reasons)
            reviewer_id = self._resolve_reviewer(subject, asset, reviewer_strategy, owner_id)
            items.append(
                AccessReviewItem(
                    item_id=str(uuid.uuid4()),
                    entitlement=entitlement,
                    subject=subject,
                    asset=asset,
                    reviewer_id=reviewer_id,
                    risk_level=risk,
                    recommendation=recommendation,
                    recommendation_reasons=reasons,
                )
            )

        campaign = AccessReviewCampaign(
            campaign_id=str(uuid.uuid4()),
            name=name,
            items=items,
            owner_id=owner_id,
            due_at=due_at,
            metadata=metadata or {},
        )
        self.repository.campaigns[campaign.campaign_id] = campaign
        self.audit.emit("access_review_campaign_created", campaign.summary())
        return campaign

    def decide_review_item(
        self,
        campaign_id: str,
        item_id: str,
        *,
        reviewer_id: str,
        decision: ReviewDecision,
        reason: str,
    ) -> AccessReviewItem:
        campaign = self.repository.campaigns.get(campaign_id)
        if not campaign:
            raise AccessReviewError(f"Campaign not found: {campaign_id}")
        item = next((candidate for candidate in campaign.items if candidate.item_id == item_id), None)
        if not item:
            raise AccessReviewError(f"Review item not found: {item_id}")
        if item.reviewer_id and item.reviewer_id != reviewer_id:
            raise AccessReviewError(f"Reviewer {reviewer_id} is not assigned to item {item_id}")

        item.decision = decision
        item.decision_reason = reason
        item.decided_at = dt.datetime.now(dt.timezone.utc)
        campaign.update_status()
        self.audit.emit(
            "access_review_item_decided",
            {"campaign_id": campaign_id, "item": item.to_dict(), "campaign_status": campaign.status.value},
        )
        return item

    def finalize_access_review_campaign(self, campaign_id: str, *, apply_revocations: bool = False) -> AccessReviewResult:
        campaign = self.repository.campaigns.get(campaign_id)
        if not campaign:
            raise AccessReviewError(f"Campaign not found: {campaign_id}")
        campaign.update_status()

        result = AccessReviewResult(campaign=campaign)
        for item in campaign.items:
            if item.decision == ReviewDecision.APPROVE:
                result.approved.append(item.entitlement)
            elif item.decision == ReviewDecision.REVOKE:
                result.revoked.append(item.entitlement)
                if apply_revocations:
                    self.revoke_entitlement(item.entitlement.entitlement_id, revoked_by="access_review", reason=item.decision_reason)
            elif item.decision == ReviewDecision.MODIFY:
                result.modified.append(item.entitlement)
            elif item.decision == ReviewDecision.ESCALATE:
                result.escalated.append(item.entitlement)

        self.audit.emit("access_review_campaign_finalized", result.to_dict())
        return result

    def entitlement_inventory(self) -> JsonDict:
        active = self.repository.active_entitlements()
        by_subject = Counter(ent.subject_id for ent in active)
        by_asset = Counter(ent.asset_id for ent in active)
        by_action = Counter(action.value for ent in active for action in ent.actions)
        expired = sum(1 for ent in self.repository.entitlements.values() if ent.is_expired())
        return {
            "total_entitlements": len(self.repository.entitlements),
            "active_entitlements": len(active),
            "expired_entitlements": expired,
            "by_subject": dict(by_subject),
            "by_asset": dict(by_asset),
            "by_action": dict(by_action),
        }

    def privileged_access_report(self) -> JsonDict:
        privileged_actions = {AccessAction.ADMIN, AccessAction.DELETE, AccessAction.EXPORT, AccessAction.SHARE}
        entries = []
        for entitlement in self.repository.active_entitlements():
            if entitlement.actions.intersection(privileged_actions):
                subject = self.repository.subjects.get(entitlement.subject_id)
                asset = self.repository.assets.get(entitlement.asset_id)
                score, risk, reasons = self.risk_scorer.score_entitlement(entitlement, subject, asset)
                entries.append(
                    {
                        "entitlement": entitlement.to_dict(),
                        "subject": subject.to_context() if subject else None,
                        "asset": asset.to_context() if asset else None,
                        "risk_score": score,
                        "risk_level": risk.value,
                        "risk_reasons": reasons,
                    }
                )
        return {"count": len(entries), "entries": entries}

    @staticmethod
    def _compute_expiration(policies: Sequence[AccessPolicy]) -> Optional[dt.datetime]:
        durations = [policy.max_duration_hours for policy in policies if policy.max_duration_hours]
        if not durations:
            return None
        return dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=min(durations))

    @staticmethod
    def _resolve_reviewer(
        subject: Optional[AccessSubject],
        asset: Optional[DataAsset],
        strategy: str,
        fallback_owner_id: str,
    ) -> str:
        if strategy == "asset_owner" and asset and asset.owner_id:
            return asset.owner_id
        if strategy == "asset_steward" and asset and asset.steward_id:
            return asset.steward_id
        if strategy == "manager" and subject and subject.manager_id:
            return subject.manager_id
        return fallback_owner_id

    @staticmethod
    def _recommend_review_decision(
        entitlement: Entitlement,
        subject: Optional[AccessSubject],
        asset: Optional[DataAsset],
        risk: RiskLevel,
        reasons: Sequence[str],
    ) -> ReviewDecision:
        if entitlement.is_expired():
            return ReviewDecision.REVOKE
        if subject and subject.employment_status.lower() != "active":
            return ReviewDecision.REVOKE
        if risk in {RiskLevel.CRITICAL, RiskLevel.HIGH}:
            return ReviewDecision.ESCALATE
        if not subject or not asset:
            return ReviewDecision.ESCALATE
        return ReviewDecision.APPROVE


# -----------------------------------------------------------------------------
# Helpers
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
# Example factory
# -----------------------------------------------------------------------------


def build_default_access_governance_engine() -> AccessGovernanceEngine:
    repo = InMemoryGovernanceRepository()

    repo.upsert_policy(
        AccessPolicy(
            policy_id="deny_inactive_users",
            name="Deny inactive users",
            effect=AccessEffect.DENY,
            conditions=(PolicyCondition("subject.employment_status", "ne", "active"),),
            priority=1,
            description="Inactive identities cannot access data assets.",
        )
    )
    repo.upsert_policy(
        AccessPolicy(
            policy_id="analyst_read_internal",
            name="Analysts can read internal datasets",
            effect=AccessEffect.ALLOW,
            actions={AccessAction.READ},
            roles={"analyst", "data_analyst"},
            sensitivity_levels={SensitivityLevel.INTERNAL, SensitivityLevel.CONFIDENTIAL},
            purposes={"analytics", "reporting"},
            requires_mfa=True,
            max_duration_hours=24,
            priority=50,
        )
    )
    repo.upsert_policy(
        AccessPolicy(
            policy_id="deny_export_restricted_without_approval",
            name="Restricted export requires manual review",
            effect=AccessEffect.DENY,
            actions={AccessAction.EXPORT},
            sensitivity_levels={SensitivityLevel.RESTRICTED, SensitivityLevel.HIGHLY_RESTRICTED},
            priority=10,
        )
    )
    repo.upsert_sod_rule(
        SoDRule(
            rule_id="sod_admin_approver",
            name="Admin and approver role conflict",
            conflicting_roles={"data_admin", "access_approver"},
            risk_level=RiskLevel.HIGH,
        )
    )

    return AccessGovernanceEngine(repository=repo)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    engine = build_default_access_governance_engine()
    subject = AccessSubject(
        subject_id="u-100",
        display_name="Ana Silva",
        email="ana@example.com",
        roles={"analyst"},
        groups={"bi"},
        department="finance",
    )
    asset = DataAsset(
        asset_id="sales_daily",
        name="Daily Sales",
        domain="retail",
        owner_id="owner-1",
        sensitivity=SensitivityLevel.CONFIDENTIAL,
        classifications={"financial_sensitive"},
        tags={"sales", "gold"},
    )
    engine.repository.upsert_subject(subject)
    engine.repository.upsert_asset(asset)

    request = AccessRequest(subject=subject, asset=asset, action=AccessAction.READ, purpose="analytics")
    decision = engine.evaluate_access(request)
    print(json.dumps(decision.to_dict(), indent=2, ensure_ascii=False))

    entitlement = engine.grant_entitlement(
        subject_id=subject.subject_id,
        asset_id=asset.asset_id,
        actions={AccessAction.READ},
        granted_by="owner-1",
        purpose="analytics",
        duration_hours=24,
    )
    campaign = engine.create_access_review_campaign(
        name="Quarterly access review",
        owner_id="governance-owner",
        due_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=14),
    )
    print(json.dumps(campaign.summary(), indent=2, ensure_ascii=False))

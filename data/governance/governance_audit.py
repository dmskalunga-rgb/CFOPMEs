"""
governance_audit.py
===================

Enterprise-grade governance audit module for data governance platforms.

Core capabilities
-----------------
- Specialized governance audit layer on top of generic audit trails.
- Decision audit for access, policy, privacy, classification, masking and compliance.
- Evidence chain-of-custody tracking.
- Auditability scoring by governance domain, asset, actor, control and time window.
- Governance event normalization and correlation across modules.
- Immutable audit bundles for internal/external audits.
- Gap detection for missing evidence, missing approver, stale attestations and weak lineage.
- Executive and technical audit reports.
- Pluggable sink architecture with a default in-memory repository.

This module is intentionally vendor-neutral and dependency-light. It can integrate
with audit_trail.py, compliance_engine.py, access_governance.py, classification_engine.py,
data_masking.py, encryption_manager.py and external SIEM/GRC platforms.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import hashlib
import json
import logging
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Set, Tuple, Union, runtime_checkable

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]


class GovernanceAuditError(Exception):
    """Base exception for governance audit failures."""


class EvidenceChainError(GovernanceAuditError):
    """Raised when evidence chain-of-custody is invalid."""


class AuditBundleError(GovernanceAuditError):
    """Raised when audit bundle generation fails."""


class GovernanceDomain(str, enum.Enum):
    ACCESS = "access"
    CLASSIFICATION = "classification"
    MASKING = "masking"
    ENCRYPTION = "encryption"
    PRIVACY = "privacy"
    RETENTION = "retention"
    COMPLIANCE = "compliance"
    POLICY = "policy"
    CATALOG = "catalog"
    LINEAGE = "lineage"
    QUALITY = "quality"
    STEWARDSHIP = "stewardship"
    SECURITY = "security"


class GovernanceAuditAction(str, enum.Enum):
    DECISION_RECORDED = "decision_recorded"
    POLICY_EVALUATED = "policy_evaluated"
    POLICY_CHANGED = "policy_changed"
    ACCESS_GRANTED = "access_granted"
    ACCESS_REVOKED = "access_revoked"
    ACCESS_REVIEWED = "access_reviewed"
    DATA_CLASSIFIED = "data_classified"
    DATA_MASKED = "data_masked"
    DATA_ENCRYPTED = "data_encrypted"
    EVIDENCE_COLLECTED = "evidence_collected"
    EVIDENCE_ATTESTED = "evidence_attested"
    CONTROL_ASSESSED = "control_assessed"
    FINDING_CREATED = "finding_created"
    REMEDIATION_UPDATED = "remediation_updated"
    RETENTION_APPLIED = "retention_applied"
    PRIVACY_REQUEST_PROCESSED = "privacy_request_processed"
    LINEAGE_UPDATED = "lineage_updated"
    QUALITY_EXCEPTION_CREATED = "quality_exception_created"
    STEWARDSHIP_TASK_DECIDED = "stewardship_task_decided"


class GovernanceAuditOutcome(str, enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"
    PARTIAL = "partial"
    NEEDS_REVIEW = "needs_review"
    NOT_APPLICABLE = "not_applicable"


class GovernanceAuditSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class EvidenceState(str, enum.Enum):
    COLLECTED = "collected"
    VALIDATED = "validated"
    ATTESTED = "attested"
    EXPIRED = "expired"
    REVOKED = "revoked"
    SUPERSEDED = "superseded"


class CustodyAction(str, enum.Enum):
    CREATED = "created"
    VIEWED = "viewed"
    COPIED = "copied"
    EXPORTED = "exported"
    VALIDATED = "validated"
    ATTESTED = "attested"
    REVOKED = "revoked"
    ARCHIVED = "archived"


class AuditabilityLevel(str, enum.Enum):
    WEAK = "weak"
    BASIC = "basic"
    GOOD = "good"
    STRONG = "strong"
    EXCELLENT = "excellent"


class BundleFormat(str, enum.Enum):
    JSON = "json"
    JSONL = "jsonl"


@dataclass(frozen=True)
class GovernanceActor:
    actor_id: str
    actor_type: str = "user"
    display_name: Optional[str] = None
    email: Optional[str] = None
    department: Optional[str] = None
    roles: Tuple[str, ...] = field(default_factory=tuple)
    attributes: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class GovernanceResource:
    resource_id: str
    resource_type: str
    name: Optional[str] = None
    domain: Optional[str] = None
    tenant_id: Optional[str] = None
    owner_id: Optional[str] = None
    classifications: Tuple[str, ...] = field(default_factory=tuple)
    tags: Tuple[str, ...] = field(default_factory=tuple)
    attributes: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class GovernanceAuditContext:
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    workflow_id: Optional[str] = None
    assessment_id: Optional[str] = None
    policy_id: Optional[str] = None
    control_id: Optional[str] = None
    environment: str = "prod"
    source_module: Optional[str] = None
    source_system: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class GovernanceAuditEvent:
    event_id: str
    timestamp: dt.datetime
    domain: GovernanceDomain
    action: GovernanceAuditAction
    outcome: GovernanceAuditOutcome
    severity: GovernanceAuditSeverity
    actor: GovernanceActor
    resource: GovernanceResource
    context: GovernanceAuditContext
    message: str = ""
    decision_id: Optional[str] = None
    evidence_ids: Tuple[str, ...] = field(default_factory=tuple)
    policy_ids: Tuple[str, ...] = field(default_factory=tuple)
    control_ids: Tuple[str, ...] = field(default_factory=tuple)
    before: Optional[JsonDict] = None
    after: Optional[JsonDict] = None
    details: JsonDict = field(default_factory=dict)
    risk_score: Optional[float] = None
    audit_hash: Optional[str] = None

    def canonical_payload(self, include_hash: bool = False) -> JsonDict:
        payload = {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "domain": self.domain.value,
            "action": self.action.value,
            "outcome": self.outcome.value,
            "severity": self.severity.value,
            "actor": self.actor.to_dict(),
            "resource": self.resource.to_dict(),
            "context": self.context.to_dict(),
            "message": self.message,
            "decision_id": self.decision_id,
            "evidence_ids": list(self.evidence_ids),
            "policy_ids": list(self.policy_ids),
            "control_ids": list(self.control_ids),
            "before": self.before,
            "after": self.after,
            "details": self.details,
            "risk_score": self.risk_score,
        }
        if include_hash:
            payload["audit_hash"] = self.audit_hash
        return to_json_safe(payload)

    def to_dict(self) -> JsonDict:
        return self.canonical_payload(include_hash=True)


@dataclass(frozen=True)
class DecisionAuditRecord:
    decision_id: str
    decision_type: str
    domain: GovernanceDomain
    actor: GovernanceActor
    resource: GovernanceResource
    outcome: str
    rationale: Tuple[str, ...] = field(default_factory=tuple)
    policies_evaluated: Tuple[str, ...] = field(default_factory=tuple)
    policies_matched: Tuple[str, ...] = field(default_factory=tuple)
    controls_impacted: Tuple[str, ...] = field(default_factory=tuple)
    approver_id: Optional[str] = None
    reviewer_id: Optional[str] = None
    risk_score: Optional[float] = None
    evidence_ids: Tuple[str, ...] = field(default_factory=tuple)
    decided_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    context: GovernanceAuditContext = field(default_factory=GovernanceAuditContext)
    metadata: JsonDict = field(default_factory=dict)

    def to_event(self) -> GovernanceAuditEvent:
        severity = GovernanceAuditSeverity.WARNING if self.outcome.lower() in {"denied", "manual_review", "needs_review"} else GovernanceAuditSeverity.INFO
        return GovernanceAuditEvent(
            event_id=str(uuid.uuid4()),
            timestamp=self.decided_at,
            domain=self.domain,
            action=GovernanceAuditAction.DECISION_RECORDED,
            outcome=map_outcome(self.outcome),
            severity=severity,
            actor=self.actor,
            resource=self.resource,
            context=self.context,
            message=f"Governance decision recorded: {self.decision_type}",
            decision_id=self.decision_id,
            evidence_ids=self.evidence_ids,
            policy_ids=tuple(set(self.policies_evaluated).union(self.policies_matched)),
            control_ids=self.controls_impacted,
            details={
                "decision_type": self.decision_type,
                "rationale": list(self.rationale),
                "policies_evaluated": list(self.policies_evaluated),
                "policies_matched": list(self.policies_matched),
                "approver_id": self.approver_id,
                "reviewer_id": self.reviewer_id,
                "metadata": self.metadata,
            },
            risk_score=self.risk_score,
        )

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    title: str
    evidence_type: str
    domain: GovernanceDomain
    resource: GovernanceResource
    collected_by: GovernanceActor
    collected_at: dt.datetime
    source_system: str
    uri: Optional[str] = None
    content_hash: Optional[str] = None
    state: EvidenceState = EvidenceState.COLLECTED
    valid_until: Optional[dt.datetime] = None
    control_ids: Tuple[str, ...] = field(default_factory=tuple)
    policy_ids: Tuple[str, ...] = field(default_factory=tuple)
    metadata: JsonDict = field(default_factory=dict)

    def is_expired(self, as_of: Optional[dt.datetime] = None) -> bool:
        as_of = as_of or dt.datetime.now(dt.timezone.utc)
        return bool(self.valid_until and self.valid_until < as_of)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class CustodyRecord:
    custody_id: str
    evidence_id: str
    action: CustodyAction
    actor: GovernanceActor
    timestamp: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    source_location: Optional[str] = None
    target_location: Optional[str] = None
    previous_hash: Optional[str] = None
    custody_hash: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def canonical_payload(self, include_hash: bool = False) -> JsonDict:
        payload = {
            "custody_id": self.custody_id,
            "evidence_id": self.evidence_id,
            "action": self.action.value,
            "actor": self.actor.to_dict(),
            "timestamp": self.timestamp.isoformat(),
            "source_location": self.source_location,
            "target_location": self.target_location,
            "previous_hash": self.previous_hash,
            "metadata": self.metadata,
        }
        if include_hash:
            payload["custody_hash"] = self.custody_hash
        return to_json_safe(payload)

    def to_dict(self) -> JsonDict:
        return self.canonical_payload(include_hash=True)


@dataclass
class AuditGap:
    gap_id: str
    domain: GovernanceDomain
    resource_id: Optional[str]
    gap_type: str
    severity: GovernanceAuditSeverity
    message: str
    recommendation: str
    detected_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass
class AuditabilityScore:
    score: float
    level: AuditabilityLevel
    domain_scores: JsonDict
    gaps: List[AuditGap]
    event_count: int
    evidence_count: int
    decision_count: int
    generated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def to_dict(self) -> JsonDict:
        return {
            "score": self.score,
            "level": self.level.value,
            "domain_scores": dict(self.domain_scores),
            "event_count": self.event_count,
            "evidence_count": self.evidence_count,
            "decision_count": self.decision_count,
            "generated_at": self.generated_at.isoformat(),
            "gaps": [gap.to_dict() for gap in self.gaps],
        }


@dataclass
class GovernanceAuditReport:
    report_id: str
    title: str
    start_time: Optional[dt.datetime]
    end_time: Optional[dt.datetime]
    domains: Tuple[GovernanceDomain, ...]
    summary: JsonDict
    auditability: AuditabilityScore
    events: List[GovernanceAuditEvent]
    decisions: List[DecisionAuditRecord]
    evidence: List[EvidenceRecord]
    custody_records: List[CustodyRecord]
    generated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    audit_hash: Optional[str] = None

    def to_dict(self) -> JsonDict:
        return {
            "report_id": self.report_id,
            "title": self.title,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "domains": [domain.value for domain in self.domains],
            "summary": dict(self.summary),
            "auditability": self.auditability.to_dict(),
            "events": [event.to_dict() for event in self.events],
            "decisions": [decision.to_dict() for decision in self.decisions],
            "evidence": [item.to_dict() for item in self.evidence],
            "custody_records": [item.to_dict() for item in self.custody_records],
            "generated_at": self.generated_at.isoformat(),
            "audit_hash": self.audit_hash,
        }


@dataclass(frozen=True)
class GovernanceAuditQuery:
    start_time: Optional[dt.datetime] = None
    end_time: Optional[dt.datetime] = None
    domains: Tuple[GovernanceDomain, ...] = field(default_factory=tuple)
    actor_id: Optional[str] = None
    resource_id: Optional[str] = None
    resource_type: Optional[str] = None
    tenant_id: Optional[str] = None
    outcome: Optional[GovernanceAuditOutcome] = None
    severity_at_least: Optional[GovernanceAuditSeverity] = None
    correlation_id: Optional[str] = None
    policy_id: Optional[str] = None
    control_id: Optional[str] = None
    evidence_id: Optional[str] = None
    text: Optional[str] = None
    limit: int = 1000
    offset: int = 0


@runtime_checkable
class GovernanceAuditSink(Protocol):
    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        ...


class LoggingGovernanceAuditSink:
    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self.log.info("governance_audit", extra={"event_type": event_type, "payload": dict(payload)})


class InMemoryGovernanceAuditRepository:
    """In-memory repository for governance audit artifacts."""

    def __init__(self) -> None:
        self.events: Dict[str, GovernanceAuditEvent] = {}
        self.decisions: Dict[str, DecisionAuditRecord] = {}
        self.evidence: Dict[str, EvidenceRecord] = {}
        self.custody: Dict[str, CustodyRecord] = {}
        self.reports: Dict[str, GovernanceAuditReport] = {}

    def add_event(self, event: GovernanceAuditEvent) -> None:
        self.events[event.event_id] = event

    def add_decision(self, decision: DecisionAuditRecord) -> None:
        self.decisions[decision.decision_id] = decision

    def add_evidence(self, evidence: EvidenceRecord) -> None:
        self.evidence[evidence.evidence_id] = evidence

    def add_custody(self, custody: CustodyRecord) -> None:
        self.custody[custody.custody_id] = custody

    def add_report(self, report: GovernanceAuditReport) -> None:
        self.reports[report.report_id] = report

    def query_events(self, query: GovernanceAuditQuery) -> List[GovernanceAuditEvent]:
        matched = [event for event in self.events.values() if matches_query(event, query)]
        matched.sort(key=lambda event: event.timestamp, reverse=True)
        return matched[query.offset : query.offset + query.limit]

    def query_decisions(self, query: GovernanceAuditQuery) -> List[DecisionAuditRecord]:
        matched = []
        for decision in self.decisions.values():
            event = decision.to_event()
            if matches_query(event, query):
                matched.append(decision)
        matched.sort(key=lambda decision: decision.decided_at, reverse=True)
        return matched[query.offset : query.offset + query.limit]

    def query_evidence(self, query: GovernanceAuditQuery) -> List[EvidenceRecord]:
        evidence = list(self.evidence.values())
        if query.evidence_id:
            evidence = [item for item in evidence if item.evidence_id == query.evidence_id]
        if query.domains:
            evidence = [item for item in evidence if item.domain in query.domains]
        if query.resource_id:
            evidence = [item for item in evidence if item.resource.resource_id == query.resource_id]
        if query.tenant_id:
            evidence = [item for item in evidence if item.resource.tenant_id == query.tenant_id]
        if query.control_id:
            evidence = [item for item in evidence if query.control_id in item.control_ids]
        if query.policy_id:
            evidence = [item for item in evidence if query.policy_id in item.policy_ids]
        return sorted(evidence, key=lambda item: item.collected_at, reverse=True)[query.offset : query.offset + query.limit]

    def custody_for_evidence(self, evidence_id: str) -> List[CustodyRecord]:
        records = [record for record in self.custody.values() if record.evidence_id == evidence_id]
        return sorted(records, key=lambda record: record.timestamp)


class GovernanceAuditService:
    """Main enterprise governance audit service."""

    def __init__(
        self,
        repository: Optional[InMemoryGovernanceAuditRepository] = None,
        *,
        audit_sink: Optional[GovernanceAuditSink] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.repository = repository or InMemoryGovernanceAuditRepository()
        self.audit = audit_sink or LoggingGovernanceAuditSink()
        self.log = log or logger

    def record_event(self, event: GovernanceAuditEvent) -> GovernanceAuditEvent:
        sealed = self._seal_event(event)
        self.repository.add_event(sealed)
        self.audit.emit("governance_event_recorded", sealed.to_dict())
        return sealed

    def record_decision(self, decision: DecisionAuditRecord) -> GovernanceAuditEvent:
        self.repository.add_decision(decision)
        event = self.record_event(decision.to_event())
        self.audit.emit("governance_decision_recorded", decision.to_dict())
        return event

    def collect_evidence(self, evidence: EvidenceRecord) -> EvidenceRecord:
        if evidence.content_hash is None:
            evidence = dataclasses.replace(evidence, content_hash=stable_hash(evidence.to_dict()))
        self.repository.add_evidence(evidence)
        self.add_custody_record(
            evidence_id=evidence.evidence_id,
            action=CustodyAction.CREATED,
            actor=evidence.collected_by,
            target_location=evidence.uri,
            metadata={"source_system": evidence.source_system},
        )
        self.record_event(
            GovernanceAuditEvent(
                event_id=str(uuid.uuid4()),
                timestamp=evidence.collected_at,
                domain=evidence.domain,
                action=GovernanceAuditAction.EVIDENCE_COLLECTED,
                outcome=GovernanceAuditOutcome.SUCCESS,
                severity=GovernanceAuditSeverity.INFO,
                actor=evidence.collected_by,
                resource=evidence.resource,
                context=GovernanceAuditContext(source_system=evidence.source_system),
                message=f"Evidence collected: {evidence.title}",
                evidence_ids=(evidence.evidence_id,),
                control_ids=evidence.control_ids,
                policy_ids=evidence.policy_ids,
                details={"evidence_type": evidence.evidence_type, "state": evidence.state.value},
            )
        )
        self.audit.emit("governance_evidence_collected", evidence.to_dict())
        return evidence

    def add_custody_record(
        self,
        *,
        evidence_id: str,
        action: CustodyAction,
        actor: GovernanceActor,
        source_location: Optional[str] = None,
        target_location: Optional[str] = None,
        metadata: Optional[JsonDict] = None,
    ) -> CustodyRecord:
        previous = self.repository.custody_for_evidence(evidence_id)
        previous_hash = previous[-1].custody_hash if previous else None
        record = CustodyRecord(
            custody_id=str(uuid.uuid4()),
            evidence_id=evidence_id,
            action=action,
            actor=actor,
            source_location=source_location,
            target_location=target_location,
            previous_hash=previous_hash,
            metadata=metadata or {},
        )
        custody_hash = stable_hash(record.canonical_payload(include_hash=False))
        sealed = dataclasses.replace(record, custody_hash=custody_hash)
        self.repository.add_custody(sealed)
        self.audit.emit("custody_record_added", sealed.to_dict())
        return sealed

    def verify_evidence_chain(self, evidence_id: str, *, raise_on_error: bool = False) -> JsonDict:
        records = self.repository.custody_for_evidence(evidence_id)
        issues: List[JsonDict] = []
        previous_hash = None
        for record in records:
            expected_hash = stable_hash(dataclasses.replace(record, custody_hash=None).canonical_payload(include_hash=False))
            if record.previous_hash != previous_hash:
                issues.append(
                    {
                        "code": "PREVIOUS_HASH_MISMATCH",
                        "custody_id": record.custody_id,
                        "expected": previous_hash,
                        "actual": record.previous_hash,
                    }
                )
            if record.custody_hash != expected_hash:
                issues.append(
                    {
                        "code": "CUSTODY_HASH_MISMATCH",
                        "custody_id": record.custody_id,
                        "expected": expected_hash,
                        "actual": record.custody_hash,
                    }
                )
            previous_hash = record.custody_hash
        result = {"evidence_id": evidence_id, "verified": not issues, "records_checked": len(records), "issues": issues}
        if raise_on_error and issues:
            raise EvidenceChainError(json.dumps(result, ensure_ascii=False))
        return result

    def query_events(self, query: GovernanceAuditQuery) -> List[GovernanceAuditEvent]:
        return self.repository.query_events(query)

    def generate_report(
        self,
        *,
        title: str,
        query: Optional[GovernanceAuditQuery] = None,
        domains: Sequence[GovernanceDomain] = (),
    ) -> GovernanceAuditReport:
        query = query or GovernanceAuditQuery(domains=tuple(domains))
        events = self.repository.query_events(query)
        decisions = self.repository.query_decisions(query)
        evidence = self.repository.query_evidence(query)
        custody_records = [record for item in evidence for record in self.repository.custody_for_evidence(item.evidence_id)]
        auditability = self.calculate_auditability(query=query)
        summary = self._build_summary(events, decisions, evidence, custody_records)
        report = GovernanceAuditReport(
            report_id=str(uuid.uuid4()),
            title=title,
            start_time=query.start_time,
            end_time=query.end_time,
            domains=query.domains,
            summary=summary,
            auditability=auditability,
            events=events,
            decisions=decisions,
            evidence=evidence,
            custody_records=custody_records,
        )
        report.audit_hash = stable_hash(report.to_dict())
        self.repository.add_report(report)
        self.audit.emit("governance_audit_report_generated", {"report_id": report.report_id, "summary": summary, "audit_hash": report.audit_hash})
        return report

    def calculate_auditability(self, *, query: Optional[GovernanceAuditQuery] = None) -> AuditabilityScore:
        query = query or GovernanceAuditQuery(limit=100_000)
        events = self.repository.query_events(dataclasses.replace(query, limit=100_000, offset=0))
        decisions = self.repository.query_decisions(dataclasses.replace(query, limit=100_000, offset=0))
        evidence = self.repository.query_evidence(dataclasses.replace(query, limit=100_000, offset=0))
        gaps = self.detect_gaps(events=events, decisions=decisions, evidence=evidence)

        domains = set([event.domain for event in events] + [decision.domain for decision in decisions] + [item.domain for item in evidence])
        domain_scores: JsonDict = {}
        for domain in domains:
            domain_events = [event for event in events if event.domain == domain]
            domain_decisions = [decision for decision in decisions if decision.domain == domain]
            domain_evidence = [item for item in evidence if item.domain == domain]
            domain_gaps = [gap for gap in gaps if gap.domain == domain]
            domain_scores[domain.value] = self._score_components(domain_events, domain_decisions, domain_evidence, domain_gaps)

        score = round(sum(domain_scores.values()) / len(domain_scores), 6) if domain_scores else 0.0
        return AuditabilityScore(
            score=score,
            level=auditability_level(score),
            domain_scores=domain_scores,
            gaps=gaps,
            event_count=len(events),
            evidence_count=len(evidence),
            decision_count=len(decisions),
        )

    def detect_gaps(
        self,
        *,
        events: Optional[Sequence[GovernanceAuditEvent]] = None,
        decisions: Optional[Sequence[DecisionAuditRecord]] = None,
        evidence: Optional[Sequence[EvidenceRecord]] = None,
    ) -> List[AuditGap]:
        events = list(events if events is not None else self.repository.events.values())
        decisions = list(decisions if decisions is not None else self.repository.decisions.values())
        evidence = list(evidence if evidence is not None else self.repository.evidence.values())
        gaps: List[AuditGap] = []

        evidence_ids = {item.evidence_id for item in evidence}
        for decision in decisions:
            if not decision.evidence_ids:
                gaps.append(
                    AuditGap(
                        gap_id=str(uuid.uuid4()),
                        domain=decision.domain,
                        resource_id=decision.resource.resource_id,
                        gap_type="decision_without_evidence",
                        severity=GovernanceAuditSeverity.WARNING,
                        message=f"Decision {decision.decision_id} has no evidence attached.",
                        recommendation="Attach evidence records supporting the decision rationale.",
                    )
                )
            elif not set(decision.evidence_ids).issubset(evidence_ids):
                gaps.append(
                    AuditGap(
                        gap_id=str(uuid.uuid4()),
                        domain=decision.domain,
                        resource_id=decision.resource.resource_id,
                        gap_type="decision_references_missing_evidence",
                        severity=GovernanceAuditSeverity.ERROR,
                        message=f"Decision {decision.decision_id} references missing evidence.",
                        recommendation="Collect or restore referenced evidence records.",
                    )
                )
            if decision.outcome.lower() in {"approved", "allow", "conditional_allow"} and not decision.approver_id and decision.risk_score and decision.risk_score >= 70:
                gaps.append(
                    AuditGap(
                        gap_id=str(uuid.uuid4()),
                        domain=decision.domain,
                        resource_id=decision.resource.resource_id,
                        gap_type="high_risk_decision_without_approver",
                        severity=GovernanceAuditSeverity.ERROR,
                        message=f"High-risk decision {decision.decision_id} lacks approver.",
                        recommendation="Require named approver for high-risk governance decisions.",
                    )
                )

        now = dt.datetime.now(dt.timezone.utc)
        for item in evidence:
            if item.is_expired(now):
                gaps.append(
                    AuditGap(
                        gap_id=str(uuid.uuid4()),
                        domain=item.domain,
                        resource_id=item.resource.resource_id,
                        gap_type="expired_evidence",
                        severity=GovernanceAuditSeverity.WARNING,
                        message=f"Evidence {item.evidence_id} is expired.",
                        recommendation="Refresh evidence and attest current control state.",
                    )
                )
            if not self.repository.custody_for_evidence(item.evidence_id):
                gaps.append(
                    AuditGap(
                        gap_id=str(uuid.uuid4()),
                        domain=item.domain,
                        resource_id=item.resource.resource_id,
                        gap_type="evidence_without_custody_chain",
                        severity=GovernanceAuditSeverity.ERROR,
                        message=f"Evidence {item.evidence_id} has no custody chain.",
                        recommendation="Create custody records for evidence lifecycle actions.",
                    )
                )

        event_keys = {(event.domain, event.resource.resource_id) for event in events}
        for item in evidence:
            if (item.domain, item.resource.resource_id) not in event_keys:
                gaps.append(
                    AuditGap(
                        gap_id=str(uuid.uuid4()),
                        domain=item.domain,
                        resource_id=item.resource.resource_id,
                        gap_type="evidence_without_related_event",
                        severity=GovernanceAuditSeverity.WARNING,
                        message=f"Evidence {item.evidence_id} has no related governance event for the resource/domain.",
                        recommendation="Record governance event linking evidence to control, decision or workflow.",
                    )
                )
        return gaps

    def export_report(self, report: GovernanceAuditReport, *, fmt: BundleFormat = BundleFormat.JSON, indent: int = 2) -> str:
        if fmt == BundleFormat.JSON:
            return json.dumps(report.to_dict(), ensure_ascii=False, indent=indent, default=str)
        if fmt == BundleFormat.JSONL:
            rows = []
            for event in report.events:
                rows.append(json.dumps({"type": "event", "payload": event.to_dict()}, ensure_ascii=False, default=str))
            for decision in report.decisions:
                rows.append(json.dumps({"type": "decision", "payload": decision.to_dict()}, ensure_ascii=False, default=str))
            for evidence in report.evidence:
                rows.append(json.dumps({"type": "evidence", "payload": evidence.to_dict()}, ensure_ascii=False, default=str))
            for custody in report.custody_records:
                rows.append(json.dumps({"type": "custody", "payload": custody.to_dict()}, ensure_ascii=False, default=str))
            return "\n".join(rows)
        raise AuditBundleError(f"Unsupported bundle format: {fmt}")

    def _seal_event(self, event: GovernanceAuditEvent) -> GovernanceAuditEvent:
        audit_hash = stable_hash(event.canonical_payload(include_hash=False))
        return dataclasses.replace(event, audit_hash=audit_hash)

    @staticmethod
    def _build_summary(
        events: Sequence[GovernanceAuditEvent],
        decisions: Sequence[DecisionAuditRecord],
        evidence: Sequence[EvidenceRecord],
        custody: Sequence[CustodyRecord],
    ) -> JsonDict:
        return {
            "events": len(events),
            "decisions": len(decisions),
            "evidence": len(evidence),
            "custody_records": len(custody),
            "events_by_domain": dict(Counter(event.domain.value for event in events)),
            "events_by_outcome": dict(Counter(event.outcome.value for event in events)),
            "events_by_severity": dict(Counter(event.severity.value for event in events)),
            "decisions_by_domain": dict(Counter(decision.domain.value for decision in decisions)),
            "evidence_by_state": dict(Counter(item.state.value for item in evidence)),
            "top_resources": dict(Counter(event.resource.resource_id for event in events).most_common(20)),
        }

    @staticmethod
    def _score_components(
        events: Sequence[GovernanceAuditEvent],
        decisions: Sequence[DecisionAuditRecord],
        evidence: Sequence[EvidenceRecord],
        gaps: Sequence[AuditGap],
    ) -> float:
        event_score = 1.0 if events else 0.0
        decision_evidence_ratio = 1.0
        if decisions:
            decision_evidence_ratio = sum(1 for decision in decisions if decision.evidence_ids) / len(decisions)
        custody_ratio = 1.0
        if evidence:
            custody_ratio = 0.90  # baseline; detailed chain verification is evidence-specific
        gap_penalty = min(0.75, len(gaps) * 0.08)
        return round(max(0.0, (event_score * 0.35) + (decision_evidence_ratio * 0.35) + (custody_ratio * 0.30) - gap_penalty), 6)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


_SEVERITY_ORDER = {
    GovernanceAuditSeverity.INFO: 10,
    GovernanceAuditSeverity.WARNING: 20,
    GovernanceAuditSeverity.ERROR: 30,
    GovernanceAuditSeverity.CRITICAL: 40,
}


def matches_query(event: GovernanceAuditEvent, query: GovernanceAuditQuery) -> bool:
    if query.start_time and event.timestamp < query.start_time:
        return False
    if query.end_time and event.timestamp > query.end_time:
        return False
    if query.domains and event.domain not in query.domains:
        return False
    if query.actor_id and event.actor.actor_id != query.actor_id:
        return False
    if query.resource_id and event.resource.resource_id != query.resource_id:
        return False
    if query.resource_type and event.resource.resource_type != query.resource_type:
        return False
    if query.tenant_id and event.resource.tenant_id != query.tenant_id:
        return False
    if query.outcome and event.outcome != query.outcome:
        return False
    if query.severity_at_least and _SEVERITY_ORDER[event.severity] < _SEVERITY_ORDER[query.severity_at_least]:
        return False
    if query.correlation_id and event.context.correlation_id != query.correlation_id:
        return False
    if query.policy_id and query.policy_id not in event.policy_ids:
        return False
    if query.control_id and query.control_id not in event.control_ids:
        return False
    if query.evidence_id and query.evidence_id not in event.evidence_ids:
        return False
    if query.text:
        text = json.dumps(event.to_dict(), ensure_ascii=False, default=str).lower()
        if query.text.lower() not in text:
            return False
    return True


def map_outcome(value: str) -> GovernanceAuditOutcome:
    normalized = str(value).strip().lower()
    if normalized in {"allow", "allowed", "approved", "approve", "success", "compliant", "completed"}:
        return GovernanceAuditOutcome.SUCCESS
    if normalized in {"deny", "denied", "rejected", "reject", "non_compliant"}:
        return GovernanceAuditOutcome.DENIED
    if normalized in {"partial", "partially_compliant", "conditional_allow"}:
        return GovernanceAuditOutcome.PARTIAL
    if normalized in {"manual_review", "needs_review", "review"}:
        return GovernanceAuditOutcome.NEEDS_REVIEW
    if normalized in {"not_applicable", "na", "n/a"}:
        return GovernanceAuditOutcome.NOT_APPLICABLE
    return GovernanceAuditOutcome.FAILURE


def auditability_level(score: float) -> AuditabilityLevel:
    if score >= 0.95:
        return AuditabilityLevel.EXCELLENT
    if score >= 0.80:
        return AuditabilityLevel.STRONG
    if score >= 0.65:
        return AuditabilityLevel.GOOD
    if score >= 0.40:
        return AuditabilityLevel.BASIC
    return AuditabilityLevel.WEAK


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
# Convenience builders
# -----------------------------------------------------------------------------


def build_governance_actor(actor_id: str, *, display_name: Optional[str] = None, email: Optional[str] = None) -> GovernanceActor:
    return GovernanceActor(actor_id=actor_id, display_name=display_name, email=email)


def build_governance_resource(
    resource_id: str,
    resource_type: str,
    *,
    name: Optional[str] = None,
    tenant_id: Optional[str] = None,
    domain: Optional[str] = None,
) -> GovernanceResource:
    return GovernanceResource(resource_id=resource_id, resource_type=resource_type, name=name, tenant_id=tenant_id, domain=domain)


def build_default_governance_audit_service() -> GovernanceAuditService:
    return GovernanceAuditService()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    service = build_default_governance_audit_service()
    actor = build_governance_actor("u-100", display_name="Ana Silva", email="ana@example.com")
    resource = build_governance_resource("sales_daily", "dataset", name="Daily Sales", tenant_id="tenant-a", domain="retail")

    evidence = service.collect_evidence(
        EvidenceRecord(
            evidence_id="ev-001",
            title="Access review report",
            evidence_type="report",
            domain=GovernanceDomain.ACCESS,
            resource=resource,
            collected_by=actor,
            collected_at=dt.datetime.now(dt.timezone.utc),
            source_system="access-governance",
            uri="s3://audit/evidence/ev-001.json",
            control_ids=("DG-AC-001",),
        )
    )

    service.record_decision(
        DecisionAuditRecord(
            decision_id="dec-001",
            decision_type="access_review",
            domain=GovernanceDomain.ACCESS,
            actor=actor,
            resource=resource,
            outcome="approved",
            rationale=("Quarterly review completed.",),
            policies_evaluated=("analyst_read_internal",),
            policies_matched=("analyst_read_internal",),
            controls_impacted=("DG-AC-001",),
            approver_id="owner-1",
            risk_score=35,
            evidence_ids=(evidence.evidence_id,),
        )
    )

    report = service.generate_report(title="Governance Audit Report", domains=(GovernanceDomain.ACCESS,))
    print(service.export_report(report))

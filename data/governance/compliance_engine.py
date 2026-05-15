"""
compliance_engine.py
====================

Enterprise-grade compliance engine for data governance platforms.

Core capabilities
-----------------
- Regulatory framework and control catalog management.
- Control assessment with automated/manual evidence evaluation.
- Compliance scoring by framework, domain, control family and asset scope.
- Gap analysis, findings, risk levels and remediation plans.
- Evidence registry with freshness, trust and coverage scoring.
- Policy/control mapping and cross-framework reuse.
- Audit-ready reports and executive summaries.
- Vendor-neutral architecture with pluggable evidence providers and audit sinks.

Designed for data governance, security, privacy and compliance programs covering
frameworks such as LGPD, GDPR, ISO 27001, SOC 2, PCI DSS, HIPAA, NIST and internal
enterprise control baselines.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import hashlib
import json
import logging
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Set, Tuple, Union, runtime_checkable

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
EvidenceEvaluator = Callable[["ComplianceControl", Sequence["EvidenceRecord"], "AssessmentContext"], "ControlAssessment"]


class ComplianceError(Exception):
    """Base exception for compliance engine failures."""


class EvidenceError(ComplianceError):
    """Raised when evidence is invalid or unavailable."""


class AssessmentError(ComplianceError):
    """Raised when a compliance assessment fails."""


class ComplianceFramework(str, enum.Enum):
    LGPD = "lgpd"
    GDPR = "gdpr"
    ISO_27001 = "iso_27001"
    SOC2 = "soc2"
    PCI_DSS = "pci_dss"
    HIPAA = "hipaa"
    NIST_CSF = "nist_csf"
    INTERNAL = "internal"


class ControlDomain(str, enum.Enum):
    GOVERNANCE = "governance"
    ACCESS_CONTROL = "access_control"
    DATA_PROTECTION = "data_protection"
    PRIVACY = "privacy"
    RETENTION = "retention"
    INCIDENT_RESPONSE = "incident_response"
    CHANGE_MANAGEMENT = "change_management"
    MONITORING = "monitoring"
    RISK_MANAGEMENT = "risk_management"
    THIRD_PARTY = "third_party"
    BUSINESS_CONTINUITY = "business_continuity"
    SECURITY_OPERATIONS = "security_operations"


class ControlType(str, enum.Enum):
    PREVENTIVE = "preventive"
    DETECTIVE = "detective"
    CORRECTIVE = "corrective"
    DIRECTIVE = "directive"


class ControlFrequency(str, enum.Enum):
    CONTINUOUS = "continuous"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMI_ANNUAL = "semi_annual"
    ANNUAL = "annual"
    AD_HOC = "ad_hoc"


class ComplianceStatus(str, enum.Enum):
    COMPLIANT = "compliant"
    PARTIALLY_COMPLIANT = "partially_compliant"
    NON_COMPLIANT = "non_compliant"
    NOT_APPLICABLE = "not_applicable"
    NOT_ASSESSED = "not_assessed"
    NEEDS_REVIEW = "needs_review"


class EvidenceType(str, enum.Enum):
    POLICY = "policy"
    PROCEDURE = "procedure"
    CONFIGURATION = "configuration"
    LOG = "log"
    REPORT = "report"
    SCREENSHOT = "screenshot"
    TICKET = "ticket"
    ATTESTATION = "attestation"
    TEST_RESULT = "test_result"
    DATA_SAMPLE = "data_sample"
    API_RESPONSE = "api_response"
    QUERY_RESULT = "query_result"


class EvidenceTrustLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    AUTHORITATIVE = "authoritative"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RemediationStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    ACCEPTED_RISK = "accepted_risk"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ComplianceRequirement:
    requirement_id: str
    title: str
    description: str
    framework: ComplianceFramework
    section: Optional[str] = None
    citation: Optional[str] = None
    domain: ControlDomain = ControlDomain.GOVERNANCE
    mandatory: bool = True
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class ComplianceControl:
    control_id: str
    title: str
    description: str
    domain: ControlDomain
    control_type: ControlType = ControlType.PREVENTIVE
    frequency: ControlFrequency = ControlFrequency.CONTINUOUS
    owner_id: Optional[str] = None
    requirements: Tuple[str, ...] = field(default_factory=tuple)
    evidence_types_required: Tuple[EvidenceType, ...] = field(default_factory=tuple)
    automated: bool = False
    weight: float = 1.0
    enabled: bool = True
    tags: Tuple[str, ...] = field(default_factory=tuple)
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    title: str
    evidence_type: EvidenceType
    control_ids: Tuple[str, ...]
    source_system: str
    collected_at: dt.datetime
    collected_by: Optional[str] = None
    uri: Optional[str] = None
    content_hash: Optional[str] = None
    payload: Optional[JsonDict] = None
    trust_level: EvidenceTrustLevel = EvidenceTrustLevel.MEDIUM
    valid_from: Optional[dt.datetime] = None
    valid_until: Optional[dt.datetime] = None
    immutable: bool = True
    metadata: JsonDict = field(default_factory=dict)

    def is_fresh(self, as_of: Optional[dt.datetime] = None, max_age_days: Optional[int] = None) -> bool:
        as_of = as_of or dt.datetime.now(dt.timezone.utc)
        if self.valid_until and self.valid_until < as_of:
            return False
        if self.valid_from and self.valid_from > as_of:
            return False
        if max_age_days is not None:
            return self.collected_at >= as_of - dt.timedelta(days=max_age_days)
        return True

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class AssessmentContext:
    assessment_id: str
    framework: Optional[ComplianceFramework] = None
    scope: Tuple[str, ...] = field(default_factory=tuple)
    asset_ids: Tuple[str, ...] = field(default_factory=tuple)
    tenant_id: Optional[str] = None
    as_of: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    assessor_id: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass
class ControlAssessment:
    control_id: str
    status: ComplianceStatus
    score: float
    risk_level: RiskLevel
    evidence_ids: List[str] = field(default_factory=list)
    missing_evidence_types: List[EvidenceType] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    assessed_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    assessor_id: Optional[str] = None
    confidence: float = 0.0
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass
class ComplianceFinding:
    finding_id: str
    control_id: str
    requirement_ids: Tuple[str, ...]
    title: str
    description: str
    risk_level: RiskLevel
    status: ComplianceStatus
    evidence_ids: Tuple[str, ...] = field(default_factory=tuple)
    detected_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    owner_id: Optional[str] = None
    due_at: Optional[dt.datetime] = None
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass
class RemediationPlan:
    remediation_id: str
    finding_id: str
    title: str
    description: str
    owner_id: str
    status: RemediationStatus = RemediationStatus.OPEN
    priority: RiskLevel = RiskLevel.MEDIUM
    due_at: Optional[dt.datetime] = None
    tasks: List[str] = field(default_factory=list)
    progress_percent: float = 0.0
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    completed_at: Optional[dt.datetime] = None
    metadata: JsonDict = field(default_factory=dict)

    def complete(self) -> None:
        self.status = RemediationStatus.COMPLETED
        self.progress_percent = 100.0
        self.completed_at = dt.datetime.now(dt.timezone.utc)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass
class ComplianceAssessmentReport:
    assessment_id: str
    framework: Optional[ComplianceFramework]
    status: ComplianceStatus
    overall_score: float
    risk_level: RiskLevel
    controls_assessed: int
    controls_compliant: int
    controls_partial: int
    controls_non_compliant: int
    controls_not_applicable: int
    control_assessments: List[ControlAssessment]
    findings: List[ComplianceFinding]
    remediation_plans: List[RemediationPlan]
    domain_scores: JsonDict = field(default_factory=dict)
    requirement_coverage: JsonDict = field(default_factory=dict)
    generated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    audit_hash: Optional[str] = None

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@runtime_checkable
class ComplianceAuditSink(Protocol):
    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        ...


class LoggingComplianceAuditSink:
    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self.log.info("compliance_audit", extra={"event_type": event_type, "payload": dict(payload)})


@runtime_checkable
class EvidenceProvider(Protocol):
    def collect(self, control: ComplianceControl, context: AssessmentContext) -> Sequence[EvidenceRecord]:
        ...


class InMemoryComplianceRepository:
    """In-memory repository for compliance catalogs and assessment artifacts."""

    def __init__(self) -> None:
        self.requirements: Dict[str, ComplianceRequirement] = {}
        self.controls: Dict[str, ComplianceControl] = {}
        self.evidence: Dict[str, EvidenceRecord] = {}
        self.assessments: Dict[str, ControlAssessment] = {}
        self.findings: Dict[str, ComplianceFinding] = {}
        self.remediations: Dict[str, RemediationPlan] = {}
        self.reports: Dict[str, ComplianceAssessmentReport] = {}

    def upsert_requirement(self, requirement: ComplianceRequirement) -> None:
        self.requirements[requirement.requirement_id] = requirement

    def upsert_control(self, control: ComplianceControl) -> None:
        self.controls[control.control_id] = control

    def upsert_evidence(self, evidence: EvidenceRecord) -> None:
        self.evidence[evidence.evidence_id] = evidence

    def evidence_for_control(self, control_id: str) -> List[EvidenceRecord]:
        return [e for e in self.evidence.values() if control_id in e.control_ids]

    def controls_for_framework(self, framework: Optional[ComplianceFramework]) -> List[ComplianceControl]:
        if framework is None:
            return [c for c in self.controls.values() if c.enabled]
        requirement_ids = {r.requirement_id for r in self.requirements.values() if r.framework == framework}
        return [c for c in self.controls.values() if c.enabled and set(c.requirements).intersection(requirement_ids)]


class DefaultControlEvaluator:
    """Default evidence-based control evaluator."""

    TRUST_WEIGHT = {
        EvidenceTrustLevel.LOW: 0.35,
        EvidenceTrustLevel.MEDIUM: 0.60,
        EvidenceTrustLevel.HIGH: 0.85,
        EvidenceTrustLevel.AUTHORITATIVE: 1.00,
    }

    FREQUENCY_MAX_AGE_DAYS = {
        ControlFrequency.CONTINUOUS: 7,
        ControlFrequency.DAILY: 2,
        ControlFrequency.WEEKLY: 10,
        ControlFrequency.MONTHLY: 40,
        ControlFrequency.QUARTERLY: 120,
        ControlFrequency.SEMI_ANNUAL: 220,
        ControlFrequency.ANNUAL: 420,
        ControlFrequency.AD_HOC: None,
    }

    def evaluate(self, control: ComplianceControl, evidence: Sequence[EvidenceRecord], context: AssessmentContext) -> ControlAssessment:
        if not control.enabled:
            return ControlAssessment(
                control_id=control.control_id,
                status=ComplianceStatus.NOT_APPLICABLE,
                score=1.0,
                risk_level=RiskLevel.LOW,
                findings=["Control disabled or not applicable."],
                confidence=1.0,
                assessor_id=context.assessor_id,
            )

        max_age = self.FREQUENCY_MAX_AGE_DAYS.get(control.frequency)
        fresh_evidence = [e for e in evidence if e.is_fresh(context.as_of, max_age_days=max_age)]
        available_types = {e.evidence_type for e in fresh_evidence}
        required_types = set(control.evidence_types_required)
        missing_types = sorted(required_types - available_types, key=lambda x: x.value)

        coverage = 1.0 if not required_types else len(required_types & available_types) / len(required_types)
        trust = statistics.mean([self.TRUST_WEIGHT.get(e.trust_level, 0.5) for e in fresh_evidence]) if fresh_evidence else 0.0
        freshness = 1.0 if fresh_evidence else 0.0
        score = round((coverage * 0.55) + (trust * 0.30) + (freshness * 0.15), 6)

        findings: List[str] = []
        recommendations: List[str] = []
        if missing_types:
            findings.append("Missing required evidence types: " + ", ".join(t.value for t in missing_types))
            recommendations.append("Collect and attach missing evidence for this control.")
        if not fresh_evidence:
            findings.append("No fresh evidence available for the control frequency.")
            recommendations.append("Refresh evidence collection or automate evidence provider.")
        if trust < 0.6 and fresh_evidence:
            findings.append("Evidence trust level is below target.")
            recommendations.append("Use authoritative evidence sources where possible.")

        status = status_from_score(score, missing_types=bool(missing_types))
        risk = risk_from_status_score(status, score)
        return ControlAssessment(
            control_id=control.control_id,
            status=status,
            score=score,
            risk_level=risk,
            evidence_ids=[e.evidence_id for e in fresh_evidence],
            missing_evidence_types=missing_types,
            findings=findings,
            recommendations=recommendations,
            assessor_id=context.assessor_id,
            confidence=round(min(1.0, max(score, trust)), 6),
            metadata={"coverage": coverage, "trust": trust, "freshness": freshness, "max_age_days": max_age},
        )


class ComplianceEngine:
    """Main enterprise compliance engine."""

    def __init__(
        self,
        repository: Optional[InMemoryComplianceRepository] = None,
        *,
        evaluator: Optional[DefaultControlEvaluator] = None,
        evidence_providers: Optional[Sequence[EvidenceProvider]] = None,
        audit_sink: Optional[ComplianceAuditSink] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.repository = repository or InMemoryComplianceRepository()
        self.evaluator = evaluator or DefaultControlEvaluator()
        self.evidence_providers = list(evidence_providers or [])
        self.audit = audit_sink or LoggingComplianceAuditSink()
        self.log = log or logger

    def register_requirement(self, requirement: ComplianceRequirement) -> None:
        self.repository.upsert_requirement(requirement)
        self.audit.emit("requirement_registered", requirement.to_dict())

    def register_control(self, control: ComplianceControl) -> None:
        self.repository.upsert_control(control)
        self.audit.emit("control_registered", control.to_dict())

    def add_evidence(self, evidence: EvidenceRecord) -> None:
        if evidence.content_hash is None and evidence.payload is not None:
            evidence = dataclasses.replace(evidence, content_hash=stable_hash(evidence.payload))
        self.repository.upsert_evidence(evidence)
        self.audit.emit("evidence_added", evidence.to_dict())

    def collect_evidence(self, control: ComplianceControl, context: AssessmentContext) -> List[EvidenceRecord]:
        collected: List[EvidenceRecord] = []
        for provider in self.evidence_providers:
            try:
                provider_records = list(provider.collect(control, context))
                for record in provider_records:
                    self.add_evidence(record)
                collected.extend(provider_records)
            except Exception as exc:
                self.audit.emit("evidence_provider_error", {"control_id": control.control_id, "error": str(exc)})
        return collected

    def assess_control(self, control_id: str, context: Optional[AssessmentContext] = None) -> ControlAssessment:
        control = self.repository.controls.get(control_id)
        if not control:
            raise AssessmentError(f"Control not found: {control_id}")
        context = context or AssessmentContext(assessment_id=str(uuid.uuid4()))
        self.collect_evidence(control, context)
        evidence = self.repository.evidence_for_control(control_id)
        assessment = self.evaluator.evaluate(control, evidence, context)
        self.repository.assessments[f"{context.assessment_id}:{control_id}"] = assessment
        self.audit.emit("control_assessed", assessment.to_dict())
        return assessment

    def assess_framework(
        self,
        framework: Optional[ComplianceFramework],
        *,
        scope: Sequence[str] = (),
        asset_ids: Sequence[str] = (),
        tenant_id: Optional[str] = None,
        assessor_id: Optional[str] = None,
    ) -> ComplianceAssessmentReport:
        context = AssessmentContext(
            assessment_id=str(uuid.uuid4()),
            framework=framework,
            scope=tuple(scope),
            asset_ids=tuple(asset_ids),
            tenant_id=tenant_id,
            assessor_id=assessor_id,
        )
        controls = self.repository.controls_for_framework(framework)
        assessments = [self.assess_control(control.control_id, context) for control in controls]
        report = self._build_report(context, controls, assessments)
        self.repository.reports[report.assessment_id] = report
        self.audit.emit("framework_assessed", report.to_dict())
        return report

    def _build_report(
        self,
        context: AssessmentContext,
        controls: Sequence[ComplianceControl],
        assessments: Sequence[ControlAssessment],
    ) -> ComplianceAssessmentReport:
        weighted_total = sum(max(control.weight, 0.0) for control in controls) or 1.0
        assessment_by_control = {a.control_id: a for a in assessments}
        weighted_score = 0.0
        for control in controls:
            assessment = assessment_by_control.get(control.control_id)
            weighted_score += (assessment.score if assessment else 0.0) * max(control.weight, 0.0)
        overall_score = round(weighted_score / weighted_total, 6)

        findings = self._generate_findings(controls, assessments)
        remediation_plans = self._generate_remediations(findings, controls)
        for finding in findings:
            self.repository.findings[finding.finding_id] = finding
        for plan in remediation_plans:
            self.repository.remediations[plan.remediation_id] = plan

        statuses = Counter(a.status for a in assessments)
        status = status_from_score(overall_score, missing_types=bool(statuses.get(ComplianceStatus.NON_COMPLIANT)))
        risk = risk_from_status_score(status, overall_score)
        domain_scores = self._domain_scores(controls, assessments)
        coverage = self._requirement_coverage(controls, assessments)

        report = ComplianceAssessmentReport(
            assessment_id=context.assessment_id,
            framework=context.framework,
            status=status,
            overall_score=overall_score,
            risk_level=risk,
            controls_assessed=len(assessments),
            controls_compliant=statuses.get(ComplianceStatus.COMPLIANT, 0),
            controls_partial=statuses.get(ComplianceStatus.PARTIALLY_COMPLIANT, 0),
            controls_non_compliant=statuses.get(ComplianceStatus.NON_COMPLIANT, 0),
            controls_not_applicable=statuses.get(ComplianceStatus.NOT_APPLICABLE, 0),
            control_assessments=list(assessments),
            findings=findings,
            remediation_plans=remediation_plans,
            domain_scores=domain_scores,
            requirement_coverage=coverage,
        )
        report.audit_hash = stable_hash(report.to_dict())
        return report

    def _generate_findings(
        self,
        controls: Sequence[ComplianceControl],
        assessments: Sequence[ControlAssessment],
    ) -> List[ComplianceFinding]:
        control_by_id = {control.control_id: control for control in controls}
        findings: List[ComplianceFinding] = []
        for assessment in assessments:
            if assessment.status in {ComplianceStatus.COMPLIANT, ComplianceStatus.NOT_APPLICABLE}:
                continue
            control = control_by_id.get(assessment.control_id)
            if not control:
                continue
            title = f"Compliance gap in control {control.control_id}: {control.title}"
            description = "; ".join(assessment.findings) or "Control assessment indicates a compliance gap."
            due_days = {RiskLevel.CRITICAL: 7, RiskLevel.HIGH: 30, RiskLevel.MEDIUM: 60, RiskLevel.LOW: 120}[assessment.risk_level]
            findings.append(
                ComplianceFinding(
                    finding_id=str(uuid.uuid4()),
                    control_id=control.control_id,
                    requirement_ids=tuple(control.requirements),
                    title=title,
                    description=description,
                    risk_level=assessment.risk_level,
                    status=assessment.status,
                    evidence_ids=tuple(assessment.evidence_ids),
                    owner_id=control.owner_id,
                    due_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=due_days),
                    metadata={"recommendations": assessment.recommendations},
                )
            )
        return findings

    def _generate_remediations(self, findings: Sequence[ComplianceFinding], controls: Sequence[ComplianceControl]) -> List[RemediationPlan]:
        control_by_id = {control.control_id: control for control in controls}
        plans: List[RemediationPlan] = []
        for finding in findings:
            control = control_by_id.get(finding.control_id)
            owner = finding.owner_id or (control.owner_id if control else None) or "compliance-owner"
            recs = list(finding.metadata.get("recommendations", [])) if finding.metadata else []
            tasks = recs or ["Review control design and operating effectiveness.", "Collect updated evidence.", "Re-test control after remediation."]
            plans.append(
                RemediationPlan(
                    remediation_id=str(uuid.uuid4()),
                    finding_id=finding.finding_id,
                    title=f"Remediate {finding.control_id}",
                    description=finding.description,
                    owner_id=owner,
                    priority=finding.risk_level,
                    due_at=finding.due_at,
                    tasks=tasks,
                )
            )
        return plans

    def _domain_scores(self, controls: Sequence[ComplianceControl], assessments: Sequence[ControlAssessment]) -> JsonDict:
        assessment_by_control = {a.control_id: a for a in assessments}
        grouped: Dict[str, List[float]] = defaultdict(list)
        for control in controls:
            assessment = assessment_by_control.get(control.control_id)
            if assessment:
                grouped[control.domain.value].append(assessment.score)
        return {domain: round(sum(scores) / len(scores), 6) for domain, scores in grouped.items() if scores}

    def _requirement_coverage(self, controls: Sequence[ComplianceControl], assessments: Sequence[ControlAssessment]) -> JsonDict:
        assessment_by_control = {a.control_id: a for a in assessments}
        coverage: Dict[str, List[float]] = defaultdict(list)
        for control in controls:
            assessment = assessment_by_control.get(control.control_id)
            for requirement_id in control.requirements:
                coverage[requirement_id].append(assessment.score if assessment else 0.0)
        return {req: round(sum(scores) / len(scores), 6) for req, scores in coverage.items() if scores}

    def executive_summary(self, report: ComplianceAssessmentReport) -> JsonDict:
        critical = [f for f in report.findings if f.risk_level == RiskLevel.CRITICAL]
        high = [f for f in report.findings if f.risk_level == RiskLevel.HIGH]
        return {
            "assessment_id": report.assessment_id,
            "framework": report.framework.value if report.framework else "all",
            "status": report.status.value,
            "overall_score": report.overall_score,
            "risk_level": report.risk_level.value,
            "controls_assessed": report.controls_assessed,
            "compliance_distribution": {
                "compliant": report.controls_compliant,
                "partial": report.controls_partial,
                "non_compliant": report.controls_non_compliant,
                "not_applicable": report.controls_not_applicable,
            },
            "critical_findings": len(critical),
            "high_findings": len(high),
            "open_remediations": sum(1 for plan in report.remediation_plans if plan.status != RemediationStatus.COMPLETED),
            "top_domains_by_gap": sorted(report.domain_scores.items(), key=lambda item: item[1])[:5],
            "generated_at": report.generated_at.isoformat(),
        }

    def export_report(self, report: ComplianceAssessmentReport, *, indent: int = 2) -> str:
        return json.dumps(report.to_dict(), ensure_ascii=False, indent=indent, default=str)


# -----------------------------------------------------------------------------
# Utility functions
# -----------------------------------------------------------------------------


def status_from_score(score: float, *, missing_types: bool = False) -> ComplianceStatus:
    if score >= 0.95 and not missing_types:
        return ComplianceStatus.COMPLIANT
    if score >= 0.65:
        return ComplianceStatus.PARTIALLY_COMPLIANT
    return ComplianceStatus.NON_COMPLIANT


def risk_from_status_score(status: ComplianceStatus, score: float) -> RiskLevel:
    if status == ComplianceStatus.NON_COMPLIANT:
        if score < 0.25:
            return RiskLevel.CRITICAL
        return RiskLevel.HIGH
    if status == ComplianceStatus.PARTIALLY_COMPLIANT:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def stable_hash(value: Any) -> str:
    raw = json.dumps(to_json_safe(value), ensure_ascii=False, sort_keys=True, default=str)
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
# Default catalog factory
# -----------------------------------------------------------------------------


def build_default_compliance_engine() -> ComplianceEngine:
    repo = InMemoryComplianceRepository()

    requirements = [
        ComplianceRequirement(
            requirement_id="LGPD-SEC-01",
            title="Security safeguards",
            description="Personal data must be protected using technical and administrative security measures.",
            framework=ComplianceFramework.LGPD,
            section="Art. 46",
            domain=ControlDomain.DATA_PROTECTION,
        ),
        ComplianceRequirement(
            requirement_id="LGPD-ACCESS-01",
            title="Access limitation",
            description="Access to personal data must be limited to authorized personnel and legitimate purposes.",
            framework=ComplianceFramework.LGPD,
            section="Art. 6/46",
            domain=ControlDomain.ACCESS_CONTROL,
        ),
        ComplianceRequirement(
            requirement_id="SOC2-CC6.1",
            title="Logical access controls",
            description="Logical access controls restrict access to systems and protected information assets.",
            framework=ComplianceFramework.SOC2,
            section="CC6.1",
            domain=ControlDomain.ACCESS_CONTROL,
        ),
        ComplianceRequirement(
            requirement_id="ISO-A.8.2",
            title="Privileged access rights",
            description="Privileged access rights must be restricted and managed.",
            framework=ComplianceFramework.ISO_27001,
            section="A.8.2",
            domain=ControlDomain.ACCESS_CONTROL,
        ),
    ]
    for requirement in requirements:
        repo.upsert_requirement(requirement)

    controls = [
        ComplianceControl(
            control_id="DG-AC-001",
            title="Quarterly access review",
            description="Access to sensitive data assets is reviewed at least quarterly.",
            domain=ControlDomain.ACCESS_CONTROL,
            control_type=ControlType.DETECTIVE,
            frequency=ControlFrequency.QUARTERLY,
            owner_id="access-governance-owner",
            requirements=("LGPD-ACCESS-01", "SOC2-CC6.1", "ISO-A.8.2"),
            evidence_types_required=(EvidenceType.REPORT, EvidenceType.ATTESTATION),
            automated=False,
            weight=1.5,
            tags=("access_review", "sensitive_data"),
        ),
        ComplianceControl(
            control_id="DG-DP-001",
            title="Encryption for restricted data",
            description="Restricted and highly restricted datasets are encrypted at rest and in transit.",
            domain=ControlDomain.DATA_PROTECTION,
            control_type=ControlType.PREVENTIVE,
            frequency=ControlFrequency.CONTINUOUS,
            owner_id="security-owner",
            requirements=("LGPD-SEC-01",),
            evidence_types_required=(EvidenceType.CONFIGURATION, EvidenceType.TEST_RESULT),
            automated=True,
            weight=2.0,
            tags=("encryption", "data_protection"),
        ),
        ComplianceControl(
            control_id="DG-MON-001",
            title="Audit logging enabled",
            description="Access and governance decisions are logged with integrity controls.",
            domain=ControlDomain.MONITORING,
            control_type=ControlType.DETECTIVE,
            frequency=ControlFrequency.CONTINUOUS,
            owner_id="platform-owner",
            requirements=("LGPD-SEC-01", "SOC2-CC6.1"),
            evidence_types_required=(EvidenceType.LOG, EvidenceType.CONFIGURATION),
            automated=True,
            weight=1.2,
            tags=("audit", "logging"),
        ),
    ]
    for control in controls:
        repo.upsert_control(control)

    now = dt.datetime.now(dt.timezone.utc)
    repo.upsert_evidence(
        EvidenceRecord(
            evidence_id="ev-access-review-q1",
            title="Q1 access review report",
            evidence_type=EvidenceType.REPORT,
            control_ids=("DG-AC-001",),
            source_system="access-governance",
            collected_at=now - dt.timedelta(days=30),
            trust_level=EvidenceTrustLevel.HIGH,
            payload={"reviewed_entitlements": 120, "revoked": 8, "approved": 112},
        )
    )
    repo.upsert_evidence(
        EvidenceRecord(
            evidence_id="ev-audit-config",
            title="Audit logging configuration",
            evidence_type=EvidenceType.CONFIGURATION,
            control_ids=("DG-MON-001",),
            source_system="platform-config",
            collected_at=now - dt.timedelta(days=1),
            trust_level=EvidenceTrustLevel.AUTHORITATIVE,
            payload={"audit_logging": True, "hash_chain": True},
        )
    )

    return ComplianceEngine(repository=repo)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    engine = build_default_compliance_engine()
    report = engine.assess_framework(ComplianceFramework.LGPD, assessor_id="compliance-analyst")
    print(json.dumps(engine.executive_summary(report), indent=2, ensure_ascii=False, default=str))
    print(engine.export_report(report))

"""
data/validation/compliance_validator.py

Enterprise-grade compliance validation engine.

This module validates records, datasets, pipeline outputs and AI/data operations
against compliance policies such as privacy, security, retention, governance,
contractual obligations and internal controls.

Core capabilities:

- Policy and control definitions
- Rule-based compliance checks
- Evidence collection
- Severity/risk scoring
- Blocking/review/allow decisions
- Dataset and record validation
- Batch execution
- Audit and metrics hooks
- Exception-safe execution
- Lightweight dependency-free defaults

Python:
    3.10+
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)


class ComplianceValidationError(Exception):
    """Base exception for compliance validation."""


class ComplianceConfigurationError(ComplianceValidationError):
    """Raised when compliance configuration is invalid."""


class ComplianceRuleExecutionError(ComplianceValidationError):
    """Raised when a compliance rule fails unexpectedly."""


class ComplianceSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ComplianceStatus(str, Enum):
    COMPLIANT = "compliant"
    WARNING = "warning"
    NON_COMPLIANT = "non_compliant"
    REVIEW_REQUIRED = "review_required"
    ERROR = "error"


class ComplianceDecision(str, Enum):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


class ComplianceDomain(str, Enum):
    PRIVACY = "privacy"
    SECURITY = "security"
    DATA_QUALITY = "data_quality"
    RETENTION = "retention"
    GOVERNANCE = "governance"
    CONTRACT = "contract"
    AI_SAFETY = "ai_safety"
    FINANCIAL = "financial"
    LEGAL = "legal"
    OPERATIONAL = "operational"


class ComplianceScope(str, Enum):
    RECORD = "record"
    DATASET = "dataset"
    PIPELINE = "pipeline"
    MODEL_OUTPUT = "model_output"
    SYSTEM = "system"


@dataclass(frozen=True)
class ComplianceValidatorConfig:
    """Compliance validator configuration."""

    fail_fast: bool = False
    audit_enabled: bool = True
    metrics_enabled: bool = True
    include_passed_controls: bool = False
    max_evidence_chars: int = 2_000
    block_on_critical: bool = True
    review_on_high: bool = True
    default_version: str = "1.0.0"

    def validate(self) -> None:
        if self.max_evidence_chars < 0:
            raise ComplianceConfigurationError("max_evidence_chars must be >= 0")


@dataclass(frozen=True)
class ComplianceContext:
    """Execution context for compliance validation."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    application: Optional[str] = None
    pipeline_id: Optional[str] = None
    dataset_id: Optional[str] = None
    environment: Optional[str] = None
    jurisdiction: Optional[str] = None
    trace_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComplianceEvidence:
    """Evidence attached to a compliance finding."""

    key: str
    value: Any
    source: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComplianceFinding:
    """One compliance finding."""

    finding_id: str
    control_id: str
    policy_id: str
    domain: ComplianceDomain
    scope: ComplianceScope
    status: ComplianceStatus
    severity: ComplianceSeverity
    message: str
    field: Optional[str] = None
    evidence: Sequence[ComplianceEvidence] = field(default_factory=tuple)
    remediation: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComplianceControl:
    """Compliance control definition."""

    control_id: str
    name: str
    domain: ComplianceDomain
    scope: ComplianceScope
    description: str
    severity: ComplianceSeverity = ComplianceSeverity.MEDIUM
    enabled: bool = True
    required: bool = True
    tags: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.control_id:
            raise ComplianceConfigurationError("control_id is required")
        if not self.name:
            raise ComplianceConfigurationError("control name is required")


@dataclass(frozen=True)
class CompliancePolicy:
    """Compliance policy grouping controls."""

    policy_id: str
    name: str
    version: str
    domains: Sequence[ComplianceDomain]
    controls: Sequence[ComplianceControl]
    description: Optional[str] = None
    enabled: bool = True
    jurisdictions: Sequence[str] = field(default_factory=tuple)
    applications: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.policy_id:
            raise ComplianceConfigurationError("policy_id is required")
        if not self.name:
            raise ComplianceConfigurationError("policy name is required")
        if not self.version:
            raise ComplianceConfigurationError("policy version is required")
        for control in self.controls:
            control.validate()

    def applies_to(self, context: ComplianceContext) -> bool:
        if not self.enabled:
            return False
        if self.jurisdictions and context.jurisdiction not in self.jurisdictions:
            return False
        if self.applications and context.application not in self.applications:
            return False
        return True


@dataclass(frozen=True)
class ComplianceRuleResult:
    """Result returned by a rule implementation."""

    passed: bool
    message: str
    severity: Optional[ComplianceSeverity] = None
    field: Optional[str] = None
    evidence: Sequence[ComplianceEvidence] = field(default_factory=tuple)
    remediation: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComplianceReport:
    """Compliance validation report."""

    report_id: str
    request_id: str
    created_at: str
    status: ComplianceStatus
    decision: ComplianceDecision
    risk_score: float
    policies_evaluated: int
    controls_evaluated: int
    passed_controls: int
    failed_controls: int
    warning_controls: int
    findings: Sequence[ComplianceFinding]
    recommendations: Sequence[str]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.decision == ComplianceDecision.ALLOW

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


class ComplianceRule(Protocol):
    """Protocol for compliance rule implementations."""

    async def evaluate(
        self,
        data: Any,
        *,
        control: ComplianceControl,
        policy: CompliancePolicy,
        context: ComplianceContext,
    ) -> ComplianceRuleResult:
        """Evaluate data against a control."""


class AuditSink(Protocol):
    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        """Emit audit event."""


class MetricsSink(Protocol):
    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Increment metric."""

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Observe metric."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def get_path(data: Any, path: str, default: Any = None) -> Any:
    """Read nested values using dot notation from mappings/objects."""

    current = data
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part, default)
        else:
            current = getattr(current, part, default)
        if current is default:
            return default
    return current


def truncate(value: Any, max_chars: int) -> str:
    text = str(value)
    if max_chars and len(text) > max_chars:
        return text[: max_chars - 15] + "...[TRUNCATED]"
    return text


class LoggingAuditSink:
    """Logging-based audit sink."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        self.logger.info("compliance_audit=%s payload=%s", event_name, safe_json(payload))


class LoggingMetricsSink:
    """Logging-based metrics sink."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("compliance_metric_counter=%s value=%s tags=%s", name, value, dict(tags or {}))

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("compliance_metric_observe=%s value=%s tags=%s", name, value, dict(tags or {}))


class RequiredFieldRule:
    """Rule that requires a field to be present and non-empty."""

    def __init__(self, field: str) -> None:
        self.field = field

    async def evaluate(
        self,
        data: Any,
        *,
        control: ComplianceControl,
        policy: CompliancePolicy,
        context: ComplianceContext,
    ) -> ComplianceRuleResult:
        await asyncio.sleep(0)
        value = get_path(data, self.field)
        passed = value is not None and value != ""
        return ComplianceRuleResult(
            passed=passed,
            message=f"Required field '{self.field}' {'is present' if passed else 'is missing'}.",
            field=self.field,
            evidence=(ComplianceEvidence(key=self.field, value=value, source="payload"),),
            remediation=None if passed else f"Populate required field '{self.field}'.",
        )


class RegexDenyRule:
    """Rule that fails if a regex pattern appears in selected text fields."""

    def __init__(self, *, pattern: str, fields: Sequence[str], message: str, flags: int = re.IGNORECASE) -> None:
        self.pattern = re.compile(pattern, flags)
        self.fields = tuple(fields)
        self.message = message

    async def evaluate(
        self,
        data: Any,
        *,
        control: ComplianceControl,
        policy: CompliancePolicy,
        context: ComplianceContext,
    ) -> ComplianceRuleResult:
        await asyncio.sleep(0)
        matches: List[ComplianceEvidence] = []
        for field in self.fields:
            value = get_path(data, field)
            if value is None:
                continue
            text = str(value)
            if self.pattern.search(text):
                matches.append(ComplianceEvidence(key=field, value="pattern_match", source="regex"))
        passed = not matches
        return ComplianceRuleResult(
            passed=passed,
            message="No denied pattern found." if passed else self.message,
            evidence=tuple(matches),
            remediation=None if passed else "Remove, mask or tokenize the prohibited/sensitive value.",
        )


class AllowedValuesRule:
    """Rule that requires a field value to be within an allowed set."""

    def __init__(self, field: str, allowed_values: Sequence[Any]) -> None:
        self.field = field
        self.allowed_values = set(allowed_values)

    async def evaluate(
        self,
        data: Any,
        *,
        control: ComplianceControl,
        policy: CompliancePolicy,
        context: ComplianceContext,
    ) -> ComplianceRuleResult:
        await asyncio.sleep(0)
        value = get_path(data, self.field)
        passed = value in self.allowed_values
        return ComplianceRuleResult(
            passed=passed,
            message=f"Field '{self.field}' is within allowed values." if passed else f"Field '{self.field}' has a disallowed value.",
            field=self.field,
            evidence=(ComplianceEvidence(key=self.field, value=value, source="payload"),),
            remediation=None if passed else f"Use one of the allowed values: {sorted(map(str, self.allowed_values))}.",
        )


class CallableComplianceRule:
    """Adapter for custom async/sync rule callables."""

    def __init__(self, func: Callable[..., ComplianceRuleResult]) -> None:
        self.func = func

    async def evaluate(
        self,
        data: Any,
        *,
        control: ComplianceControl,
        policy: CompliancePolicy,
        context: ComplianceContext,
    ) -> ComplianceRuleResult:
        result = self.func(data, control=control, policy=policy, context=context)
        if asyncio.iscoroutine(result):
            result = await result
        if not isinstance(result, ComplianceRuleResult):
            raise ComplianceRuleExecutionError("custom rule must return ComplianceRuleResult")
        return result


class ComplianceValidator:
    """Enterprise compliance validator."""

    def __init__(
        self,
        *,
        policies: Sequence[CompliancePolicy],
        rules: Mapping[str, ComplianceRule],
        config: Optional[ComplianceValidatorConfig] = None,
        audit_sink: Optional[AuditSink] = None,
        metrics_sink: Optional[MetricsSink] = None,
    ) -> None:
        self.config = config or ComplianceValidatorConfig()
        self.config.validate()
        self.policies = tuple(policies)
        self.rules = dict(rules)
        self.audit_sink = audit_sink or LoggingAuditSink()
        self.metrics_sink = metrics_sink or LoggingMetricsSink()
        for policy in self.policies:
            policy.validate()

    async def validate(
        self,
        data: Any,
        *,
        context: Optional[ComplianceContext] = None,
        scope: Optional[ComplianceScope] = None,
    ) -> ComplianceReport:
        """Validate data against applicable compliance policies."""

        context = context or ComplianceContext()
        started = time.perf_counter()
        findings: List[ComplianceFinding] = []
        policies_evaluated = 0
        controls_evaluated = 0
        passed_controls = 0
        warning_controls = 0
        failed_controls = 0

        try:
            applicable_policies = [policy for policy in self.policies if policy.applies_to(context)]
            policies_evaluated = len(applicable_policies)

            for policy in applicable_policies:
                for control in policy.controls:
                    if not control.enabled:
                        continue
                    if scope and control.scope != scope:
                        continue
                    controls_evaluated += 1
                    rule = self.rules.get(control.control_id)
                    if rule is None:
                        finding = self._finding_from_missing_rule(policy, control)
                        findings.append(finding)
                        failed_controls += 1
                        if self.config.fail_fast:
                            raise ComplianceRuleExecutionError(f"No rule registered for control {control.control_id}")
                        continue

                    try:
                        result = await rule.evaluate(data, control=control, policy=policy, context=context)
                        finding = self._finding_from_rule_result(policy, control, result)
                        if result.passed:
                            passed_controls += 1
                            if self.config.include_passed_controls:
                                findings.append(finding)
                        else:
                            findings.append(finding)
                            if finding.severity in {ComplianceSeverity.INFO, ComplianceSeverity.LOW}:
                                warning_controls += 1
                            else:
                                failed_controls += 1
                            if self.config.fail_fast:
                                break
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Compliance rule failed: %s", control.control_id)
                        finding = self._finding_from_exception(policy, control, exc)
                        findings.append(finding)
                        failed_controls += 1
                        if self.config.fail_fast:
                            raise

                if self.config.fail_fast and any(f.severity in {ComplianceSeverity.HIGH, ComplianceSeverity.CRITICAL} for f in findings):
                    break

            report = self._build_report(
                context=context,
                findings=findings,
                policies_evaluated=policies_evaluated,
                controls_evaluated=controls_evaluated,
                passed_controls=passed_controls,
                warning_controls=warning_controls,
                failed_controls=failed_controls,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            await self._record_success(context, report)
            await self._audit_completed(context, report)
            return report

        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            await self._record_failure(context, exc, latency_ms)
            await self._audit_failure(context, exc, latency_ms)
            raise

    def validate_sync(
        self,
        data: Any,
        *,
        context: Optional[ComplianceContext] = None,
        scope: Optional[ComplianceScope] = None,
    ) -> ComplianceReport:
        """Synchronous convenience wrapper."""

        return asyncio.run(self.validate(data, context=context, scope=scope))

    async def validate_many(
        self,
        records: Sequence[Any],
        *,
        context: Optional[ComplianceContext] = None,
        concurrency: int = 10,
    ) -> Sequence[ComplianceReport]:
        """Validate many records with bounded concurrency."""

        if concurrency <= 0:
            raise ComplianceConfigurationError("concurrency must be positive")
        semaphore = asyncio.Semaphore(concurrency)

        async def run_one(record: Any) -> ComplianceReport:
            async with semaphore:
                return await self.validate(record, context=context, scope=ComplianceScope.RECORD)

        return tuple(await asyncio.gather(*(run_one(record) for record in records)))

    def _finding_from_rule_result(
        self,
        policy: CompliancePolicy,
        control: ComplianceControl,
        result: ComplianceRuleResult,
    ) -> ComplianceFinding:
        status = ComplianceStatus.COMPLIANT if result.passed else ComplianceStatus.NON_COMPLIANT
        severity = result.severity or (ComplianceSeverity.INFO if result.passed else control.severity)
        evidence = tuple(
            ComplianceEvidence(
                key=item.key,
                value=truncate(item.value, self.config.max_evidence_chars),
                source=item.source,
                metadata=item.metadata,
            )
            for item in result.evidence
        )
        return ComplianceFinding(
            finding_id=str(uuid.uuid4()),
            control_id=control.control_id,
            policy_id=policy.policy_id,
            domain=control.domain,
            scope=control.scope,
            status=status,
            severity=severity,
            message=result.message,
            field=result.field,
            evidence=evidence,
            remediation=result.remediation,
            metadata={**dict(control.metadata), **dict(result.metadata)},
        )

    def _finding_from_missing_rule(self, policy: CompliancePolicy, control: ComplianceControl) -> ComplianceFinding:
        return ComplianceFinding(
            finding_id=str(uuid.uuid4()),
            control_id=control.control_id,
            policy_id=policy.policy_id,
            domain=control.domain,
            scope=control.scope,
            status=ComplianceStatus.ERROR,
            severity=ComplianceSeverity.HIGH,
            message=f"No rule registered for control '{control.control_id}'.",
            remediation="Register a rule implementation for this control.",
        )

    def _finding_from_exception(self, policy: CompliancePolicy, control: ComplianceControl, exc: BaseException) -> ComplianceFinding:
        return ComplianceFinding(
            finding_id=str(uuid.uuid4()),
            control_id=control.control_id,
            policy_id=policy.policy_id,
            domain=control.domain,
            scope=control.scope,
            status=ComplianceStatus.ERROR,
            severity=ComplianceSeverity.HIGH,
            message=f"Control execution failed: {type(exc).__name__}: {exc}",
            remediation="Inspect rule implementation and input payload.",
            metadata={"error_type": type(exc).__name__},
        )

    def _build_report(
        self,
        *,
        context: ComplianceContext,
        findings: Sequence[ComplianceFinding],
        policies_evaluated: int,
        controls_evaluated: int,
        passed_controls: int,
        warning_controls: int,
        failed_controls: int,
        latency_ms: float,
    ) -> ComplianceReport:
        risk_score = self._risk_score(findings)
        decision = self._decision(findings, risk_score)
        status = self._status(decision, findings)
        return ComplianceReport(
            report_id=str(uuid.uuid4()),
            request_id=context.request_id,
            created_at=utc_now_iso(),
            status=status,
            decision=decision,
            risk_score=risk_score,
            policies_evaluated=policies_evaluated,
            controls_evaluated=controls_evaluated,
            passed_controls=passed_controls,
            failed_controls=failed_controls,
            warning_controls=warning_controls,
            findings=tuple(findings),
            recommendations=tuple(self._recommendations(findings, decision)),
            metadata={
                "tenant_id": context.tenant_id,
                "application": context.application,
                "dataset_id": context.dataset_id,
                "pipeline_id": context.pipeline_id,
                "jurisdiction": context.jurisdiction,
                "latency_ms": round(latency_ms, 3),
                "validator_version": self.config.default_version,
            },
        )

    def _risk_score(self, findings: Sequence[ComplianceFinding]) -> float:
        weights = {
            ComplianceSeverity.INFO: 0.02,
            ComplianceSeverity.LOW: 0.12,
            ComplianceSeverity.MEDIUM: 0.35,
            ComplianceSeverity.HIGH: 0.70,
            ComplianceSeverity.CRITICAL: 1.0,
        }
        non_compliant = [f for f in findings if f.status != ComplianceStatus.COMPLIANT]
        if not non_compliant:
            return 0.0
        max_weight = max(weights[f.severity] for f in non_compliant)
        avg_weight = sum(weights[f.severity] for f in non_compliant) / len(non_compliant)
        return clamp((max_weight * 0.70) + (avg_weight * 0.30))

    def _decision(self, findings: Sequence[ComplianceFinding], risk_score: float) -> ComplianceDecision:
        severities = {f.severity for f in findings if f.status != ComplianceStatus.COMPLIANT}
        if self.config.block_on_critical and ComplianceSeverity.CRITICAL in severities:
            return ComplianceDecision.BLOCK
        if risk_score >= 0.82:
            return ComplianceDecision.BLOCK
        if self.config.review_on_high and ComplianceSeverity.HIGH in severities:
            return ComplianceDecision.REVIEW
        if risk_score >= 0.35:
            return ComplianceDecision.REVIEW
        return ComplianceDecision.ALLOW

    def _status(self, decision: ComplianceDecision, findings: Sequence[ComplianceFinding]) -> ComplianceStatus:
        if decision == ComplianceDecision.BLOCK:
            return ComplianceStatus.NON_COMPLIANT
        if decision == ComplianceDecision.REVIEW:
            return ComplianceStatus.REVIEW_REQUIRED
        if any(f.status != ComplianceStatus.COMPLIANT for f in findings):
            return ComplianceStatus.WARNING
        return ComplianceStatus.COMPLIANT

    def _recommendations(self, findings: Sequence[ComplianceFinding], decision: ComplianceDecision) -> List[str]:
        recs: List[str] = []
        if decision == ComplianceDecision.BLOCK:
            recs.append("Block downstream processing until critical compliance issues are remediated.")
        elif decision == ComplianceDecision.REVIEW:
            recs.append("Route this payload to compliance or data governance review.")
        else:
            recs.append("Compliance checks passed or only low-risk warnings were detected.")
        for finding in findings:
            if finding.remediation and finding.status != ComplianceStatus.COMPLIANT:
                recs.append(finding.remediation)
        return list(dict.fromkeys(recs))

    async def _record_success(self, context: ComplianceContext, report: ComplianceReport) -> None:
        if not self.config.metrics_enabled:
            return
        tags = self._metric_tags(context, report.decision)
        await self.metrics_sink.increment("data.validation.compliance.success", 1, tags)
        await self.metrics_sink.observe("data.validation.compliance.risk_score", report.risk_score, tags)
        await self.metrics_sink.observe("data.validation.compliance.findings", len(report.findings), tags)

    async def _record_failure(self, context: ComplianceContext, exc: BaseException, latency_ms: float) -> None:
        if not self.config.metrics_enabled:
            return
        tags = {**self._metric_tags(context, ComplianceDecision.BLOCK), "error_type": type(exc).__name__}
        await self.metrics_sink.increment("data.validation.compliance.failure", 1, tags)
        await self.metrics_sink.observe("data.validation.compliance.failure_latency_ms", latency_ms, tags)

    def _metric_tags(self, context: ComplianceContext, decision: ComplianceDecision) -> Mapping[str, str]:
        return {
            "tenant_id": context.tenant_id or "unknown",
            "application": context.application or "unknown",
            "environment": context.environment or "unknown",
            "jurisdiction": context.jurisdiction or "unknown",
            "decision": decision.value,
        }

    async def _audit_completed(self, context: ComplianceContext, report: ComplianceReport) -> None:
        if not self.config.audit_enabled:
            return
        await self.audit_sink.emit("compliance_validation_completed", {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": context.request_id,
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "application": context.application,
            "pipeline_id": context.pipeline_id,
            "dataset_id": context.dataset_id,
            "environment": context.environment,
            "jurisdiction": context.jurisdiction,
            "trace_id": context.trace_id,
            "report_id": report.report_id,
            "status": report.status.value,
            "decision": report.decision.value,
            "risk_score": report.risk_score,
            "policies_evaluated": report.policies_evaluated,
            "controls_evaluated": report.controls_evaluated,
            "findings": [asdict(f) for f in report.findings],
        })

    async def _audit_failure(self, context: ComplianceContext, exc: BaseException, latency_ms: float) -> None:
        if not self.config.audit_enabled:
            return
        await self.audit_sink.emit("compliance_validation_failed", {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": context.request_id,
            "tenant_id": context.tenant_id,
            "application": context.application,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "latency_ms": round(latency_ms, 3),
        })


# =============================================================================
# Default policy factory
# =============================================================================


def build_default_compliance_policy() -> Tuple[CompliancePolicy, Mapping[str, ComplianceRule]]:
    """Build a pragmatic default compliance policy and rules."""

    controls = (
        ComplianceControl(
            control_id="privacy.no_plain_email",
            name="No plain email leakage",
            domain=ComplianceDomain.PRIVACY,
            scope=ComplianceScope.RECORD,
            description="Detects plain email addresses in text fields.",
            severity=ComplianceSeverity.MEDIUM,
        ),
        ComplianceControl(
            control_id="security.no_secret_tokens",
            name="No secret tokens",
            domain=ComplianceDomain.SECURITY,
            scope=ComplianceScope.RECORD,
            description="Detects common API key or secret token patterns.",
            severity=ComplianceSeverity.CRITICAL,
        ),
        ComplianceControl(
            control_id="governance.owner_required",
            name="Owner required",
            domain=ComplianceDomain.GOVERNANCE,
            scope=ComplianceScope.RECORD,
            description="Requires an owner field for governance traceability.",
            severity=ComplianceSeverity.HIGH,
        ),
        ComplianceControl(
            control_id="retention.classification_required",
            name="Retention classification required",
            domain=ComplianceDomain.RETENTION,
            scope=ComplianceScope.RECORD,
            description="Requires data classification for retention policy enforcement.",
            severity=ComplianceSeverity.MEDIUM,
        ),
    )
    policy = CompliancePolicy(
        policy_id="default_enterprise_compliance",
        name="Default Enterprise Compliance Policy",
        version="1.0.0",
        domains=(ComplianceDomain.PRIVACY, ComplianceDomain.SECURITY, ComplianceDomain.GOVERNANCE, ComplianceDomain.RETENTION),
        controls=controls,
    )
    rules: Dict[str, ComplianceRule] = {
        "privacy.no_plain_email": RegexDenyRule(
            pattern=r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
            fields=("text", "description", "content", "payload"),
            message="Plain email address detected in payload.",
        ),
        "security.no_secret_tokens": RegexDenyRule(
            pattern=r"\b(?:sk-|ghp_|xoxb-|AKIA)[A-Za-z0-9_\-]{12,}\b",
            fields=("text", "description", "content", "payload", "token", "api_key"),
            message="Potential secret token detected.",
        ),
        "governance.owner_required": RequiredFieldRule("owner"),
        "retention.classification_required": RequiredFieldRule("classification"),
    }
    return policy, rules


def build_default_compliance_validator(
    *,
    config: Optional[ComplianceValidatorConfig] = None,
) -> ComplianceValidator:
    policy, rules = build_default_compliance_policy()
    return ComplianceValidator(policies=(policy,), rules=rules, config=config)


async def _demo_async() -> None:
    logging.basicConfig(level=logging.INFO)
    validator = build_default_compliance_validator()
    payload = {
        "owner": "data-governance",
        "classification": "internal",
        "text": "Documento sem segredo aparente.",
    }
    report = await validator.validate(
        payload,
        context=ComplianceContext(
            tenant_id="demo",
            application="data-platform",
            environment="dev",
            jurisdiction="BR",
        ),
    )
    print(report.to_json(indent=2))


if __name__ == "__main__":
    asyncio.run(_demo_async())

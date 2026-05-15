"""
data/validation/contract_validator.py

Enterprise-grade data contract validation engine.

This module validates datasets, records, schemas and pipeline payloads against
formal data contracts. It is designed for enterprise data platforms where data
products must respect schema definitions, ownership, classification, quality
expectations, SLAs, compatibility rules and governance metadata.

Core capabilities:

- Data contract model and versioning
- Field schema validation
- Required/nullable/default checks
- Type validation and coercion-safe checks
- Enum/domain validation
- Regex/pattern validation
- Numeric/string/date constraints
- Dataset-level quality expectations
- SLA and freshness validation
- Ownership/classification validation
- Backward/forward compatibility checks
- Breaking-change detection
- Custom contract rules
- Audit and metrics hooks
- Batch validation
- Dependency-light defaults

Python:
    3.10+
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import statistics
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class ContractValidationError(Exception):
    """Base exception for contract validation."""


class ContractConfigurationError(ContractValidationError):
    """Raised when contract configuration is invalid."""


class ContractInputError(ContractValidationError):
    """Raised when validation input is invalid."""


class ContractRuleExecutionError(ContractValidationError):
    """Raised when custom rule execution fails."""


class ContractCompatibilityError(ContractValidationError):
    """Raised when contract compatibility validation fails."""


# =============================================================================
# Enums
# =============================================================================


class ContractStatus(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class ContractDecision(str, Enum):
    APPROVE = "approve"
    REVIEW = "review"
    BLOCK = "block"


class ContractSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class CompatibilityMode(str, Enum):
    NONE = "none"
    BACKWARD = "backward"
    FORWARD = "forward"
    FULL = "full"


class FieldType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    ARRAY = "array"
    OBJECT = "object"
    ANY = "any"


class DataClassification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class QualityRuleType(str, Enum):
    NOT_NULL_RATE = "not_null_rate"
    UNIQUE_RATE = "unique_rate"
    MIN_VALUE = "min_value"
    MAX_VALUE = "max_value"
    MEAN_BETWEEN = "mean_between"
    ROW_COUNT_MIN = "row_count_min"
    ROW_COUNT_MAX = "row_count_max"
    ACCEPTED_VALUES = "accepted_values"
    CUSTOM = "custom"


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class ContractValidatorConfig:
    """Contract validator configuration."""

    fail_fast: bool = False
    audit_enabled: bool = True
    metrics_enabled: bool = True
    include_passed_checks: bool = False
    block_on_critical: bool = True
    review_on_error: bool = True
    max_evidence_chars: int = 2_000
    allow_extra_fields_by_default: bool = True
    validate_all_records: bool = True
    sample_size: int = 1_000
    version: str = "1.0.0"

    def validate(self) -> None:
        if self.max_evidence_chars < 0:
            raise ContractConfigurationError("max_evidence_chars must be >= 0")
        if self.sample_size <= 0:
            raise ContractConfigurationError("sample_size must be positive")


@dataclass(frozen=True)
class ContractContext:
    """Execution context for contract validation."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    application: Optional[str] = None
    pipeline_id: Optional[str] = None
    dataset_id: Optional[str] = None
    producer: Optional[str] = None
    consumer: Optional[str] = None
    environment: Optional[str] = None
    trace_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContractVersion:
    """Semantic contract version."""

    major: int
    minor: int = 0
    patch: int = 0

    def __post_init__(self) -> None:
        if self.major < 0 or self.minor < 0 or self.patch < 0:
            raise ContractConfigurationError("contract version parts must be >= 0")

    @classmethod
    def parse(cls, value: str | "ContractVersion") -> "ContractVersion":
        if isinstance(value, ContractVersion):
            return value
        parts = str(value).strip().split(".")
        if not 1 <= len(parts) <= 3:
            raise ContractConfigurationError(f"invalid contract version: {value}")
        numbers = [int(part) for part in parts] + [0] * (3 - len(parts))
        return cls(numbers[0], numbers[1], numbers[2])

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class FieldConstraint:
    """Validation constraints for one field."""

    min_value: Optional[float] = None
    max_value: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None
    accepted_values: Optional[Sequence[Any]] = None
    item_type: Optional[FieldType] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContractField:
    """Field definition in a data contract."""

    name: str
    field_type: FieldType
    required: bool = True
    nullable: bool = False
    description: Optional[str] = None
    classification: DataClassification = DataClassification.INTERNAL
    constraints: FieldConstraint = field(default_factory=FieldConstraint)
    aliases: Sequence[str] = field(default_factory=tuple)
    deprecated: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.name:
            raise ContractConfigurationError("contract field name is required")
        if self.constraints.min_length is not None and self.constraints.min_length < 0:
            raise ContractConfigurationError(f"field {self.name} min_length must be >= 0")
        if self.constraints.max_length is not None and self.constraints.max_length < 0:
            raise ContractConfigurationError(f"field {self.name} max_length must be >= 0")
        if (
            self.constraints.min_length is not None
            and self.constraints.max_length is not None
            and self.constraints.min_length > self.constraints.max_length
        ):
            raise ContractConfigurationError(f"field {self.name} min_length cannot exceed max_length")
        if (
            self.constraints.min_value is not None
            and self.constraints.max_value is not None
            and self.constraints.min_value > self.constraints.max_value
        ):
            raise ContractConfigurationError(f"field {self.name} min_value cannot exceed max_value")
        if self.constraints.pattern:
            re.compile(self.constraints.pattern)


@dataclass(frozen=True)
class QualityExpectation:
    """Dataset or field-level quality expectation."""

    expectation_id: str
    rule_type: QualityRuleType
    severity: ContractSeverity = ContractSeverity.ERROR
    field: Optional[str] = None
    threshold: Optional[float] = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True
    description: Optional[str] = None

    def validate(self) -> None:
        if not self.expectation_id:
            raise ContractConfigurationError("expectation_id is required")


@dataclass(frozen=True)
class ServiceLevelAgreement:
    """Data contract SLA definition."""

    freshness_field: Optional[str] = None
    max_age_seconds: Optional[int] = None
    expected_frequency_seconds: Optional[int] = None
    min_rows: Optional[int] = None
    max_rows: Optional[int] = None
    availability_target: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.max_age_seconds is not None and self.max_age_seconds < 0:
            raise ContractConfigurationError("max_age_seconds must be >= 0")
        if self.expected_frequency_seconds is not None and self.expected_frequency_seconds <= 0:
            raise ContractConfigurationError("expected_frequency_seconds must be positive")
        if self.min_rows is not None and self.min_rows < 0:
            raise ContractConfigurationError("min_rows must be >= 0")
        if self.max_rows is not None and self.max_rows < 0:
            raise ContractConfigurationError("max_rows must be >= 0")
        if self.availability_target is not None and not 0 <= self.availability_target <= 1:
            raise ContractConfigurationError("availability_target must be between 0 and 1")


@dataclass(frozen=True)
class DataContract:
    """Enterprise data contract."""

    contract_id: str
    name: str
    version: ContractVersion
    fields: Sequence[ContractField]
    owner: str
    producer: Optional[str] = None
    description: Optional[str] = None
    classification: DataClassification = DataClassification.INTERNAL
    compatibility_mode: CompatibilityMode = CompatibilityMode.BACKWARD
    allow_extra_fields: Optional[bool] = None
    quality_expectations: Sequence[QualityExpectation] = field(default_factory=tuple)
    sla: Optional[ServiceLevelAgreement] = None
    tags: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.contract_id:
            raise ContractConfigurationError("contract_id is required")
        if not self.name:
            raise ContractConfigurationError("contract name is required")
        if not self.owner:
            raise ContractConfigurationError("contract owner is required")
        seen = set()
        for field_def in self.fields:
            field_def.validate()
            if field_def.name in seen:
                raise ContractConfigurationError(f"duplicate field in contract: {field_def.name}")
            seen.add(field_def.name)
        for expectation in self.quality_expectations:
            expectation.validate()
        if self.sla:
            self.sla.validate()

    @property
    def field_map(self) -> Mapping[str, ContractField]:
        return {item.name: item for item in self.fields}


@dataclass(frozen=True)
class ContractEvidence:
    """Evidence for a contract finding."""

    key: str
    value: Any
    expected: Optional[Any] = None
    actual: Optional[Any] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContractFinding:
    """One contract validation finding."""

    finding_id: str
    check_id: str
    status: ContractStatus
    severity: ContractSeverity
    message: str
    field: Optional[str] = None
    record_index: Optional[int] = None
    evidence: Sequence[ContractEvidence] = field(default_factory=tuple)
    remediation: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContractReport:
    """Final contract validation report."""

    report_id: str
    request_id: str
    contract_id: str
    contract_version: str
    created_at: str
    status: ContractStatus
    decision: ContractDecision
    risk_score: float
    checks_evaluated: int
    passed_checks: int
    warning_checks: int
    failed_checks: int
    skipped_checks: int
    record_count: int
    findings: Sequence[ContractFinding]
    recommendations: Sequence[str]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.decision == ContractDecision.APPROVE

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class CompatibilityFinding:
    """Finding for contract compatibility checks."""

    finding_id: str
    status: ContractStatus
    severity: ContractSeverity
    message: str
    field: Optional[str] = None
    breaking_change: bool = False
    remediation: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CompatibilityReport:
    """Contract compatibility validation report."""

    report_id: str
    old_contract_id: str
    old_version: str
    new_contract_id: str
    new_version: str
    mode: CompatibilityMode
    compatible: bool
    breaking_changes: int
    findings: Sequence[CompatibilityFinding]
    created_at: str

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=indent, default=str)


class CustomContractRule(Protocol):
    """Custom contract rule protocol."""

    async def evaluate(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        contract: DataContract,
        expectation: QualityExpectation,
        context: ContractContext,
    ) -> ContractFinding:
        """Evaluate custom contract rule."""


class AuditSink(Protocol):
    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        """Emit audit event."""


class MetricsSink(Protocol):
    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Increment metric."""

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Observe metric value."""


# =============================================================================
# Utilities
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def truncate(value: Any, max_chars: int) -> str:
    text = str(value)
    if max_chars and len(text) > max_chars:
        return text[: max_chars - 15] + "...[TRUNCATED]"
    return text


def get_path(data: Mapping[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return default
        current = current.get(part, default)
        if current is default:
            return default
    return current


def parse_datetime_value(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def is_nullish(value: Any) -> bool:
    return value is None or value == ""


def value_matches_type(value: Any, field_type: FieldType) -> bool:
    if field_type == FieldType.ANY:
        return True
    if field_type == FieldType.STRING:
        return isinstance(value, str)
    if field_type == FieldType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if field_type in {FieldType.FLOAT, FieldType.DECIMAL}:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if field_type == FieldType.BOOLEAN:
        return isinstance(value, bool)
    if field_type == FieldType.DATE:
        return isinstance(value, date) or parse_datetime_value(value) is not None
    if field_type == FieldType.DATETIME:
        return parse_datetime_value(value) is not None
    if field_type == FieldType.ARRAY:
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
    if field_type == FieldType.OBJECT:
        return isinstance(value, Mapping)
    return False


def normalize_records(data: Any) -> Sequence[Mapping[str, Any]]:
    if isinstance(data, Mapping):
        return (data,)
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        records = []
        for item in data:
            if not isinstance(item, Mapping):
                raise ContractInputError("all dataset records must be mappings")
            records.append(item)
        return tuple(records)
    raise ContractInputError("data must be a mapping or a sequence of mappings")


# =============================================================================
# Default sinks
# =============================================================================


class LoggingAuditSink:
    """Logging-based audit sink."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        self.logger.info("contract_audit=%s payload=%s", event_name, safe_json(payload))


class LoggingMetricsSink:
    """Logging-based metrics sink."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("contract_metric_counter=%s value=%s tags=%s", name, value, dict(tags or {}))

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("contract_metric_observe=%s value=%s tags=%s", name, value, dict(tags or {}))


class CallableContractRule:
    """Adapter for custom sync/async contract rules."""

    def __init__(self, func: Callable[..., ContractFinding]) -> None:
        self.func = func

    async def evaluate(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        contract: DataContract,
        expectation: QualityExpectation,
        context: ContractContext,
    ) -> ContractFinding:
        result = self.func(records, contract=contract, expectation=expectation, context=context)
        if asyncio.iscoroutine(result):
            result = await result
        if not isinstance(result, ContractFinding):
            raise ContractRuleExecutionError("custom contract rule must return ContractFinding")
        return result


# =============================================================================
# Validator
# =============================================================================


class DataContractValidator:
    """Enterprise data contract validator."""

    def __init__(
        self,
        *,
        config: Optional[ContractValidatorConfig] = None,
        custom_rules: Optional[Mapping[str, CustomContractRule]] = None,
        audit_sink: Optional[AuditSink] = None,
        metrics_sink: Optional[MetricsSink] = None,
    ) -> None:
        self.config = config or ContractValidatorConfig()
        self.config.validate()
        self.custom_rules = dict(custom_rules or {})
        self.audit_sink = audit_sink or LoggingAuditSink()
        self.metrics_sink = metrics_sink or LoggingMetricsSink()

    async def validate(
        self,
        data: Any,
        *,
        contract: DataContract,
        context: Optional[ContractContext] = None,
    ) -> ContractReport:
        """Validate records/dataset against a data contract."""

        context = context or ContractContext(dataset_id=contract.contract_id)
        started = time.perf_counter()
        findings: List[ContractFinding] = []
        checks = passed = warnings = failed = skipped = 0

        try:
            contract.validate()
            records = normalize_records(data)
            sample = records if self.config.validate_all_records else records[: self.config.sample_size]

            ownership_findings = self._validate_contract_metadata(contract, context)
            for finding in ownership_findings:
                checks += 1
                if finding.status == ContractStatus.PASSED:
                    passed += 1
                    if self.config.include_passed_checks:
                        findings.append(finding)
                else:
                    findings.append(finding)
                    if finding.severity == ContractSeverity.WARNING:
                        warnings += 1
                    else:
                        failed += 1

            schema_findings = self._validate_schema(sample, contract)
            for finding in schema_findings:
                checks += 1
                if finding.status == ContractStatus.PASSED:
                    passed += 1
                    if self.config.include_passed_checks:
                        findings.append(finding)
                else:
                    findings.append(finding)
                    if finding.severity == ContractSeverity.WARNING:
                        warnings += 1
                    else:
                        failed += 1
                    if self.config.fail_fast:
                        break

            if not self.config.fail_fast or failed == 0:
                quality_findings = await self._validate_quality(records, contract, context)
                for finding in quality_findings:
                    checks += 1
                    if finding.status == ContractStatus.PASSED:
                        passed += 1
                        if self.config.include_passed_checks:
                            findings.append(finding)
                    else:
                        findings.append(finding)
                        if finding.severity == ContractSeverity.WARNING:
                            warnings += 1
                        else:
                            failed += 1
                        if self.config.fail_fast:
                            break

            if not self.config.fail_fast or failed == 0:
                sla_findings = self._validate_sla(records, contract)
                for finding in sla_findings:
                    checks += 1
                    if finding.status == ContractStatus.PASSED:
                        passed += 1
                        if self.config.include_passed_checks:
                            findings.append(finding)
                    else:
                        findings.append(finding)
                        if finding.severity == ContractSeverity.WARNING:
                            warnings += 1
                        else:
                            failed += 1

            report = self._build_report(
                context=context,
                contract=contract,
                records=records,
                findings=findings,
                checks_evaluated=checks,
                passed_checks=passed,
                warning_checks=warnings,
                failed_checks=failed,
                skipped_checks=skipped,
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
        contract: DataContract,
        context: Optional[ContractContext] = None,
    ) -> ContractReport:
        return asyncio.run(self.validate(data, contract=contract, context=context))

    async def validate_many(
        self,
        datasets: Sequence[Any],
        *,
        contract: DataContract,
        context: Optional[ContractContext] = None,
        concurrency: int = 5,
    ) -> Sequence[ContractReport]:
        if concurrency <= 0:
            raise ContractConfigurationError("concurrency must be positive")
        semaphore = asyncio.Semaphore(concurrency)

        async def run_one(dataset: Any) -> ContractReport:
            async with semaphore:
                return await self.validate(dataset, contract=contract, context=context)

        return tuple(await asyncio.gather(*(run_one(dataset) for dataset in datasets)))

    def check_compatibility(
        self,
        old_contract: DataContract,
        new_contract: DataContract,
        *,
        mode: Optional[CompatibilityMode] = None,
    ) -> CompatibilityReport:
        """Check contract version compatibility and breaking changes."""

        old_contract.validate()
        new_contract.validate()
        active_mode = mode or new_contract.compatibility_mode
        findings: List[CompatibilityFinding] = []
        old_fields = old_contract.field_map
        new_fields = new_contract.field_map

        if active_mode == CompatibilityMode.NONE:
            return CompatibilityReport(
                report_id=str(uuid.uuid4()),
                old_contract_id=old_contract.contract_id,
                old_version=str(old_contract.version),
                new_contract_id=new_contract.contract_id,
                new_version=str(new_contract.version),
                mode=active_mode,
                compatible=True,
                breaking_changes=0,
                findings=tuple(),
                created_at=utc_now_iso(),
            )

        for name, old_field in old_fields.items():
            new_field = new_fields.get(name)
            if not new_field:
                findings.append(
                    CompatibilityFinding(
                        finding_id=str(uuid.uuid4()),
                        status=ContractStatus.FAILED,
                        severity=ContractSeverity.CRITICAL,
                        message=f"Field removed: {name}",
                        field=name,
                        breaking_change=True,
                        remediation="Keep the field, deprecate it first, or bump major version and notify consumers.",
                    )
                )
                continue
            if old_field.field_type != new_field.field_type:
                findings.append(
                    CompatibilityFinding(
                        finding_id=str(uuid.uuid4()),
                        status=ContractStatus.FAILED,
                        severity=ContractSeverity.CRITICAL,
                        message=f"Field type changed for {name}: {old_field.field_type.value} -> {new_field.field_type.value}",
                        field=name,
                        breaking_change=True,
                        remediation="Avoid changing field type in compatible releases; add a new field instead.",
                    )
                )
            if not old_field.required and new_field.required:
                findings.append(
                    CompatibilityFinding(
                        finding_id=str(uuid.uuid4()),
                        status=ContractStatus.FAILED,
                        severity=ContractSeverity.ERROR,
                        message=f"Optional field became required: {name}",
                        field=name,
                        breaking_change=True,
                        remediation="Do not make optional fields required without a major version bump.",
                    )
                )
            if old_field.nullable and not new_field.nullable:
                findings.append(
                    CompatibilityFinding(
                        finding_id=str(uuid.uuid4()),
                        status=ContractStatus.FAILED,
                        severity=ContractSeverity.ERROR,
                        message=f"Nullable field became non-nullable: {name}",
                        field=name,
                        breaking_change=True,
                        remediation="Keep nullable compatibility or migrate consumers first.",
                    )
                )

        for name, new_field in new_fields.items():
            if name not in old_fields and new_field.required and not new_field.nullable:
                findings.append(
                    CompatibilityFinding(
                        finding_id=str(uuid.uuid4()),
                        status=ContractStatus.FAILED,
                        severity=ContractSeverity.ERROR,
                        message=f"New required non-nullable field added: {name}",
                        field=name,
                        breaking_change=True,
                        remediation="Add the field as optional/nullable first or bump major version.",
                    )
                )

        breaking = sum(1 for item in findings if item.breaking_change)
        return CompatibilityReport(
            report_id=str(uuid.uuid4()),
            old_contract_id=old_contract.contract_id,
            old_version=str(old_contract.version),
            new_contract_id=new_contract.contract_id,
            new_version=str(new_contract.version),
            mode=active_mode,
            compatible=breaking == 0,
            breaking_changes=breaking,
            findings=tuple(findings),
            created_at=utc_now_iso(),
        )

    def _validate_contract_metadata(self, contract: DataContract, context: ContractContext) -> Sequence[ContractFinding]:
        findings: List[ContractFinding] = []
        if contract.owner:
            findings.append(self._passed("contract.owner", "Contract owner is defined."))
        else:
            findings.append(
                self._finding(
                    check_id="contract.owner",
                    status=ContractStatus.FAILED,
                    severity=ContractSeverity.ERROR,
                    message="Contract owner is required.",
                    remediation="Define a responsible owner for this data contract.",
                )
            )
        if context.producer and contract.producer and context.producer != contract.producer:
            findings.append(
                self._finding(
                    check_id="contract.producer",
                    status=ContractStatus.FAILED,
                    severity=ContractSeverity.ERROR,
                    message="Context producer does not match contract producer.",
                    evidence=(ContractEvidence(key="producer", expected=contract.producer, actual=context.producer, value=context.producer),),
                    remediation="Use the correct contract for this producer or update producer metadata.",
                )
            )
        else:
            findings.append(self._passed("contract.producer", "Producer metadata is compatible."))
        return tuple(findings)

    def _validate_schema(self, records: Sequence[Mapping[str, Any]], contract: DataContract) -> Sequence[ContractFinding]:
        findings: List[ContractFinding] = []
        field_map = contract.field_map
        allow_extra = self.config.allow_extra_fields_by_default if contract.allow_extra_fields is None else contract.allow_extra_fields

        for record_index, record in enumerate(records):
            if not isinstance(record, Mapping):
                findings.append(
                    self._finding(
                        check_id="schema.record_type",
                        status=ContractStatus.FAILED,
                        severity=ContractSeverity.CRITICAL,
                        message="Record must be a mapping/object.",
                        record_index=record_index,
                    )
                )
                continue

            for field_name, field_def in field_map.items():
                value = self._field_value(record, field_def)
                findings.extend(self._validate_field_value(record_index, field_def, value))

            if not allow_extra:
                extra_fields = sorted(set(record.keys()) - set(field_map.keys()))
                for field_name in extra_fields:
                    findings.append(
                        self._finding(
                            check_id="schema.extra_field",
                            status=ContractStatus.FAILED,
                            severity=ContractSeverity.WARNING,
                            message=f"Extra field not allowed by contract: {field_name}",
                            field=field_name,
                            record_index=record_index,
                            evidence=(ContractEvidence(key=field_name, value=record.get(field_name)),),
                            remediation="Remove field or update contract to allow/define it.",
                        )
                    )

            if self.config.fail_fast and any(item.status == ContractStatus.FAILED for item in findings):
                break

        return tuple(findings)

    def _field_value(self, record: Mapping[str, Any], field_def: ContractField) -> Any:
        if field_def.name in record:
            return record[field_def.name]
        for alias in field_def.aliases:
            if alias in record:
                return record[alias]
        return None

    def _validate_field_value(self, record_index: int, field_def: ContractField, value: Any) -> Sequence[ContractFinding]:
        findings: List[ContractFinding] = []
        if field_def.required and is_nullish(value):
            findings.append(
                self._finding(
                    check_id="schema.required",
                    status=ContractStatus.FAILED,
                    severity=ContractSeverity.ERROR,
                    message=f"Required field is missing: {field_def.name}",
                    field=field_def.name,
                    record_index=record_index,
                    evidence=(ContractEvidence(key=field_def.name, value=value, expected="present", actual=value),),
                    remediation=f"Populate required field '{field_def.name}'.",
                )
            )
            return tuple(findings)

        if is_nullish(value):
            if not field_def.nullable and field_def.required:
                findings.append(
                    self._finding(
                        check_id="schema.nullable",
                        status=ContractStatus.FAILED,
                        severity=ContractSeverity.ERROR,
                        message=f"Field cannot be null: {field_def.name}",
                        field=field_def.name,
                        record_index=record_index,
                    )
                )
            else:
                findings.append(self._passed("schema.nullable", f"Nullable/optional field accepted: {field_def.name}", field=field_def.name, record_index=record_index))
            return tuple(findings)

        if not value_matches_type(value, field_def.field_type):
            findings.append(
                self._finding(
                    check_id="schema.type",
                    status=ContractStatus.FAILED,
                    severity=ContractSeverity.ERROR,
                    message=f"Field '{field_def.name}' has invalid type. Expected {field_def.field_type.value}.",
                    field=field_def.name,
                    record_index=record_index,
                    evidence=(ContractEvidence(key=field_def.name, value=value, expected=field_def.field_type.value, actual=type(value).__name__),),
                    remediation=f"Convert '{field_def.name}' to {field_def.field_type.value}.",
                )
            )
            return tuple(findings)

        findings.extend(self._validate_constraints(record_index, field_def, value))
        if not findings:
            findings.append(self._passed("schema.field", f"Field passed schema validation: {field_def.name}", field=field_def.name, record_index=record_index))
        return tuple(findings)

    def _validate_constraints(self, record_index: int, field_def: ContractField, value: Any) -> Sequence[ContractFinding]:
        findings: List[ContractFinding] = []
        constraints = field_def.constraints
        if constraints.accepted_values is not None and value not in set(constraints.accepted_values):
            findings.append(
                self._finding(
                    check_id="schema.accepted_values",
                    status=ContractStatus.FAILED,
                    severity=ContractSeverity.ERROR,
                    message=f"Field '{field_def.name}' value is not accepted.",
                    field=field_def.name,
                    record_index=record_index,
                    evidence=(ContractEvidence(key=field_def.name, value=value, expected=list(constraints.accepted_values), actual=value),),
                    remediation=f"Use an accepted value for '{field_def.name}'.",
                )
            )
        if constraints.pattern and not re.search(constraints.pattern, str(value)):
            findings.append(
                self._finding(
                    check_id="schema.pattern",
                    status=ContractStatus.FAILED,
                    severity=ContractSeverity.ERROR,
                    message=f"Field '{field_def.name}' does not match required pattern.",
                    field=field_def.name,
                    record_index=record_index,
                    evidence=(ContractEvidence(key=field_def.name, value=value, expected=constraints.pattern, actual=value),),
                    remediation=f"Format '{field_def.name}' according to the required pattern.",
                )
            )
        if isinstance(value, str):
            if constraints.min_length is not None and len(value) < constraints.min_length:
                findings.append(self._constraint_finding("schema.min_length", field_def, record_index, value, constraints.min_length, len(value)))
            if constraints.max_length is not None and len(value) > constraints.max_length:
                findings.append(self._constraint_finding("schema.max_length", field_def, record_index, value, constraints.max_length, len(value)))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if constraints.min_value is not None and float(value) < constraints.min_value:
                findings.append(self._constraint_finding("schema.min_value", field_def, record_index, value, constraints.min_value, value))
            if constraints.max_value is not None and float(value) > constraints.max_value:
                findings.append(self._constraint_finding("schema.max_value", field_def, record_index, value, constraints.max_value, value))
        if field_def.field_type == FieldType.ARRAY and constraints.item_type:
            for index, item in enumerate(value):
                if not value_matches_type(item, constraints.item_type):
                    findings.append(
                        self._finding(
                            check_id="schema.array_item_type",
                            status=ContractStatus.FAILED,
                            severity=ContractSeverity.ERROR,
                            message=f"Array field '{field_def.name}' contains invalid item type at index {index}.",
                            field=field_def.name,
                            record_index=record_index,
                            evidence=(ContractEvidence(key=f"{field_def.name}[{index}]", value=item, expected=constraints.item_type.value, actual=type(item).__name__),),
                            remediation=f"Ensure all items in '{field_def.name}' are {constraints.item_type.value}.",
                        )
                    )
        return tuple(findings)

    def _constraint_finding(self, check_id: str, field_def: ContractField, record_index: int, value: Any, expected: Any, actual: Any) -> ContractFinding:
        return self._finding(
            check_id=check_id,
            status=ContractStatus.FAILED,
            severity=ContractSeverity.ERROR,
            message=f"Constraint failed for field '{field_def.name}': {check_id}.",
            field=field_def.name,
            record_index=record_index,
            evidence=(ContractEvidence(key=field_def.name, value=value, expected=expected, actual=actual),),
            remediation=f"Adjust '{field_def.name}' to satisfy {check_id}.",
        )

    async def _validate_quality(self, records: Sequence[Mapping[str, Any]], contract: DataContract, context: ContractContext) -> Sequence[ContractFinding]:
        findings: List[ContractFinding] = []
        for expectation in contract.quality_expectations:
            if not expectation.enabled:
                continue
            if expectation.rule_type == QualityRuleType.CUSTOM:
                rule = self.custom_rules.get(expectation.expectation_id)
                if not rule:
                    findings.append(
                        self._finding(
                            check_id=expectation.expectation_id,
                            status=ContractStatus.ERROR,
                            severity=ContractSeverity.ERROR,
                            message=f"No custom rule registered for expectation {expectation.expectation_id}.",
                            remediation="Register a custom rule implementation.",
                        )
                    )
                    continue
                try:
                    findings.append(await rule.evaluate(records, contract=contract, expectation=expectation, context=context))
                except Exception as exc:  # noqa: BLE001
                    findings.append(
                        self._finding(
                            check_id=expectation.expectation_id,
                            status=ContractStatus.ERROR,
                            severity=ContractSeverity.ERROR,
                            message=f"Custom quality rule failed: {type(exc).__name__}: {exc}",
                        )
                    )
                continue
            findings.append(self._evaluate_quality_expectation(records, expectation))
        return tuple(findings)

    def _evaluate_quality_expectation(self, records: Sequence[Mapping[str, Any]], expectation: QualityExpectation) -> ContractFinding:
        total = len(records)
        field_name = expectation.field
        values = [get_path(record, field_name) for record in records] if field_name else []
        threshold = expectation.threshold
        passed = True
        actual: Any = None
        expected: Any = threshold
        message = "Quality expectation passed."

        if expectation.rule_type == QualityRuleType.ROW_COUNT_MIN:
            expected = expectation.parameters.get("min_rows", threshold)
            actual = total
            passed = actual >= int(expected)
            message = f"Row count minimum {'passed' if passed else 'failed'}."
        elif expectation.rule_type == QualityRuleType.ROW_COUNT_MAX:
            expected = expectation.parameters.get("max_rows", threshold)
            actual = total
            passed = actual <= int(expected)
            message = f"Row count maximum {'passed' if passed else 'failed'}."
        elif expectation.rule_type == QualityRuleType.NOT_NULL_RATE:
            actual = sum(1 for value in values if not is_nullish(value)) / max(total, 1)
            passed = actual >= float(threshold if threshold is not None else 1.0)
            message = f"Not-null rate for '{field_name}' {'passed' if passed else 'failed'}."
        elif expectation.rule_type == QualityRuleType.UNIQUE_RATE:
            non_null = [value for value in values if not is_nullish(value)]
            actual = len(set(non_null)) / max(len(non_null), 1)
            passed = actual >= float(threshold if threshold is not None else 1.0)
            message = f"Unique rate for '{field_name}' {'passed' if passed else 'failed'}."
        elif expectation.rule_type == QualityRuleType.ACCEPTED_VALUES:
            accepted = set(expectation.parameters.get("values", ()))
            invalid = [value for value in values if not is_nullish(value) and value not in accepted]
            actual = len(invalid)
            expected = "all values accepted"
            passed = not invalid
            message = f"Accepted values for '{field_name}' {'passed' if passed else 'failed'}."
        elif expectation.rule_type in {QualityRuleType.MIN_VALUE, QualityRuleType.MAX_VALUE, QualityRuleType.MEAN_BETWEEN}:
            numeric = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
            if expectation.rule_type == QualityRuleType.MIN_VALUE:
                expected = expectation.parameters.get("min", threshold)
                actual = min(numeric) if numeric else None
                passed = actual is not None and actual >= float(expected)
            elif expectation.rule_type == QualityRuleType.MAX_VALUE:
                expected = expectation.parameters.get("max", threshold)
                actual = max(numeric) if numeric else None
                passed = actual is not None and actual <= float(expected)
            else:
                low = float(expectation.parameters.get("min", 0))
                high = float(expectation.parameters.get("max", 0))
                actual = statistics.mean(numeric) if numeric else None
                expected = {"min": low, "max": high}
                passed = actual is not None and low <= actual <= high
            message = f"Numeric quality expectation for '{field_name}' {'passed' if passed else 'failed'}."
        else:
            message = f"Unsupported quality expectation: {expectation.rule_type.value}"
            passed = False

        return self._finding(
            check_id=expectation.expectation_id,
            status=ContractStatus.PASSED if passed else ContractStatus.FAILED,
            severity=ContractSeverity.INFO if passed else expectation.severity,
            message=message,
            field=field_name,
            evidence=(ContractEvidence(key=field_name or expectation.rule_type.value, value=actual, expected=expected, actual=actual),),
            remediation=None if passed else "Investigate data quality issue and correct upstream data or contract threshold.",
        )

    def _validate_sla(self, records: Sequence[Mapping[str, Any]], contract: DataContract) -> Sequence[ContractFinding]:
        if not contract.sla:
            return tuple()
        sla = contract.sla
        findings: List[ContractFinding] = []
        if sla.min_rows is not None:
            findings.append(
                self._finding(
                    check_id="sla.min_rows",
                    status=ContractStatus.PASSED if len(records) >= sla.min_rows else ContractStatus.FAILED,
                    severity=ContractSeverity.INFO if len(records) >= sla.min_rows else ContractSeverity.ERROR,
                    message="SLA min_rows passed." if len(records) >= sla.min_rows else "SLA min_rows failed.",
                    evidence=(ContractEvidence(key="row_count", value=len(records), expected=sla.min_rows, actual=len(records)),),
                    remediation=None if len(records) >= sla.min_rows else "Investigate missing records or update SLA.",
                )
            )
        if sla.max_rows is not None:
            findings.append(
                self._finding(
                    check_id="sla.max_rows",
                    status=ContractStatus.PASSED if len(records) <= sla.max_rows else ContractStatus.FAILED,
                    severity=ContractSeverity.INFO if len(records) <= sla.max_rows else ContractSeverity.ERROR,
                    message="SLA max_rows passed." if len(records) <= sla.max_rows else "SLA max_rows failed.",
                    evidence=(ContractEvidence(key="row_count", value=len(records), expected=sla.max_rows, actual=len(records)),),
                    remediation=None if len(records) <= sla.max_rows else "Investigate duplicate/excess records or update SLA.",
                )
            )
        if sla.freshness_field and sla.max_age_seconds is not None and records:
            values = [parse_datetime_value(get_path(record, sla.freshness_field)) for record in records]
            values = [value for value in values if value is not None]
            latest = max(values) if values else None
            if latest:
                age_seconds = (datetime.now(timezone.utc) - latest).total_seconds()
                passed = age_seconds <= sla.max_age_seconds
            else:
                age_seconds = None
                passed = False
            findings.append(
                self._finding(
                    check_id="sla.freshness",
                    status=ContractStatus.PASSED if passed else ContractStatus.FAILED,
                    severity=ContractSeverity.INFO if passed else ContractSeverity.ERROR,
                    message="SLA freshness passed." if passed else "SLA freshness failed.",
                    field=sla.freshness_field,
                    evidence=(ContractEvidence(key=sla.freshness_field, value=latest.isoformat() if latest else None, expected=f"<= {sla.max_age_seconds}s", actual=age_seconds),),
                    remediation=None if passed else "Refresh the dataset or investigate upstream delay.",
                )
            )
        return tuple(findings)

    def _finding(
        self,
        *,
        check_id: str,
        status: ContractStatus,
        severity: ContractSeverity,
        message: str,
        field: Optional[str] = None,
        record_index: Optional[int] = None,
        evidence: Sequence[ContractEvidence] = (),
        remediation: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> ContractFinding:
        evidence = tuple(
            ContractEvidence(
                key=item.key,
                value=truncate(item.value, self.config.max_evidence_chars),
                expected=truncate(item.expected, self.config.max_evidence_chars) if item.expected is not None else None,
                actual=truncate(item.actual, self.config.max_evidence_chars) if item.actual is not None else None,
                metadata=item.metadata,
            )
            for item in evidence
        )
        return ContractFinding(
            finding_id=str(uuid.uuid4()),
            check_id=check_id,
            status=status,
            severity=severity,
            message=message,
            field=field,
            record_index=record_index,
            evidence=evidence,
            remediation=remediation,
            metadata=metadata or {},
        )

    def _passed(self, check_id: str, message: str, *, field: Optional[str] = None, record_index: Optional[int] = None) -> ContractFinding:
        return self._finding(
            check_id=check_id,
            status=ContractStatus.PASSED,
            severity=ContractSeverity.INFO,
            message=message,
            field=field,
            record_index=record_index,
        )

    def _build_report(
        self,
        *,
        context: ContractContext,
        contract: DataContract,
        records: Sequence[Mapping[str, Any]],
        findings: Sequence[ContractFinding],
        checks_evaluated: int,
        passed_checks: int,
        warning_checks: int,
        failed_checks: int,
        skipped_checks: int,
        latency_ms: float,
    ) -> ContractReport:
        risk_score = self._risk_score(findings)
        decision = self._decision(findings, risk_score)
        status = self._status(decision, findings)
        return ContractReport(
            report_id=str(uuid.uuid4()),
            request_id=context.request_id,
            contract_id=contract.contract_id,
            contract_version=str(contract.version),
            created_at=utc_now_iso(),
            status=status,
            decision=decision,
            risk_score=risk_score,
            checks_evaluated=checks_evaluated,
            passed_checks=passed_checks,
            warning_checks=warning_checks,
            failed_checks=failed_checks,
            skipped_checks=skipped_checks,
            record_count=len(records),
            findings=tuple(findings),
            recommendations=tuple(self._recommendations(findings, decision)),
            metadata={
                "tenant_id": context.tenant_id,
                "application": context.application,
                "pipeline_id": context.pipeline_id,
                "dataset_id": context.dataset_id,
                "producer": context.producer,
                "consumer": context.consumer,
                "environment": context.environment,
                "validator_version": self.config.version,
                "latency_ms": round(latency_ms, 3),
            },
        )

    def _risk_score(self, findings: Sequence[ContractFinding]) -> float:
        weights = {
            ContractSeverity.INFO: 0.0,
            ContractSeverity.WARNING: 0.25,
            ContractSeverity.ERROR: 0.65,
            ContractSeverity.CRITICAL: 1.0,
        }
        failed = [item for item in findings if item.status not in {ContractStatus.PASSED, ContractStatus.SKIPPED}]
        if not failed:
            return 0.0
        max_weight = max(weights[item.severity] for item in failed)
        avg_weight = sum(weights[item.severity] for item in failed) / len(failed)
        return clamp((max_weight * 0.75) + (avg_weight * 0.25))

    def _decision(self, findings: Sequence[ContractFinding], risk_score: float) -> ContractDecision:
        severities = {item.severity for item in findings if item.status not in {ContractStatus.PASSED, ContractStatus.SKIPPED}}
        if self.config.block_on_critical and ContractSeverity.CRITICAL in severities:
            return ContractDecision.BLOCK
        if risk_score >= 0.85:
            return ContractDecision.BLOCK
        if self.config.review_on_error and ContractSeverity.ERROR in severities:
            return ContractDecision.REVIEW
        if risk_score >= 0.25:
            return ContractDecision.REVIEW
        return ContractDecision.APPROVE

    def _status(self, decision: ContractDecision, findings: Sequence[ContractFinding]) -> ContractStatus:
        if any(item.status == ContractStatus.ERROR for item in findings):
            return ContractStatus.ERROR
        if decision == ContractDecision.BLOCK:
            return ContractStatus.FAILED
        if decision == ContractDecision.REVIEW:
            return ContractStatus.WARNING
        return ContractStatus.PASSED

    def _recommendations(self, findings: Sequence[ContractFinding], decision: ContractDecision) -> List[str]:
        recs: List[str] = []
        if decision == ContractDecision.BLOCK:
            recs.append("Block downstream publication or consumption until contract violations are fixed.")
        elif decision == ContractDecision.REVIEW:
            recs.append("Route this data product to owner/governance review before promotion.")
        else:
            recs.append("Data contract validation passed within configured thresholds.")
        for finding in findings:
            if finding.status != ContractStatus.PASSED and finding.remediation:
                recs.append(finding.remediation)
        return list(dict.fromkeys(recs))

    async def _record_success(self, context: ContractContext, report: ContractReport) -> None:
        if not self.config.metrics_enabled:
            return
        tags = self._metric_tags(context, report.decision)
        await self.metrics_sink.increment("data.validation.contract.success", 1, tags)
        await self.metrics_sink.observe("data.validation.contract.risk_score", report.risk_score, tags)
        await self.metrics_sink.observe("data.validation.contract.findings", len(report.findings), tags)
        await self.metrics_sink.observe("data.validation.contract.records", report.record_count, tags)

    async def _record_failure(self, context: ContractContext, exc: BaseException, latency_ms: float) -> None:
        if not self.config.metrics_enabled:
            return
        tags = {**self._metric_tags(context, ContractDecision.BLOCK), "error_type": type(exc).__name__}
        await self.metrics_sink.increment("data.validation.contract.failure", 1, tags)
        await self.metrics_sink.observe("data.validation.contract.failure_latency_ms", latency_ms, tags)

    def _metric_tags(self, context: ContractContext, decision: ContractDecision) -> Mapping[str, str]:
        return {
            "tenant_id": context.tenant_id or "unknown",
            "application": context.application or "unknown",
            "environment": context.environment or "unknown",
            "decision": decision.value,
        }

    async def _audit_completed(self, context: ContractContext, report: ContractReport) -> None:
        if not self.config.audit_enabled:
            return
        await self.audit_sink.emit("contract_validation_completed", {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": context.request_id,
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "application": context.application,
            "pipeline_id": context.pipeline_id,
            "dataset_id": context.dataset_id,
            "producer": context.producer,
            "consumer": context.consumer,
            "environment": context.environment,
            "trace_id": context.trace_id,
            "report_id": report.report_id,
            "contract_id": report.contract_id,
            "contract_version": report.contract_version,
            "status": report.status.value,
            "decision": report.decision.value,
            "risk_score": report.risk_score,
            "record_count": report.record_count,
            "findings": [asdict(item) for item in report.findings],
        })

    async def _audit_failure(self, context: ContractContext, exc: BaseException, latency_ms: float) -> None:
        if not self.config.audit_enabled:
            return
        await self.audit_sink.emit("contract_validation_failed", {
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
# Factory Helpers
# =============================================================================


def build_default_customer_contract() -> DataContract:
    """Build an example customer contract for local testing."""

    return DataContract(
        contract_id="customer_contract",
        name="Customer Data Contract",
        version=ContractVersion(1, 0, 0),
        owner="data-governance",
        producer="crm",
        classification=DataClassification.CONFIDENTIAL,
        allow_extra_fields=False,
        fields=(
            ContractField(
                name="id",
                field_type=FieldType.STRING,
                required=True,
                nullable=False,
                constraints=FieldConstraint(min_length=1, max_length=64),
            ),
            ContractField(
                name="name",
                field_type=FieldType.STRING,
                required=True,
                nullable=False,
                constraints=FieldConstraint(min_length=2, max_length=200),
            ),
            ContractField(
                name="email",
                field_type=FieldType.STRING,
                required=False,
                nullable=True,
                classification=DataClassification.CONFIDENTIAL,
                constraints=FieldConstraint(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
            ),
            ContractField(
                name="created_at",
                field_type=FieldType.DATETIME,
                required=True,
                nullable=False,
            ),
        ),
        quality_expectations=(
            QualityExpectation(
                expectation_id="customer.id.not_null_rate",
                rule_type=QualityRuleType.NOT_NULL_RATE,
                field="id",
                threshold=1.0,
                severity=ContractSeverity.CRITICAL,
            ),
            QualityExpectation(
                expectation_id="customer.id.unique_rate",
                rule_type=QualityRuleType.UNIQUE_RATE,
                field="id",
                threshold=1.0,
                severity=ContractSeverity.ERROR,
            ),
            QualityExpectation(
                expectation_id="customer.row_count_min",
                rule_type=QualityRuleType.ROW_COUNT_MIN,
                threshold=1,
                severity=ContractSeverity.ERROR,
            ),
        ),
        sla=ServiceLevelAgreement(freshness_field="created_at", max_age_seconds=60 * 60 * 24 * 365, min_rows=1),
    )


def build_default_contract_validator(
    *,
    config: Optional[ContractValidatorConfig] = None,
    custom_rules: Optional[Mapping[str, CustomContractRule]] = None,
) -> DataContractValidator:
    return DataContractValidator(config=config, custom_rules=custom_rules)


async def _demo_async() -> None:
    logging.basicConfig(level=logging.INFO)
    validator = build_default_contract_validator()
    contract = build_default_customer_contract()
    data = [
        {"id": "1", "name": "Thiago", "email": "thiago@example.com", "created_at": datetime.now(timezone.utc).isoformat()},
    ]
    report = await validator.validate(
        data,
        contract=contract,
        context=ContractContext(
            tenant_id="demo",
            application="data-platform",
            producer="crm",
            environment="dev",
        ),
    )
    print(report.to_json(indent=2))


if __name__ == "__main__":
    asyncio.run(_demo_async())

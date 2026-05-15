"""
data/validation/consistency_validator.py

Enterprise-grade consistency validation engine.

This module validates data consistency across records, fields, datasets and
pipeline outputs. It is designed for data platforms that need deterministic,
auditable and extensible consistency checks before ingestion, transformation,
analytics, ML/AI usage or publication.

Core capabilities:

- Field-to-field consistency checks
- Required relationship checks
- Temporal consistency checks
- Referential consistency checks
- Duplicate/key uniqueness checks
- Numeric tolerance checks
- Aggregate consistency checks
- Cross-dataset consistency checks
- Custom rule execution
- Severity and risk scoring
- Batch validation
- Audit and metrics hooks
- Dependency-light defaults

Python:
    3.10+
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import statistics
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class ConsistencyValidationError(Exception):
    """Base exception for consistency validation."""


class ConsistencyConfigurationError(ConsistencyValidationError):
    """Raised when consistency validator configuration is invalid."""


class ConsistencyRuleExecutionError(ConsistencyValidationError):
    """Raised when a consistency rule fails unexpectedly."""


class ConsistencyInputError(ConsistencyValidationError):
    """Raised when validator input is invalid."""


# =============================================================================
# Enums
# =============================================================================


class ConsistencySeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ConsistencyStatus(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class ConsistencyDecision(str, Enum):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


class ConsistencyScope(str, Enum):
    RECORD = "record"
    BATCH = "batch"
    DATASET = "dataset"
    CROSS_DATASET = "cross_dataset"
    PIPELINE = "pipeline"


class ConsistencyRuleType(str, Enum):
    FIELD_EQUALS = "field_equals"
    FIELD_NOT_EQUALS = "field_not_equals"
    FIELD_REQUIRED_IF = "field_required_if"
    NUMERIC_TOLERANCE = "numeric_tolerance"
    TEMPORAL_ORDER = "temporal_order"
    UNIQUE_KEY = "unique_key"
    REFERENTIAL_INTEGRITY = "referential_integrity"
    AGGREGATE_MATCH = "aggregate_match"
    CUSTOM = "custom"


class ComparisonOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class ConsistencyValidatorConfig:
    """Consistency validator configuration."""

    fail_fast: bool = False
    audit_enabled: bool = True
    metrics_enabled: bool = True
    include_passed_checks: bool = False
    block_on_critical: bool = True
    review_on_error: bool = True
    default_numeric_tolerance: float = 0.000001
    max_evidence_chars: int = 2_000
    version: str = "1.0.0"

    def validate(self) -> None:
        if self.default_numeric_tolerance < 0:
            raise ConsistencyConfigurationError("default_numeric_tolerance must be >= 0")
        if self.max_evidence_chars < 0:
            raise ConsistencyConfigurationError("max_evidence_chars must be >= 0")


@dataclass(frozen=True)
class ConsistencyContext:
    """Execution context for consistency validation."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    application: Optional[str] = None
    pipeline_id: Optional[str] = None
    dataset_id: Optional[str] = None
    batch_id: Optional[str] = None
    environment: Optional[str] = None
    trace_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConsistencyEvidence:
    """Evidence for a consistency finding."""

    key: str
    value: Any
    expected: Optional[Any] = None
    actual: Optional[Any] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConsistencyFinding:
    """One consistency finding."""

    finding_id: str
    rule_id: str
    rule_type: ConsistencyRuleType
    scope: ConsistencyScope
    status: ConsistencyStatus
    severity: ConsistencySeverity
    message: str
    field: Optional[str] = None
    record_id: Optional[str] = None
    evidence: Sequence[ConsistencyEvidence] = field(default_factory=tuple)
    remediation: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConsistencyRuleDefinition:
    """Rule definition for consistency checks."""

    rule_id: str
    name: str
    rule_type: ConsistencyRuleType
    scope: ConsistencyScope
    severity: ConsistencySeverity = ConsistencySeverity.ERROR
    enabled: bool = True
    description: Optional[str] = None
    fields: Sequence[str] = field(default_factory=tuple)
    parameters: Mapping[str, Any] = field(default_factory=dict)
    tags: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.rule_id:
            raise ConsistencyConfigurationError("rule_id is required")
        if not self.name:
            raise ConsistencyConfigurationError("rule name is required")


@dataclass(frozen=True)
class ConsistencyRuleResult:
    """Result returned by a consistency rule."""

    passed: bool
    message: str
    severity: Optional[ConsistencySeverity] = None
    field: Optional[str] = None
    record_id: Optional[str] = None
    evidence: Sequence[ConsistencyEvidence] = field(default_factory=tuple)
    remediation: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConsistencyReport:
    """Final consistency validation report."""

    report_id: str
    request_id: str
    created_at: str
    status: ConsistencyStatus
    decision: ConsistencyDecision
    risk_score: float
    rules_evaluated: int
    passed_rules: int
    warning_rules: int
    failed_rules: int
    skipped_rules: int
    findings: Sequence[ConsistencyFinding]
    recommendations: Sequence[str]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.decision == ConsistencyDecision.ALLOW

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


class ConsistencyRule(Protocol):
    """Protocol for rule implementation."""

    async def evaluate(
        self,
        data: Any,
        *,
        definition: ConsistencyRuleDefinition,
        context: ConsistencyContext,
        reference_data: Optional[Mapping[str, Any]] = None,
    ) -> ConsistencyRuleResult:
        """Evaluate consistency rule."""


class AuditSink(Protocol):
    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        """Emit audit event."""


class MetricsSink(Protocol):
    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Increment metric."""

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Observe metric."""


# =============================================================================
# Utility Functions
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


def get_path(data: Any, path: str, default: Any = None) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part, default)
        else:
            current = getattr(current, part, default)
        if current is default:
            return default
    return current


def set_of_values(records: Sequence[Any], field: str) -> set[Any]:
    return {get_path(record, field) for record in records if get_path(record, field) is not None}


def parse_datetime_value(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    candidates = [text, text.replace("Z", "+00:00")]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue
    return None


def compare_values(actual: Any, operator: ComparisonOperator, expected: Any = None) -> bool:
    if operator == ComparisonOperator.EXISTS:
        return actual is not None and actual != ""
    if operator == ComparisonOperator.NOT_EXISTS:
        return actual is None or actual == ""
    if operator == ComparisonOperator.EQ:
        return actual == expected
    if operator == ComparisonOperator.NE:
        return actual != expected
    if operator == ComparisonOperator.IN:
        return actual in set(expected or [])
    if operator == ComparisonOperator.NOT_IN:
        return actual not in set(expected or [])
    try:
        if operator == ComparisonOperator.GT:
            return actual > expected
        if operator == ComparisonOperator.GTE:
            return actual >= expected
        if operator == ComparisonOperator.LT:
            return actual < expected
        if operator == ComparisonOperator.LTE:
            return actual <= expected
    except TypeError:
        return False
    return False


# =============================================================================
# Default Sinks
# =============================================================================


class LoggingAuditSink:
    """Logging-based audit sink."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        self.logger.info("consistency_audit=%s payload=%s", event_name, safe_json(payload))


class LoggingMetricsSink:
    """Logging-based metrics sink."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("consistency_metric_counter=%s value=%s tags=%s", name, value, dict(tags or {}))

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("consistency_metric_observe=%s value=%s tags=%s", name, value, dict(tags or {}))


# =============================================================================
# Rule Implementations
# =============================================================================


class FieldComparisonRule:
    """Compares two fields or one field against a constant."""

    async def evaluate(
        self,
        data: Any,
        *,
        definition: ConsistencyRuleDefinition,
        context: ConsistencyContext,
        reference_data: Optional[Mapping[str, Any]] = None,
    ) -> ConsistencyRuleResult:
        await asyncio.sleep(0)
        left_field = str(definition.parameters.get("left_field") or (definition.fields[0] if definition.fields else ""))
        right_field = definition.parameters.get("right_field")
        expected = definition.parameters.get("expected")
        operator = ComparisonOperator(str(definition.parameters.get("operator", "eq")))
        actual = get_path(data, left_field)
        comparison_value = get_path(data, str(right_field)) if right_field else expected
        passed = compare_values(actual, operator, comparison_value)
        return ConsistencyRuleResult(
            passed=passed,
            message=(
                f"Field comparison passed: {left_field} {operator.value} {right_field or expected}."
                if passed
                else f"Field comparison failed: {left_field} expected {operator.value} {right_field or expected}."
            ),
            field=left_field,
            record_id=str(get_path(data, definition.parameters.get("record_id_field", "id"), "")) or None,
            evidence=(ConsistencyEvidence(key=left_field, value=actual, expected=comparison_value, actual=actual),),
            remediation=None if passed else f"Align '{left_field}' with expected consistency rule.",
        )


class RequiredIfRule:
    """Requires a target field when a condition is true."""

    async def evaluate(
        self,
        data: Any,
        *,
        definition: ConsistencyRuleDefinition,
        context: ConsistencyContext,
        reference_data: Optional[Mapping[str, Any]] = None,
    ) -> ConsistencyRuleResult:
        await asyncio.sleep(0)
        condition_field = str(definition.parameters["condition_field"])
        condition_operator = ComparisonOperator(str(definition.parameters.get("condition_operator", "eq")))
        condition_value = definition.parameters.get("condition_value")
        required_field = str(definition.parameters["required_field"])
        condition_actual = get_path(data, condition_field)
        condition_met = compare_values(condition_actual, condition_operator, condition_value)
        required_value = get_path(data, required_field)
        passed = True if not condition_met else required_value is not None and required_value != ""
        return ConsistencyRuleResult(
            passed=passed,
            message=(
                f"Required-if rule passed for '{required_field}'."
                if passed
                else f"Field '{required_field}' is required when '{condition_field}' condition is met."
            ),
            field=required_field,
            record_id=str(get_path(data, definition.parameters.get("record_id_field", "id"), "")) or None,
            evidence=(
                ConsistencyEvidence(key=condition_field, value=condition_actual, expected=condition_value, actual=condition_actual),
                ConsistencyEvidence(key=required_field, value=required_value, expected="non-empty", actual=required_value),
            ),
            remediation=None if passed else f"Populate '{required_field}' or adjust '{condition_field}'.",
        )


class NumericToleranceRule:
    """Checks numeric consistency between fields or a formula-like expected value."""

    async def evaluate(
        self,
        data: Any,
        *,
        definition: ConsistencyRuleDefinition,
        context: ConsistencyContext,
        reference_data: Optional[Mapping[str, Any]] = None,
    ) -> ConsistencyRuleResult:
        await asyncio.sleep(0)
        left_field = str(definition.parameters["left_field"])
        right_field = definition.parameters.get("right_field")
        expected_constant = definition.parameters.get("expected")
        tolerance = float(definition.parameters.get("tolerance", 0.000001))
        relative = bool(definition.parameters.get("relative", False))
        actual_raw = get_path(data, left_field)
        expected_raw = get_path(data, str(right_field)) if right_field else expected_constant
        try:
            actual = float(actual_raw)
            expected = float(expected_raw)
            diff = abs(actual - expected)
            allowed = tolerance * max(abs(expected), 1.0) if relative else tolerance
            passed = diff <= allowed
        except (TypeError, ValueError):
            actual = actual_raw
            expected = expected_raw
            diff = None
            allowed = tolerance
            passed = False
        return ConsistencyRuleResult(
            passed=passed,
            message=(
                f"Numeric tolerance passed for '{left_field}'."
                if passed
                else f"Numeric tolerance failed for '{left_field}'."
            ),
            field=left_field,
            record_id=str(get_path(data, definition.parameters.get("record_id_field", "id"), "")) or None,
            evidence=(ConsistencyEvidence(key=left_field, value=actual_raw, expected=expected_raw, actual=actual_raw, metadata={"diff": diff, "allowed": allowed}),),
            remediation=None if passed else f"Adjust '{left_field}' or source value to be within tolerance {tolerance}.",
        )


class TemporalOrderRule:
    """Validates chronological ordering between two fields."""

    async def evaluate(
        self,
        data: Any,
        *,
        definition: ConsistencyRuleDefinition,
        context: ConsistencyContext,
        reference_data: Optional[Mapping[str, Any]] = None,
    ) -> ConsistencyRuleResult:
        await asyncio.sleep(0)
        start_field = str(definition.parameters["start_field"])
        end_field = str(definition.parameters["end_field"])
        allow_equal = bool(definition.parameters.get("allow_equal", True))
        start_raw = get_path(data, start_field)
        end_raw = get_path(data, end_field)
        start_dt = parse_datetime_value(start_raw)
        end_dt = parse_datetime_value(end_raw)
        if start_dt is None or end_dt is None:
            passed = False
        else:
            passed = start_dt <= end_dt if allow_equal else start_dt < end_dt
        return ConsistencyRuleResult(
            passed=passed,
            message=(
                f"Temporal order passed: {start_field} before {end_field}."
                if passed
                else f"Temporal order failed: {start_field} must be before {end_field}."
            ),
            field=start_field,
            record_id=str(get_path(data, definition.parameters.get("record_id_field", "id"), "")) or None,
            evidence=(
                ConsistencyEvidence(key=start_field, value=start_raw, actual=start_dt.isoformat() if start_dt else None),
                ConsistencyEvidence(key=end_field, value=end_raw, actual=end_dt.isoformat() if end_dt else None),
            ),
            remediation=None if passed else f"Correct '{start_field}' and '{end_field}' chronological order.",
        )


class UniqueKeyRule:
    """Validates uniqueness across a batch/dataset."""

    async def evaluate(
        self,
        data: Any,
        *,
        definition: ConsistencyRuleDefinition,
        context: ConsistencyContext,
        reference_data: Optional[Mapping[str, Any]] = None,
    ) -> ConsistencyRuleResult:
        await asyncio.sleep(0)
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes, bytearray)):
            raise ConsistencyInputError("UniqueKeyRule requires a sequence of records")
        fields = tuple(definition.parameters.get("fields") or definition.fields)
        if not fields:
            raise ConsistencyConfigurationError("UniqueKeyRule requires fields")
        seen: Dict[Tuple[Any, ...], int] = {}
        duplicates: List[Tuple[Any, ...]] = []
        for record in data:
            key = tuple(get_path(record, field) for field in fields)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] == 2:
                duplicates.append(key)
        passed = not duplicates
        return ConsistencyRuleResult(
            passed=passed,
            message="Unique key check passed." if passed else f"Duplicate key(s) detected for fields {fields}.",
            evidence=tuple(ConsistencyEvidence(key="duplicate_key", value=dup, metadata={"fields": fields}) for dup in duplicates[:20]),
            remediation=None if passed else f"Deduplicate records by key fields: {', '.join(fields)}.",
            metadata={"duplicate_count": len(duplicates), "fields": fields},
        )


class ReferentialIntegrityRule:
    """Validates that foreign-key values exist in a reference set."""

    async def evaluate(
        self,
        data: Any,
        *,
        definition: ConsistencyRuleDefinition,
        context: ConsistencyContext,
        reference_data: Optional[Mapping[str, Any]] = None,
    ) -> ConsistencyRuleResult:
        await asyncio.sleep(0)
        field = str(definition.parameters["field"])
        reference_name = str(definition.parameters["reference"])
        reference_field = str(definition.parameters.get("reference_field", "id"))
        refs = (reference_data or {}).get(reference_name, ())
        if isinstance(refs, set):
            reference_values = refs
        else:
            reference_values = set_of_values(refs, reference_field) if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes)) else set(refs or [])

        records = data if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray, Mapping)) else (data,)
        missing: List[Any] = []
        for record in records:
            value = get_path(record, field)
            if value is not None and value not in reference_values:
                missing.append(value)
        passed = not missing
        return ConsistencyRuleResult(
            passed=passed,
            message="Referential integrity check passed." if passed else f"Missing reference values for field '{field}'.",
            field=field,
            evidence=tuple(ConsistencyEvidence(key=field, value=value, expected=f"exists in {reference_name}", actual=value) for value in missing[:20]),
            remediation=None if passed else f"Load missing reference data or fix '{field}' values.",
            metadata={"missing_count": len(missing), "reference": reference_name},
        )


class AggregateMatchRule:
    """Validates aggregate consistency, such as sum(records.amount) == expected."""

    async def evaluate(
        self,
        data: Any,
        *,
        definition: ConsistencyRuleDefinition,
        context: ConsistencyContext,
        reference_data: Optional[Mapping[str, Any]] = None,
    ) -> ConsistencyRuleResult:
        await asyncio.sleep(0)
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes, bytearray)):
            raise ConsistencyInputError("AggregateMatchRule requires a sequence of records")
        field_name = str(definition.parameters["field"])
        operation = str(definition.parameters.get("operation", "sum"))
        expected = definition.parameters.get("expected")
        expected_reference = definition.parameters.get("expected_reference")
        tolerance = float(definition.parameters.get("tolerance", 0.000001))
        values = [float(get_path(record, field_name, 0) or 0) for record in data]
        if operation == "sum":
            actual = sum(values)
        elif operation == "count":
            actual = len(values)
        elif operation == "avg":
            actual = statistics.mean(values) if values else 0.0
        elif operation == "min":
            actual = min(values) if values else 0.0
        elif operation == "max":
            actual = max(values) if values else 0.0
        else:
            raise ConsistencyConfigurationError(f"Unsupported aggregate operation: {operation}")

        if expected_reference:
            expected = get_path(reference_data or {}, str(expected_reference), expected)
        try:
            expected_num = float(expected)
            diff = abs(float(actual) - expected_num)
            passed = diff <= tolerance
        except (TypeError, ValueError):
            expected_num = expected
            diff = None
            passed = actual == expected

        return ConsistencyRuleResult(
            passed=passed,
            message="Aggregate consistency check passed." if passed else "Aggregate consistency check failed.",
            field=field_name,
            evidence=(ConsistencyEvidence(key=f"{operation}.{field_name}", value=actual, expected=expected_num, actual=actual, metadata={"diff": diff, "tolerance": tolerance}),),
            remediation=None if passed else "Reconcile aggregate source data with expected total.",
        )


class CallableConsistencyRule:
    """Adapter for custom sync/async rule callables."""

    def __init__(self, func: Callable[..., ConsistencyRuleResult]) -> None:
        self.func = func

    async def evaluate(
        self,
        data: Any,
        *,
        definition: ConsistencyRuleDefinition,
        context: ConsistencyContext,
        reference_data: Optional[Mapping[str, Any]] = None,
    ) -> ConsistencyRuleResult:
        result = self.func(data, definition=definition, context=context, reference_data=reference_data)
        if asyncio.iscoroutine(result):
            result = await result
        if not isinstance(result, ConsistencyRuleResult):
            raise ConsistencyRuleExecutionError("custom rule must return ConsistencyRuleResult")
        return result


# =============================================================================
# Validator
# =============================================================================


class ConsistencyValidator:
    """Enterprise consistency validator."""

    DEFAULT_RULES: Mapping[ConsistencyRuleType, ConsistencyRule] = {
        ConsistencyRuleType.FIELD_EQUALS: FieldComparisonRule(),
        ConsistencyRuleType.FIELD_NOT_EQUALS: FieldComparisonRule(),
        ConsistencyRuleType.FIELD_REQUIRED_IF: RequiredIfRule(),
        ConsistencyRuleType.NUMERIC_TOLERANCE: NumericToleranceRule(),
        ConsistencyRuleType.TEMPORAL_ORDER: TemporalOrderRule(),
        ConsistencyRuleType.UNIQUE_KEY: UniqueKeyRule(),
        ConsistencyRuleType.REFERENTIAL_INTEGRITY: ReferentialIntegrityRule(),
        ConsistencyRuleType.AGGREGATE_MATCH: AggregateMatchRule(),
    }

    def __init__(
        self,
        *,
        rules: Sequence[ConsistencyRuleDefinition],
        rule_handlers: Optional[Mapping[str, ConsistencyRule]] = None,
        config: Optional[ConsistencyValidatorConfig] = None,
        audit_sink: Optional[AuditSink] = None,
        metrics_sink: Optional[MetricsSink] = None,
    ) -> None:
        self.config = config or ConsistencyValidatorConfig()
        self.config.validate()
        self.rules = tuple(rules)
        self.rule_handlers = dict(rule_handlers or {})
        self.audit_sink = audit_sink or LoggingAuditSink()
        self.metrics_sink = metrics_sink or LoggingMetricsSink()
        for rule in self.rules:
            rule.validate()

    async def validate(
        self,
        data: Any,
        *,
        context: Optional[ConsistencyContext] = None,
        reference_data: Optional[Mapping[str, Any]] = None,
        scope: Optional[ConsistencyScope] = None,
    ) -> ConsistencyReport:
        """Validate data consistency."""

        context = context or ConsistencyContext()
        started = time.perf_counter()
        findings: List[ConsistencyFinding] = []
        rules_evaluated = 0
        passed_rules = 0
        warning_rules = 0
        failed_rules = 0
        skipped_rules = 0

        try:
            for definition in self.rules:
                if not definition.enabled:
                    skipped_rules += 1
                    continue
                if scope and definition.scope != scope:
                    skipped_rules += 1
                    continue

                rules_evaluated += 1
                handler = self._handler_for(definition)
                try:
                    effective_definition = self._normalize_definition(definition)
                    result = await handler.evaluate(
                        data,
                        definition=effective_definition,
                        context=context,
                        reference_data=reference_data,
                    )
                    finding = self._finding_from_result(effective_definition, result)
                    if result.passed:
                        passed_rules += 1
                        if self.config.include_passed_checks:
                            findings.append(finding)
                    else:
                        findings.append(finding)
                        if finding.severity == ConsistencySeverity.WARNING:
                            warning_rules += 1
                        else:
                            failed_rules += 1
                        if self.config.fail_fast:
                            break
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Consistency rule failed: %s", definition.rule_id)
                    finding = self._finding_from_exception(definition, exc)
                    findings.append(finding)
                    failed_rules += 1
                    if self.config.fail_fast:
                        break

            report = self._build_report(
                context=context,
                findings=findings,
                rules_evaluated=rules_evaluated,
                passed_rules=passed_rules,
                warning_rules=warning_rules,
                failed_rules=failed_rules,
                skipped_rules=skipped_rules,
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
        context: Optional[ConsistencyContext] = None,
        reference_data: Optional[Mapping[str, Any]] = None,
        scope: Optional[ConsistencyScope] = None,
    ) -> ConsistencyReport:
        return asyncio.run(self.validate(data, context=context, reference_data=reference_data, scope=scope))

    async def validate_many(
        self,
        batches: Sequence[Any],
        *,
        context: Optional[ConsistencyContext] = None,
        reference_data: Optional[Mapping[str, Any]] = None,
        concurrency: int = 10,
    ) -> Sequence[ConsistencyReport]:
        if concurrency <= 0:
            raise ConsistencyConfigurationError("concurrency must be positive")
        semaphore = asyncio.Semaphore(concurrency)

        async def run_one(item: Any) -> ConsistencyReport:
            async with semaphore:
                return await self.validate(item, context=context, reference_data=reference_data)

        return tuple(await asyncio.gather(*(run_one(item) for item in batches)))

    def _normalize_definition(self, definition: ConsistencyRuleDefinition) -> ConsistencyRuleDefinition:
        if definition.rule_type == ConsistencyRuleType.FIELD_NOT_EQUALS:
            params = dict(definition.parameters)
            params.setdefault("operator", ComparisonOperator.NE.value)
            return ConsistencyRuleDefinition(**{**asdict(definition), "parameters": params})
        if definition.rule_type == ConsistencyRuleType.FIELD_EQUALS:
            params = dict(definition.parameters)
            params.setdefault("operator", ComparisonOperator.EQ.value)
            return ConsistencyRuleDefinition(**{**asdict(definition), "parameters": params})
        if definition.rule_type == ConsistencyRuleType.NUMERIC_TOLERANCE:
            params = dict(definition.parameters)
            params.setdefault("tolerance", self.config.default_numeric_tolerance)
            return ConsistencyRuleDefinition(**{**asdict(definition), "parameters": params})
        return definition

    def _handler_for(self, definition: ConsistencyRuleDefinition) -> ConsistencyRule:
        if definition.rule_id in self.rule_handlers:
            return self.rule_handlers[definition.rule_id]
        if definition.rule_type == ConsistencyRuleType.CUSTOM:
            raise ConsistencyConfigurationError(f"No custom handler registered for rule {definition.rule_id}")
        handler = self.DEFAULT_RULES.get(definition.rule_type)
        if handler is None:
            raise ConsistencyConfigurationError(f"No handler registered for rule type {definition.rule_type.value}")
        return handler

    def _finding_from_result(self, definition: ConsistencyRuleDefinition, result: ConsistencyRuleResult) -> ConsistencyFinding:
        status = ConsistencyStatus.PASSED if result.passed else ConsistencyStatus.FAILED
        severity = result.severity or (ConsistencySeverity.INFO if result.passed else definition.severity)
        evidence = tuple(
            ConsistencyEvidence(
                key=item.key,
                value=truncate(item.value, self.config.max_evidence_chars),
                expected=truncate(item.expected, self.config.max_evidence_chars) if item.expected is not None else None,
                actual=truncate(item.actual, self.config.max_evidence_chars) if item.actual is not None else None,
                metadata=item.metadata,
            )
            for item in result.evidence
        )
        return ConsistencyFinding(
            finding_id=str(uuid.uuid4()),
            rule_id=definition.rule_id,
            rule_type=definition.rule_type,
            scope=definition.scope,
            status=status,
            severity=severity,
            message=result.message,
            field=result.field,
            record_id=result.record_id,
            evidence=evidence,
            remediation=result.remediation,
            metadata={**dict(definition.metadata), **dict(result.metadata)},
        )

    def _finding_from_exception(self, definition: ConsistencyRuleDefinition, exc: BaseException) -> ConsistencyFinding:
        return ConsistencyFinding(
            finding_id=str(uuid.uuid4()),
            rule_id=definition.rule_id,
            rule_type=definition.rule_type,
            scope=definition.scope,
            status=ConsistencyStatus.ERROR,
            severity=ConsistencySeverity.ERROR,
            message=f"Rule execution failed: {type(exc).__name__}: {exc}",
            remediation="Inspect rule configuration, handler implementation and input data.",
            metadata={"error_type": type(exc).__name__},
        )

    def _build_report(
        self,
        *,
        context: ConsistencyContext,
        findings: Sequence[ConsistencyFinding],
        rules_evaluated: int,
        passed_rules: int,
        warning_rules: int,
        failed_rules: int,
        skipped_rules: int,
        latency_ms: float,
    ) -> ConsistencyReport:
        risk_score = self._risk_score(findings)
        decision = self._decision(findings, risk_score)
        status = self._status(decision, findings)
        return ConsistencyReport(
            report_id=str(uuid.uuid4()),
            request_id=context.request_id,
            created_at=utc_now_iso(),
            status=status,
            decision=decision,
            risk_score=risk_score,
            rules_evaluated=rules_evaluated,
            passed_rules=passed_rules,
            warning_rules=warning_rules,
            failed_rules=failed_rules,
            skipped_rules=skipped_rules,
            findings=tuple(findings),
            recommendations=tuple(self._recommendations(findings, decision)),
            metadata={
                "tenant_id": context.tenant_id,
                "application": context.application,
                "pipeline_id": context.pipeline_id,
                "dataset_id": context.dataset_id,
                "batch_id": context.batch_id,
                "environment": context.environment,
                "latency_ms": round(latency_ms, 3),
                "validator_version": self.config.version,
            },
        )

    def _risk_score(self, findings: Sequence[ConsistencyFinding]) -> float:
        weights = {
            ConsistencySeverity.INFO: 0.0,
            ConsistencySeverity.WARNING: 0.25,
            ConsistencySeverity.ERROR: 0.65,
            ConsistencySeverity.CRITICAL: 1.0,
        }
        failed = [finding for finding in findings if finding.status != ConsistencyStatus.PASSED]
        if not failed:
            return 0.0
        max_weight = max(weights[f.severity] for f in failed)
        avg_weight = sum(weights[f.severity] for f in failed) / len(failed)
        return clamp((max_weight * 0.75) + (avg_weight * 0.25))

    def _decision(self, findings: Sequence[ConsistencyFinding], risk_score: float) -> ConsistencyDecision:
        severities = {f.severity for f in findings if f.status != ConsistencyStatus.PASSED}
        if self.config.block_on_critical and ConsistencySeverity.CRITICAL in severities:
            return ConsistencyDecision.BLOCK
        if risk_score >= 0.85:
            return ConsistencyDecision.BLOCK
        if self.config.review_on_error and ConsistencySeverity.ERROR in severities:
            return ConsistencyDecision.REVIEW
        if risk_score >= 0.25:
            return ConsistencyDecision.REVIEW
        return ConsistencyDecision.ALLOW

    def _status(self, decision: ConsistencyDecision, findings: Sequence[ConsistencyFinding]) -> ConsistencyStatus:
        if any(f.status == ConsistencyStatus.ERROR for f in findings):
            return ConsistencyStatus.ERROR
        if decision == ConsistencyDecision.BLOCK:
            return ConsistencyStatus.FAILED
        if decision == ConsistencyDecision.REVIEW:
            return ConsistencyStatus.WARNING
        return ConsistencyStatus.PASSED

    def _recommendations(self, findings: Sequence[ConsistencyFinding], decision: ConsistencyDecision) -> List[str]:
        recommendations: List[str] = []
        if decision == ConsistencyDecision.BLOCK:
            recommendations.append("Block downstream processing until critical consistency failures are corrected.")
        elif decision == ConsistencyDecision.REVIEW:
            recommendations.append("Route the dataset or batch to data quality review before publication.")
        else:
            recommendations.append("Consistency validation passed within configured thresholds.")
        for finding in findings:
            if finding.status != ConsistencyStatus.PASSED and finding.remediation:
                recommendations.append(finding.remediation)
        return list(dict.fromkeys(recommendations))

    async def _record_success(self, context: ConsistencyContext, report: ConsistencyReport) -> None:
        if not self.config.metrics_enabled:
            return
        tags = self._metric_tags(context, report.decision)
        await self.metrics_sink.increment("data.validation.consistency.success", 1, tags)
        await self.metrics_sink.observe("data.validation.consistency.risk_score", report.risk_score, tags)
        await self.metrics_sink.observe("data.validation.consistency.findings", len(report.findings), tags)

    async def _record_failure(self, context: ConsistencyContext, exc: BaseException, latency_ms: float) -> None:
        if not self.config.metrics_enabled:
            return
        tags = {**self._metric_tags(context, ConsistencyDecision.BLOCK), "error_type": type(exc).__name__}
        await self.metrics_sink.increment("data.validation.consistency.failure", 1, tags)
        await self.metrics_sink.observe("data.validation.consistency.failure_latency_ms", latency_ms, tags)

    def _metric_tags(self, context: ConsistencyContext, decision: ConsistencyDecision) -> Mapping[str, str]:
        return {
            "tenant_id": context.tenant_id or "unknown",
            "application": context.application or "unknown",
            "environment": context.environment or "unknown",
            "decision": decision.value,
        }

    async def _audit_completed(self, context: ConsistencyContext, report: ConsistencyReport) -> None:
        if not self.config.audit_enabled:
            return
        await self.audit_sink.emit("consistency_validation_completed", {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": context.request_id,
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "application": context.application,
            "pipeline_id": context.pipeline_id,
            "dataset_id": context.dataset_id,
            "batch_id": context.batch_id,
            "environment": context.environment,
            "trace_id": context.trace_id,
            "report_id": report.report_id,
            "status": report.status.value,
            "decision": report.decision.value,
            "risk_score": report.risk_score,
            "rules_evaluated": report.rules_evaluated,
            "findings": [asdict(finding) for finding in report.findings],
        })

    async def _audit_failure(self, context: ConsistencyContext, exc: BaseException, latency_ms: float) -> None:
        if not self.config.audit_enabled:
            return
        await self.audit_sink.emit("consistency_validation_failed", {
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


def build_default_consistency_rules() -> Sequence[ConsistencyRuleDefinition]:
    """Build practical default consistency rules."""

    return (
        ConsistencyRuleDefinition(
            rule_id="record.created_before_updated",
            name="created_at before updated_at",
            rule_type=ConsistencyRuleType.TEMPORAL_ORDER,
            scope=ConsistencyScope.RECORD,
            severity=ConsistencySeverity.ERROR,
            parameters={"start_field": "created_at", "end_field": "updated_at", "allow_equal": True},
        ),
        ConsistencyRuleDefinition(
            rule_id="batch.unique_id",
            name="Unique id in batch",
            rule_type=ConsistencyRuleType.UNIQUE_KEY,
            scope=ConsistencyScope.BATCH,
            severity=ConsistencySeverity.CRITICAL,
            fields=("id",),
            parameters={"fields": ("id",)},
        ),
        ConsistencyRuleDefinition(
            rule_id="record.status_requires_reason",
            name="Inactive records require reason",
            rule_type=ConsistencyRuleType.FIELD_REQUIRED_IF,
            scope=ConsistencyScope.RECORD,
            severity=ConsistencySeverity.WARNING,
            parameters={
                "condition_field": "status",
                "condition_operator": "in",
                "condition_value": ("inactive", "disabled", "archived"),
                "required_field": "status_reason",
            },
        ),
    )


def build_default_consistency_validator(
    *,
    config: Optional[ConsistencyValidatorConfig] = None,
    extra_rules: Sequence[ConsistencyRuleDefinition] = (),
    rule_handlers: Optional[Mapping[str, ConsistencyRule]] = None,
) -> ConsistencyValidator:
    return ConsistencyValidator(
        rules=tuple(build_default_consistency_rules()) + tuple(extra_rules),
        rule_handlers=rule_handlers,
        config=config,
    )


async def _demo_async() -> None:
    logging.basicConfig(level=logging.INFO)
    validator = build_default_consistency_validator()
    batch = [
        {"id": 1, "created_at": "2026-01-01T10:00:00+00:00", "updated_at": "2026-01-02T10:00:00+00:00", "status": "active"},
        {"id": 2, "created_at": "2026-01-01T10:00:00+00:00", "updated_at": "2026-01-02T10:00:00+00:00", "status": "archived", "status_reason": "old"},
    ]
    report = await validator.validate(
        batch,
        scope=ConsistencyScope.BATCH,
        context=ConsistencyContext(tenant_id="demo", application="data-platform", environment="dev"),
    )
    print(report.to_json(indent=2))


if __name__ == "__main__":
    asyncio.run(_demo_async())

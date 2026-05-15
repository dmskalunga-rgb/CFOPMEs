"""
data/quality/completeness_checker.py

Enterprise-grade Completeness Checker for data quality validation.

This module validates whether datasets are complete enough for trusted
analytics, machine learning, regulatory reporting, operational processes,
and downstream data products.

Main capabilities:
- Column-level completeness checks
- Row-level completeness checks
- Required-field validation
- Conditional completeness rules
- Group/entity completeness checks
- Expected reference/key coverage checks
- Time-window completeness checks
- Duplicate-aware completeness analysis
- Weighted completeness scoring
- Severity-based findings
- Audit-ready execution reports
- Metrics sink integration
- Pandas-native implementation with clean extension points

Designed for enterprise data platforms, lakehouse quality gates, batch/stream
ETL pipelines, orchestration frameworks, data contracts, and compliance-ready
quality audits.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    Union,
)

try:
    import pandas as pd
except Exception:  # pragma: no cover - optional dependency isolation
    pd = None  # type: ignore


# =============================================================================
# Logging
# =============================================================================

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# =============================================================================
# Exceptions
# =============================================================================


class CompletenessCheckerError(Exception):
    """Base exception for completeness checker failures."""


class CompletenessConfigurationError(CompletenessCheckerError):
    """Raised when a completeness rule/configuration is invalid."""


class CompletenessExecutionError(CompletenessCheckerError):
    """Raised when a completeness check cannot be executed safely."""


class DatasetValidationError(CompletenessCheckerError):
    """Raised when input dataset is invalid or unsupported."""


# =============================================================================
# Enums
# =============================================================================


class Severity(str, Enum):
    """Severity level for completeness findings."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CompletenessStatus(str, Enum):
    """Execution status for rules and reports."""

    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"
    ERROR = "error"


class CompletenessRuleType(str, Enum):
    """Supported completeness rule categories."""

    COLUMN_NOT_NULL = "column_not_null"
    MULTI_COLUMN_NOT_NULL = "multi_column_not_null"
    REQUIRED_COLUMNS_PRESENT = "required_columns_present"
    ROW_COMPLETENESS = "row_completeness"
    CONDITIONAL_NOT_NULL = "conditional_not_null"
    GROUP_COMPLETENESS = "group_completeness"
    REFERENCE_COVERAGE = "reference_coverage"
    TIME_WINDOW_COVERAGE = "time_window_coverage"
    EXPECTED_RECORD_COUNT = "expected_record_count"
    EXPECTED_DISTINCT_KEYS = "expected_distinct_keys"
    CUSTOM = "custom"


class NullSemantics(str, Enum):
    """Values treated as missing by completeness checks."""

    STRICT_NULL_ONLY = "strict_null_only"
    NULL_AND_EMPTY_STRING = "null_and_empty_string"
    NULL_EMPTY_AND_WHITESPACE = "null_empty_and_whitespace"
    NULL_EMPTY_ZERO = "null_empty_zero"
    CUSTOM = "custom"


class CountComparison(str, Enum):
    """Comparison operation for expected count checks."""

    EQUAL = "equal"
    GREATER_OR_EQUAL = "greater_or_equal"
    LESS_OR_EQUAL = "less_or_equal"
    BETWEEN = "between"


class TimeFrequency(str, Enum):
    """Supported time-window frequencies."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


# =============================================================================
# Protocols / Interfaces
# =============================================================================


class MetricsSink(Protocol):
    """Optional sink for publishing checker metrics."""

    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def timing(self, metric_name: str, value_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...


class AuditSink(Protocol):
    """Optional sink for persisting audit events/reports."""

    def write_event(self, event: Mapping[str, Any]) -> None:
        ...


class ReferenceDataProvider(Protocol):
    """Optional provider for reference/key coverage datasets."""

    def get_reference_dataset(self, name: str) -> Any:
        ...


ConditionFunction = Callable[[Mapping[str, Any]], bool]
CustomCompletenessFunction = Callable[[Mapping[str, Any]], bool]
MissingValueFunction = Callable[[Any], bool]


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class CompletenessThreshold:
    """Threshold configuration for a rule or full report."""

    min_score: float = 0.95
    warning_score: float = 0.98
    max_missing_records: Optional[int] = None
    max_missing_rate: Optional[float] = None

    def validate(self) -> None:
        if not 0 <= self.min_score <= 1:
            raise CompletenessConfigurationError("min_score must be between 0 and 1.")
        if not 0 <= self.warning_score <= 1:
            raise CompletenessConfigurationError("warning_score must be between 0 and 1.")
        if self.warning_score < self.min_score:
            raise CompletenessConfigurationError("warning_score must be greater than or equal to min_score.")
        if self.max_missing_records is not None and self.max_missing_records < 0:
            raise CompletenessConfigurationError("max_missing_records cannot be negative.")
        if self.max_missing_rate is not None and not 0 <= self.max_missing_rate <= 1:
            raise CompletenessConfigurationError("max_missing_rate must be between 0 and 1.")


@dataclass(frozen=True)
class TimeWindowExpectation:
    """Expected time-window coverage configuration."""

    start: Any
    end: Any
    frequency: TimeFrequency = TimeFrequency.DAILY
    timezone_name: Optional[str] = None
    allow_partial_current_window: bool = True

    def validate(self) -> None:
        if self.start is None or self.end is None:
            raise CompletenessConfigurationError("TimeWindowExpectation requires start and end.")


@dataclass(frozen=True)
class CompletenessRule:
    """Definition of a completeness validation rule."""

    name: str
    rule_type: CompletenessRuleType
    columns: Sequence[str] = field(default_factory=list)
    column: Optional[str] = None
    key_columns: Sequence[str] = field(default_factory=list)
    group_by: Sequence[str] = field(default_factory=list)
    required_columns: Sequence[str] = field(default_factory=list)
    min_required_fields_per_row: Optional[int] = None
    required_ratio_per_row: Optional[float] = None
    expected_count: Optional[int] = None
    min_count: Optional[int] = None
    max_count: Optional[int] = None
    count_comparison: CountComparison = CountComparison.GREATER_OR_EQUAL
    expected_distinct_keys: Optional[int] = None
    reference_dataset: Optional[str] = None
    reference_key_columns: Sequence[str] = field(default_factory=list)
    time_column: Optional[str] = None
    time_window: Optional[TimeWindowExpectation] = None
    condition_function: Optional[ConditionFunction] = None
    custom_function: Optional[CustomCompletenessFunction] = None
    null_semantics: NullSemantics = NullSemantics.NULL_EMPTY_AND_WHITESPACE
    custom_missing_function: Optional[MissingValueFunction] = None
    weight: float = 1.0
    severity: Severity = Severity.HIGH
    threshold: CompletenessThreshold = field(default_factory=CompletenessThreshold)
    metadata: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def resolved_columns(self) -> List[str]:
        result = list(self.columns)
        if self.column and self.column not in result:
            result.append(self.column)
        return result

    def validate(self) -> None:
        if not self.name or not self.name.strip():
            raise CompletenessConfigurationError("Rule name is required.")
        if self.weight <= 0:
            raise CompletenessConfigurationError(f"Rule '{self.name}' weight must be greater than zero.")
        self.threshold.validate()

        if self.null_semantics == NullSemantics.CUSTOM and self.custom_missing_function is None:
            raise CompletenessConfigurationError(
                f"Rule '{self.name}' uses CUSTOM null_semantics but no custom_missing_function was provided."
            )

        if self.rule_type == CompletenessRuleType.COLUMN_NOT_NULL and not self.column:
            raise CompletenessConfigurationError(f"Rule '{self.name}' requires column.")

        if self.rule_type == CompletenessRuleType.MULTI_COLUMN_NOT_NULL and not self.columns:
            raise CompletenessConfigurationError(f"Rule '{self.name}' requires columns.")

        if self.rule_type == CompletenessRuleType.REQUIRED_COLUMNS_PRESENT and not self.required_columns:
            raise CompletenessConfigurationError(f"Rule '{self.name}' requires required_columns.")

        if self.rule_type == CompletenessRuleType.ROW_COMPLETENESS:
            if not self.columns:
                raise CompletenessConfigurationError(f"Rule '{self.name}' requires columns.")
            if self.min_required_fields_per_row is None and self.required_ratio_per_row is None:
                raise CompletenessConfigurationError(
                    f"Rule '{self.name}' requires min_required_fields_per_row or required_ratio_per_row."
                )
            if self.required_ratio_per_row is not None and not 0 <= self.required_ratio_per_row <= 1:
                raise CompletenessConfigurationError(
                    f"Rule '{self.name}' required_ratio_per_row must be between 0 and 1."
                )

        if self.rule_type == CompletenessRuleType.CONDITIONAL_NOT_NULL:
            if not self.column:
                raise CompletenessConfigurationError(f"Rule '{self.name}' requires column.")
            if self.condition_function is None:
                raise CompletenessConfigurationError(f"Rule '{self.name}' requires condition_function.")

        if self.rule_type == CompletenessRuleType.GROUP_COMPLETENESS:
            if not self.group_by:
                raise CompletenessConfigurationError(f"Rule '{self.name}' requires group_by.")
            if not self.columns:
                raise CompletenessConfigurationError(f"Rule '{self.name}' requires columns.")

        if self.rule_type == CompletenessRuleType.REFERENCE_COVERAGE:
            if not self.reference_dataset:
                raise CompletenessConfigurationError(f"Rule '{self.name}' requires reference_dataset.")
            if not self.key_columns:
                raise CompletenessConfigurationError(f"Rule '{self.name}' requires key_columns.")

        if self.rule_type == CompletenessRuleType.TIME_WINDOW_COVERAGE:
            if not self.time_column:
                raise CompletenessConfigurationError(f"Rule '{self.name}' requires time_column.")
            if not self.time_window:
                raise CompletenessConfigurationError(f"Rule '{self.name}' requires time_window.")
            self.time_window.validate()

        if self.rule_type == CompletenessRuleType.EXPECTED_RECORD_COUNT:
            if self.count_comparison == CountComparison.EQUAL and self.expected_count is None:
                raise CompletenessConfigurationError(f"Rule '{self.name}' requires expected_count.")
            if self.count_comparison == CountComparison.GREATER_OR_EQUAL and self.min_count is None:
                raise CompletenessConfigurationError(f"Rule '{self.name}' requires min_count.")
            if self.count_comparison == CountComparison.LESS_OR_EQUAL and self.max_count is None:
                raise CompletenessConfigurationError(f"Rule '{self.name}' requires max_count.")
            if self.count_comparison == CountComparison.BETWEEN:
                if self.min_count is None or self.max_count is None:
                    raise CompletenessConfigurationError(f"Rule '{self.name}' requires min_count and max_count.")
                if self.min_count > self.max_count:
                    raise CompletenessConfigurationError(f"Rule '{self.name}' min_count cannot exceed max_count.")

        if self.rule_type == CompletenessRuleType.EXPECTED_DISTINCT_KEYS:
            if not self.key_columns:
                raise CompletenessConfigurationError(f"Rule '{self.name}' requires key_columns.")
            if self.expected_distinct_keys is None and self.min_count is None:
                raise CompletenessConfigurationError(
                    f"Rule '{self.name}' requires expected_distinct_keys or min_count."
                )

        if self.rule_type == CompletenessRuleType.CUSTOM and self.custom_function is None:
            raise CompletenessConfigurationError(f"Rule '{self.name}' requires custom_function.")


@dataclass
class CompletenessFinding:
    """Single row-level, column-level, group-level, or rule-level finding."""

    rule_name: str
    severity: Severity
    status: CompletenessStatus
    message: str
    row_index: Optional[Any] = None
    column: Optional[str] = None
    key: Optional[Any] = None
    actual_value: Optional[Any] = None
    expected_value: Optional[Any] = None
    missing_fields: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["severity"] = self.severity.value
        payload["status"] = self.status.value
        return _json_safe(payload)


@dataclass
class CompletenessRuleResult:
    """Aggregated execution result for one completeness rule."""

    rule_name: str
    rule_type: CompletenessRuleType
    status: CompletenessStatus
    severity: Severity
    total_records: int
    evaluated_records: int
    complete_records: int
    incomplete_records: int
    skipped_records: int
    error_records: int
    completeness_score: float
    threshold: CompletenessThreshold
    duration_ms: float
    findings: List[CompletenessFinding] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def missing_rate(self) -> float:
        if self.evaluated_records <= 0:
            return 0.0
        return self.incomplete_records / self.evaluated_records

    @property
    def error_rate(self) -> float:
        if self.evaluated_records <= 0:
            return 0.0
        return self.error_records / self.evaluated_records

    def to_dict(self, include_findings: bool = True) -> Dict[str, Any]:
        data = {
            "rule_name": self.rule_name,
            "rule_type": self.rule_type.value,
            "status": self.status.value,
            "severity": self.severity.value,
            "total_records": self.total_records,
            "evaluated_records": self.evaluated_records,
            "complete_records": self.complete_records,
            "incomplete_records": self.incomplete_records,
            "skipped_records": self.skipped_records,
            "error_records": self.error_records,
            "completeness_score": self.completeness_score,
            "missing_rate": self.missing_rate,
            "error_rate": self.error_rate,
            "threshold": asdict(self.threshold),
            "duration_ms": self.duration_ms,
            "metadata": _json_safe(self.metadata),
        }
        if include_findings:
            data["findings"] = [finding.to_dict() for finding in self.findings]
        return data


@dataclass
class CompletenessReport:
    """Complete completeness validation report."""

    report_id: str
    dataset_name: str
    status: CompletenessStatus
    started_at: str
    finished_at: str
    duration_ms: float
    total_records: int
    overall_score: float
    weighted_score: float
    passed_rules: int
    failed_rules: int
    warning_rules: int
    skipped_rules: int
    error_rules: int
    rule_results: List[CompletenessRuleResult]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_findings: bool = True) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "dataset_name": self.dataset_name,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "total_records": self.total_records,
            "overall_score": self.overall_score,
            "weighted_score": self.weighted_score,
            "passed_rules": self.passed_rules,
            "failed_rules": self.failed_rules,
            "warning_rules": self.warning_rules,
            "skipped_rules": self.skipped_rules,
            "error_rules": self.error_rules,
            "metadata": _json_safe(self.metadata),
            "rule_results": [
                result.to_dict(include_findings=include_findings)
                for result in self.rule_results
            ],
        }

    def to_json(self, include_findings: bool = True, indent: int = 2) -> str:
        return json.dumps(self.to_dict(include_findings=include_findings), indent=indent, ensure_ascii=False)


# =============================================================================
# Helpers
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if pd is not None:
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
    return value


def _require_pandas() -> None:
    if pd is None:
        raise DatasetValidationError(
            "pandas is required for CompletenessChecker. Install pandas or adapt the dataset adapter."
        )


def _as_dataframe(dataset: Any) -> "pd.DataFrame":
    _require_pandas()
    if isinstance(dataset, pd.DataFrame):
        return dataset.copy()
    if isinstance(dataset, list):
        return pd.DataFrame(dataset)
    if isinstance(dataset, tuple):
        return pd.DataFrame(list(dataset))
    if isinstance(dataset, Mapping):
        return pd.DataFrame(dataset)
    raise DatasetValidationError(f"Unsupported dataset type: {type(dataset)!r}")


def _dataset_fingerprint(dataset: Any, limit: int = 10_000) -> str:
    try:
        if pd is not None and isinstance(dataset, pd.DataFrame):
            sample = dataset.head(limit).to_json(orient="records", date_format="iso")
        else:
            sample = json.dumps(list(dataset)[:limit], default=str, sort_keys=True)
    except Exception:
        sample = repr(dataset)[:1_000_000]
    return hashlib.sha256(sample.encode("utf-8", errors="ignore")).hexdigest()


def _row_to_mapping(row: Any) -> Mapping[str, Any]:
    if hasattr(row, "to_dict"):
        return row.to_dict()
    if isinstance(row, Mapping):
        return row
    raise DatasetValidationError("Cannot convert row to mapping.")


def _to_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if pd is not None:
        try:
            parsed = pd.to_datetime(value, errors="coerce")
            if pd.isna(parsed):
                return None
            return parsed.to_pydatetime()
        except Exception:
            return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _period_start(value: datetime, frequency: TimeFrequency) -> datetime:
    if frequency == TimeFrequency.HOURLY:
        return value.replace(minute=0, second=0, microsecond=0)
    if frequency == TimeFrequency.DAILY:
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    if frequency == TimeFrequency.WEEKLY:
        start = value - timedelta(days=value.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    if frequency == TimeFrequency.MONTHLY:
        return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise CompletenessConfigurationError(f"Unsupported frequency: {frequency}")


def _period_increment(value: datetime, frequency: TimeFrequency) -> datetime:
    if frequency == TimeFrequency.HOURLY:
        return value + timedelta(hours=1)
    if frequency == TimeFrequency.DAILY:
        return value + timedelta(days=1)
    if frequency == TimeFrequency.WEEKLY:
        return value + timedelta(weeks=1)
    if frequency == TimeFrequency.MONTHLY:
        year = value.year + (1 if value.month == 12 else 0)
        month = 1 if value.month == 12 else value.month + 1
        return value.replace(year=year, month=month, day=1)
    raise CompletenessConfigurationError(f"Unsupported frequency: {frequency}")


def _expected_periods(start: datetime, end: datetime, frequency: TimeFrequency) -> Set[datetime]:
    periods: Set[datetime] = set()
    current = _period_start(start, frequency)
    end_period = _period_start(end, frequency)
    while current <= end_period:
        periods.add(current)
        current = _period_increment(current, frequency)
    return periods


def _key_tuple(row: Mapping[str, Any], columns: Sequence[str]) -> Tuple[Any, ...]:
    return tuple(row.get(col) for col in columns)


# =============================================================================
# Sinks / Providers
# =============================================================================


class NoopMetricsSink:
    """Default metrics sink that intentionally does nothing."""

    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        return None

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        return None

    def timing(self, metric_name: str, value_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        return None


class InMemoryAuditSink:
    """Simple audit sink useful for tests, local execution, and debugging."""

    def __init__(self) -> None:
        self.events: List[Mapping[str, Any]] = []

    def write_event(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))


class StaticReferenceDataProvider:
    """Reference provider backed by an in-memory dictionary."""

    def __init__(self, datasets: Optional[Mapping[str, Any]] = None) -> None:
        self.datasets = dict(datasets or {})

    def get_reference_dataset(self, name: str) -> Any:
        if name not in self.datasets:
            raise CompletenessExecutionError(f"Reference dataset not found: {name}")
        return self.datasets[name]


# =============================================================================
# Completeness Checker
# =============================================================================


class CompletenessChecker:
    """
    Enterprise data completeness checker.

    Example:
        checker = CompletenessChecker([
            CompletenessRuleFactory.column_not_null("customer_id_required", "customer_id"),
            CompletenessRuleFactory.row_completeness(
                "minimum_profile_fields",
                ["name", "email", "phone", "document"],
                min_required_fields_per_row=3,
            ),
        ])
        report = checker.run(dataset, dataset_name="customers")
    """

    def __init__(
        self,
        rules: Sequence[CompletenessRule],
        *,
        reference_provider: Optional[ReferenceDataProvider] = None,
        metrics_sink: Optional[MetricsSink] = None,
        audit_sink: Optional[AuditSink] = None,
        max_findings_per_rule: int = 1_000,
        fail_fast: bool = False,
        global_threshold: CompletenessThreshold = CompletenessThreshold(min_score=0.95, warning_score=0.98),
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self.rules = list(rules)
        self.reference_provider = reference_provider or StaticReferenceDataProvider()
        self.metrics_sink = metrics_sink or NoopMetricsSink()
        self.audit_sink = audit_sink
        self.max_findings_per_rule = max_findings_per_rule
        self.fail_fast = fail_fast
        self.global_threshold = global_threshold
        self.logger = logger_ or logger

        self._validate_configuration()

    def _validate_configuration(self) -> None:
        if self.max_findings_per_rule < 0:
            raise CompletenessConfigurationError("max_findings_per_rule cannot be negative.")
        self.global_threshold.validate()
        seen = set()
        for rule in self.rules:
            rule.validate()
            if rule.name in seen:
                raise CompletenessConfigurationError(f"Duplicate completeness rule name: {rule.name}")
            seen.add(rule.name)

    def run(
        self,
        dataset: Any,
        *,
        dataset_name: str = "dataset",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CompletenessReport:
        """Run all enabled completeness rules against a dataset."""
        started_at = utc_now_iso()
        started = time.perf_counter()
        report_id = str(uuid.uuid4())
        metadata = dict(metadata or {})

        df = _as_dataframe(dataset)
        total_records = len(df)
        metadata.setdefault("dataset_fingerprint", _dataset_fingerprint(df))
        metadata.setdefault("columns", list(df.columns))

        self.logger.info("Starting completeness check report_id=%s dataset=%s", report_id, dataset_name)
        self.metrics_sink.increment("data_quality.completeness.run.started", tags={"dataset": dataset_name})

        results: List[CompletenessRuleResult] = []

        for rule in self.rules:
            if not rule.enabled:
                results.append(self._skipped_result(rule, total_records, "Rule disabled"))
                continue

            try:
                result = self._execute_rule(df, rule)
                results.append(result)
                self._publish_rule_metrics(dataset_name, result)

                if self.audit_sink:
                    self.audit_sink.write_event(
                        {
                            "event_type": "completeness_rule_executed",
                            "report_id": report_id,
                            "dataset_name": dataset_name,
                            "timestamp": utc_now_iso(),
                            "rule_result": result.to_dict(include_findings=False),
                        }
                    )

                if self.fail_fast and result.status in {CompletenessStatus.FAILED, CompletenessStatus.ERROR}:
                    self.logger.warning("Fail-fast triggered by rule=%s", rule.name)
                    break

            except Exception as exc:  # noqa: BLE001 - enterprise boundary handling
                self.logger.exception("Completeness rule failed unexpectedly: %s", rule.name)
                result = self._error_result(rule, total_records, exc)
                results.append(result)
                self.metrics_sink.increment(
                    "data_quality.completeness.rule.error",
                    tags={"dataset": dataset_name, "rule": rule.name},
                )
                if self.fail_fast:
                    break

        finished_at = utc_now_iso()
        duration_ms = (time.perf_counter() - started) * 1000
        report = self._build_report(
            report_id=report_id,
            dataset_name=dataset_name,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            total_records=total_records,
            results=results,
            metadata=metadata,
        )

        self._publish_report_metrics(report)
        if self.audit_sink:
            self.audit_sink.write_event(
                {
                    "event_type": "completeness_report_completed",
                    "report_id": report_id,
                    "dataset_name": dataset_name,
                    "timestamp": utc_now_iso(),
                    "report": report.to_dict(include_findings=False),
                }
            )

        self.logger.info(
            "Completed completeness check report_id=%s dataset=%s status=%s score=%.5f duration_ms=%.2f",
            report_id,
            dataset_name,
            report.status.value,
            report.weighted_score,
            duration_ms,
        )
        return report

    def _execute_rule(self, df: "pd.DataFrame", rule: CompletenessRule) -> CompletenessRuleResult:
        started = time.perf_counter()
        self._validate_dataset_columns(df, rule)

        if rule.rule_type == CompletenessRuleType.COLUMN_NOT_NULL:
            counters, findings, metadata = self._check_column_not_null(df, rule)
        elif rule.rule_type == CompletenessRuleType.MULTI_COLUMN_NOT_NULL:
            counters, findings, metadata = self._check_multi_column_not_null(df, rule)
        elif rule.rule_type == CompletenessRuleType.REQUIRED_COLUMNS_PRESENT:
            counters, findings, metadata = self._check_required_columns_present(df, rule)
        elif rule.rule_type == CompletenessRuleType.ROW_COMPLETENESS:
            counters, findings, metadata = self._check_row_completeness(df, rule)
        elif rule.rule_type == CompletenessRuleType.CONDITIONAL_NOT_NULL:
            counters, findings, metadata = self._check_conditional_not_null(df, rule)
        elif rule.rule_type == CompletenessRuleType.GROUP_COMPLETENESS:
            counters, findings, metadata = self._check_group_completeness(df, rule)
        elif rule.rule_type == CompletenessRuleType.REFERENCE_COVERAGE:
            counters, findings, metadata = self._check_reference_coverage(df, rule)
        elif rule.rule_type == CompletenessRuleType.TIME_WINDOW_COVERAGE:
            counters, findings, metadata = self._check_time_window_coverage(df, rule)
        elif rule.rule_type == CompletenessRuleType.EXPECTED_RECORD_COUNT:
            counters, findings, metadata = self._check_expected_record_count(df, rule)
        elif rule.rule_type == CompletenessRuleType.EXPECTED_DISTINCT_KEYS:
            counters, findings, metadata = self._check_expected_distinct_keys(df, rule)
        elif rule.rule_type == CompletenessRuleType.CUSTOM:
            counters, findings, metadata = self._check_custom(df, rule)
        else:
            raise CompletenessExecutionError(f"Unsupported rule type: {rule.rule_type}")

        duration_ms = (time.perf_counter() - started) * 1000
        score = self._score(counters["complete"], counters["evaluated"])
        status = self._status_from_score(
            score=score,
            incomplete_records=counters["incomplete"],
            error_records=counters["error"],
            threshold=rule.threshold,
        )

        return CompletenessRuleResult(
            rule_name=rule.name,
            rule_type=rule.rule_type,
            status=status,
            severity=rule.severity,
            total_records=len(df),
            evaluated_records=counters["evaluated"],
            complete_records=counters["complete"],
            incomplete_records=counters["incomplete"],
            skipped_records=counters["skipped"],
            error_records=counters["error"],
            completeness_score=score,
            threshold=rule.threshold,
            duration_ms=duration_ms,
            findings=findings,
            metadata={"weight": rule.weight, **rule.metadata, **metadata},
        )

    def _validate_dataset_columns(self, df: "pd.DataFrame", rule: CompletenessRule) -> None:
        if rule.rule_type == CompletenessRuleType.REQUIRED_COLUMNS_PRESENT:
            return

        required = set(rule.resolved_columns()) | set(rule.key_columns) | set(rule.group_by)
        if rule.time_column:
            required.add(rule.time_column)
        missing = sorted(col for col in required if col not in df.columns)
        if missing:
            raise CompletenessExecutionError(f"Rule '{rule.name}' missing dataset columns: {missing}")

    def _initial_counters(self) -> Dict[str, int]:
        return {"evaluated": 0, "complete": 0, "incomplete": 0, "skipped": 0, "error": 0}

    def _is_missing(self, value: Any, rule: CompletenessRule) -> bool:
        if rule.null_semantics == NullSemantics.CUSTOM:
            if rule.custom_missing_function is None:
                raise CompletenessConfigurationError(f"Rule '{rule.name}' missing custom_missing_function.")
            return bool(rule.custom_missing_function(value))

        if value is None:
            return True
        if isinstance(value, float) and math.isnan(value):
            return True
        if pd is not None:
            try:
                if pd.isna(value):
                    return True
            except Exception:
                pass

        if rule.null_semantics == NullSemantics.STRICT_NULL_ONLY:
            return False

        if isinstance(value, str):
            if rule.null_semantics in {
                NullSemantics.NULL_AND_EMPTY_STRING,
                NullSemantics.NULL_EMPTY_AND_WHITESPACE,
                NullSemantics.NULL_EMPTY_ZERO,
            }:
                if value == "":
                    return True
            if rule.null_semantics in {
                NullSemantics.NULL_EMPTY_AND_WHITESPACE,
                NullSemantics.NULL_EMPTY_ZERO,
            }:
                if value.strip() == "":
                    return True

        if rule.null_semantics == NullSemantics.NULL_EMPTY_ZERO:
            if value == 0 or value == 0.0:
                return True
            if isinstance(value, str) and value.strip() in {"0", "0.0", "0,0"}:
                return True

        return False

    def _append_finding(self, findings: List[CompletenessFinding], finding: CompletenessFinding) -> None:
        if len(findings) < self.max_findings_per_rule:
            findings.append(finding)

    def _check_column_not_null(
        self, df: "pd.DataFrame", rule: CompletenessRule
    ) -> Tuple[Dict[str, int], List[CompletenessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[CompletenessFinding] = []
        column = rule.column or ""

        for idx, row in df.iterrows():
            value = row[column]
            counters["evaluated"] += 1
            if self._is_missing(value, rule):
                counters["incomplete"] += 1
                self._append_finding(
                    findings,
                    CompletenessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=CompletenessStatus.FAILED,
                        message=f"Required value is missing for column '{column}'.",
                        row_index=idx,
                        column=column,
                        actual_value=value,
                        expected_value="non-missing value",
                        missing_fields=[column],
                    ),
                )
            else:
                counters["complete"] += 1

        return counters, findings, {"checked_column": column}

    def _check_multi_column_not_null(
        self, df: "pd.DataFrame", rule: CompletenessRule
    ) -> Tuple[Dict[str, int], List[CompletenessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[CompletenessFinding] = []
        columns = list(rule.columns)

        for idx, row in df.iterrows():
            counters["evaluated"] += 1
            missing_fields = [col for col in columns if self._is_missing(row[col], rule)]
            if missing_fields:
                counters["incomplete"] += 1
                self._append_finding(
                    findings,
                    CompletenessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=CompletenessStatus.FAILED,
                        message="One or more required columns are missing in the row.",
                        row_index=idx,
                        missing_fields=missing_fields,
                        expected_value="all configured columns populated",
                    ),
                )
            else:
                counters["complete"] += 1

        return counters, findings, {"checked_columns": columns}

    def _check_required_columns_present(
        self, df: "pd.DataFrame", rule: CompletenessRule
    ) -> Tuple[Dict[str, int], List[CompletenessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[CompletenessFinding] = []
        required = set(rule.required_columns)
        present = set(df.columns)
        missing = sorted(required - present)

        counters["evaluated"] = len(required)
        counters["complete"] = len(required) - len(missing)
        counters["incomplete"] = len(missing)

        for column in missing:
            self._append_finding(
                findings,
                CompletenessFinding(
                    rule_name=rule.name,
                    severity=rule.severity,
                    status=CompletenessStatus.FAILED,
                    message=f"Required column is missing from dataset: '{column}'.",
                    column=column,
                    expected_value="column present",
                    missing_fields=[column],
                ),
            )

        return counters, findings, {"required_columns": sorted(required), "missing_columns": missing}

    def _check_row_completeness(
        self, df: "pd.DataFrame", rule: CompletenessRule
    ) -> Tuple[Dict[str, int], List[CompletenessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[CompletenessFinding] = []
        columns = list(rule.columns)
        minimum = rule.min_required_fields_per_row
        if minimum is None and rule.required_ratio_per_row is not None:
            minimum = math.ceil(len(columns) * rule.required_ratio_per_row)
        if minimum is None:
            raise CompletenessConfigurationError(f"Rule '{rule.name}' has no row completeness minimum.")

        for idx, row in df.iterrows():
            counters["evaluated"] += 1
            missing_fields = [col for col in columns if self._is_missing(row[col], rule)]
            complete_count = len(columns) - len(missing_fields)
            if complete_count >= minimum:
                counters["complete"] += 1
            else:
                counters["incomplete"] += 1
                self._append_finding(
                    findings,
                    CompletenessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=CompletenessStatus.FAILED,
                        message="Row does not meet minimum completeness requirement.",
                        row_index=idx,
                        expected_value={"minimum_complete_fields": minimum},
                        actual_value={"complete_fields": complete_count},
                        missing_fields=missing_fields,
                    ),
                )

        return counters, findings, {"checked_columns": columns, "minimum_complete_fields": minimum}

    def _check_conditional_not_null(
        self, df: "pd.DataFrame", rule: CompletenessRule
    ) -> Tuple[Dict[str, int], List[CompletenessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[CompletenessFinding] = []
        column = rule.column or ""
        condition = rule.condition_function
        if condition is None:
            raise CompletenessConfigurationError(f"Rule '{rule.name}' requires condition_function.")

        for idx, row in df.iterrows():
            row_map = _row_to_mapping(row)
            try:
                applies = bool(condition(row_map))
            except Exception as exc:  # noqa: BLE001
                counters["error"] += 1
                self._append_finding(
                    findings,
                    CompletenessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=CompletenessStatus.ERROR,
                        message=f"Condition function execution error: {exc}",
                        row_index=idx,
                        column=column,
                    ),
                )
                continue

            if not applies:
                counters["skipped"] += 1
                continue

            counters["evaluated"] += 1
            value = row[column]
            if self._is_missing(value, rule):
                counters["incomplete"] += 1
                self._append_finding(
                    findings,
                    CompletenessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=CompletenessStatus.FAILED,
                        message=f"Conditionally required value is missing for column '{column}'.",
                        row_index=idx,
                        column=column,
                        actual_value=value,
                        expected_value="non-missing value when condition is true",
                        missing_fields=[column],
                    ),
                )
            else:
                counters["complete"] += 1

        return counters, findings, {"checked_column": column, "conditional": True}

    def _check_group_completeness(
        self, df: "pd.DataFrame", rule: CompletenessRule
    ) -> Tuple[Dict[str, int], List[CompletenessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[CompletenessFinding] = []
        columns = list(rule.columns)
        group_by = list(rule.group_by)

        grouped = df.groupby(group_by, dropna=False)
        group_count = 0
        for group_key, group_df in grouped:
            group_count += 1
            counters["evaluated"] += 1
            group_missing: Dict[str, int] = {}
            for column in columns:
                missing_count = int(group_df[column].apply(lambda v: self._is_missing(v, rule)).sum())
                if missing_count > 0:
                    group_missing[column] = missing_count

            if group_missing:
                counters["incomplete"] += 1
                self._append_finding(
                    findings,
                    CompletenessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=CompletenessStatus.FAILED,
                        message="Group contains missing values in required columns.",
                        key=group_key,
                        expected_value="all group values populated",
                        actual_value=group_missing,
                        missing_fields=sorted(group_missing.keys()),
                        metadata={"group_by": group_by},
                    ),
                )
            else:
                counters["complete"] += 1

        return counters, findings, {"group_by": group_by, "checked_columns": columns, "group_count": group_count}

    def _check_reference_coverage(
        self, df: "pd.DataFrame", rule: CompletenessRule
    ) -> Tuple[Dict[str, int], List[CompletenessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[CompletenessFinding] = []

        reference_raw = self.reference_provider.get_reference_dataset(rule.reference_dataset or "")
        ref_df = _as_dataframe(reference_raw)
        reference_keys = list(rule.reference_key_columns or rule.key_columns)

        missing_ref_cols = [col for col in reference_keys if col not in ref_df.columns]
        if missing_ref_cols:
            raise CompletenessExecutionError(
                f"Rule '{rule.name}' missing reference columns: {missing_ref_cols}"
            )

        actual_key_set = set(
            tuple(row[col] for col in rule.key_columns)
            for _, row in df.iterrows()
        )
        expected_key_set = set(
            tuple(row[col] for col in reference_keys)
            for _, row in ref_df.iterrows()
        )

        missing_keys = sorted(expected_key_set - actual_key_set, key=lambda item: str(item))
        counters["evaluated"] = len(expected_key_set)
        counters["complete"] = len(expected_key_set) - len(missing_keys)
        counters["incomplete"] = len(missing_keys)

        for key in missing_keys:
            self._append_finding(
                findings,
                CompletenessFinding(
                    rule_name=rule.name,
                    severity=rule.severity,
                    status=CompletenessStatus.FAILED,
                    message="Expected reference key is missing from dataset.",
                    key=key,
                    expected_value="key present in dataset",
                    actual_value="missing",
                    metadata={"reference_dataset": rule.reference_dataset},
                ),
            )

        return counters, findings, {
            "reference_dataset": rule.reference_dataset,
            "expected_key_count": len(expected_key_set),
            "actual_key_count": len(actual_key_set),
            "missing_key_count": len(missing_keys),
        }

    def _check_time_window_coverage(
        self, df: "pd.DataFrame", rule: CompletenessRule
    ) -> Tuple[Dict[str, int], List[CompletenessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[CompletenessFinding] = []

        if not rule.time_window or not rule.time_column:
            raise CompletenessConfigurationError(f"Rule '{rule.name}' requires time_window and time_column.")

        start = _to_datetime(rule.time_window.start)
        end = _to_datetime(rule.time_window.end)
        if start is None or end is None:
            raise CompletenessConfigurationError(f"Rule '{rule.name}' time_window start/end must be date-like.")
        if start > end:
            raise CompletenessConfigurationError(f"Rule '{rule.name}' time_window start cannot be after end.")

        expected = _expected_periods(start, end, rule.time_window.frequency)
        actual: Set[datetime] = set()

        for value in df[rule.time_column].tolist():
            parsed = _to_datetime(value)
            if parsed is None:
                continue
            if start <= parsed <= end:
                actual.add(_period_start(parsed, rule.time_window.frequency))

        missing_periods = sorted(expected - actual)
        counters["evaluated"] = len(expected)
        counters["complete"] = len(expected) - len(missing_periods)
        counters["incomplete"] = len(missing_periods)

        for period in missing_periods:
            self._append_finding(
                findings,
                CompletenessFinding(
                    rule_name=rule.name,
                    severity=rule.severity,
                    status=CompletenessStatus.FAILED,
                    message="Expected time window has no records.",
                    column=rule.time_column,
                    key=period.isoformat(),
                    expected_value="at least one record in period",
                    actual_value="missing",
                    metadata={"frequency": rule.time_window.frequency.value},
                ),
            )

        return counters, findings, {
            "time_column": rule.time_column,
            "frequency": rule.time_window.frequency.value,
            "expected_period_count": len(expected),
            "actual_period_count": len(actual),
            "missing_period_count": len(missing_periods),
        }

    def _check_expected_record_count(
        self, df: "pd.DataFrame", rule: CompletenessRule
    ) -> Tuple[Dict[str, int], List[CompletenessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[CompletenessFinding] = []
        actual_count = len(df)
        counters["evaluated"] = 1

        passed = False
        expected_description: Dict[str, Any] = {"comparison": rule.count_comparison.value}

        if rule.count_comparison == CountComparison.EQUAL:
            passed = actual_count == rule.expected_count
            expected_description["expected_count"] = rule.expected_count
        elif rule.count_comparison == CountComparison.GREATER_OR_EQUAL:
            passed = actual_count >= int(rule.min_count or 0)
            expected_description["min_count"] = rule.min_count
        elif rule.count_comparison == CountComparison.LESS_OR_EQUAL:
            passed = actual_count <= int(rule.max_count or 0)
            expected_description["max_count"] = rule.max_count
        elif rule.count_comparison == CountComparison.BETWEEN:
            passed = int(rule.min_count or 0) <= actual_count <= int(rule.max_count or 0)
            expected_description["min_count"] = rule.min_count
            expected_description["max_count"] = rule.max_count
        else:
            raise CompletenessConfigurationError(f"Unsupported count comparison: {rule.count_comparison}")

        if passed:
            counters["complete"] = 1
        else:
            counters["incomplete"] = 1
            self._append_finding(
                findings,
                CompletenessFinding(
                    rule_name=rule.name,
                    severity=rule.severity,
                    status=CompletenessStatus.FAILED,
                    message="Dataset record count does not meet expected completeness requirement.",
                    actual_value=actual_count,
                    expected_value=expected_description,
                ),
            )

        return counters, findings, {"actual_count": actual_count, **expected_description}

    def _check_expected_distinct_keys(
        self, df: "pd.DataFrame", rule: CompletenessRule
    ) -> Tuple[Dict[str, int], List[CompletenessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[CompletenessFinding] = []

        key_set = set(tuple(row[col] for col in rule.key_columns) for _, row in df.iterrows())
        actual_distinct = len(key_set)
        counters["evaluated"] = 1

        if rule.expected_distinct_keys is not None:
            passed = actual_distinct == rule.expected_distinct_keys
            expected_value: Any = {"expected_distinct_keys": rule.expected_distinct_keys}
        else:
            passed = actual_distinct >= int(rule.min_count or 0)
            expected_value = {"min_distinct_keys": rule.min_count}

        if passed:
            counters["complete"] = 1
        else:
            counters["incomplete"] = 1
            self._append_finding(
                findings,
                CompletenessFinding(
                    rule_name=rule.name,
                    severity=rule.severity,
                    status=CompletenessStatus.FAILED,
                    message="Distinct key count does not meet expected completeness requirement.",
                    actual_value={"actual_distinct_keys": actual_distinct},
                    expected_value=expected_value,
                    metadata={"key_columns": list(rule.key_columns)},
                ),
            )

        return counters, findings, {"actual_distinct_keys": actual_distinct, "key_columns": list(rule.key_columns)}

    def _check_custom(
        self, df: "pd.DataFrame", rule: CompletenessRule
    ) -> Tuple[Dict[str, int], List[CompletenessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[CompletenessFinding] = []
        if rule.custom_function is None:
            raise CompletenessConfigurationError(f"Rule '{rule.name}' requires custom_function.")

        for idx, row in df.iterrows():
            counters["evaluated"] += 1
            row_map = _row_to_mapping(row)
            try:
                complete = bool(rule.custom_function(row_map))
            except Exception as exc:  # noqa: BLE001
                counters["error"] += 1
                self._append_finding(
                    findings,
                    CompletenessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=CompletenessStatus.ERROR,
                        message=f"Custom completeness rule execution error: {exc}",
                        row_index=idx,
                    ),
                )
                continue

            if complete:
                counters["complete"] += 1
            else:
                counters["incomplete"] += 1
                self._append_finding(
                    findings,
                    CompletenessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=CompletenessStatus.FAILED,
                        message="Custom completeness rule returned false.",
                        row_index=idx,
                        actual_value=_json_safe(dict(row_map)),
                        expected_value=True,
                    ),
                )

        return counters, findings, {"custom": True}

    def _score(self, complete: int, evaluated: int) -> float:
        if evaluated <= 0:
            return 1.0
        return round(complete / evaluated, 8)

    def _status_from_score(
        self,
        *,
        score: float,
        incomplete_records: int,
        error_records: int,
        threshold: CompletenessThreshold,
    ) -> CompletenessStatus:
        if error_records > 0:
            return CompletenessStatus.ERROR
        if threshold.max_missing_records is not None and incomplete_records > threshold.max_missing_records:
            return CompletenessStatus.FAILED
        if threshold.max_missing_rate is not None and incomplete_records > 0:
            evaluated_estimate = incomplete_records / max(1e-12, 1 - score) if score < 1 else incomplete_records
            missing_rate = incomplete_records / evaluated_estimate if evaluated_estimate else 0.0
            if missing_rate > threshold.max_missing_rate:
                return CompletenessStatus.FAILED
        if score < threshold.min_score:
            return CompletenessStatus.FAILED
        if score < threshold.warning_score:
            return CompletenessStatus.WARNING
        return CompletenessStatus.PASSED

    def _skipped_result(
        self, rule: CompletenessRule, total_records: int, reason: str
    ) -> CompletenessRuleResult:
        return CompletenessRuleResult(
            rule_name=rule.name,
            rule_type=rule.rule_type,
            status=CompletenessStatus.SKIPPED,
            severity=rule.severity,
            total_records=total_records,
            evaluated_records=0,
            complete_records=0,
            incomplete_records=0,
            skipped_records=total_records,
            error_records=0,
            completeness_score=1.0,
            threshold=rule.threshold,
            duration_ms=0.0,
            findings=[],
            metadata={"reason": reason, "weight": rule.weight, **rule.metadata},
        )

    def _error_result(
        self, rule: CompletenessRule, total_records: int, exc: Exception
    ) -> CompletenessRuleResult:
        return CompletenessRuleResult(
            rule_name=rule.name,
            rule_type=rule.rule_type,
            status=CompletenessStatus.ERROR,
            severity=rule.severity,
            total_records=total_records,
            evaluated_records=0,
            complete_records=0,
            incomplete_records=0,
            skipped_records=0,
            error_records=total_records,
            completeness_score=0.0,
            threshold=rule.threshold,
            duration_ms=0.0,
            findings=[
                CompletenessFinding(
                    rule_name=rule.name,
                    severity=rule.severity,
                    status=CompletenessStatus.ERROR,
                    message=str(exc),
                    metadata={"exception_type": type(exc).__name__},
                )
            ],
            metadata={"weight": rule.weight, **rule.metadata},
        )

    def _build_report(
        self,
        *,
        report_id: str,
        dataset_name: str,
        started_at: str,
        finished_at: str,
        duration_ms: float,
        total_records: int,
        results: Sequence[CompletenessRuleResult],
        metadata: Dict[str, Any],
    ) -> CompletenessReport:
        passed_rules = sum(1 for r in results if r.status == CompletenessStatus.PASSED)
        failed_rules = sum(1 for r in results if r.status == CompletenessStatus.FAILED)
        warning_rules = sum(1 for r in results if r.status == CompletenessStatus.WARNING)
        skipped_rules = sum(1 for r in results if r.status == CompletenessStatus.SKIPPED)
        error_rules = sum(1 for r in results if r.status == CompletenessStatus.ERROR)

        executable_results = [r for r in results if r.status != CompletenessStatus.SKIPPED]
        if executable_results:
            overall_score = round(
                sum(r.completeness_score for r in executable_results) / len(executable_results),
                8,
            )
        else:
            overall_score = 1.0

        weight_by_rule = {rule.name: rule.weight for rule in self.rules}
        weighted_denominator = sum(weight_by_rule.get(r.rule_name, 1.0) for r in executable_results)
        if weighted_denominator > 0:
            weighted_score = round(
                sum(r.completeness_score * weight_by_rule.get(r.rule_name, 1.0) for r in executable_results)
                / weighted_denominator,
                8,
            )
        else:
            weighted_score = 1.0

        if error_rules > 0:
            status = CompletenessStatus.ERROR
        elif failed_rules > 0 or weighted_score < self.global_threshold.min_score:
            status = CompletenessStatus.FAILED
        elif warning_rules > 0 or weighted_score < self.global_threshold.warning_score:
            status = CompletenessStatus.WARNING
        else:
            status = CompletenessStatus.PASSED

        return CompletenessReport(
            report_id=report_id,
            dataset_name=dataset_name,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            total_records=total_records,
            overall_score=overall_score,
            weighted_score=weighted_score,
            passed_rules=passed_rules,
            failed_rules=failed_rules,
            warning_rules=warning_rules,
            skipped_rules=skipped_rules,
            error_rules=error_rules,
            rule_results=list(results),
            metadata=metadata,
        )

    def _publish_rule_metrics(self, dataset_name: str, result: CompletenessRuleResult) -> None:
        tags = {
            "dataset": dataset_name,
            "rule": result.rule_name,
            "rule_type": result.rule_type.value,
            "status": result.status.value,
            "severity": result.severity.value,
        }
        self.metrics_sink.gauge("data_quality.completeness.rule.score", result.completeness_score, tags=tags)
        self.metrics_sink.gauge("data_quality.completeness.rule.incomplete_records", result.incomplete_records, tags=tags)
        self.metrics_sink.gauge("data_quality.completeness.rule.error_records", result.error_records, tags=tags)
        self.metrics_sink.timing("data_quality.completeness.rule.duration_ms", result.duration_ms, tags=tags)

    def _publish_report_metrics(self, report: CompletenessReport) -> None:
        tags = {"dataset": report.dataset_name, "status": report.status.value}
        self.metrics_sink.gauge("data_quality.completeness.report.weighted_score", report.weighted_score, tags=tags)
        self.metrics_sink.gauge("data_quality.completeness.report.overall_score", report.overall_score, tags=tags)
        self.metrics_sink.timing("data_quality.completeness.report.duration_ms", report.duration_ms, tags=tags)
        self.metrics_sink.increment("data_quality.completeness.run.completed", tags=tags)


# =============================================================================
# Rule Factory
# =============================================================================


class CompletenessRuleFactory:
    """Factory helpers for concise standardized rule creation."""

    @staticmethod
    def column_not_null(
        name: str,
        column: str,
        *,
        min_score: float = 0.99,
        severity: Severity = Severity.HIGH,
        weight: float = 1.0,
        null_semantics: NullSemantics = NullSemantics.NULL_EMPTY_AND_WHITESPACE,
    ) -> CompletenessRule:
        return CompletenessRule(
            name=name,
            rule_type=CompletenessRuleType.COLUMN_NOT_NULL,
            column=column,
            threshold=CompletenessThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
            null_semantics=null_semantics,
        )

    @staticmethod
    def multi_column_not_null(
        name: str,
        columns: Sequence[str],
        *,
        min_score: float = 0.99,
        severity: Severity = Severity.HIGH,
        weight: float = 1.0,
    ) -> CompletenessRule:
        return CompletenessRule(
            name=name,
            rule_type=CompletenessRuleType.MULTI_COLUMN_NOT_NULL,
            columns=list(columns),
            threshold=CompletenessThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def required_columns_present(
        name: str,
        required_columns: Sequence[str],
        *,
        min_score: float = 1.0,
        severity: Severity = Severity.CRITICAL,
        weight: float = 2.0,
    ) -> CompletenessRule:
        return CompletenessRule(
            name=name,
            rule_type=CompletenessRuleType.REQUIRED_COLUMNS_PRESENT,
            required_columns=list(required_columns),
            threshold=CompletenessThreshold(min_score=min_score, warning_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def row_completeness(
        name: str,
        columns: Sequence[str],
        *,
        min_required_fields_per_row: Optional[int] = None,
        required_ratio_per_row: Optional[float] = None,
        min_score: float = 0.95,
        severity: Severity = Severity.MEDIUM,
        weight: float = 1.0,
    ) -> CompletenessRule:
        return CompletenessRule(
            name=name,
            rule_type=CompletenessRuleType.ROW_COMPLETENESS,
            columns=list(columns),
            min_required_fields_per_row=min_required_fields_per_row,
            required_ratio_per_row=required_ratio_per_row,
            threshold=CompletenessThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def conditional_not_null(
        name: str,
        column: str,
        condition_function: ConditionFunction,
        *,
        min_score: float = 0.99,
        severity: Severity = Severity.HIGH,
        weight: float = 1.0,
    ) -> CompletenessRule:
        return CompletenessRule(
            name=name,
            rule_type=CompletenessRuleType.CONDITIONAL_NOT_NULL,
            column=column,
            condition_function=condition_function,
            threshold=CompletenessThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def reference_coverage(
        name: str,
        key_columns: Sequence[str],
        reference_dataset: str,
        *,
        reference_key_columns: Optional[Sequence[str]] = None,
        min_score: float = 0.995,
        severity: Severity = Severity.CRITICAL,
        weight: float = 2.0,
    ) -> CompletenessRule:
        return CompletenessRule(
            name=name,
            rule_type=CompletenessRuleType.REFERENCE_COVERAGE,
            key_columns=list(key_columns),
            reference_dataset=reference_dataset,
            reference_key_columns=list(reference_key_columns or key_columns),
            threshold=CompletenessThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def time_window_coverage(
        name: str,
        time_column: str,
        start: Any,
        end: Any,
        *,
        frequency: TimeFrequency = TimeFrequency.DAILY,
        min_score: float = 0.99,
        severity: Severity = Severity.HIGH,
        weight: float = 1.5,
    ) -> CompletenessRule:
        return CompletenessRule(
            name=name,
            rule_type=CompletenessRuleType.TIME_WINDOW_COVERAGE,
            time_column=time_column,
            time_window=TimeWindowExpectation(start=start, end=end, frequency=frequency),
            threshold=CompletenessThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def expected_record_count(
        name: str,
        *,
        expected_count: Optional[int] = None,
        min_count: Optional[int] = None,
        max_count: Optional[int] = None,
        comparison: CountComparison = CountComparison.GREATER_OR_EQUAL,
        severity: Severity = Severity.HIGH,
        weight: float = 1.0,
    ) -> CompletenessRule:
        return CompletenessRule(
            name=name,
            rule_type=CompletenessRuleType.EXPECTED_RECORD_COUNT,
            expected_count=expected_count,
            min_count=min_count,
            max_count=max_count,
            count_comparison=comparison,
            threshold=CompletenessThreshold(min_score=1.0, warning_score=1.0),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def custom(
        name: str,
        function: CustomCompletenessFunction,
        *,
        min_score: float = 0.95,
        severity: Severity = Severity.HIGH,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CompletenessRule:
        return CompletenessRule(
            name=name,
            rule_type=CompletenessRuleType.CUSTOM,
            custom_function=function,
            threshold=CompletenessThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
            metadata=metadata or {},
        )


# =============================================================================
# Local Smoke Example
# =============================================================================


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if pd is None:
        raise SystemExit("pandas is required to run the local example: pip install pandas")

    dataset = pd.DataFrame(
        [
            {
                "customer_id": 1,
                "name": "Ana",
                "email": "ana@example.com",
                "phone": "51999990000",
                "document": "123",
                "country": "BR",
                "created_at": "2026-05-01",
            },
            {
                "customer_id": 2,
                "name": "Bruno",
                "email": "",
                "phone": None,
                "document": "456",
                "country": "BR",
                "created_at": "2026-05-02",
            },
            {
                "customer_id": 3,
                "name": "",
                "email": None,
                "phone": None,
                "document": None,
                "country": "AR",
                "created_at": "2026-05-04",
            },
        ]
    )

    reference_customers = pd.DataFrame(
        [
            {"customer_id": 1},
            {"customer_id": 2},
            {"customer_id": 3},
            {"customer_id": 4},
        ]
    )

    rules = [
        CompletenessRuleFactory.required_columns_present(
            "required_customer_columns",
            ["customer_id", "name", "email", "document", "created_at"],
        ),
        CompletenessRuleFactory.column_not_null(
            "customer_id_required",
            "customer_id",
            min_score=1.0,
            severity=Severity.CRITICAL,
            weight=2.0,
        ),
        CompletenessRuleFactory.row_completeness(
            "minimum_profile_completeness",
            ["name", "email", "phone", "document"],
            min_required_fields_per_row=2,
            min_score=0.90,
        ),
        CompletenessRuleFactory.conditional_not_null(
            "document_required_for_br_customers",
            "document",
            lambda row: row.get("country") == "BR",
            min_score=1.0,
        ),
        CompletenessRuleFactory.reference_coverage(
            "customer_master_coverage",
            ["customer_id"],
            "customer_master",
            min_score=0.95,
        ),
        CompletenessRuleFactory.time_window_coverage(
            "daily_customer_load_coverage",
            "created_at",
            "2026-05-01",
            "2026-05-04",
            frequency=TimeFrequency.DAILY,
            min_score=0.75,
        ),
        CompletenessRuleFactory.expected_record_count(
            "minimum_customer_records",
            min_count=3,
            comparison=CountComparison.GREATER_OR_EQUAL,
        ),
    ]

    checker = CompletenessChecker(
        rules,
        reference_provider=StaticReferenceDataProvider({"customer_master": reference_customers}),
        audit_sink=InMemoryAuditSink(),
        max_findings_per_rule=100,
    )

    report = checker.run(dataset, dataset_name="customers")
    print(report.to_json(include_findings=True))

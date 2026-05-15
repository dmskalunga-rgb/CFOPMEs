"""
data/quality/consistency_checker.py

Enterprise-grade Consistency Checker for data quality validation.

This module validates whether data is internally and externally consistent
across columns, rows, grouped aggregates, temporal sequences, reference
sources, and business invariants.

Main capabilities:
- Cross-field consistency checks
- Conditional consistency rules
- Referential consistency against trusted datasets
- Duplicate key consistency checks
- Aggregation reconciliation checks
- Temporal sequence consistency checks
- Status transition consistency checks
- Type/value normalization before comparison
- Weighted consistency scoring
- Severity-based findings
- Audit-ready execution reports
- Metrics sink integration
- Pandas-native implementation with clean extension points

Designed for enterprise data platforms, lakehouse quality gates, ETL/ELT
pipelines, orchestration workflows, data contracts, master-data validation,
and compliance-ready data quality audits.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import operator
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
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


class ConsistencyCheckerError(Exception):
    """Base exception for consistency checker failures."""


class ConsistencyConfigurationError(ConsistencyCheckerError):
    """Raised when a consistency rule/configuration is invalid."""


class ConsistencyExecutionError(ConsistencyCheckerError):
    """Raised when a consistency check cannot be executed safely."""


class DatasetValidationError(ConsistencyCheckerError):
    """Raised when an input dataset is invalid or unsupported."""


# =============================================================================
# Enums
# =============================================================================


class Severity(str, Enum):
    """Severity level for consistency findings."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ConsistencyStatus(str, Enum):
    """Execution status for rules and reports."""

    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"
    ERROR = "error"


class ConsistencyRuleType(str, Enum):
    """Supported consistency rule categories."""

    CROSS_FIELD_COMPARISON = "cross_field_comparison"
    FIELD_EXPRESSION = "field_expression"
    CONDITIONAL_CONSISTENCY = "conditional_consistency"
    REFERENTIAL_CONSISTENCY = "referential_consistency"
    DUPLICATE_KEY_CONSISTENCY = "duplicate_key_consistency"
    AGGREGATE_RECONCILIATION = "aggregate_reconciliation"
    TEMPORAL_ORDERING = "temporal_ordering"
    STATUS_TRANSITION = "status_transition"
    MUTUAL_EXCLUSIVITY = "mutual_exclusivity"
    CO_OCCURRENCE = "co_occurrence"
    CUSTOM = "custom"


class ComparisonOperator(str, Enum):
    """Comparison operators used by cross-field and aggregate rules."""

    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    MATCHES = "matches"


class NullHandling(str, Enum):
    """How null values should be handled during consistency checks."""

    IGNORE = "ignore"
    FAIL = "fail"
    PASS = "pass"
    COMPARE = "compare"


class AggregationFunction(str, Enum):
    """Supported aggregation functions for reconciliation."""

    SUM = "sum"
    COUNT = "count"
    DISTINCT_COUNT = "distinct_count"
    MIN = "min"
    MAX = "max"
    MEAN = "mean"


class SortDirection(str, Enum):
    """Sort direction used by temporal and status-sequence rules."""

    ASC = "asc"
    DESC = "desc"


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
    """Optional provider for source-of-truth/reference datasets."""

    def get_reference_dataset(self, name: str) -> Any:
        ...


RowPredicate = Callable[[Mapping[str, Any]], bool]
RowExpression = Callable[[Mapping[str, Any]], Any]
CustomConsistencyFunction = Callable[[Mapping[str, Any]], bool]


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class ConsistencyThreshold:
    """Threshold configuration for a rule or full report."""

    min_score: float = 0.95
    warning_score: float = 0.98
    max_inconsistent_records: Optional[int] = None
    max_error_rate: Optional[float] = None

    def validate(self) -> None:
        if not 0 <= self.min_score <= 1:
            raise ConsistencyConfigurationError("min_score must be between 0 and 1.")
        if not 0 <= self.warning_score <= 1:
            raise ConsistencyConfigurationError("warning_score must be between 0 and 1.")
        if self.warning_score < self.min_score:
            raise ConsistencyConfigurationError("warning_score must be greater than or equal to min_score.")
        if self.max_inconsistent_records is not None and self.max_inconsistent_records < 0:
            raise ConsistencyConfigurationError("max_inconsistent_records cannot be negative.")
        if self.max_error_rate is not None and not 0 <= self.max_error_rate <= 1:
            raise ConsistencyConfigurationError("max_error_rate must be between 0 and 1.")


@dataclass(frozen=True)
class AggregateSpec:
    """Aggregate expression used by reconciliation rules."""

    function: AggregationFunction
    column: Optional[str] = None
    alias: Optional[str] = None

    def validate(self) -> None:
        if self.function != AggregationFunction.COUNT and not self.column:
            raise ConsistencyConfigurationError(
                f"Aggregation function '{self.function.value}' requires column."
            )


@dataclass(frozen=True)
class StatusTransitionSpec:
    """Allowed transition graph for status consistency checks."""

    status_column: str
    allowed_transitions: Mapping[Any, Sequence[Any]]
    allow_initial_statuses: Optional[Sequence[Any]] = None
    allow_terminal_repeats: bool = True

    def validate(self) -> None:
        if not self.status_column:
            raise ConsistencyConfigurationError("StatusTransitionSpec requires status_column.")
        if not self.allowed_transitions:
            raise ConsistencyConfigurationError("StatusTransitionSpec requires allowed_transitions.")


@dataclass(frozen=True)
class ConsistencyRule:
    """Definition of a consistency validation rule."""

    name: str
    rule_type: ConsistencyRuleType
    left_column: Optional[str] = None
    right_column: Optional[str] = None
    columns: Sequence[str] = field(default_factory=list)
    key_columns: Sequence[str] = field(default_factory=list)
    group_by: Sequence[str] = field(default_factory=list)
    order_by: Optional[str] = None
    comparison: ComparisonOperator = ComparisonOperator.EQ
    expected_value: Optional[Any] = None
    expected_values: Optional[Sequence[Any]] = None
    regex: Optional[str] = None
    tolerance: Optional[float] = None
    percent_tolerance: Optional[float] = None
    null_handling: NullHandling = NullHandling.IGNORE
    case_sensitive: bool = True
    trim_strings: bool = True
    condition_function: Optional[RowPredicate] = None
    expression_function: Optional[RowExpression] = None
    custom_function: Optional[CustomConsistencyFunction] = None
    reference_dataset: Optional[str] = None
    reference_key_columns: Sequence[str] = field(default_factory=list)
    reference_columns: Sequence[str] = field(default_factory=list)
    aggregate_left: Optional[AggregateSpec] = None
    aggregate_right: Optional[AggregateSpec] = None
    status_transition: Optional[StatusTransitionSpec] = None
    sort_direction: SortDirection = SortDirection.ASC
    weight: float = 1.0
    severity: Severity = Severity.HIGH
    threshold: ConsistencyThreshold = field(default_factory=ConsistencyThreshold)
    metadata: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def all_dataset_columns(self) -> Set[str]:
        cols: Set[str] = set(self.columns)
        for value in [self.left_column, self.right_column, self.order_by]:
            if value:
                cols.add(value)
        cols.update(self.key_columns)
        cols.update(self.group_by)
        if self.aggregate_left and self.aggregate_left.column:
            cols.add(self.aggregate_left.column)
        if self.aggregate_right and self.aggregate_right.column:
            cols.add(self.aggregate_right.column)
        if self.status_transition:
            cols.add(self.status_transition.status_column)
        return cols

    def validate(self) -> None:
        if not self.name or not self.name.strip():
            raise ConsistencyConfigurationError("Rule name is required.")
        if self.weight <= 0:
            raise ConsistencyConfigurationError(f"Rule '{self.name}' weight must be greater than zero.")
        self.threshold.validate()

        if self.rule_type == ConsistencyRuleType.CROSS_FIELD_COMPARISON:
            if not self.left_column or not self.right_column:
                raise ConsistencyConfigurationError(
                    f"Rule '{self.name}' requires left_column and right_column."
                )

        if self.rule_type == ConsistencyRuleType.FIELD_EXPRESSION:
            if not self.left_column:
                raise ConsistencyConfigurationError(f"Rule '{self.name}' requires left_column.")
            if self.expression_function is None:
                raise ConsistencyConfigurationError(f"Rule '{self.name}' requires expression_function.")

        if self.rule_type == ConsistencyRuleType.CONDITIONAL_CONSISTENCY:
            if self.condition_function is None:
                raise ConsistencyConfigurationError(f"Rule '{self.name}' requires condition_function.")
            if not self.left_column:
                raise ConsistencyConfigurationError(f"Rule '{self.name}' requires left_column.")

        if self.rule_type == ConsistencyRuleType.REFERENTIAL_CONSISTENCY:
            if not self.reference_dataset:
                raise ConsistencyConfigurationError(f"Rule '{self.name}' requires reference_dataset.")
            if not self.key_columns:
                raise ConsistencyConfigurationError(f"Rule '{self.name}' requires key_columns.")

        if self.rule_type == ConsistencyRuleType.DUPLICATE_KEY_CONSISTENCY:
            if not self.key_columns:
                raise ConsistencyConfigurationError(f"Rule '{self.name}' requires key_columns.")
            if not self.columns:
                raise ConsistencyConfigurationError(
                    f"Rule '{self.name}' requires columns to verify across duplicate keys."
                )

        if self.rule_type == ConsistencyRuleType.AGGREGATE_RECONCILIATION:
            if not self.aggregate_left or not self.aggregate_right:
                raise ConsistencyConfigurationError(
                    f"Rule '{self.name}' requires aggregate_left and aggregate_right."
                )
            self.aggregate_left.validate()
            self.aggregate_right.validate()

        if self.rule_type == ConsistencyRuleType.TEMPORAL_ORDERING:
            if not self.key_columns:
                raise ConsistencyConfigurationError(f"Rule '{self.name}' requires key_columns.")
            if not self.order_by:
                raise ConsistencyConfigurationError(f"Rule '{self.name}' requires order_by.")
            if not self.left_column:
                raise ConsistencyConfigurationError(
                    f"Rule '{self.name}' requires left_column as the temporal value to test."
                )

        if self.rule_type == ConsistencyRuleType.STATUS_TRANSITION:
            if not self.key_columns:
                raise ConsistencyConfigurationError(f"Rule '{self.name}' requires key_columns.")
            if not self.order_by:
                raise ConsistencyConfigurationError(f"Rule '{self.name}' requires order_by.")
            if not self.status_transition:
                raise ConsistencyConfigurationError(f"Rule '{self.name}' requires status_transition.")
            self.status_transition.validate()

        if self.rule_type in {ConsistencyRuleType.MUTUAL_EXCLUSIVITY, ConsistencyRuleType.CO_OCCURRENCE}:
            if len(self.columns) < 2:
                raise ConsistencyConfigurationError(f"Rule '{self.name}' requires at least two columns.")

        if self.rule_type == ConsistencyRuleType.CUSTOM and self.custom_function is None:
            raise ConsistencyConfigurationError(f"Rule '{self.name}' requires custom_function.")

        if self.comparison == ComparisonOperator.MATCHES:
            if not self.regex:
                raise ConsistencyConfigurationError(f"Rule '{self.name}' requires regex.")
            try:
                re.compile(self.regex)
            except re.error as exc:
                raise ConsistencyConfigurationError(f"Rule '{self.name}' has invalid regex: {exc}") from exc


@dataclass
class ConsistencyFinding:
    """Single row-level, key-level, group-level, or rule-level consistency finding."""

    rule_name: str
    severity: Severity
    status: ConsistencyStatus
    message: str
    row_index: Optional[Any] = None
    column: Optional[str] = None
    key: Optional[Any] = None
    actual_value: Optional[Any] = None
    expected_value: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["severity"] = self.severity.value
        payload["status"] = self.status.value
        return _json_safe(payload)


@dataclass
class ConsistencyRuleResult:
    """Aggregated execution result for one consistency rule."""

    rule_name: str
    rule_type: ConsistencyRuleType
    status: ConsistencyStatus
    severity: Severity
    total_records: int
    evaluated_records: int
    consistent_records: int
    inconsistent_records: int
    skipped_records: int
    error_records: int
    consistency_score: float
    threshold: ConsistencyThreshold
    duration_ms: float
    findings: List[ConsistencyFinding] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def inconsistency_rate(self) -> float:
        if self.evaluated_records <= 0:
            return 0.0
        return self.inconsistent_records / self.evaluated_records

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
            "consistent_records": self.consistent_records,
            "inconsistent_records": self.inconsistent_records,
            "skipped_records": self.skipped_records,
            "error_records": self.error_records,
            "consistency_score": self.consistency_score,
            "inconsistency_rate": self.inconsistency_rate,
            "error_rate": self.error_rate,
            "threshold": asdict(self.threshold),
            "duration_ms": self.duration_ms,
            "metadata": _json_safe(self.metadata),
        }
        if include_findings:
            data["findings"] = [finding.to_dict() for finding in self.findings]
        return data


@dataclass
class ConsistencyReport:
    """Complete consistency validation report."""

    report_id: str
    dataset_name: str
    status: ConsistencyStatus
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
    rule_results: List[ConsistencyRuleResult]
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
            "pandas is required for ConsistencyChecker. Install pandas or adapt the dataset adapter."
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


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if pd is not None:
        try:
            return bool(pd.isna(value))
        except Exception:
            return False
    return False


def _normalize_value(value: Any, *, trim: bool = True, case_sensitive: bool = True) -> Any:
    if isinstance(value, str):
        normalized = value.strip() if trim else value
        return normalized if case_sensitive else normalized.casefold()
    return value


def _to_decimal(value: Any) -> Optional[Decimal]:
    if _is_null(value):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _to_datetime(value: Any) -> Optional[datetime]:
    if _is_null(value):
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


def _key_from_row(row: Mapping[str, Any], columns: Sequence[str]) -> Tuple[Any, ...]:
    return tuple(row.get(col) for col in columns)


def _operator_func(comparison: ComparisonOperator) -> Callable[[Any, Any], bool]:
    mapping = {
        ComparisonOperator.EQ: operator.eq,
        ComparisonOperator.NE: operator.ne,
        ComparisonOperator.GT: operator.gt,
        ComparisonOperator.GTE: operator.ge,
        ComparisonOperator.LT: operator.lt,
        ComparisonOperator.LTE: operator.le,
    }
    if comparison not in mapping:
        raise ConsistencyConfigurationError(f"Unsupported simple comparison operator: {comparison}")
    return mapping[comparison]


def _numeric_compare_with_tolerance(
    left: Any,
    right: Any,
    comparison: ComparisonOperator,
    tolerance: Optional[float],
    percent_tolerance: Optional[float],
) -> Optional[bool]:
    left_dec = _to_decimal(left)
    right_dec = _to_decimal(right)
    if left_dec is None or right_dec is None:
        return None

    if comparison == ComparisonOperator.EQ:
        difference = abs(left_dec - right_dec)
        if percent_tolerance is not None:
            if right_dec == 0:
                return difference == 0
            return difference <= abs(right_dec) * Decimal(str(percent_tolerance))
        if tolerance is not None:
            return difference <= Decimal(str(tolerance))
        return left_dec == right_dec

    if comparison == ComparisonOperator.NE:
        eq_result = _numeric_compare_with_tolerance(
            left_dec,
            right_dec,
            ComparisonOperator.EQ,
            tolerance,
            percent_tolerance,
        )
        return not eq_result if eq_result is not None else None

    return _operator_func(comparison)(left_dec, right_dec)


def _aggregate(df: "pd.DataFrame", spec: AggregateSpec) -> Any:
    if spec.function == AggregationFunction.COUNT:
        return len(df)
    if spec.column is None:
        raise ConsistencyConfigurationError(f"Aggregation '{spec.function.value}' requires column.")
    series = df[spec.column]
    if spec.function == AggregationFunction.SUM:
        return series.sum()
    if spec.function == AggregationFunction.DISTINCT_COUNT:
        return series.nunique(dropna=True)
    if spec.function == AggregationFunction.MIN:
        return series.min()
    if spec.function == AggregationFunction.MAX:
        return series.max()
    if spec.function == AggregationFunction.MEAN:
        return series.mean()
    raise ConsistencyConfigurationError(f"Unsupported aggregation: {spec.function}")


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
            raise ConsistencyExecutionError(f"Reference dataset not found: {name}")
        return self.datasets[name]


# =============================================================================
# Consistency Checker
# =============================================================================


class ConsistencyChecker:
    """
    Enterprise data consistency checker.

    Example:
        checker = ConsistencyChecker([
            ConsistencyRuleFactory.cross_field_comparison(
                "paid_not_above_total",
                left_column="paid_amount",
                right_column="total_amount",
                comparison=ComparisonOperator.LTE,
            )
        ])
        report = checker.run(dataset, dataset_name="orders")
    """

    def __init__(
        self,
        rules: Sequence[ConsistencyRule],
        *,
        reference_provider: Optional[ReferenceDataProvider] = None,
        metrics_sink: Optional[MetricsSink] = None,
        audit_sink: Optional[AuditSink] = None,
        max_findings_per_rule: int = 1_000,
        fail_fast: bool = False,
        global_threshold: ConsistencyThreshold = ConsistencyThreshold(min_score=0.95, warning_score=0.98),
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
            raise ConsistencyConfigurationError("max_findings_per_rule cannot be negative.")
        self.global_threshold.validate()
        seen = set()
        for rule in self.rules:
            rule.validate()
            if rule.name in seen:
                raise ConsistencyConfigurationError(f"Duplicate consistency rule name: {rule.name}")
            seen.add(rule.name)

    def run(
        self,
        dataset: Any,
        *,
        dataset_name: str = "dataset",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConsistencyReport:
        """Run all enabled consistency rules against a dataset."""
        started_at = utc_now_iso()
        started = time.perf_counter()
        report_id = str(uuid.uuid4())
        metadata = dict(metadata or {})

        df = _as_dataframe(dataset)
        total_records = len(df)
        metadata.setdefault("dataset_fingerprint", _dataset_fingerprint(df))
        metadata.setdefault("columns", list(df.columns))

        self.logger.info("Starting consistency check report_id=%s dataset=%s", report_id, dataset_name)
        self.metrics_sink.increment("data_quality.consistency.run.started", tags={"dataset": dataset_name})

        results: List[ConsistencyRuleResult] = []

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
                            "event_type": "consistency_rule_executed",
                            "report_id": report_id,
                            "dataset_name": dataset_name,
                            "timestamp": utc_now_iso(),
                            "rule_result": result.to_dict(include_findings=False),
                        }
                    )

                if self.fail_fast and result.status in {ConsistencyStatus.FAILED, ConsistencyStatus.ERROR}:
                    self.logger.warning("Fail-fast triggered by rule=%s", rule.name)
                    break

            except Exception as exc:  # noqa: BLE001 - enterprise boundary handling
                self.logger.exception("Consistency rule failed unexpectedly: %s", rule.name)
                result = self._error_result(rule, total_records, exc)
                results.append(result)
                self.metrics_sink.increment(
                    "data_quality.consistency.rule.error",
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
                    "event_type": "consistency_report_completed",
                    "report_id": report_id,
                    "dataset_name": dataset_name,
                    "timestamp": utc_now_iso(),
                    "report": report.to_dict(include_findings=False),
                }
            )

        self.logger.info(
            "Completed consistency check report_id=%s dataset=%s status=%s score=%.5f duration_ms=%.2f",
            report_id,
            dataset_name,
            report.status.value,
            report.weighted_score,
            duration_ms,
        )
        return report

    def _execute_rule(self, df: "pd.DataFrame", rule: ConsistencyRule) -> ConsistencyRuleResult:
        started = time.perf_counter()
        self._validate_dataset_columns(df, rule)

        if rule.rule_type == ConsistencyRuleType.CROSS_FIELD_COMPARISON:
            counters, findings, metadata = self._check_cross_field_comparison(df, rule)
        elif rule.rule_type == ConsistencyRuleType.FIELD_EXPRESSION:
            counters, findings, metadata = self._check_field_expression(df, rule)
        elif rule.rule_type == ConsistencyRuleType.CONDITIONAL_CONSISTENCY:
            counters, findings, metadata = self._check_conditional_consistency(df, rule)
        elif rule.rule_type == ConsistencyRuleType.REFERENTIAL_CONSISTENCY:
            counters, findings, metadata = self._check_referential_consistency(df, rule)
        elif rule.rule_type == ConsistencyRuleType.DUPLICATE_KEY_CONSISTENCY:
            counters, findings, metadata = self._check_duplicate_key_consistency(df, rule)
        elif rule.rule_type == ConsistencyRuleType.AGGREGATE_RECONCILIATION:
            counters, findings, metadata = self._check_aggregate_reconciliation(df, rule)
        elif rule.rule_type == ConsistencyRuleType.TEMPORAL_ORDERING:
            counters, findings, metadata = self._check_temporal_ordering(df, rule)
        elif rule.rule_type == ConsistencyRuleType.STATUS_TRANSITION:
            counters, findings, metadata = self._check_status_transition(df, rule)
        elif rule.rule_type == ConsistencyRuleType.MUTUAL_EXCLUSIVITY:
            counters, findings, metadata = self._check_mutual_exclusivity(df, rule)
        elif rule.rule_type == ConsistencyRuleType.CO_OCCURRENCE:
            counters, findings, metadata = self._check_co_occurrence(df, rule)
        elif rule.rule_type == ConsistencyRuleType.CUSTOM:
            counters, findings, metadata = self._check_custom(df, rule)
        else:
            raise ConsistencyExecutionError(f"Unsupported rule type: {rule.rule_type}")

        duration_ms = (time.perf_counter() - started) * 1000
        score = self._score(counters["consistent"], counters["evaluated"])
        status = self._status_from_score(
            score=score,
            inconsistent_records=counters["inconsistent"],
            error_records=counters["error"],
            evaluated_records=counters["evaluated"],
            threshold=rule.threshold,
        )

        return ConsistencyRuleResult(
            rule_name=rule.name,
            rule_type=rule.rule_type,
            status=status,
            severity=rule.severity,
            total_records=len(df),
            evaluated_records=counters["evaluated"],
            consistent_records=counters["consistent"],
            inconsistent_records=counters["inconsistent"],
            skipped_records=counters["skipped"],
            error_records=counters["error"],
            consistency_score=score,
            threshold=rule.threshold,
            duration_ms=duration_ms,
            findings=findings,
            metadata={"weight": rule.weight, **rule.metadata, **metadata},
        )

    def _validate_dataset_columns(self, df: "pd.DataFrame", rule: ConsistencyRule) -> None:
        missing = sorted(col for col in rule.all_dataset_columns() if col not in df.columns)
        if missing:
            raise ConsistencyExecutionError(f"Rule '{rule.name}' missing dataset columns: {missing}")

    def _initial_counters(self) -> Dict[str, int]:
        return {"evaluated": 0, "consistent": 0, "inconsistent": 0, "skipped": 0, "error": 0}

    def _append_finding(self, findings: List[ConsistencyFinding], finding: ConsistencyFinding) -> None:
        if len(findings) < self.max_findings_per_rule:
            findings.append(finding)

    def _handle_null_pair(self, left: Any, right: Any, rule: ConsistencyRule) -> Optional[bool]:
        left_null = _is_null(left)
        right_null = _is_null(right)
        if not left_null and not right_null:
            return None
        if rule.null_handling == NullHandling.IGNORE:
            return None if not (left_null or right_null) else True
        if rule.null_handling == NullHandling.FAIL:
            return False
        if rule.null_handling == NullHandling.PASS:
            return True
        if rule.null_handling == NullHandling.COMPARE:
            return left_null == right_null
        return False

    def _compare_values(self, left: Any, right: Any, rule: ConsistencyRule) -> bool:
        null_result = self._handle_null_pair(left, right, rule)
        if null_result is not None:
            return null_result

        if rule.comparison in {
            ComparisonOperator.EQ,
            ComparisonOperator.NE,
            ComparisonOperator.GT,
            ComparisonOperator.GTE,
            ComparisonOperator.LT,
            ComparisonOperator.LTE,
        }:
            numeric_result = _numeric_compare_with_tolerance(
                left,
                right,
                rule.comparison,
                rule.tolerance,
                rule.percent_tolerance,
            )
            if numeric_result is not None:
                return numeric_result

            left_norm = _normalize_value(left, trim=rule.trim_strings, case_sensitive=rule.case_sensitive)
            right_norm = _normalize_value(right, trim=rule.trim_strings, case_sensitive=rule.case_sensitive)
            return bool(_operator_func(rule.comparison)(left_norm, right_norm))

        if rule.comparison == ComparisonOperator.IN:
            expected = rule.expected_values if rule.expected_values is not None else right
            if isinstance(expected, (list, tuple, set)):
                normalized = [_normalize_value(v, trim=rule.trim_strings, case_sensitive=rule.case_sensitive) for v in expected]
                left_norm = _normalize_value(left, trim=rule.trim_strings, case_sensitive=rule.case_sensitive)
                return left_norm in normalized
            return False

        if rule.comparison == ComparisonOperator.NOT_IN:
            expected = rule.expected_values if rule.expected_values is not None else right
            if isinstance(expected, (list, tuple, set)):
                normalized = [_normalize_value(v, trim=rule.trim_strings, case_sensitive=rule.case_sensitive) for v in expected]
                left_norm = _normalize_value(left, trim=rule.trim_strings, case_sensitive=rule.case_sensitive)
                return left_norm not in normalized
            return False

        if rule.comparison == ComparisonOperator.MATCHES:
            flags = 0 if rule.case_sensitive else re.IGNORECASE
            pattern = re.compile(rule.regex or "", flags)
            value = str(left).strip() if rule.trim_strings else str(left)
            return bool(pattern.fullmatch(value))

        raise ConsistencyConfigurationError(f"Unsupported comparison: {rule.comparison}")

    def _check_cross_field_comparison(
        self, df: "pd.DataFrame", rule: ConsistencyRule
    ) -> Tuple[Dict[str, int], List[ConsistencyFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[ConsistencyFinding] = []

        for idx, row in df.iterrows():
            left = row[rule.left_column]  # type: ignore[index]
            right = row[rule.right_column]  # type: ignore[index]
            if rule.null_handling == NullHandling.IGNORE and (_is_null(left) or _is_null(right)):
                counters["skipped"] += 1
                continue

            counters["evaluated"] += 1
            try:
                consistent = self._compare_values(left, right, rule)
            except Exception as exc:  # noqa: BLE001
                counters["error"] += 1
                self._append_finding(
                    findings,
                    ConsistencyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=ConsistencyStatus.ERROR,
                        message=f"Cross-field comparison error: {exc}",
                        row_index=idx,
                        column=rule.left_column,
                        actual_value=left,
                        expected_value=right,
                    ),
                )
                continue

            if consistent:
                counters["consistent"] += 1
            else:
                counters["inconsistent"] += 1
                self._append_finding(
                    findings,
                    ConsistencyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=ConsistencyStatus.FAILED,
                        message="Cross-field consistency comparison failed.",
                        row_index=idx,
                        column=rule.left_column,
                        actual_value=left,
                        expected_value={
                            "operator": rule.comparison.value,
                            "right_column": rule.right_column,
                            "right_value": right,
                        },
                    ),
                )

        return counters, findings, {
            "left_column": rule.left_column,
            "right_column": rule.right_column,
            "comparison": rule.comparison.value,
        }

    def _check_field_expression(
        self, df: "pd.DataFrame", rule: ConsistencyRule
    ) -> Tuple[Dict[str, int], List[ConsistencyFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[ConsistencyFinding] = []
        if rule.expression_function is None:
            raise ConsistencyConfigurationError(f"Rule '{rule.name}' requires expression_function.")

        for idx, row in df.iterrows():
            row_map = _row_to_mapping(row)
            actual = row[rule.left_column]  # type: ignore[index]
            if rule.null_handling == NullHandling.IGNORE and _is_null(actual):
                counters["skipped"] += 1
                continue
            counters["evaluated"] += 1
            try:
                expected = rule.expression_function(row_map)
                consistent = self._compare_values(actual, expected, rule)
            except Exception as exc:  # noqa: BLE001
                counters["error"] += 1
                self._append_finding(
                    findings,
                    ConsistencyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=ConsistencyStatus.ERROR,
                        message=f"Expression consistency error: {exc}",
                        row_index=idx,
                        column=rule.left_column,
                        actual_value=actual,
                    ),
                )
                continue

            if consistent:
                counters["consistent"] += 1
            else:
                counters["inconsistent"] += 1
                self._append_finding(
                    findings,
                    ConsistencyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=ConsistencyStatus.FAILED,
                        message="Field does not match expected expression result.",
                        row_index=idx,
                        column=rule.left_column,
                        actual_value=actual,
                        expected_value=expected,
                    ),
                )

        return counters, findings, {"left_column": rule.left_column, "expression": True}

    def _check_conditional_consistency(
        self, df: "pd.DataFrame", rule: ConsistencyRule
    ) -> Tuple[Dict[str, int], List[ConsistencyFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[ConsistencyFinding] = []
        if rule.condition_function is None:
            raise ConsistencyConfigurationError(f"Rule '{rule.name}' requires condition_function.")

        for idx, row in df.iterrows():
            row_map = _row_to_mapping(row)
            try:
                applies = bool(rule.condition_function(row_map))
            except Exception as exc:  # noqa: BLE001
                counters["error"] += 1
                self._append_finding(
                    findings,
                    ConsistencyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=ConsistencyStatus.ERROR,
                        message=f"Condition function error: {exc}",
                        row_index=idx,
                        column=rule.left_column,
                    ),
                )
                continue

            if not applies:
                counters["skipped"] += 1
                continue

            actual = row[rule.left_column]  # type: ignore[index]
            counters["evaluated"] += 1
            expected = rule.expected_value
            consistent = self._compare_values(actual, expected, rule)

            if consistent:
                counters["consistent"] += 1
            else:
                counters["inconsistent"] += 1
                self._append_finding(
                    findings,
                    ConsistencyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=ConsistencyStatus.FAILED,
                        message="Conditional consistency rule failed.",
                        row_index=idx,
                        column=rule.left_column,
                        actual_value=actual,
                        expected_value=expected,
                    ),
                )

        return counters, findings, {"conditional": True, "left_column": rule.left_column}

    def _check_referential_consistency(
        self, df: "pd.DataFrame", rule: ConsistencyRule
    ) -> Tuple[Dict[str, int], List[ConsistencyFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[ConsistencyFinding] = []

        ref_df = _as_dataframe(self.reference_provider.get_reference_dataset(rule.reference_dataset or ""))
        reference_keys = list(rule.reference_key_columns or rule.key_columns)
        missing_ref_columns = [col for col in reference_keys if col not in ref_df.columns]
        missing_ref_columns.extend(col for col in rule.reference_columns if col not in ref_df.columns)
        if missing_ref_columns:
            raise ConsistencyExecutionError(
                f"Rule '{rule.name}' missing reference columns: {sorted(set(missing_ref_columns))}"
            )

        ref_lookup: Dict[Tuple[Any, ...], Mapping[str, Any]] = {}
        for _, ref_row in ref_df.iterrows():
            ref_map = _row_to_mapping(ref_row)
            ref_key = tuple(ref_map.get(col) for col in reference_keys)
            ref_lookup[ref_key] = ref_map

        compare_columns = list(rule.columns or rule.reference_columns)
        for idx, row in df.iterrows():
            row_map = _row_to_mapping(row)
            key = tuple(row_map.get(col) for col in rule.key_columns)
            counters["evaluated"] += 1

            if key not in ref_lookup:
                counters["inconsistent"] += 1
                self._append_finding(
                    findings,
                    ConsistencyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=ConsistencyStatus.FAILED,
                        message="Dataset key does not exist in reference dataset.",
                        row_index=idx,
                        key=key,
                        actual_value="key present in dataset",
                        expected_value="key present in reference",
                        metadata={"reference_dataset": rule.reference_dataset},
                    ),
                )
                continue

            ref_row = ref_lookup[key]
            mismatches: Dict[str, Dict[str, Any]] = {}
            for column in compare_columns:
                if column not in row_map or column not in ref_row:
                    continue
                actual = row_map.get(column)
                expected = ref_row.get(column)
                if rule.null_handling == NullHandling.IGNORE and (_is_null(actual) or _is_null(expected)):
                    continue
                if not self._compare_values(actual, expected, rule):
                    mismatches[column] = {"actual": actual, "expected": expected}

            if mismatches:
                counters["inconsistent"] += 1
                self._append_finding(
                    findings,
                    ConsistencyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=ConsistencyStatus.FAILED,
                        message="Dataset values differ from reference dataset.",
                        row_index=idx,
                        key=key,
                        actual_value=mismatches,
                        expected_value="reference values",
                        metadata={"reference_dataset": rule.reference_dataset},
                    ),
                )
            else:
                counters["consistent"] += 1

        return counters, findings, {
            "reference_dataset": rule.reference_dataset,
            "key_columns": list(rule.key_columns),
            "reference_key_columns": reference_keys,
            "compare_columns": compare_columns,
        }

    def _check_duplicate_key_consistency(
        self, df: "pd.DataFrame", rule: ConsistencyRule
    ) -> Tuple[Dict[str, int], List[ConsistencyFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[ConsistencyFinding] = []

        grouped = df.groupby(list(rule.key_columns), dropna=False)
        group_count = 0
        duplicate_group_count = 0

        for key, group_df in grouped:
            group_count += 1
            if len(group_df) <= 1:
                counters["skipped"] += 1
                continue
            duplicate_group_count += 1
            counters["evaluated"] += 1

            inconsistent_columns: Dict[str, List[Any]] = {}
            for column in rule.columns:
                normalized_values = [
                    _normalize_value(v, trim=rule.trim_strings, case_sensitive=rule.case_sensitive)
                    for v in group_df[column].tolist()
                    if not (rule.null_handling == NullHandling.IGNORE and _is_null(v))
                ]
                unique_values = list(dict.fromkeys(normalized_values))
                if len(unique_values) > 1:
                    inconsistent_columns[column] = unique_values

            if inconsistent_columns:
                counters["inconsistent"] += 1
                self._append_finding(
                    findings,
                    ConsistencyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=ConsistencyStatus.FAILED,
                        message="Duplicate key group contains inconsistent attribute values.",
                        key=key,
                        actual_value=inconsistent_columns,
                        expected_value="same values for configured columns within duplicate key group",
                        metadata={"key_columns": list(rule.key_columns), "group_size": len(group_df)},
                    ),
                )
            else:
                counters["consistent"] += 1

        return counters, findings, {
            "key_columns": list(rule.key_columns),
            "checked_columns": list(rule.columns),
            "group_count": group_count,
            "duplicate_group_count": duplicate_group_count,
        }

    def _check_aggregate_reconciliation(
        self, df: "pd.DataFrame", rule: ConsistencyRule
    ) -> Tuple[Dict[str, int], List[ConsistencyFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[ConsistencyFinding] = []
        if not rule.aggregate_left or not rule.aggregate_right:
            raise ConsistencyConfigurationError(f"Rule '{rule.name}' requires aggregate specs.")

        if rule.group_by:
            grouped = df.groupby(list(rule.group_by), dropna=False)
            for key, group_df in grouped:
                counters["evaluated"] += 1
                left_value = _aggregate(group_df, rule.aggregate_left)
                right_value = _aggregate(group_df, rule.aggregate_right)
                consistent = self._compare_values(left_value, right_value, rule)
                if consistent:
                    counters["consistent"] += 1
                else:
                    counters["inconsistent"] += 1
                    self._append_finding(
                        findings,
                        ConsistencyFinding(
                            rule_name=rule.name,
                            severity=rule.severity,
                            status=ConsistencyStatus.FAILED,
                            message="Aggregate reconciliation failed for group.",
                            key=key,
                            actual_value=left_value,
                            expected_value=right_value,
                            metadata={"group_by": list(rule.group_by)},
                        ),
                    )
        else:
            counters["evaluated"] = 1
            left_value = _aggregate(df, rule.aggregate_left)
            right_value = _aggregate(df, rule.aggregate_right)
            consistent = self._compare_values(left_value, right_value, rule)
            if consistent:
                counters["consistent"] = 1
            else:
                counters["inconsistent"] = 1
                self._append_finding(
                    findings,
                    ConsistencyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=ConsistencyStatus.FAILED,
                        message="Aggregate reconciliation failed.",
                        actual_value=left_value,
                        expected_value=right_value,
                    ),
                )

        return counters, findings, {
            "aggregate_left": asdict(rule.aggregate_left),
            "aggregate_right": asdict(rule.aggregate_right),
            "group_by": list(rule.group_by),
        }

    def _check_temporal_ordering(
        self, df: "pd.DataFrame", rule: ConsistencyRule
    ) -> Tuple[Dict[str, int], List[ConsistencyFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[ConsistencyFinding] = []
        ascending = rule.sort_direction == SortDirection.ASC
        grouped = df.sort_values(list(rule.key_columns) + [rule.order_by]).groupby(list(rule.key_columns), dropna=False)

        for key, group_df in grouped:
            ordered = group_df.sort_values(rule.order_by, ascending=ascending)
            previous_value: Optional[datetime] = None
            previous_index: Optional[Any] = None
            for idx, row in ordered.iterrows():
                current_value = _to_datetime(row[rule.left_column])  # type: ignore[index]
                if current_value is None:
                    counters["skipped"] += 1
                    continue
                if previous_value is None:
                    previous_value = current_value
                    previous_index = idx
                    continue
                counters["evaluated"] += 1
                consistent = current_value >= previous_value if ascending else current_value <= previous_value
                if consistent:
                    counters["consistent"] += 1
                else:
                    counters["inconsistent"] += 1
                    self._append_finding(
                        findings,
                        ConsistencyFinding(
                            rule_name=rule.name,
                            severity=rule.severity,
                            status=ConsistencyStatus.FAILED,
                            message="Temporal ordering consistency failed within key sequence.",
                            row_index=idx,
                            key=key,
                            column=rule.left_column,
                            actual_value=current_value,
                            expected_value={
                                "previous_value": previous_value,
                                "previous_row_index": previous_index,
                                "direction": rule.sort_direction.value,
                            },
                        ),
                    )
                previous_value = current_value
                previous_index = idx

        return counters, findings, {
            "key_columns": list(rule.key_columns),
            "order_by": rule.order_by,
            "temporal_column": rule.left_column,
            "sort_direction": rule.sort_direction.value,
        }

    def _check_status_transition(
        self, df: "pd.DataFrame", rule: ConsistencyRule
    ) -> Tuple[Dict[str, int], List[ConsistencyFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[ConsistencyFinding] = []
        spec = rule.status_transition
        if spec is None:
            raise ConsistencyConfigurationError(f"Rule '{rule.name}' requires status_transition.")

        ascending = rule.sort_direction == SortDirection.ASC
        grouped = df.sort_values(list(rule.key_columns) + [rule.order_by]).groupby(list(rule.key_columns), dropna=False)
        allowed_initial = set(spec.allow_initial_statuses or [])
        allowed_map = {k: set(v) for k, v in spec.allowed_transitions.items()}

        for key, group_df in grouped:
            ordered = group_df.sort_values(rule.order_by, ascending=ascending)
            previous_status: Optional[Any] = None
            previous_index: Optional[Any] = None

            for idx, row in ordered.iterrows():
                status = row[spec.status_column]
                if _is_null(status):
                    counters["skipped"] += 1
                    continue

                if previous_status is None:
                    if allowed_initial and status not in allowed_initial:
                        counters["evaluated"] += 1
                        counters["inconsistent"] += 1
                        self._append_finding(
                            findings,
                            ConsistencyFinding(
                                rule_name=rule.name,
                                severity=rule.severity,
                                status=ConsistencyStatus.FAILED,
                                message="Initial status is not allowed.",
                                row_index=idx,
                                key=key,
                                column=spec.status_column,
                                actual_value=status,
                                expected_value=list(allowed_initial),
                            ),
                        )
                    previous_status = status
                    previous_index = idx
                    continue

                counters["evaluated"] += 1
                allowed_next = allowed_map.get(previous_status, set())
                if spec.allow_terminal_repeats and status == previous_status and not allowed_next:
                    consistent = True
                else:
                    consistent = status in allowed_next or status == previous_status

                if consistent:
                    counters["consistent"] += 1
                else:
                    counters["inconsistent"] += 1
                    self._append_finding(
                        findings,
                        ConsistencyFinding(
                            rule_name=rule.name,
                            severity=rule.severity,
                            status=ConsistencyStatus.FAILED,
                            message="Invalid status transition detected.",
                            row_index=idx,
                            key=key,
                            column=spec.status_column,
                            actual_value={"previous_status": previous_status, "current_status": status},
                            expected_value={"allowed_next_statuses": list(allowed_next)},
                            metadata={"previous_row_index": previous_index},
                        ),
                    )

                previous_status = status
                previous_index = idx

        return counters, findings, {
            "key_columns": list(rule.key_columns),
            "order_by": rule.order_by,
            "status_column": spec.status_column,
            "sort_direction": rule.sort_direction.value,
        }

    def _check_mutual_exclusivity(
        self, df: "pd.DataFrame", rule: ConsistencyRule
    ) -> Tuple[Dict[str, int], List[ConsistencyFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[ConsistencyFinding] = []
        columns = list(rule.columns)

        for idx, row in df.iterrows():
            present = [col for col in columns if not _is_null(row[col])]
            counters["evaluated"] += 1
            if len(present) <= 1:
                counters["consistent"] += 1
            else:
                counters["inconsistent"] += 1
                self._append_finding(
                    findings,
                    ConsistencyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=ConsistencyStatus.FAILED,
                        message="Mutually exclusive fields are populated together.",
                        row_index=idx,
                        actual_value={col: row[col] for col in present},
                        expected_value="at most one populated field",
                        metadata={"columns": columns},
                    ),
                )

        return counters, findings, {"columns": columns}

    def _check_co_occurrence(
        self, df: "pd.DataFrame", rule: ConsistencyRule
    ) -> Tuple[Dict[str, int], List[ConsistencyFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[ConsistencyFinding] = []
        columns = list(rule.columns)

        for idx, row in df.iterrows():
            present = [col for col in columns if not _is_null(row[col])]
            counters["evaluated"] += 1
            if len(present) in {0, len(columns)}:
                counters["consistent"] += 1
            else:
                counters["inconsistent"] += 1
                missing = [col for col in columns if col not in present]
                self._append_finding(
                    findings,
                    ConsistencyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=ConsistencyStatus.FAILED,
                        message="Co-occurring fields are only partially populated.",
                        row_index=idx,
                        actual_value={"present": present, "missing": missing},
                        expected_value="all or none of the configured fields populated",
                        metadata={"columns": columns},
                    ),
                )

        return counters, findings, {"columns": columns}

    def _check_custom(
        self, df: "pd.DataFrame", rule: ConsistencyRule
    ) -> Tuple[Dict[str, int], List[ConsistencyFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[ConsistencyFinding] = []
        if rule.custom_function is None:
            raise ConsistencyConfigurationError(f"Rule '{rule.name}' requires custom_function.")

        for idx, row in df.iterrows():
            counters["evaluated"] += 1
            row_map = _row_to_mapping(row)
            try:
                consistent = bool(rule.custom_function(row_map))
            except Exception as exc:  # noqa: BLE001
                counters["error"] += 1
                self._append_finding(
                    findings,
                    ConsistencyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=ConsistencyStatus.ERROR,
                        message=f"Custom consistency rule execution error: {exc}",
                        row_index=idx,
                    ),
                )
                continue

            if consistent:
                counters["consistent"] += 1
            else:
                counters["inconsistent"] += 1
                self._append_finding(
                    findings,
                    ConsistencyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=ConsistencyStatus.FAILED,
                        message="Custom consistency rule returned false.",
                        row_index=idx,
                        actual_value=_json_safe(dict(row_map)),
                        expected_value=True,
                    ),
                )

        return counters, findings, {"custom": True}

    def _score(self, consistent: int, evaluated: int) -> float:
        if evaluated <= 0:
            return 1.0
        return round(consistent / evaluated, 8)

    def _status_from_score(
        self,
        *,
        score: float,
        inconsistent_records: int,
        error_records: int,
        evaluated_records: int,
        threshold: ConsistencyThreshold,
    ) -> ConsistencyStatus:
        if error_records > 0:
            if threshold.max_error_rate is None:
                return ConsistencyStatus.ERROR
            error_rate = error_records / evaluated_records if evaluated_records else 1.0
            if error_rate > threshold.max_error_rate:
                return ConsistencyStatus.ERROR
        if threshold.max_inconsistent_records is not None and inconsistent_records > threshold.max_inconsistent_records:
            return ConsistencyStatus.FAILED
        if score < threshold.min_score:
            return ConsistencyStatus.FAILED
        if score < threshold.warning_score:
            return ConsistencyStatus.WARNING
        return ConsistencyStatus.PASSED

    def _skipped_result(
        self, rule: ConsistencyRule, total_records: int, reason: str
    ) -> ConsistencyRuleResult:
        return ConsistencyRuleResult(
            rule_name=rule.name,
            rule_type=rule.rule_type,
            status=ConsistencyStatus.SKIPPED,
            severity=rule.severity,
            total_records=total_records,
            evaluated_records=0,
            consistent_records=0,
            inconsistent_records=0,
            skipped_records=total_records,
            error_records=0,
            consistency_score=1.0,
            threshold=rule.threshold,
            duration_ms=0.0,
            findings=[],
            metadata={"reason": reason, "weight": rule.weight, **rule.metadata},
        )

    def _error_result(
        self, rule: ConsistencyRule, total_records: int, exc: Exception
    ) -> ConsistencyRuleResult:
        return ConsistencyRuleResult(
            rule_name=rule.name,
            rule_type=rule.rule_type,
            status=ConsistencyStatus.ERROR,
            severity=rule.severity,
            total_records=total_records,
            evaluated_records=0,
            consistent_records=0,
            inconsistent_records=0,
            skipped_records=0,
            error_records=total_records,
            consistency_score=0.0,
            threshold=rule.threshold,
            duration_ms=0.0,
            findings=[
                ConsistencyFinding(
                    rule_name=rule.name,
                    severity=rule.severity,
                    status=ConsistencyStatus.ERROR,
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
        results: Sequence[ConsistencyRuleResult],
        metadata: Dict[str, Any],
    ) -> ConsistencyReport:
        passed_rules = sum(1 for r in results if r.status == ConsistencyStatus.PASSED)
        failed_rules = sum(1 for r in results if r.status == ConsistencyStatus.FAILED)
        warning_rules = sum(1 for r in results if r.status == ConsistencyStatus.WARNING)
        skipped_rules = sum(1 for r in results if r.status == ConsistencyStatus.SKIPPED)
        error_rules = sum(1 for r in results if r.status == ConsistencyStatus.ERROR)

        executable_results = [r for r in results if r.status != ConsistencyStatus.SKIPPED]
        if executable_results:
            overall_score = round(
                sum(r.consistency_score for r in executable_results) / len(executable_results),
                8,
            )
        else:
            overall_score = 1.0

        weight_by_rule = {rule.name: rule.weight for rule in self.rules}
        weighted_denominator = sum(weight_by_rule.get(r.rule_name, 1.0) for r in executable_results)
        if weighted_denominator > 0:
            weighted_score = round(
                sum(r.consistency_score * weight_by_rule.get(r.rule_name, 1.0) for r in executable_results)
                / weighted_denominator,
                8,
            )
        else:
            weighted_score = 1.0

        if error_rules > 0:
            status = ConsistencyStatus.ERROR
        elif failed_rules > 0 or weighted_score < self.global_threshold.min_score:
            status = ConsistencyStatus.FAILED
        elif warning_rules > 0 or weighted_score < self.global_threshold.warning_score:
            status = ConsistencyStatus.WARNING
        else:
            status = ConsistencyStatus.PASSED

        return ConsistencyReport(
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

    def _publish_rule_metrics(self, dataset_name: str, result: ConsistencyRuleResult) -> None:
        tags = {
            "dataset": dataset_name,
            "rule": result.rule_name,
            "rule_type": result.rule_type.value,
            "status": result.status.value,
            "severity": result.severity.value,
        }
        self.metrics_sink.gauge("data_quality.consistency.rule.score", result.consistency_score, tags=tags)
        self.metrics_sink.gauge("data_quality.consistency.rule.inconsistent_records", result.inconsistent_records, tags=tags)
        self.metrics_sink.gauge("data_quality.consistency.rule.error_records", result.error_records, tags=tags)
        self.metrics_sink.timing("data_quality.consistency.rule.duration_ms", result.duration_ms, tags=tags)

    def _publish_report_metrics(self, report: ConsistencyReport) -> None:
        tags = {"dataset": report.dataset_name, "status": report.status.value}
        self.metrics_sink.gauge("data_quality.consistency.report.weighted_score", report.weighted_score, tags=tags)
        self.metrics_sink.gauge("data_quality.consistency.report.overall_score", report.overall_score, tags=tags)
        self.metrics_sink.timing("data_quality.consistency.report.duration_ms", report.duration_ms, tags=tags)
        self.metrics_sink.increment("data_quality.consistency.run.completed", tags=tags)


# =============================================================================
# Rule Factory
# =============================================================================


class ConsistencyRuleFactory:
    """Factory helpers for concise and standardized rule creation."""

    @staticmethod
    def cross_field_comparison(
        name: str,
        left_column: str,
        right_column: str,
        *,
        comparison: ComparisonOperator = ComparisonOperator.EQ,
        tolerance: Optional[float] = None,
        percent_tolerance: Optional[float] = None,
        min_score: float = 0.99,
        severity: Severity = Severity.HIGH,
        weight: float = 1.0,
    ) -> ConsistencyRule:
        return ConsistencyRule(
            name=name,
            rule_type=ConsistencyRuleType.CROSS_FIELD_COMPARISON,
            left_column=left_column,
            right_column=right_column,
            comparison=comparison,
            tolerance=tolerance,
            percent_tolerance=percent_tolerance,
            threshold=ConsistencyThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def field_expression(
        name: str,
        left_column: str,
        expression_function: RowExpression,
        *,
        comparison: ComparisonOperator = ComparisonOperator.EQ,
        tolerance: Optional[float] = None,
        min_score: float = 0.99,
        severity: Severity = Severity.HIGH,
        weight: float = 1.0,
    ) -> ConsistencyRule:
        return ConsistencyRule(
            name=name,
            rule_type=ConsistencyRuleType.FIELD_EXPRESSION,
            left_column=left_column,
            expression_function=expression_function,
            comparison=comparison,
            tolerance=tolerance,
            threshold=ConsistencyThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def conditional_value(
        name: str,
        column: str,
        condition_function: RowPredicate,
        expected_value: Any,
        *,
        comparison: ComparisonOperator = ComparisonOperator.EQ,
        min_score: float = 0.99,
        severity: Severity = Severity.HIGH,
        weight: float = 1.0,
    ) -> ConsistencyRule:
        return ConsistencyRule(
            name=name,
            rule_type=ConsistencyRuleType.CONDITIONAL_CONSISTENCY,
            left_column=column,
            condition_function=condition_function,
            expected_value=expected_value,
            comparison=comparison,
            threshold=ConsistencyThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def referential_consistency(
        name: str,
        key_columns: Sequence[str],
        reference_dataset: str,
        *,
        reference_key_columns: Optional[Sequence[str]] = None,
        compare_columns: Optional[Sequence[str]] = None,
        min_score: float = 0.995,
        severity: Severity = Severity.CRITICAL,
        weight: float = 2.0,
    ) -> ConsistencyRule:
        return ConsistencyRule(
            name=name,
            rule_type=ConsistencyRuleType.REFERENTIAL_CONSISTENCY,
            key_columns=list(key_columns),
            reference_dataset=reference_dataset,
            reference_key_columns=list(reference_key_columns or key_columns),
            reference_columns=list(compare_columns or []),
            columns=list(compare_columns or []),
            threshold=ConsistencyThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def duplicate_key_consistency(
        name: str,
        key_columns: Sequence[str],
        columns: Sequence[str],
        *,
        min_score: float = 1.0,
        severity: Severity = Severity.HIGH,
        weight: float = 1.5,
    ) -> ConsistencyRule:
        return ConsistencyRule(
            name=name,
            rule_type=ConsistencyRuleType.DUPLICATE_KEY_CONSISTENCY,
            key_columns=list(key_columns),
            columns=list(columns),
            threshold=ConsistencyThreshold(min_score=min_score, warning_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def aggregate_reconciliation(
        name: str,
        aggregate_left: AggregateSpec,
        aggregate_right: AggregateSpec,
        *,
        group_by: Optional[Sequence[str]] = None,
        comparison: ComparisonOperator = ComparisonOperator.EQ,
        tolerance: Optional[float] = None,
        percent_tolerance: Optional[float] = None,
        min_score: float = 0.99,
        severity: Severity = Severity.CRITICAL,
        weight: float = 2.0,
    ) -> ConsistencyRule:
        return ConsistencyRule(
            name=name,
            rule_type=ConsistencyRuleType.AGGREGATE_RECONCILIATION,
            aggregate_left=aggregate_left,
            aggregate_right=aggregate_right,
            group_by=list(group_by or []),
            comparison=comparison,
            tolerance=tolerance,
            percent_tolerance=percent_tolerance,
            threshold=ConsistencyThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def temporal_ordering(
        name: str,
        key_columns: Sequence[str],
        temporal_column: str,
        order_by: str,
        *,
        sort_direction: SortDirection = SortDirection.ASC,
        min_score: float = 1.0,
        severity: Severity = Severity.HIGH,
        weight: float = 1.5,
    ) -> ConsistencyRule:
        return ConsistencyRule(
            name=name,
            rule_type=ConsistencyRuleType.TEMPORAL_ORDERING,
            key_columns=list(key_columns),
            left_column=temporal_column,
            order_by=order_by,
            sort_direction=sort_direction,
            threshold=ConsistencyThreshold(min_score=min_score, warning_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def status_transition(
        name: str,
        key_columns: Sequence[str],
        order_by: str,
        status_column: str,
        allowed_transitions: Mapping[Any, Sequence[Any]],
        *,
        allow_initial_statuses: Optional[Sequence[Any]] = None,
        min_score: float = 1.0,
        severity: Severity = Severity.HIGH,
        weight: float = 1.5,
    ) -> ConsistencyRule:
        return ConsistencyRule(
            name=name,
            rule_type=ConsistencyRuleType.STATUS_TRANSITION,
            key_columns=list(key_columns),
            order_by=order_by,
            status_transition=StatusTransitionSpec(
                status_column=status_column,
                allowed_transitions=allowed_transitions,
                allow_initial_statuses=allow_initial_statuses,
            ),
            threshold=ConsistencyThreshold(min_score=min_score, warning_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def mutual_exclusivity(
        name: str,
        columns: Sequence[str],
        *,
        min_score: float = 1.0,
        severity: Severity = Severity.MEDIUM,
        weight: float = 1.0,
    ) -> ConsistencyRule:
        return ConsistencyRule(
            name=name,
            rule_type=ConsistencyRuleType.MUTUAL_EXCLUSIVITY,
            columns=list(columns),
            threshold=ConsistencyThreshold(min_score=min_score, warning_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def co_occurrence(
        name: str,
        columns: Sequence[str],
        *,
        min_score: float = 0.99,
        severity: Severity = Severity.MEDIUM,
        weight: float = 1.0,
    ) -> ConsistencyRule:
        return ConsistencyRule(
            name=name,
            rule_type=ConsistencyRuleType.CO_OCCURRENCE,
            columns=list(columns),
            threshold=ConsistencyThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def custom(
        name: str,
        function: CustomConsistencyFunction,
        *,
        min_score: float = 0.95,
        severity: Severity = Severity.HIGH,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConsistencyRule:
        return ConsistencyRule(
            name=name,
            rule_type=ConsistencyRuleType.CUSTOM,
            custom_function=function,
            threshold=ConsistencyThreshold(min_score=min_score),
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
                "order_id": 1,
                "event_seq": 1,
                "status": "created",
                "event_at": "2026-05-01T10:00:00",
                "subtotal": 100.00,
                "tax": 10.00,
                "total": 110.00,
                "paid": 0.00,
                "customer_id": 10,
                "email": "ana@example.com",
                "phone": None,
            },
            {
                "order_id": 1,
                "event_seq": 2,
                "status": "paid",
                "event_at": "2026-05-01T10:05:00",
                "subtotal": 100.00,
                "tax": 10.00,
                "total": 110.00,
                "paid": 110.00,
                "customer_id": 10,
                "email": "ana@example.com",
                "phone": None,
            },
            {
                "order_id": 2,
                "event_seq": 1,
                "status": "paid",
                "event_at": "2026-05-02T09:00:00",
                "subtotal": 80.00,
                "tax": 5.00,
                "total": 90.00,
                "paid": 95.00,
                "customer_id": 11,
                "email": None,
                "phone": "51999990000",
            },
        ]
    )

    reference_customers = pd.DataFrame(
        [
            {"customer_id": 10},
            {"customer_id": 11},
            {"customer_id": 12},
        ]
    )

    rules = [
        ConsistencyRuleFactory.field_expression(
            "total_equals_subtotal_plus_tax",
            "total",
            lambda row: Decimal(str(row["subtotal"])) + Decimal(str(row["tax"])),
            tolerance=0.01,
            min_score=0.95,
        ),
        ConsistencyRuleFactory.cross_field_comparison(
            "paid_not_above_total",
            "paid",
            "total",
            comparison=ComparisonOperator.LTE,
            min_score=0.95,
        ),
        ConsistencyRuleFactory.temporal_ordering(
            "event_time_in_sequence_order",
            key_columns=["order_id"],
            temporal_column="event_at",
            order_by="event_seq",
        ),
        ConsistencyRuleFactory.status_transition(
            "valid_order_status_transitions",
            key_columns=["order_id"],
            order_by="event_seq",
            status_column="status",
            allowed_transitions={"created": ["paid", "cancelled"], "paid": ["shipped", "refunded"]},
            allow_initial_statuses=["created"],
        ),
        ConsistencyRuleFactory.referential_consistency(
            "customer_exists_in_reference",
            key_columns=["customer_id"],
            reference_dataset="customer_master",
            compare_columns=[],
            min_score=0.95,
        ),
        ConsistencyRuleFactory.co_occurrence(
            "contact_email_phone_all_or_none",
            columns=["email", "phone"],
            min_score=0.50,
        ),
    ]

    checker = ConsistencyChecker(
        rules,
        reference_provider=StaticReferenceDataProvider({"customer_master": reference_customers}),
        audit_sink=InMemoryAuditSink(),
        max_findings_per_rule=100,
    )

    report = checker.run(dataset, dataset_name="orders")
    print(report.to_json(include_findings=True))

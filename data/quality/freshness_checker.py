"""
data/quality/freshness_checker.py

Enterprise-grade Freshness Checker for data quality validation.

This module validates whether data is fresh, recently updated, delivered
within expected SLA, and aligned with ingestion/partition windows required by
analytics, operations, regulatory reports, ML features, and downstream data
products.

Main capabilities:
- Dataset-level max age checks
- Column timestamp freshness checks
- Ingestion delay / latency checks
- Partition freshness validation
- Time-window coverage recency checks
- Source heartbeat freshness checks
- Group/source-specific freshness SLA checks
- Staleness scoring and severity-based findings
- Weighted freshness scoring
- Audit-ready execution reports
- Metrics sink integration
- Pandas-native implementation with extension points

Designed for enterprise lakehouse quality gates, ETL/ELT pipelines,
orchestration workflows, operational monitoring, data contracts, and
compliance-ready data quality audits.
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


class FreshnessCheckerError(Exception):
    """Base exception for freshness checker failures."""


class FreshnessConfigurationError(FreshnessCheckerError):
    """Raised when a freshness rule/configuration is invalid."""


class FreshnessExecutionError(FreshnessCheckerError):
    """Raised when a freshness check cannot be executed safely."""


class DatasetValidationError(FreshnessCheckerError):
    """Raised when input dataset is invalid or unsupported."""


# =============================================================================
# Enums
# =============================================================================


class Severity(str, Enum):
    """Severity level for freshness findings."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FreshnessStatus(str, Enum):
    """Execution status for rules and reports."""

    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"
    ERROR = "error"


class FreshnessRuleType(str, Enum):
    """Supported freshness rule categories."""

    MAX_DATA_AGE = "max_data_age"
    TIMESTAMP_COLUMN_FRESHNESS = "timestamp_column_freshness"
    INGESTION_DELAY = "ingestion_delay"
    PARTITION_FRESHNESS = "partition_freshness"
    EXPECTED_TIME_WINDOW = "expected_time_window"
    GROUP_FRESHNESS = "group_freshness"
    SOURCE_HEARTBEAT = "source_heartbeat"
    CUSTOM = "custom"


class TimeUnit(str, Enum):
    """Time units for SLA configuration."""

    SECONDS = "seconds"
    MINUTES = "minutes"
    HOURS = "hours"
    DAYS = "days"
    WEEKS = "weeks"


class TimeFrequency(str, Enum):
    """Frequency used for expected time windows and partitions."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class AggregationMode(str, Enum):
    """How timestamps should be aggregated for freshness checks."""

    MAX = "max"
    MIN = "min"
    MEAN = "mean"
    MEDIAN = "median"
    PERCENTILE = "percentile"


class NullHandling(str, Enum):
    """How null timestamps should be handled."""

    IGNORE = "ignore"
    FAIL = "fail"
    PASS = "pass"


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
    """Optional provider for heartbeat, metadata, or SLA reference datasets."""

    def get_reference_dataset(self, name: str) -> Any:
        ...


ClockFunction = Callable[[], datetime]
CustomFreshnessFunction = Callable[[Mapping[str, Any]], bool]


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class FreshnessThreshold:
    """Threshold configuration for a rule or full report."""

    min_score: float = 0.95
    warning_score: float = 0.98
    max_stale_records: Optional[int] = None
    max_stale_rate: Optional[float] = None

    def validate(self) -> None:
        if not 0 <= self.min_score <= 1:
            raise FreshnessConfigurationError("min_score must be between 0 and 1.")
        if not 0 <= self.warning_score <= 1:
            raise FreshnessConfigurationError("warning_score must be between 0 and 1.")
        if self.warning_score < self.min_score:
            raise FreshnessConfigurationError("warning_score must be greater than or equal to min_score.")
        if self.max_stale_records is not None and self.max_stale_records < 0:
            raise FreshnessConfigurationError("max_stale_records cannot be negative.")
        if self.max_stale_rate is not None and not 0 <= self.max_stale_rate <= 1:
            raise FreshnessConfigurationError("max_stale_rate must be between 0 and 1.")


@dataclass(frozen=True)
class FreshnessSLA:
    """Freshness service-level agreement."""

    max_age: int
    unit: TimeUnit = TimeUnit.HOURS
    warning_age: Optional[int] = None

    def validate(self) -> None:
        if self.max_age < 0:
            raise FreshnessConfigurationError("max_age cannot be negative.")
        if self.warning_age is not None:
            if self.warning_age < 0:
                raise FreshnessConfigurationError("warning_age cannot be negative.")
            if self.warning_age > self.max_age:
                raise FreshnessConfigurationError("warning_age cannot be greater than max_age.")

    def max_timedelta(self) -> timedelta:
        return _to_timedelta(self.max_age, self.unit)

    def warning_timedelta(self) -> Optional[timedelta]:
        if self.warning_age is None:
            return None
        return _to_timedelta(self.warning_age, self.unit)


@dataclass(frozen=True)
class TimeWindowExpectation:
    """Expected time-window coverage configuration."""

    start: Any
    end: Any
    frequency: TimeFrequency = TimeFrequency.DAILY
    allow_partial_current_window: bool = True

    def validate(self) -> None:
        if self.start is None or self.end is None:
            raise FreshnessConfigurationError("TimeWindowExpectation requires start and end.")


@dataclass(frozen=True)
class FreshnessRule:
    """Definition of a freshness validation rule."""

    name: str
    rule_type: FreshnessRuleType
    timestamp_column: Optional[str] = None
    ingestion_timestamp_column: Optional[str] = None
    event_timestamp_column: Optional[str] = None
    partition_column: Optional[str] = None
    group_by: Sequence[str] = field(default_factory=list)
    key_columns: Sequence[str] = field(default_factory=list)
    reference_dataset: Optional[str] = None
    reference_timestamp_column: Optional[str] = None
    sla: FreshnessSLA = field(default_factory=lambda: FreshnessSLA(max_age=24, unit=TimeUnit.HOURS))
    time_window: Optional[TimeWindowExpectation] = None
    aggregation_mode: AggregationMode = AggregationMode.MAX
    percentile: Optional[float] = None
    null_handling: NullHandling = NullHandling.IGNORE
    min_records_per_window: int = 1
    expected_latest_partition: Optional[Any] = None
    custom_function: Optional[CustomFreshnessFunction] = None
    weight: float = 1.0
    severity: Severity = Severity.HIGH
    threshold: FreshnessThreshold = field(default_factory=FreshnessThreshold)
    metadata: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def all_dataset_columns(self) -> Set[str]:
        cols: Set[str] = set(self.group_by) | set(self.key_columns)
        for value in [
            self.timestamp_column,
            self.ingestion_timestamp_column,
            self.event_timestamp_column,
            self.partition_column,
        ]:
            if value:
                cols.add(value)
        return cols

    def validate(self) -> None:
        if not self.name or not self.name.strip():
            raise FreshnessConfigurationError("Rule name is required.")
        if self.weight <= 0:
            raise FreshnessConfigurationError(f"Rule '{self.name}' weight must be greater than zero.")
        if self.min_records_per_window < 1:
            raise FreshnessConfigurationError("min_records_per_window must be at least 1.")
        self.threshold.validate()
        self.sla.validate()

        if self.aggregation_mode == AggregationMode.PERCENTILE:
            if self.percentile is None or not 0 <= self.percentile <= 1:
                raise FreshnessConfigurationError(
                    f"Rule '{self.name}' percentile must be between 0 and 1."
                )

        if self.rule_type in {
            FreshnessRuleType.MAX_DATA_AGE,
            FreshnessRuleType.TIMESTAMP_COLUMN_FRESHNESS,
            FreshnessRuleType.GROUP_FRESHNESS,
        } and not self.timestamp_column:
            raise FreshnessConfigurationError(f"Rule '{self.name}' requires timestamp_column.")

        if self.rule_type == FreshnessRuleType.INGESTION_DELAY:
            if not self.ingestion_timestamp_column or not self.event_timestamp_column:
                raise FreshnessConfigurationError(
                    f"Rule '{self.name}' requires ingestion_timestamp_column and event_timestamp_column."
                )

        if self.rule_type == FreshnessRuleType.PARTITION_FRESHNESS and not self.partition_column:
            raise FreshnessConfigurationError(f"Rule '{self.name}' requires partition_column.")

        if self.rule_type == FreshnessRuleType.EXPECTED_TIME_WINDOW:
            if not self.timestamp_column:
                raise FreshnessConfigurationError(f"Rule '{self.name}' requires timestamp_column.")
            if not self.time_window:
                raise FreshnessConfigurationError(f"Rule '{self.name}' requires time_window.")
            self.time_window.validate()

        if self.rule_type == FreshnessRuleType.SOURCE_HEARTBEAT:
            if not self.reference_dataset:
                raise FreshnessConfigurationError(f"Rule '{self.name}' requires reference_dataset.")
            if not self.reference_timestamp_column:
                raise FreshnessConfigurationError(f"Rule '{self.name}' requires reference_timestamp_column.")

        if self.rule_type == FreshnessRuleType.CUSTOM and self.custom_function is None:
            raise FreshnessConfigurationError(f"Rule '{self.name}' requires custom_function.")


@dataclass
class FreshnessFinding:
    """Single row-level, group-level, source-level, or rule-level finding."""

    rule_name: str
    severity: Severity
    status: FreshnessStatus
    message: str
    row_index: Optional[Any] = None
    column: Optional[str] = None
    key: Optional[Any] = None
    actual_value: Optional[Any] = None
    expected_value: Optional[Any] = None
    age_seconds: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["severity"] = self.severity.value
        payload["status"] = self.status.value
        return _json_safe(payload)


@dataclass
class FreshnessRuleResult:
    """Aggregated execution result for one freshness rule."""

    rule_name: str
    rule_type: FreshnessRuleType
    status: FreshnessStatus
    severity: Severity
    total_records: int
    evaluated_records: int
    fresh_records: int
    stale_records: int
    skipped_records: int
    error_records: int
    freshness_score: float
    threshold: FreshnessThreshold
    duration_ms: float
    findings: List[FreshnessFinding] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def stale_rate(self) -> float:
        if self.evaluated_records <= 0:
            return 0.0
        return self.stale_records / self.evaluated_records

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
            "fresh_records": self.fresh_records,
            "stale_records": self.stale_records,
            "skipped_records": self.skipped_records,
            "error_records": self.error_records,
            "freshness_score": self.freshness_score,
            "stale_rate": self.stale_rate,
            "error_rate": self.error_rate,
            "threshold": asdict(self.threshold),
            "duration_ms": self.duration_ms,
            "metadata": _json_safe(self.metadata),
        }
        if include_findings:
            data["findings"] = [finding.to_dict() for finding in self.findings]
        return data


@dataclass
class FreshnessReport:
    """Complete freshness validation report."""

    report_id: str
    dataset_name: str
    status: FreshnessStatus
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
    rule_results: List[FreshnessRuleResult]
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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def _to_timedelta(value: int, unit: TimeUnit) -> timedelta:
    if unit == TimeUnit.SECONDS:
        return timedelta(seconds=value)
    if unit == TimeUnit.MINUTES:
        return timedelta(minutes=value)
    if unit == TimeUnit.HOURS:
        return timedelta(hours=value)
    if unit == TimeUnit.DAYS:
        return timedelta(days=value)
    if unit == TimeUnit.WEEKS:
        return timedelta(weeks=value)
    raise FreshnessConfigurationError(f"Unsupported time unit: {unit}")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, timedelta):
        return value.total_seconds()
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
            "pandas is required for FreshnessChecker. Install pandas or adapt the dataset adapter."
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


def _to_datetime(value: Any) -> Optional[datetime]:
    if _is_null(value):
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif pd is not None:
        try:
            parsed = pd.to_datetime(value, errors="coerce", utc=False)
            if pd.isna(parsed):
                return None
            dt = parsed.to_pydatetime()
        except Exception:
            return None
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except Exception:
            return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _period_start(value: datetime, frequency: TimeFrequency) -> datetime:
    value = value.astimezone(timezone.utc)
    if frequency == TimeFrequency.HOURLY:
        return value.replace(minute=0, second=0, microsecond=0)
    if frequency == TimeFrequency.DAILY:
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    if frequency == TimeFrequency.WEEKLY:
        start = value - timedelta(days=value.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    if frequency == TimeFrequency.MONTHLY:
        return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise FreshnessConfigurationError(f"Unsupported frequency: {frequency}")


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
    raise FreshnessConfigurationError(f"Unsupported frequency: {frequency}")


def _expected_periods(start: datetime, end: datetime, frequency: TimeFrequency) -> Set[datetime]:
    periods: Set[datetime] = set()
    current = _period_start(start, frequency)
    end_period = _period_start(end, frequency)
    while current <= end_period:
        periods.add(current)
        current = _period_increment(current, frequency)
    return periods


def _aggregate_timestamp(values: Sequence[datetime], mode: AggregationMode, percentile: Optional[float]) -> Optional[datetime]:
    if not values:
        return None
    sorted_values = sorted(values)
    if mode == AggregationMode.MAX:
        return max(sorted_values)
    if mode == AggregationMode.MIN:
        return min(sorted_values)
    if mode == AggregationMode.MEDIAN:
        return sorted_values[len(sorted_values) // 2]
    if mode == AggregationMode.PERCENTILE:
        p = 0.5 if percentile is None else percentile
        idx = min(len(sorted_values) - 1, max(0, int(round((len(sorted_values) - 1) * p))))
        return sorted_values[idx]
    if mode == AggregationMode.MEAN:
        avg_ts = sum(v.timestamp() for v in sorted_values) / len(sorted_values)
        return datetime.fromtimestamp(avg_ts, tz=timezone.utc)
    raise FreshnessConfigurationError(f"Unsupported aggregation mode: {mode}")


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
            raise FreshnessExecutionError(f"Reference dataset not found: {name}")
        return self.datasets[name]


# =============================================================================
# Freshness Checker
# =============================================================================


class FreshnessChecker:
    """
    Enterprise data freshness checker.

    Example:
        checker = FreshnessChecker([
            FreshnessRuleFactory.max_data_age(
                "orders_updated_recently",
                timestamp_column="updated_at",
                max_age=2,
                unit=TimeUnit.HOURS,
            )
        ])
        report = checker.run(dataset, dataset_name="orders")
    """

    def __init__(
        self,
        rules: Sequence[FreshnessRule],
        *,
        reference_provider: Optional[ReferenceDataProvider] = None,
        metrics_sink: Optional[MetricsSink] = None,
        audit_sink: Optional[AuditSink] = None,
        clock: ClockFunction = utc_now,
        max_findings_per_rule: int = 1_000,
        fail_fast: bool = False,
        global_threshold: FreshnessThreshold = FreshnessThreshold(min_score=0.95, warning_score=0.98),
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self.rules = list(rules)
        self.reference_provider = reference_provider or StaticReferenceDataProvider()
        self.metrics_sink = metrics_sink or NoopMetricsSink()
        self.audit_sink = audit_sink
        self.clock = clock
        self.max_findings_per_rule = max_findings_per_rule
        self.fail_fast = fail_fast
        self.global_threshold = global_threshold
        self.logger = logger_ or logger

        self._validate_configuration()

    def _validate_configuration(self) -> None:
        if self.max_findings_per_rule < 0:
            raise FreshnessConfigurationError("max_findings_per_rule cannot be negative.")
        self.global_threshold.validate()
        seen = set()
        for rule in self.rules:
            rule.validate()
            if rule.name in seen:
                raise FreshnessConfigurationError(f"Duplicate freshness rule name: {rule.name}")
            seen.add(rule.name)

    def run(
        self,
        dataset: Any,
        *,
        dataset_name: str = "dataset",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FreshnessReport:
        """Run all enabled freshness rules against a dataset."""
        started_at = utc_now_iso()
        started = time.perf_counter()
        report_id = str(uuid.uuid4())
        metadata = dict(metadata or {})

        df = _as_dataframe(dataset)
        total_records = len(df)
        metadata.setdefault("dataset_fingerprint", _dataset_fingerprint(df))
        metadata.setdefault("columns", list(df.columns))
        metadata.setdefault("evaluation_time_utc", self.clock().astimezone(timezone.utc).isoformat())

        self.logger.info("Starting freshness check report_id=%s dataset=%s", report_id, dataset_name)
        self.metrics_sink.increment("data_quality.freshness.run.started", tags={"dataset": dataset_name})

        results: List[FreshnessRuleResult] = []

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
                            "event_type": "freshness_rule_executed",
                            "report_id": report_id,
                            "dataset_name": dataset_name,
                            "timestamp": utc_now_iso(),
                            "rule_result": result.to_dict(include_findings=False),
                        }
                    )

                if self.fail_fast and result.status in {FreshnessStatus.FAILED, FreshnessStatus.ERROR}:
                    self.logger.warning("Fail-fast triggered by rule=%s", rule.name)
                    break

            except Exception as exc:  # noqa: BLE001 - enterprise boundary handling
                self.logger.exception("Freshness rule failed unexpectedly: %s", rule.name)
                result = self._error_result(rule, total_records, exc)
                results.append(result)
                self.metrics_sink.increment(
                    "data_quality.freshness.rule.error",
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
                    "event_type": "freshness_report_completed",
                    "report_id": report_id,
                    "dataset_name": dataset_name,
                    "timestamp": utc_now_iso(),
                    "report": report.to_dict(include_findings=False),
                }
            )

        self.logger.info(
            "Completed freshness check report_id=%s dataset=%s status=%s score=%.5f duration_ms=%.2f",
            report_id,
            dataset_name,
            report.status.value,
            report.weighted_score,
            duration_ms,
        )
        return report

    def _execute_rule(self, df: "pd.DataFrame", rule: FreshnessRule) -> FreshnessRuleResult:
        started = time.perf_counter()
        self._validate_dataset_columns(df, rule)

        if rule.rule_type == FreshnessRuleType.MAX_DATA_AGE:
            counters, findings, metadata = self._check_max_data_age(df, rule)
        elif rule.rule_type == FreshnessRuleType.TIMESTAMP_COLUMN_FRESHNESS:
            counters, findings, metadata = self._check_timestamp_column_freshness(df, rule)
        elif rule.rule_type == FreshnessRuleType.INGESTION_DELAY:
            counters, findings, metadata = self._check_ingestion_delay(df, rule)
        elif rule.rule_type == FreshnessRuleType.PARTITION_FRESHNESS:
            counters, findings, metadata = self._check_partition_freshness(df, rule)
        elif rule.rule_type == FreshnessRuleType.EXPECTED_TIME_WINDOW:
            counters, findings, metadata = self._check_expected_time_window(df, rule)
        elif rule.rule_type == FreshnessRuleType.GROUP_FRESHNESS:
            counters, findings, metadata = self._check_group_freshness(df, rule)
        elif rule.rule_type == FreshnessRuleType.SOURCE_HEARTBEAT:
            counters, findings, metadata = self._check_source_heartbeat(df, rule)
        elif rule.rule_type == FreshnessRuleType.CUSTOM:
            counters, findings, metadata = self._check_custom(df, rule)
        else:
            raise FreshnessExecutionError(f"Unsupported rule type: {rule.rule_type}")

        duration_ms = (time.perf_counter() - started) * 1000
        score = self._score(counters["fresh"], counters["evaluated"])
        status = self._status_from_score(
            score=score,
            stale_records=counters["stale"],
            error_records=counters["error"],
            evaluated_records=counters["evaluated"],
            threshold=rule.threshold,
        )

        return FreshnessRuleResult(
            rule_name=rule.name,
            rule_type=rule.rule_type,
            status=status,
            severity=rule.severity,
            total_records=len(df),
            evaluated_records=counters["evaluated"],
            fresh_records=counters["fresh"],
            stale_records=counters["stale"],
            skipped_records=counters["skipped"],
            error_records=counters["error"],
            freshness_score=score,
            threshold=rule.threshold,
            duration_ms=duration_ms,
            findings=findings,
            metadata={"weight": rule.weight, **rule.metadata, **metadata},
        )

    def _validate_dataset_columns(self, df: "pd.DataFrame", rule: FreshnessRule) -> None:
        if rule.rule_type == FreshnessRuleType.SOURCE_HEARTBEAT:
            return
        missing = sorted(col for col in rule.all_dataset_columns() if col not in df.columns)
        if missing:
            raise FreshnessExecutionError(f"Rule '{rule.name}' missing dataset columns: {missing}")

    def _initial_counters(self) -> Dict[str, int]:
        return {"evaluated": 0, "fresh": 0, "stale": 0, "skipped": 0, "error": 0}

    def _append_finding(self, findings: List[FreshnessFinding], finding: FreshnessFinding) -> None:
        if len(findings) < self.max_findings_per_rule:
            findings.append(finding)

    def _age_seconds(self, timestamp: datetime) -> float:
        return max(0.0, (self.clock().astimezone(timezone.utc) - timestamp).total_seconds())

    def _is_fresh_age(self, age: timedelta, rule: FreshnessRule) -> bool:
        return age <= rule.sla.max_timedelta()

    def _check_max_data_age(
        self, df: "pd.DataFrame", rule: FreshnessRule
    ) -> Tuple[Dict[str, int], List[FreshnessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[FreshnessFinding] = []
        column = rule.timestamp_column or ""
        timestamps = [_to_datetime(v) for v in df[column].tolist()]
        valid_timestamps = [ts for ts in timestamps if ts is not None]
        invalid_count = len(timestamps) - len(valid_timestamps)

        if not valid_timestamps:
            counters["evaluated"] = 1
            counters["error"] = 1
            self._append_finding(
                findings,
                FreshnessFinding(
                    rule_name=rule.name,
                    severity=rule.severity,
                    status=FreshnessStatus.ERROR,
                    message="No valid timestamps found for max data age check.",
                    column=column,
                    expected_value="at least one valid timestamp",
                ),
            )
            return counters, findings, {"timestamp_column": column, "invalid_timestamp_count": invalid_count}

        aggregate_ts = _aggregate_timestamp(valid_timestamps, rule.aggregation_mode, rule.percentile)
        assert aggregate_ts is not None
        age = self.clock().astimezone(timezone.utc) - aggregate_ts
        counters["evaluated"] = 1

        if self._is_fresh_age(age, rule):
            counters["fresh"] = 1
        else:
            counters["stale"] = 1
            self._append_finding(
                findings,
                FreshnessFinding(
                    rule_name=rule.name,
                    severity=rule.severity,
                    status=FreshnessStatus.FAILED,
                    message="Dataset is older than allowed freshness SLA.",
                    column=column,
                    actual_value=aggregate_ts,
                    expected_value={"max_age_seconds": rule.sla.max_timedelta().total_seconds()},
                    age_seconds=age.total_seconds(),
                    metadata={"aggregation_mode": rule.aggregation_mode.value},
                ),
            )

        return counters, findings, {
            "timestamp_column": column,
            "aggregate_timestamp": aggregate_ts,
            "age_seconds": age.total_seconds(),
            "max_age_seconds": rule.sla.max_timedelta().total_seconds(),
            "invalid_timestamp_count": invalid_count,
            "aggregation_mode": rule.aggregation_mode.value,
        }

    def _check_timestamp_column_freshness(
        self, df: "pd.DataFrame", rule: FreshnessRule
    ) -> Tuple[Dict[str, int], List[FreshnessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[FreshnessFinding] = []
        column = rule.timestamp_column or ""
        now = self.clock().astimezone(timezone.utc)
        max_age = rule.sla.max_timedelta()

        ages: List[float] = []
        for idx, row in df.iterrows():
            value = row[column]
            ts = _to_datetime(value)
            if ts is None:
                if rule.null_handling == NullHandling.IGNORE:
                    counters["skipped"] += 1
                    continue
                counters["evaluated"] += 1
                if rule.null_handling == NullHandling.PASS:
                    counters["fresh"] += 1
                else:
                    counters["stale"] += 1
                    self._append_finding(
                        findings,
                        FreshnessFinding(
                            rule_name=rule.name,
                            severity=rule.severity,
                            status=FreshnessStatus.FAILED,
                            message="Timestamp is missing or invalid.",
                            row_index=idx,
                            column=column,
                            actual_value=value,
                            expected_value="valid timestamp",
                        ),
                    )
                continue

            counters["evaluated"] += 1
            age = now - ts
            age_seconds = age.total_seconds()
            ages.append(age_seconds)
            if age <= max_age:
                counters["fresh"] += 1
            else:
                counters["stale"] += 1
                self._append_finding(
                    findings,
                    FreshnessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=FreshnessStatus.FAILED,
                        message="Record timestamp is stale according to freshness SLA.",
                        row_index=idx,
                        column=column,
                        actual_value=ts,
                        expected_value={"max_age_seconds": max_age.total_seconds()},
                        age_seconds=age_seconds,
                    ),
                )

        return counters, findings, {
            "timestamp_column": column,
            "max_age_seconds": max_age.total_seconds(),
            "max_observed_age_seconds": max(ages) if ages else None,
            "min_observed_age_seconds": min(ages) if ages else None,
            "avg_observed_age_seconds": sum(ages) / len(ages) if ages else None,
        }

    def _check_ingestion_delay(
        self, df: "pd.DataFrame", rule: FreshnessRule
    ) -> Tuple[Dict[str, int], List[FreshnessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[FreshnessFinding] = []
        ingestion_col = rule.ingestion_timestamp_column or ""
        event_col = rule.event_timestamp_column or ""
        max_delay = rule.sla.max_timedelta()
        delays: List[float] = []

        for idx, row in df.iterrows():
            ingestion_ts = _to_datetime(row[ingestion_col])
            event_ts = _to_datetime(row[event_col])

            if ingestion_ts is None or event_ts is None:
                if rule.null_handling == NullHandling.IGNORE:
                    counters["skipped"] += 1
                    continue
                counters["evaluated"] += 1
                if rule.null_handling == NullHandling.PASS:
                    counters["fresh"] += 1
                else:
                    counters["stale"] += 1
                    self._append_finding(
                        findings,
                        FreshnessFinding(
                            rule_name=rule.name,
                            severity=rule.severity,
                            status=FreshnessStatus.FAILED,
                            message="Event or ingestion timestamp is missing/invalid.",
                            row_index=idx,
                            actual_value={"event": row[event_col], "ingestion": row[ingestion_col]},
                            expected_value="valid event and ingestion timestamps",
                        ),
                    )
                continue

            counters["evaluated"] += 1
            delay = ingestion_ts - event_ts
            delay_seconds = delay.total_seconds()
            delays.append(delay_seconds)
            if timedelta(seconds=delay_seconds) <= max_delay and delay_seconds >= 0:
                counters["fresh"] += 1
            else:
                counters["stale"] += 1
                self._append_finding(
                    findings,
                    FreshnessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=FreshnessStatus.FAILED,
                        message="Ingestion delay exceeds freshness SLA or is negative.",
                        row_index=idx,
                        actual_value={"event_timestamp": event_ts, "ingestion_timestamp": ingestion_ts},
                        expected_value={"max_delay_seconds": max_delay.total_seconds()},
                        age_seconds=delay_seconds,
                    ),
                )

        return counters, findings, {
            "event_timestamp_column": event_col,
            "ingestion_timestamp_column": ingestion_col,
            "max_delay_seconds": max_delay.total_seconds(),
            "max_observed_delay_seconds": max(delays) if delays else None,
            "avg_observed_delay_seconds": sum(delays) / len(delays) if delays else None,
        }

    def _check_partition_freshness(
        self, df: "pd.DataFrame", rule: FreshnessRule
    ) -> Tuple[Dict[str, int], List[FreshnessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[FreshnessFinding] = []
        column = rule.partition_column or ""
        values = [v for v in df[column].tolist() if not _is_null(v)]

        counters["evaluated"] = 1
        if not values:
            counters["stale"] = 1
            self._append_finding(
                findings,
                FreshnessFinding(
                    rule_name=rule.name,
                    severity=rule.severity,
                    status=FreshnessStatus.FAILED,
                    message="No valid partition values found.",
                    column=column,
                    expected_value="at least one partition",
                ),
            )
            return counters, findings, {"partition_column": column, "latest_partition": None}

        latest_partition = max(values)
        expected = rule.expected_latest_partition
        if expected is not None:
            fresh = latest_partition >= expected
            expected_payload = {"expected_latest_partition": expected}
            age_seconds = None
        else:
            parsed = _to_datetime(latest_partition)
            if parsed is None:
                counters["error"] = 1
                self._append_finding(
                    findings,
                    FreshnessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=FreshnessStatus.ERROR,
                        message="Latest partition cannot be parsed as datetime and no expected_latest_partition was provided.",
                        column=column,
                        actual_value=latest_partition,
                    ),
                )
                return counters, findings, {"partition_column": column, "latest_partition": latest_partition}
            age = self.clock().astimezone(timezone.utc) - parsed
            age_seconds = age.total_seconds()
            fresh = age <= rule.sla.max_timedelta()
            expected_payload = {"max_age_seconds": rule.sla.max_timedelta().total_seconds()}

        if fresh:
            counters["fresh"] = 1
        else:
            counters["stale"] = 1
            self._append_finding(
                findings,
                FreshnessFinding(
                    rule_name=rule.name,
                    severity=rule.severity,
                    status=FreshnessStatus.FAILED,
                    message="Latest partition is stale or behind expectation.",
                    column=column,
                    actual_value=latest_partition,
                    expected_value=expected_payload,
                    age_seconds=age_seconds,
                ),
            )

        return counters, findings, {
            "partition_column": column,
            "latest_partition": latest_partition,
            "expected_latest_partition": expected,
            "age_seconds": age_seconds,
            "partition_count": len(set(values)),
        }

    def _check_expected_time_window(
        self, df: "pd.DataFrame", rule: FreshnessRule
    ) -> Tuple[Dict[str, int], List[FreshnessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[FreshnessFinding] = []
        if not rule.time_window or not rule.timestamp_column:
            raise FreshnessConfigurationError(f"Rule '{rule.name}' requires time_window and timestamp_column.")

        start = _to_datetime(rule.time_window.start)
        end = _to_datetime(rule.time_window.end)
        if start is None or end is None:
            raise FreshnessConfigurationError(f"Rule '{rule.name}' time_window start/end must be date-like.")
        if start > end:
            raise FreshnessConfigurationError(f"Rule '{rule.name}' time_window start cannot be after end.")

        expected = _expected_periods(start, end, rule.time_window.frequency)
        counts_by_period: Dict[datetime, int] = {period: 0 for period in expected}

        for value in df[rule.timestamp_column].tolist():
            ts = _to_datetime(value)
            if ts is None:
                continue
            if start <= ts <= end:
                period = _period_start(ts, rule.time_window.frequency)
                if period in counts_by_period:
                    counts_by_period[period] += 1

        for period, count in sorted(counts_by_period.items()):
            counters["evaluated"] += 1
            if count >= rule.min_records_per_window:
                counters["fresh"] += 1
            else:
                counters["stale"] += 1
                self._append_finding(
                    findings,
                    FreshnessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=FreshnessStatus.FAILED,
                        message="Expected time window period has insufficient records.",
                        column=rule.timestamp_column,
                        key=period.isoformat(),
                        actual_value={"record_count": count},
                        expected_value={"min_records_per_window": rule.min_records_per_window},
                    ),
                )

        return counters, findings, {
            "timestamp_column": rule.timestamp_column,
            "frequency": rule.time_window.frequency.value,
            "expected_period_count": len(expected),
            "fresh_period_count": counters["fresh"],
            "stale_period_count": counters["stale"],
        }

    def _check_group_freshness(
        self, df: "pd.DataFrame", rule: FreshnessRule
    ) -> Tuple[Dict[str, int], List[FreshnessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[FreshnessFinding] = []
        if not rule.group_by:
            raise FreshnessConfigurationError(f"Rule '{rule.name}' requires group_by for group freshness.")
        column = rule.timestamp_column or ""
        now = self.clock().astimezone(timezone.utc)
        max_age = rule.sla.max_timedelta()

        group_count = 0
        for key, group_df in df.groupby(list(rule.group_by), dropna=False):
            group_count += 1
            timestamps = [_to_datetime(v) for v in group_df[column].tolist()]
            valid = [ts for ts in timestamps if ts is not None]
            if not valid:
                counters["evaluated"] += 1
                counters["stale"] += 1
                self._append_finding(
                    findings,
                    FreshnessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=FreshnessStatus.FAILED,
                        message="Group has no valid freshness timestamp.",
                        key=key,
                        column=column,
                        expected_value="at least one valid timestamp per group",
                    ),
                )
                continue

            aggregate_ts = _aggregate_timestamp(valid, rule.aggregation_mode, rule.percentile)
            assert aggregate_ts is not None
            age = now - aggregate_ts
            counters["evaluated"] += 1
            if age <= max_age:
                counters["fresh"] += 1
            else:
                counters["stale"] += 1
                self._append_finding(
                    findings,
                    FreshnessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=FreshnessStatus.FAILED,
                        message="Group data is stale according to freshness SLA.",
                        key=key,
                        column=column,
                        actual_value=aggregate_ts,
                        expected_value={"max_age_seconds": max_age.total_seconds()},
                        age_seconds=age.total_seconds(),
                        metadata={"group_by": list(rule.group_by)},
                    ),
                )

        return counters, findings, {
            "timestamp_column": column,
            "group_by": list(rule.group_by),
            "group_count": group_count,
            "max_age_seconds": max_age.total_seconds(),
        }

    def _check_source_heartbeat(
        self, df: "pd.DataFrame", rule: FreshnessRule
    ) -> Tuple[Dict[str, int], List[FreshnessFinding], Dict[str, Any]]:
        del df
        counters = self._initial_counters()
        findings: List[FreshnessFinding] = []
        ref_df = _as_dataframe(self.reference_provider.get_reference_dataset(rule.reference_dataset or ""))
        column = rule.reference_timestamp_column or ""
        if column not in ref_df.columns:
            raise FreshnessExecutionError(f"Rule '{rule.name}' missing heartbeat timestamp column: {column}")

        now = self.clock().astimezone(timezone.utc)
        max_age = rule.sla.max_timedelta()
        group_cols = [col for col in rule.group_by if col in ref_df.columns]

        if group_cols:
            iterator = ref_df.groupby(group_cols, dropna=False)
            for key, group_df in iterator:
                timestamps = [_to_datetime(v) for v in group_df[column].tolist()]
                valid = [ts for ts in timestamps if ts is not None]
                counters["evaluated"] += 1
                if not valid:
                    counters["stale"] += 1
                    self._append_finding(
                        findings,
                        FreshnessFinding(
                            rule_name=rule.name,
                            severity=rule.severity,
                            status=FreshnessStatus.FAILED,
                            message="Heartbeat group has no valid timestamp.",
                            key=key,
                            column=column,
                        ),
                    )
                    continue
                latest = max(valid)
                age = now - latest
                if age <= max_age:
                    counters["fresh"] += 1
                else:
                    counters["stale"] += 1
                    self._append_finding(
                        findings,
                        FreshnessFinding(
                            rule_name=rule.name,
                            severity=rule.severity,
                            status=FreshnessStatus.FAILED,
                            message="Source heartbeat is stale.",
                            key=key,
                            column=column,
                            actual_value=latest,
                            expected_value={"max_age_seconds": max_age.total_seconds()},
                            age_seconds=age.total_seconds(),
                        ),
                    )
        else:
            timestamps = [_to_datetime(v) for v in ref_df[column].tolist()]
            valid = [ts for ts in timestamps if ts is not None]
            counters["evaluated"] = 1
            if not valid:
                counters["stale"] = 1
                self._append_finding(
                    findings,
                    FreshnessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=FreshnessStatus.FAILED,
                        message="No valid heartbeat timestamp found.",
                        column=column,
                    ),
                )
            else:
                latest = max(valid)
                age = now - latest
                if age <= max_age:
                    counters["fresh"] = 1
                else:
                    counters["stale"] = 1
                    self._append_finding(
                        findings,
                        FreshnessFinding(
                            rule_name=rule.name,
                            severity=rule.severity,
                            status=FreshnessStatus.FAILED,
                            message="Source heartbeat is stale.",
                            column=column,
                            actual_value=latest,
                            expected_value={"max_age_seconds": max_age.total_seconds()},
                            age_seconds=age.total_seconds(),
                        ),
                    )

        return counters, findings, {
            "reference_dataset": rule.reference_dataset,
            "reference_timestamp_column": column,
            "group_by": group_cols,
            "max_age_seconds": max_age.total_seconds(),
        }

    def _check_custom(
        self, df: "pd.DataFrame", rule: FreshnessRule
    ) -> Tuple[Dict[str, int], List[FreshnessFinding], Dict[str, Any]]:
        counters = self._initial_counters()
        findings: List[FreshnessFinding] = []
        if rule.custom_function is None:
            raise FreshnessConfigurationError(f"Rule '{rule.name}' requires custom_function.")

        for idx, row in df.iterrows():
            counters["evaluated"] += 1
            row_map = _row_to_mapping(row)
            try:
                fresh = bool(rule.custom_function(row_map))
            except Exception as exc:  # noqa: BLE001
                counters["error"] += 1
                self._append_finding(
                    findings,
                    FreshnessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=FreshnessStatus.ERROR,
                        message=f"Custom freshness rule execution error: {exc}",
                        row_index=idx,
                    ),
                )
                continue

            if fresh:
                counters["fresh"] += 1
            else:
                counters["stale"] += 1
                self._append_finding(
                    findings,
                    FreshnessFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=FreshnessStatus.FAILED,
                        message="Custom freshness rule returned false.",
                        row_index=idx,
                        actual_value=_json_safe(dict(row_map)),
                        expected_value=True,
                    ),
                )

        return counters, findings, {"custom": True}

    def _score(self, fresh: int, evaluated: int) -> float:
        if evaluated <= 0:
            return 1.0
        return round(fresh / evaluated, 8)

    def _status_from_score(
        self,
        *,
        score: float,
        stale_records: int,
        error_records: int,
        evaluated_records: int,
        threshold: FreshnessThreshold,
    ) -> FreshnessStatus:
        if error_records > 0:
            return FreshnessStatus.ERROR
        if threshold.max_stale_records is not None and stale_records > threshold.max_stale_records:
            return FreshnessStatus.FAILED
        if threshold.max_stale_rate is not None and evaluated_records > 0:
            if stale_records / evaluated_records > threshold.max_stale_rate:
                return FreshnessStatus.FAILED
        if score < threshold.min_score:
            return FreshnessStatus.FAILED
        if score < threshold.warning_score:
            return FreshnessStatus.WARNING
        return FreshnessStatus.PASSED

    def _skipped_result(self, rule: FreshnessRule, total_records: int, reason: str) -> FreshnessRuleResult:
        return FreshnessRuleResult(
            rule_name=rule.name,
            rule_type=rule.rule_type,
            status=FreshnessStatus.SKIPPED,
            severity=rule.severity,
            total_records=total_records,
            evaluated_records=0,
            fresh_records=0,
            stale_records=0,
            skipped_records=total_records,
            error_records=0,
            freshness_score=1.0,
            threshold=rule.threshold,
            duration_ms=0.0,
            findings=[],
            metadata={"reason": reason, "weight": rule.weight, **rule.metadata},
        )

    def _error_result(self, rule: FreshnessRule, total_records: int, exc: Exception) -> FreshnessRuleResult:
        return FreshnessRuleResult(
            rule_name=rule.name,
            rule_type=rule.rule_type,
            status=FreshnessStatus.ERROR,
            severity=rule.severity,
            total_records=total_records,
            evaluated_records=0,
            fresh_records=0,
            stale_records=0,
            skipped_records=0,
            error_records=total_records,
            freshness_score=0.0,
            threshold=rule.threshold,
            duration_ms=0.0,
            findings=[
                FreshnessFinding(
                    rule_name=rule.name,
                    severity=rule.severity,
                    status=FreshnessStatus.ERROR,
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
        results: Sequence[FreshnessRuleResult],
        metadata: Dict[str, Any],
    ) -> FreshnessReport:
        passed_rules = sum(1 for r in results if r.status == FreshnessStatus.PASSED)
        failed_rules = sum(1 for r in results if r.status == FreshnessStatus.FAILED)
        warning_rules = sum(1 for r in results if r.status == FreshnessStatus.WARNING)
        skipped_rules = sum(1 for r in results if r.status == FreshnessStatus.SKIPPED)
        error_rules = sum(1 for r in results if r.status == FreshnessStatus.ERROR)

        executable_results = [r for r in results if r.status != FreshnessStatus.SKIPPED]
        if executable_results:
            overall_score = round(sum(r.freshness_score for r in executable_results) / len(executable_results), 8)
        else:
            overall_score = 1.0

        weight_by_rule = {rule.name: rule.weight for rule in self.rules}
        weighted_denominator = sum(weight_by_rule.get(r.rule_name, 1.0) for r in executable_results)
        if weighted_denominator > 0:
            weighted_score = round(
                sum(r.freshness_score * weight_by_rule.get(r.rule_name, 1.0) for r in executable_results)
                / weighted_denominator,
                8,
            )
        else:
            weighted_score = 1.0

        if error_rules > 0:
            status = FreshnessStatus.ERROR
        elif failed_rules > 0 or weighted_score < self.global_threshold.min_score:
            status = FreshnessStatus.FAILED
        elif warning_rules > 0 or weighted_score < self.global_threshold.warning_score:
            status = FreshnessStatus.WARNING
        else:
            status = FreshnessStatus.PASSED

        return FreshnessReport(
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

    def _publish_rule_metrics(self, dataset_name: str, result: FreshnessRuleResult) -> None:
        tags = {
            "dataset": dataset_name,
            "rule": result.rule_name,
            "rule_type": result.rule_type.value,
            "status": result.status.value,
            "severity": result.severity.value,
        }
        self.metrics_sink.gauge("data_quality.freshness.rule.score", result.freshness_score, tags=tags)
        self.metrics_sink.gauge("data_quality.freshness.rule.stale_records", result.stale_records, tags=tags)
        self.metrics_sink.gauge("data_quality.freshness.rule.error_records", result.error_records, tags=tags)
        self.metrics_sink.timing("data_quality.freshness.rule.duration_ms", result.duration_ms, tags=tags)

    def _publish_report_metrics(self, report: FreshnessReport) -> None:
        tags = {"dataset": report.dataset_name, "status": report.status.value}
        self.metrics_sink.gauge("data_quality.freshness.report.weighted_score", report.weighted_score, tags=tags)
        self.metrics_sink.gauge("data_quality.freshness.report.overall_score", report.overall_score, tags=tags)
        self.metrics_sink.timing("data_quality.freshness.report.duration_ms", report.duration_ms, tags=tags)
        self.metrics_sink.increment("data_quality.freshness.run.completed", tags=tags)


# =============================================================================
# Rule Factory
# =============================================================================


class FreshnessRuleFactory:
    """Factory helpers for concise standardized freshness rule creation."""

    @staticmethod
    def max_data_age(
        name: str,
        timestamp_column: str,
        *,
        max_age: int,
        unit: TimeUnit = TimeUnit.HOURS,
        warning_age: Optional[int] = None,
        aggregation_mode: AggregationMode = AggregationMode.MAX,
        min_score: float = 1.0,
        severity: Severity = Severity.CRITICAL,
        weight: float = 2.0,
    ) -> FreshnessRule:
        return FreshnessRule(
            name=name,
            rule_type=FreshnessRuleType.MAX_DATA_AGE,
            timestamp_column=timestamp_column,
            sla=FreshnessSLA(max_age=max_age, unit=unit, warning_age=warning_age),
            aggregation_mode=aggregation_mode,
            threshold=FreshnessThreshold(min_score=min_score, warning_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def timestamp_column_freshness(
        name: str,
        timestamp_column: str,
        *,
        max_age: int,
        unit: TimeUnit = TimeUnit.HOURS,
        min_score: float = 0.99,
        severity: Severity = Severity.HIGH,
        weight: float = 1.0,
        null_handling: NullHandling = NullHandling.IGNORE,
    ) -> FreshnessRule:
        return FreshnessRule(
            name=name,
            rule_type=FreshnessRuleType.TIMESTAMP_COLUMN_FRESHNESS,
            timestamp_column=timestamp_column,
            sla=FreshnessSLA(max_age=max_age, unit=unit),
            threshold=FreshnessThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
            null_handling=null_handling,
        )

    @staticmethod
    def ingestion_delay(
        name: str,
        event_timestamp_column: str,
        ingestion_timestamp_column: str,
        *,
        max_delay: int,
        unit: TimeUnit = TimeUnit.MINUTES,
        min_score: float = 0.99,
        severity: Severity = Severity.HIGH,
        weight: float = 1.5,
    ) -> FreshnessRule:
        return FreshnessRule(
            name=name,
            rule_type=FreshnessRuleType.INGESTION_DELAY,
            event_timestamp_column=event_timestamp_column,
            ingestion_timestamp_column=ingestion_timestamp_column,
            sla=FreshnessSLA(max_age=max_delay, unit=unit),
            threshold=FreshnessThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def partition_freshness(
        name: str,
        partition_column: str,
        *,
        max_age: int = 1,
        unit: TimeUnit = TimeUnit.DAYS,
        expected_latest_partition: Optional[Any] = None,
        min_score: float = 1.0,
        severity: Severity = Severity.CRITICAL,
        weight: float = 2.0,
    ) -> FreshnessRule:
        return FreshnessRule(
            name=name,
            rule_type=FreshnessRuleType.PARTITION_FRESHNESS,
            partition_column=partition_column,
            expected_latest_partition=expected_latest_partition,
            sla=FreshnessSLA(max_age=max_age, unit=unit),
            threshold=FreshnessThreshold(min_score=min_score, warning_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def expected_time_window(
        name: str,
        timestamp_column: str,
        start: Any,
        end: Any,
        *,
        frequency: TimeFrequency = TimeFrequency.DAILY,
        min_records_per_window: int = 1,
        min_score: float = 0.99,
        severity: Severity = Severity.HIGH,
        weight: float = 1.5,
    ) -> FreshnessRule:
        return FreshnessRule(
            name=name,
            rule_type=FreshnessRuleType.EXPECTED_TIME_WINDOW,
            timestamp_column=timestamp_column,
            time_window=TimeWindowExpectation(start=start, end=end, frequency=frequency),
            min_records_per_window=min_records_per_window,
            threshold=FreshnessThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def group_freshness(
        name: str,
        timestamp_column: str,
        group_by: Sequence[str],
        *,
        max_age: int,
        unit: TimeUnit = TimeUnit.HOURS,
        aggregation_mode: AggregationMode = AggregationMode.MAX,
        min_score: float = 0.99,
        severity: Severity = Severity.HIGH,
        weight: float = 1.5,
    ) -> FreshnessRule:
        return FreshnessRule(
            name=name,
            rule_type=FreshnessRuleType.GROUP_FRESHNESS,
            timestamp_column=timestamp_column,
            group_by=list(group_by),
            sla=FreshnessSLA(max_age=max_age, unit=unit),
            aggregation_mode=aggregation_mode,
            threshold=FreshnessThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def source_heartbeat(
        name: str,
        reference_dataset: str,
        reference_timestamp_column: str,
        *,
        max_age: int,
        unit: TimeUnit = TimeUnit.MINUTES,
        group_by: Optional[Sequence[str]] = None,
        min_score: float = 1.0,
        severity: Severity = Severity.CRITICAL,
        weight: float = 2.0,
    ) -> FreshnessRule:
        return FreshnessRule(
            name=name,
            rule_type=FreshnessRuleType.SOURCE_HEARTBEAT,
            reference_dataset=reference_dataset,
            reference_timestamp_column=reference_timestamp_column,
            group_by=list(group_by or []),
            sla=FreshnessSLA(max_age=max_age, unit=unit),
            threshold=FreshnessThreshold(min_score=min_score, warning_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def custom(
        name: str,
        function: CustomFreshnessFunction,
        *,
        min_score: float = 0.95,
        severity: Severity = Severity.HIGH,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FreshnessRule:
        return FreshnessRule(
            name=name,
            rule_type=FreshnessRuleType.CUSTOM,
            custom_function=function,
            threshold=FreshnessThreshold(min_score=min_score),
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

    fixed_now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)

    dataset = pd.DataFrame(
        [
            {
                "order_id": 1,
                "source": "pos_a",
                "event_at": "2026-05-13T10:30:00Z",
                "ingested_at": "2026-05-13T10:35:00Z",
                "updated_at": "2026-05-13T11:00:00Z",
                "partition_date": "2026-05-13",
            },
            {
                "order_id": 2,
                "source": "pos_a",
                "event_at": "2026-05-13T09:00:00Z",
                "ingested_at": "2026-05-13T11:30:00Z",
                "updated_at": "2026-05-13T11:15:00Z",
                "partition_date": "2026-05-13",
            },
            {
                "order_id": 3,
                "source": "pos_b",
                "event_at": "2026-05-12T08:00:00Z",
                "ingested_at": "2026-05-12T08:20:00Z",
                "updated_at": "2026-05-12T08:30:00Z",
                "partition_date": "2026-05-12",
            },
        ]
    )

    heartbeats = pd.DataFrame(
        [
            {"source": "pos_a", "last_seen_at": "2026-05-13T11:55:00Z"},
            {"source": "pos_b", "last_seen_at": "2026-05-13T08:00:00Z"},
        ]
    )

    rules = [
        FreshnessRuleFactory.max_data_age(
            "orders_dataset_updated_recently",
            "updated_at",
            max_age=2,
            unit=TimeUnit.HOURS,
        ),
        FreshnessRuleFactory.timestamp_column_freshness(
            "record_updates_not_stale",
            "updated_at",
            max_age=4,
            unit=TimeUnit.HOURS,
            min_score=0.80,
        ),
        FreshnessRuleFactory.ingestion_delay(
            "orders_ingestion_delay_under_60m",
            event_timestamp_column="event_at",
            ingestion_timestamp_column="ingested_at",
            max_delay=60,
            unit=TimeUnit.MINUTES,
            min_score=0.80,
        ),
        FreshnessRuleFactory.partition_freshness(
            "latest_partition_is_today",
            "partition_date",
            expected_latest_partition="2026-05-13",
        ),
        FreshnessRuleFactory.expected_time_window(
            "hourly_order_windows_present",
            "event_at",
            "2026-05-13T09:00:00Z",
            "2026-05-13T11:00:00Z",
            frequency=TimeFrequency.HOURLY,
            min_score=0.50,
        ),
        FreshnessRuleFactory.group_freshness(
            "source_group_freshness",
            "updated_at",
            group_by=["source"],
            max_age=4,
            unit=TimeUnit.HOURS,
            min_score=0.50,
        ),
        FreshnessRuleFactory.source_heartbeat(
            "source_heartbeats_recent",
            reference_dataset="source_heartbeats",
            reference_timestamp_column="last_seen_at",
            group_by=["source"],
            max_age=90,
            unit=TimeUnit.MINUTES,
            min_score=0.50,
        ),
    ]

    checker = FreshnessChecker(
        rules,
        reference_provider=StaticReferenceDataProvider({"source_heartbeats": heartbeats}),
        audit_sink=InMemoryAuditSink(),
        clock=lambda: fixed_now,
        max_findings_per_rule=100,
    )

    report = checker.run(dataset, dataset_name="orders")
    print(report.to_json(include_findings=True))

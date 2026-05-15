"""
data/quality/accuracy_checker.py

Enterprise-grade Accuracy Checker for data quality validation.

This module provides a solid, extensible architecture for validating data
accuracy across datasets, reference datasets, business rules, schemas,
statistical expectations, and trusted source-of-truth values.

Main capabilities:
- Column-level and row-level accuracy checks
- Reference/source-of-truth comparison
- Tolerance-based numeric validation
- Categorical/domain validation
- Date/time accuracy validation
- Regex/pattern validation
- Custom business-rule validation
- Weighted accuracy scoring
- Severity-based findings
- Audit-ready execution reports
- Pluggable repository and metrics hooks
- Pandas-native implementation with optional dependency isolation

Designed for enterprise data platforms, lakehouse quality gates, ETL/ELT
pipelines, orchestration workflows, and compliance-ready quality audits.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import statistics
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
    MutableMapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Union,
)

try:
    import pandas as pd
except Exception:  # pragma: no cover - optional runtime dependency
    pd = None  # type: ignore


# =============================================================================
# Logging
# =============================================================================

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# =============================================================================
# Exceptions
# =============================================================================


class AccuracyCheckerError(Exception):
    """Base exception for accuracy checker failures."""


class AccuracyConfigurationError(AccuracyCheckerError):
    """Raised when a rule/configuration is invalid."""


class AccuracyExecutionError(AccuracyCheckerError):
    """Raised when a check cannot be executed safely."""


class DatasetValidationError(AccuracyCheckerError):
    """Raised when an input dataset is invalid or unsupported."""


# =============================================================================
# Enums
# =============================================================================


class Severity(str, Enum):
    """Severity level for accuracy findings."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AccuracyStatus(str, Enum):
    """Execution status for rules and reports."""

    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"
    ERROR = "error"


class RuleType(str, Enum):
    """Supported accuracy rule categories."""

    EXACT_MATCH = "exact_match"
    NUMERIC_TOLERANCE = "numeric_tolerance"
    PERCENT_TOLERANCE = "percent_tolerance"
    DOMAIN_MEMBERSHIP = "domain_membership"
    REGEX_PATTERN = "regex_pattern"
    DATE_RANGE = "date_range"
    REFERENCE_LOOKUP = "reference_lookup"
    CROSS_FIELD_CONSISTENCY = "cross_field_consistency"
    STATISTICAL_RANGE = "statistical_range"
    CUSTOM = "custom"


class NullHandling(str, Enum):
    """How null values should be handled during accuracy checks."""

    IGNORE = "ignore"
    FAIL = "fail"
    PASS = "pass"
    COMPARE = "compare"


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


CustomRuleFunction = Callable[[Mapping[str, Any]], bool]
CrossFieldFunction = Callable[[Mapping[str, Any]], bool]


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class AccuracyThreshold:
    """Threshold configuration for a rule or full report."""

    min_score: float = 0.95
    warning_score: float = 0.98
    max_failed_records: Optional[int] = None
    max_error_rate: Optional[float] = None

    def validate(self) -> None:
        if not 0 <= self.min_score <= 1:
            raise AccuracyConfigurationError("min_score must be between 0 and 1.")
        if not 0 <= self.warning_score <= 1:
            raise AccuracyConfigurationError("warning_score must be between 0 and 1.")
        if self.warning_score < self.min_score:
            raise AccuracyConfigurationError("warning_score must be greater than or equal to min_score.")
        if self.max_failed_records is not None and self.max_failed_records < 0:
            raise AccuracyConfigurationError("max_failed_records cannot be negative.")
        if self.max_error_rate is not None and not 0 <= self.max_error_rate <= 1:
            raise AccuracyConfigurationError("max_error_rate must be between 0 and 1.")


@dataclass(frozen=True)
class AccuracyRule:
    """Definition of an accuracy validation rule."""

    name: str
    rule_type: RuleType
    column: Optional[str] = None
    reference_column: Optional[str] = None
    reference_dataset: Optional[str] = None
    key_columns: Sequence[str] = field(default_factory=list)
    expected_value: Optional[Any] = None
    expected_values: Optional[Sequence[Any]] = None
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None
    regex: Optional[str] = None
    absolute_tolerance: Optional[float] = None
    percent_tolerance: Optional[float] = None
    null_handling: NullHandling = NullHandling.IGNORE
    case_sensitive: bool = True
    trim_strings: bool = True
    weight: float = 1.0
    severity: Severity = Severity.HIGH
    threshold: AccuracyThreshold = field(default_factory=AccuracyThreshold)
    custom_function: Optional[Union[CustomRuleFunction, CrossFieldFunction]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def validate(self) -> None:
        if not self.name or not self.name.strip():
            raise AccuracyConfigurationError("Rule name is required.")
        if self.weight <= 0:
            raise AccuracyConfigurationError(f"Rule '{self.name}' weight must be greater than zero.")
        self.threshold.validate()

        if self.rule_type in {
            RuleType.EXACT_MATCH,
            RuleType.NUMERIC_TOLERANCE,
            RuleType.PERCENT_TOLERANCE,
            RuleType.DOMAIN_MEMBERSHIP,
            RuleType.REGEX_PATTERN,
            RuleType.DATE_RANGE,
            RuleType.STATISTICAL_RANGE,
        } and not self.column:
            raise AccuracyConfigurationError(f"Rule '{self.name}' requires a column.")

        if self.rule_type == RuleType.DOMAIN_MEMBERSHIP and self.expected_values is None:
            raise AccuracyConfigurationError(f"Rule '{self.name}' requires expected_values.")

        if self.rule_type == RuleType.REGEX_PATTERN:
            if not self.regex:
                raise AccuracyConfigurationError(f"Rule '{self.name}' requires regex.")
            try:
                re.compile(self.regex)
            except re.error as exc:
                raise AccuracyConfigurationError(f"Rule '{self.name}' has invalid regex: {exc}") from exc

        if self.rule_type == RuleType.NUMERIC_TOLERANCE and self.absolute_tolerance is None:
            raise AccuracyConfigurationError(f"Rule '{self.name}' requires absolute_tolerance.")

        if self.rule_type == RuleType.PERCENT_TOLERANCE and self.percent_tolerance is None:
            raise AccuracyConfigurationError(f"Rule '{self.name}' requires percent_tolerance.")

        if self.rule_type == RuleType.REFERENCE_LOOKUP:
            if not self.reference_dataset:
                raise AccuracyConfigurationError(f"Rule '{self.name}' requires reference_dataset.")
            if not self.key_columns:
                raise AccuracyConfigurationError(f"Rule '{self.name}' requires key_columns.")
            if not self.column or not self.reference_column:
                raise AccuracyConfigurationError(
                    f"Rule '{self.name}' requires column and reference_column."
                )

        if self.rule_type in {RuleType.CUSTOM, RuleType.CROSS_FIELD_CONSISTENCY} and self.custom_function is None:
            raise AccuracyConfigurationError(f"Rule '{self.name}' requires custom_function.")


@dataclass
class AccuracyFinding:
    """Single row-level or rule-level finding."""

    rule_name: str
    severity: Severity
    status: AccuracyStatus
    message: str
    row_index: Optional[Any] = None
    column: Optional[str] = None
    actual_value: Optional[Any] = None
    expected_value: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["severity"] = self.severity.value
        payload["status"] = self.status.value
        return _json_safe(payload)


@dataclass
class AccuracyRuleResult:
    """Aggregated execution result for one rule."""

    rule_name: str
    rule_type: RuleType
    status: AccuracyStatus
    severity: Severity
    total_records: int
    evaluated_records: int
    passed_records: int
    failed_records: int
    skipped_records: int
    error_records: int
    score: float
    threshold: AccuracyThreshold
    duration_ms: float
    findings: List[AccuracyFinding] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def error_rate(self) -> float:
        if self.evaluated_records <= 0:
            return 0.0
        return self.error_records / self.evaluated_records

    @property
    def failure_rate(self) -> float:
        if self.evaluated_records <= 0:
            return 0.0
        return self.failed_records / self.evaluated_records

    def to_dict(self, include_findings: bool = True) -> Dict[str, Any]:
        data = {
            "rule_name": self.rule_name,
            "rule_type": self.rule_type.value,
            "status": self.status.value,
            "severity": self.severity.value,
            "total_records": self.total_records,
            "evaluated_records": self.evaluated_records,
            "passed_records": self.passed_records,
            "failed_records": self.failed_records,
            "skipped_records": self.skipped_records,
            "error_records": self.error_records,
            "score": self.score,
            "failure_rate": self.failure_rate,
            "error_rate": self.error_rate,
            "threshold": asdict(self.threshold),
            "duration_ms": self.duration_ms,
            "metadata": _json_safe(self.metadata),
        }
        if include_findings:
            data["findings"] = [finding.to_dict() for finding in self.findings]
        return data


@dataclass
class AccuracyReport:
    """Complete accuracy validation report."""

    report_id: str
    dataset_name: str
    status: AccuracyStatus
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
    rule_results: List[AccuracyRuleResult]
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
# Helper Functions
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    """Convert common non-JSON-safe values into serializable structures."""
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


def _normalize_string(value: Any, *, trim: bool, case_sensitive: bool) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip() if trim else value
    return normalized if case_sensitive else normalized.casefold()


def _normalize_value(value: Any, *, trim: bool, case_sensitive: bool) -> Any:
    if isinstance(value, str):
        return _normalize_string(value, trim=trim, case_sensitive=case_sensitive)
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


def _dataset_fingerprint(dataset: Any, limit: int = 10_000) -> str:
    """Create a deterministic best-effort fingerprint for audit traceability."""
    try:
        if pd is not None and isinstance(dataset, pd.DataFrame):
            sample = dataset.head(limit).to_json(orient="records", date_format="iso")
        else:
            sample = json.dumps(list(dataset)[:limit], default=str, sort_keys=True)
    except Exception:
        sample = repr(dataset)[:1_000_000]
    return hashlib.sha256(sample.encode("utf-8", errors="ignore")).hexdigest()


def _require_pandas() -> None:
    if pd is None:
        raise DatasetValidationError(
            "pandas is required for AccuracyChecker. Install pandas or adapt the DatasetAdapter."
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


def _row_to_mapping(row: Any) -> Mapping[str, Any]:
    if hasattr(row, "to_dict"):
        return row.to_dict()
    if isinstance(row, Mapping):
        return row
    raise DatasetValidationError("Cannot convert row to mapping.")


# =============================================================================
# In-Memory Sinks
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
    """Reference data provider backed by an in-memory dictionary."""

    def __init__(self, datasets: Optional[Mapping[str, Any]] = None) -> None:
        self.datasets = dict(datasets or {})

    def get_reference_dataset(self, name: str) -> Any:
        if name not in self.datasets:
            raise AccuracyExecutionError(f"Reference dataset not found: {name}")
        return self.datasets[name]


# =============================================================================
# Accuracy Checker
# =============================================================================


class AccuracyChecker:
    """
    Enterprise data accuracy checker.

    Example:
        checker = AccuracyChecker(
            rules=[
                AccuracyRule(
                    name="valid_status",
                    rule_type=RuleType.DOMAIN_MEMBERSHIP,
                    column="status",
                    expected_values=["active", "inactive"],
                    threshold=AccuracyThreshold(min_score=0.99),
                )
            ]
        )
        report = checker.run(dataset, dataset_name="customers")
    """

    def __init__(
        self,
        rules: Sequence[AccuracyRule],
        *,
        reference_provider: Optional[ReferenceDataProvider] = None,
        metrics_sink: Optional[MetricsSink] = None,
        audit_sink: Optional[AuditSink] = None,
        max_findings_per_rule: int = 1_000,
        fail_fast: bool = False,
        global_threshold: AccuracyThreshold = AccuracyThreshold(min_score=0.95, warning_score=0.98),
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
            raise AccuracyConfigurationError("max_findings_per_rule cannot be negative.")
        self.global_threshold.validate()
        seen = set()
        for rule in self.rules:
            rule.validate()
            if rule.name in seen:
                raise AccuracyConfigurationError(f"Duplicate accuracy rule name: {rule.name}")
            seen.add(rule.name)

    def run(
        self,
        dataset: Any,
        *,
        dataset_name: str = "dataset",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AccuracyReport:
        """Run all enabled accuracy rules against a dataset."""
        started_at = utc_now_iso()
        started = time.perf_counter()
        report_id = str(uuid.uuid4())
        metadata = dict(metadata or {})

        df = _as_dataframe(dataset)
        total_records = len(df)
        metadata.setdefault("dataset_fingerprint", _dataset_fingerprint(df))
        metadata.setdefault("columns", list(df.columns))

        self.logger.info("Starting accuracy check report_id=%s dataset=%s", report_id, dataset_name)
        self.metrics_sink.increment("data_quality.accuracy.run.started", tags={"dataset": dataset_name})

        results: List[AccuracyRuleResult] = []

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
                            "event_type": "accuracy_rule_executed",
                            "report_id": report_id,
                            "dataset_name": dataset_name,
                            "timestamp": utc_now_iso(),
                            "rule_result": result.to_dict(include_findings=False),
                        }
                    )

                if self.fail_fast and result.status in {AccuracyStatus.FAILED, AccuracyStatus.ERROR}:
                    self.logger.warning("Fail-fast triggered by rule=%s", rule.name)
                    break

            except Exception as exc:  # noqa: BLE001 - enterprise boundary handling
                self.logger.exception("Accuracy rule failed unexpectedly: %s", rule.name)
                result = self._error_result(rule, total_records, exc)
                results.append(result)
                self.metrics_sink.increment(
                    "data_quality.accuracy.rule.error",
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
                    "event_type": "accuracy_report_completed",
                    "report_id": report_id,
                    "dataset_name": dataset_name,
                    "timestamp": utc_now_iso(),
                    "report": report.to_dict(include_findings=False),
                }
            )

        self.logger.info(
            "Completed accuracy check report_id=%s dataset=%s status=%s score=%.5f duration_ms=%.2f",
            report_id,
            dataset_name,
            report.status.value,
            report.weighted_score,
            duration_ms,
        )
        return report

    def _execute_rule(self, df: "pd.DataFrame", rule: AccuracyRule) -> AccuracyRuleResult:
        started = time.perf_counter()

        if rule.column and rule.column not in df.columns:
            raise AccuracyExecutionError(f"Rule '{rule.name}' column not found: {rule.column}")

        if rule.rule_type == RuleType.EXACT_MATCH:
            counters, findings = self._check_exact_match(df, rule)
        elif rule.rule_type == RuleType.NUMERIC_TOLERANCE:
            counters, findings = self._check_numeric_tolerance(df, rule, percent=False)
        elif rule.rule_type == RuleType.PERCENT_TOLERANCE:
            counters, findings = self._check_numeric_tolerance(df, rule, percent=True)
        elif rule.rule_type == RuleType.DOMAIN_MEMBERSHIP:
            counters, findings = self._check_domain_membership(df, rule)
        elif rule.rule_type == RuleType.REGEX_PATTERN:
            counters, findings = self._check_regex_pattern(df, rule)
        elif rule.rule_type == RuleType.DATE_RANGE:
            counters, findings = self._check_date_range(df, rule)
        elif rule.rule_type == RuleType.REFERENCE_LOOKUP:
            counters, findings = self._check_reference_lookup(df, rule)
        elif rule.rule_type == RuleType.CROSS_FIELD_CONSISTENCY:
            counters, findings = self._check_row_function(df, rule)
        elif rule.rule_type == RuleType.STATISTICAL_RANGE:
            counters, findings = self._check_statistical_range(df, rule)
        elif rule.rule_type == RuleType.CUSTOM:
            counters, findings = self._check_row_function(df, rule)
        else:
            raise AccuracyExecutionError(f"Unsupported rule type: {rule.rule_type}")

        duration_ms = (time.perf_counter() - started) * 1000
        score = self._score(counters["passed"], counters["evaluated"])
        status = self._status_from_score(
            score=score,
            failed_records=counters["failed"],
            error_records=counters["error"],
            threshold=rule.threshold,
        )

        return AccuracyRuleResult(
            rule_name=rule.name,
            rule_type=rule.rule_type,
            status=status,
            severity=rule.severity,
            total_records=len(df),
            evaluated_records=counters["evaluated"],
            passed_records=counters["passed"],
            failed_records=counters["failed"],
            skipped_records=counters["skipped"],
            error_records=counters["error"],
            score=score,
            threshold=rule.threshold,
            duration_ms=duration_ms,
            findings=findings,
            metadata={"weight": rule.weight, **rule.metadata},
        )

    def _initial_counters(self) -> Dict[str, int]:
        return {"evaluated": 0, "passed": 0, "failed": 0, "skipped": 0, "error": 0}

    def _handle_null(self, value: Any, expected: Any, rule: AccuracyRule) -> Optional[bool]:
        actual_null = _is_null(value)
        expected_null = _is_null(expected)

        if not actual_null and not expected_null:
            return None

        if rule.null_handling == NullHandling.IGNORE:
            return None if not actual_null else True  # special signal: skip in caller
        if rule.null_handling == NullHandling.FAIL:
            return False
        if rule.null_handling == NullHandling.PASS:
            return True
        if rule.null_handling == NullHandling.COMPARE:
            return actual_null == expected_null
        return False

    def _append_finding(
        self,
        findings: List[AccuracyFinding],
        finding: AccuracyFinding,
    ) -> None:
        if len(findings) < self.max_findings_per_rule:
            findings.append(finding)

    def _check_exact_match(
        self, df: "pd.DataFrame", rule: AccuracyRule
    ) -> Tuple[Dict[str, int], List[AccuracyFinding]]:
        counters = self._initial_counters()
        findings: List[AccuracyFinding] = []
        expected = rule.expected_value

        for idx, row in df.iterrows():
            actual = row[rule.column]  # type: ignore[index]
            null_result = self._handle_null(actual, expected, rule)
            if null_result is True and rule.null_handling == NullHandling.IGNORE and _is_null(actual):
                counters["skipped"] += 1
                continue

            counters["evaluated"] += 1
            if null_result is not None and not (rule.null_handling == NullHandling.IGNORE and _is_null(actual)):
                passed = null_result
            else:
                actual_norm = _normalize_value(
                    actual, trim=rule.trim_strings, case_sensitive=rule.case_sensitive
                )
                expected_norm = _normalize_value(
                    expected, trim=rule.trim_strings, case_sensitive=rule.case_sensitive
                )
                passed = actual_norm == expected_norm

            if passed:
                counters["passed"] += 1
            else:
                counters["failed"] += 1
                self._append_finding(
                    findings,
                    AccuracyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=AccuracyStatus.FAILED,
                        message=f"Value does not match expected value for column '{rule.column}'.",
                        row_index=idx,
                        column=rule.column,
                        actual_value=actual,
                        expected_value=expected,
                    ),
                )
        return counters, findings

    def _check_numeric_tolerance(
        self, df: "pd.DataFrame", rule: AccuracyRule, *, percent: bool
    ) -> Tuple[Dict[str, int], List[AccuracyFinding]]:
        counters = self._initial_counters()
        findings: List[AccuracyFinding] = []
        expected_decimal = _to_decimal(rule.expected_value)
        if expected_decimal is None:
            raise AccuracyConfigurationError(f"Rule '{rule.name}' expected_value must be numeric.")

        for idx, row in df.iterrows():
            actual = row[rule.column]  # type: ignore[index]
            if _is_null(actual):
                if rule.null_handling == NullHandling.IGNORE:
                    counters["skipped"] += 1
                    continue
                counters["evaluated"] += 1
                passed = rule.null_handling == NullHandling.PASS
            else:
                counters["evaluated"] += 1
                actual_decimal = _to_decimal(actual)
                if actual_decimal is None:
                    counters["error"] += 1
                    self._append_finding(
                        findings,
                        AccuracyFinding(
                            rule_name=rule.name,
                            severity=rule.severity,
                            status=AccuracyStatus.ERROR,
                            message=f"Value is not numeric for column '{rule.column}'.",
                            row_index=idx,
                            column=rule.column,
                            actual_value=actual,
                            expected_value=rule.expected_value,
                        ),
                    )
                    continue

                difference = abs(actual_decimal - expected_decimal)
                if percent:
                    if expected_decimal == 0:
                        passed = difference == 0
                        tolerance_value = Decimal("0")
                    else:
                        tolerance_value = abs(expected_decimal) * Decimal(str(rule.percent_tolerance))
                        passed = difference <= tolerance_value
                else:
                    tolerance_value = Decimal(str(rule.absolute_tolerance))
                    passed = difference <= tolerance_value

            if passed:
                counters["passed"] += 1
            else:
                counters["failed"] += 1
                self._append_finding(
                    findings,
                    AccuracyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=AccuracyStatus.FAILED,
                        message=f"Numeric value outside tolerance for column '{rule.column}'.",
                        row_index=idx,
                        column=rule.column,
                        actual_value=actual,
                        expected_value=rule.expected_value,
                        metadata={
                            "tolerance_type": "percent" if percent else "absolute",
                            "tolerance": rule.percent_tolerance if percent else rule.absolute_tolerance,
                        },
                    ),
                )
        return counters, findings

    def _check_domain_membership(
        self, df: "pd.DataFrame", rule: AccuracyRule
    ) -> Tuple[Dict[str, int], List[AccuracyFinding]]:
        counters = self._initial_counters()
        findings: List[AccuracyFinding] = []
        expected_values = set(
            _normalize_value(v, trim=rule.trim_strings, case_sensitive=rule.case_sensitive)
            for v in (rule.expected_values or [])
        )

        for idx, row in df.iterrows():
            actual = row[rule.column]  # type: ignore[index]
            if _is_null(actual):
                if rule.null_handling == NullHandling.IGNORE:
                    counters["skipped"] += 1
                    continue
                counters["evaluated"] += 1
                passed = rule.null_handling == NullHandling.PASS
            else:
                counters["evaluated"] += 1
                actual_norm = _normalize_value(
                    actual, trim=rule.trim_strings, case_sensitive=rule.case_sensitive
                )
                passed = actual_norm in expected_values

            if passed:
                counters["passed"] += 1
            else:
                counters["failed"] += 1
                self._append_finding(
                    findings,
                    AccuracyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=AccuracyStatus.FAILED,
                        message=f"Value is outside accepted domain for column '{rule.column}'.",
                        row_index=idx,
                        column=rule.column,
                        actual_value=actual,
                        expected_value=list(expected_values),
                    ),
                )
        return counters, findings

    def _check_regex_pattern(
        self, df: "pd.DataFrame", rule: AccuracyRule
    ) -> Tuple[Dict[str, int], List[AccuracyFinding]]:
        counters = self._initial_counters()
        findings: List[AccuracyFinding] = []
        flags = 0 if rule.case_sensitive else re.IGNORECASE
        pattern = re.compile(rule.regex or "", flags)

        for idx, row in df.iterrows():
            actual = row[rule.column]  # type: ignore[index]
            if _is_null(actual):
                if rule.null_handling == NullHandling.IGNORE:
                    counters["skipped"] += 1
                    continue
                counters["evaluated"] += 1
                passed = rule.null_handling == NullHandling.PASS
            else:
                counters["evaluated"] += 1
                value = str(actual).strip() if rule.trim_strings else str(actual)
                passed = bool(pattern.fullmatch(value))

            if passed:
                counters["passed"] += 1
            else:
                counters["failed"] += 1
                self._append_finding(
                    findings,
                    AccuracyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=AccuracyStatus.FAILED,
                        message=f"Value does not match required pattern for column '{rule.column}'.",
                        row_index=idx,
                        column=rule.column,
                        actual_value=actual,
                        expected_value=rule.regex,
                    ),
                )
        return counters, findings

    def _check_date_range(
        self, df: "pd.DataFrame", rule: AccuracyRule
    ) -> Tuple[Dict[str, int], List[AccuracyFinding]]:
        counters = self._initial_counters()
        findings: List[AccuracyFinding] = []
        min_dt = _to_datetime(rule.min_value) if rule.min_value is not None else None
        max_dt = _to_datetime(rule.max_value) if rule.max_value is not None else None

        if rule.min_value is not None and min_dt is None:
            raise AccuracyConfigurationError(f"Rule '{rule.name}' min_value must be date-like.")
        if rule.max_value is not None and max_dt is None:
            raise AccuracyConfigurationError(f"Rule '{rule.name}' max_value must be date-like.")

        for idx, row in df.iterrows():
            actual = row[rule.column]  # type: ignore[index]
            if _is_null(actual):
                if rule.null_handling == NullHandling.IGNORE:
                    counters["skipped"] += 1
                    continue
                counters["evaluated"] += 1
                passed = rule.null_handling == NullHandling.PASS
            else:
                counters["evaluated"] += 1
                actual_dt = _to_datetime(actual)
                if actual_dt is None:
                    counters["error"] += 1
                    self._append_finding(
                        findings,
                        AccuracyFinding(
                            rule_name=rule.name,
                            severity=rule.severity,
                            status=AccuracyStatus.ERROR,
                            message=f"Value is not date-like for column '{rule.column}'.",
                            row_index=idx,
                            column=rule.column,
                            actual_value=actual,
                            expected_value={"min": rule.min_value, "max": rule.max_value},
                        ),
                    )
                    continue
                passed = True
                if min_dt is not None and actual_dt < min_dt:
                    passed = False
                if max_dt is not None and actual_dt > max_dt:
                    passed = False

            if passed:
                counters["passed"] += 1
            else:
                counters["failed"] += 1
                self._append_finding(
                    findings,
                    AccuracyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=AccuracyStatus.FAILED,
                        message=f"Date value outside accepted range for column '{rule.column}'.",
                        row_index=idx,
                        column=rule.column,
                        actual_value=actual,
                        expected_value={"min": rule.min_value, "max": rule.max_value},
                    ),
                )
        return counters, findings

    def _check_reference_lookup(
        self, df: "pd.DataFrame", rule: AccuracyRule
    ) -> Tuple[Dict[str, int], List[AccuracyFinding]]:
        counters = self._initial_counters()
        findings: List[AccuracyFinding] = []

        for key_col in rule.key_columns:
            if key_col not in df.columns:
                raise AccuracyExecutionError(f"Rule '{rule.name}' key column not found: {key_col}")

        reference_raw = self.reference_provider.get_reference_dataset(rule.reference_dataset or "")
        reference_df = _as_dataframe(reference_raw)

        for key_col in rule.key_columns:
            if key_col not in reference_df.columns:
                raise AccuracyExecutionError(
                    f"Rule '{rule.name}' reference key column not found: {key_col}"
                )
        if rule.reference_column not in reference_df.columns:
            raise AccuracyExecutionError(
                f"Rule '{rule.name}' reference column not found: {rule.reference_column}"
            )

        lookup: Dict[Tuple[Any, ...], Any] = {}
        for _, ref_row in reference_df.iterrows():
            key = tuple(ref_row[col] for col in rule.key_columns)
            lookup[key] = ref_row[rule.reference_column]  # last occurrence wins intentionally

        for idx, row in df.iterrows():
            key = tuple(row[col] for col in rule.key_columns)
            actual = row[rule.column]  # type: ignore[index]
            counters["evaluated"] += 1

            if key not in lookup:
                counters["failed"] += 1
                self._append_finding(
                    findings,
                    AccuracyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=AccuracyStatus.FAILED,
                        message="Reference key not found in source-of-truth dataset.",
                        row_index=idx,
                        column=rule.column,
                        actual_value=actual,
                        expected_value=None,
                        metadata={"key": key, "reference_dataset": rule.reference_dataset},
                    ),
                )
                continue

            expected = lookup[key]
            null_result = self._handle_null(actual, expected, rule)
            if null_result is True and rule.null_handling == NullHandling.IGNORE and _is_null(actual):
                counters["skipped"] += 1
                counters["evaluated"] -= 1
                continue

            if null_result is not None and not (rule.null_handling == NullHandling.IGNORE and _is_null(actual)):
                passed = null_result
            else:
                actual_norm = _normalize_value(
                    actual, trim=rule.trim_strings, case_sensitive=rule.case_sensitive
                )
                expected_norm = _normalize_value(
                    expected, trim=rule.trim_strings, case_sensitive=rule.case_sensitive
                )
                passed = actual_norm == expected_norm

            if passed:
                counters["passed"] += 1
            else:
                counters["failed"] += 1
                self._append_finding(
                    findings,
                    AccuracyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=AccuracyStatus.FAILED,
                        message="Value does not match reference source-of-truth.",
                        row_index=idx,
                        column=rule.column,
                        actual_value=actual,
                        expected_value=expected,
                        metadata={"key": key, "reference_dataset": rule.reference_dataset},
                    ),
                )
        return counters, findings

    def _check_row_function(
        self, df: "pd.DataFrame", rule: AccuracyRule
    ) -> Tuple[Dict[str, int], List[AccuracyFinding]]:
        counters = self._initial_counters()
        findings: List[AccuracyFinding] = []

        if rule.custom_function is None:
            raise AccuracyConfigurationError(f"Rule '{rule.name}' requires custom_function.")

        for idx, row in df.iterrows():
            counters["evaluated"] += 1
            row_map = _row_to_mapping(row)
            try:
                passed = bool(rule.custom_function(row_map))
            except Exception as exc:  # noqa: BLE001
                counters["error"] += 1
                self._append_finding(
                    findings,
                    AccuracyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=AccuracyStatus.ERROR,
                        message=f"Custom rule execution error: {exc}",
                        row_index=idx,
                        column=rule.column,
                        actual_value=None,
                        expected_value=True,
                    ),
                )
                continue

            if passed:
                counters["passed"] += 1
            else:
                counters["failed"] += 1
                self._append_finding(
                    findings,
                    AccuracyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=AccuracyStatus.FAILED,
                        message="Custom accuracy rule returned false.",
                        row_index=idx,
                        column=rule.column,
                        actual_value=_json_safe(dict(row_map)),
                        expected_value=True,
                    ),
                )
        return counters, findings

    def _check_statistical_range(
        self, df: "pd.DataFrame", rule: AccuracyRule
    ) -> Tuple[Dict[str, int], List[AccuracyFinding]]:
        counters = self._initial_counters()
        findings: List[AccuracyFinding] = []

        values: List[Decimal] = []
        for value in df[rule.column].tolist():  # type: ignore[index]
            decimal_value = _to_decimal(value)
            if decimal_value is not None:
                values.append(decimal_value)

        if not values:
            raise AccuracyExecutionError(f"Rule '{rule.name}' has no numeric values to evaluate.")

        float_values = [float(v) for v in values]
        mean_value = statistics.mean(float_values)
        std_value = statistics.pstdev(float_values) if len(float_values) > 1 else 0.0

        configured_min = _to_decimal(rule.min_value) if rule.min_value is not None else None
        configured_max = _to_decimal(rule.max_value) if rule.max_value is not None else None

        for idx, row in df.iterrows():
            actual = row[rule.column]  # type: ignore[index]
            if _is_null(actual):
                if rule.null_handling == NullHandling.IGNORE:
                    counters["skipped"] += 1
                    continue
                counters["evaluated"] += 1
                passed = rule.null_handling == NullHandling.PASS
            else:
                counters["evaluated"] += 1
                actual_decimal = _to_decimal(actual)
                if actual_decimal is None:
                    counters["error"] += 1
                    self._append_finding(
                        findings,
                        AccuracyFinding(
                            rule_name=rule.name,
                            severity=rule.severity,
                            status=AccuracyStatus.ERROR,
                            message=f"Value is not numeric for column '{rule.column}'.",
                            row_index=idx,
                            column=rule.column,
                            actual_value=actual,
                        ),
                    )
                    continue

                passed = True
                if configured_min is not None and actual_decimal < configured_min:
                    passed = False
                if configured_max is not None and actual_decimal > configured_max:
                    passed = False

            if passed:
                counters["passed"] += 1
            else:
                counters["failed"] += 1
                self._append_finding(
                    findings,
                    AccuracyFinding(
                        rule_name=rule.name,
                        severity=rule.severity,
                        status=AccuracyStatus.FAILED,
                        message=f"Value outside statistical/configured range for column '{rule.column}'.",
                        row_index=idx,
                        column=rule.column,
                        actual_value=actual,
                        expected_value={"min": rule.min_value, "max": rule.max_value},
                        metadata={"mean": mean_value, "stddev": std_value},
                    ),
                )
        return counters, findings

    def _score(self, passed: int, evaluated: int) -> float:
        if evaluated <= 0:
            return 1.0
        return round(passed / evaluated, 8)

    def _status_from_score(
        self,
        *,
        score: float,
        failed_records: int,
        error_records: int,
        threshold: AccuracyThreshold,
    ) -> AccuracyStatus:
        if error_records > 0:
            if threshold.max_error_rate == 0:
                return AccuracyStatus.ERROR
        if threshold.max_failed_records is not None and failed_records > threshold.max_failed_records:
            return AccuracyStatus.FAILED
        if score < threshold.min_score:
            return AccuracyStatus.FAILED
        if score < threshold.warning_score:
            return AccuracyStatus.WARNING
        return AccuracyStatus.PASSED

    def _skipped_result(self, rule: AccuracyRule, total_records: int, reason: str) -> AccuracyRuleResult:
        return AccuracyRuleResult(
            rule_name=rule.name,
            rule_type=rule.rule_type,
            status=AccuracyStatus.SKIPPED,
            severity=rule.severity,
            total_records=total_records,
            evaluated_records=0,
            passed_records=0,
            failed_records=0,
            skipped_records=total_records,
            error_records=0,
            score=1.0,
            threshold=rule.threshold,
            duration_ms=0.0,
            findings=[],
            metadata={"reason": reason, "weight": rule.weight, **rule.metadata},
        )

    def _error_result(self, rule: AccuracyRule, total_records: int, exc: Exception) -> AccuracyRuleResult:
        return AccuracyRuleResult(
            rule_name=rule.name,
            rule_type=rule.rule_type,
            status=AccuracyStatus.ERROR,
            severity=rule.severity,
            total_records=total_records,
            evaluated_records=0,
            passed_records=0,
            failed_records=0,
            skipped_records=0,
            error_records=total_records,
            score=0.0,
            threshold=rule.threshold,
            duration_ms=0.0,
            findings=[
                AccuracyFinding(
                    rule_name=rule.name,
                    severity=rule.severity,
                    status=AccuracyStatus.ERROR,
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
        results: Sequence[AccuracyRuleResult],
        metadata: Dict[str, Any],
    ) -> AccuracyReport:
        passed_rules = sum(1 for r in results if r.status == AccuracyStatus.PASSED)
        failed_rules = sum(1 for r in results if r.status == AccuracyStatus.FAILED)
        warning_rules = sum(1 for r in results if r.status == AccuracyStatus.WARNING)
        skipped_rules = sum(1 for r in results if r.status == AccuracyStatus.SKIPPED)
        error_rules = sum(1 for r in results if r.status == AccuracyStatus.ERROR)

        executable_results = [r for r in results if r.status != AccuracyStatus.SKIPPED]
        if executable_results:
            overall_score = round(sum(r.score for r in executable_results) / len(executable_results), 8)
        else:
            overall_score = 1.0

        weight_by_rule = {rule.name: rule.weight for rule in self.rules}
        weighted_denominator = sum(weight_by_rule.get(r.rule_name, 1.0) for r in executable_results)
        if weighted_denominator > 0:
            weighted_score = round(
                sum(r.score * weight_by_rule.get(r.rule_name, 1.0) for r in executable_results)
                / weighted_denominator,
                8,
            )
        else:
            weighted_score = 1.0

        if error_rules > 0:
            status = AccuracyStatus.ERROR
        elif failed_rules > 0 or weighted_score < self.global_threshold.min_score:
            status = AccuracyStatus.FAILED
        elif warning_rules > 0 or weighted_score < self.global_threshold.warning_score:
            status = AccuracyStatus.WARNING
        else:
            status = AccuracyStatus.PASSED

        return AccuracyReport(
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

    def _publish_rule_metrics(self, dataset_name: str, result: AccuracyRuleResult) -> None:
        tags = {
            "dataset": dataset_name,
            "rule": result.rule_name,
            "rule_type": result.rule_type.value,
            "status": result.status.value,
            "severity": result.severity.value,
        }
        self.metrics_sink.gauge("data_quality.accuracy.rule.score", result.score, tags=tags)
        self.metrics_sink.gauge("data_quality.accuracy.rule.failed_records", result.failed_records, tags=tags)
        self.metrics_sink.gauge("data_quality.accuracy.rule.error_records", result.error_records, tags=tags)
        self.metrics_sink.timing("data_quality.accuracy.rule.duration_ms", result.duration_ms, tags=tags)

    def _publish_report_metrics(self, report: AccuracyReport) -> None:
        tags = {"dataset": report.dataset_name, "status": report.status.value}
        self.metrics_sink.gauge("data_quality.accuracy.report.weighted_score", report.weighted_score, tags=tags)
        self.metrics_sink.gauge("data_quality.accuracy.report.overall_score", report.overall_score, tags=tags)
        self.metrics_sink.timing("data_quality.accuracy.report.duration_ms", report.duration_ms, tags=tags)
        self.metrics_sink.increment("data_quality.accuracy.run.completed", tags=tags)


# =============================================================================
# Rule Factory Helpers
# =============================================================================


class AccuracyRuleFactory:
    """Factory helpers for concise and standardized rule creation."""

    @staticmethod
    def exact_match(
        name: str,
        column: str,
        expected_value: Any,
        *,
        min_score: float = 0.99,
        severity: Severity = Severity.HIGH,
        weight: float = 1.0,
    ) -> AccuracyRule:
        return AccuracyRule(
            name=name,
            rule_type=RuleType.EXACT_MATCH,
            column=column,
            expected_value=expected_value,
            threshold=AccuracyThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def domain(
        name: str,
        column: str,
        values: Sequence[Any],
        *,
        min_score: float = 0.99,
        severity: Severity = Severity.HIGH,
        weight: float = 1.0,
        case_sensitive: bool = False,
    ) -> AccuracyRule:
        return AccuracyRule(
            name=name,
            rule_type=RuleType.DOMAIN_MEMBERSHIP,
            column=column,
            expected_values=values,
            threshold=AccuracyThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
            case_sensitive=case_sensitive,
        )

    @staticmethod
    def regex(
        name: str,
        column: str,
        pattern: str,
        *,
        min_score: float = 0.98,
        severity: Severity = Severity.MEDIUM,
        weight: float = 1.0,
    ) -> AccuracyRule:
        return AccuracyRule(
            name=name,
            rule_type=RuleType.REGEX_PATTERN,
            column=column,
            regex=pattern,
            threshold=AccuracyThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def numeric_tolerance(
        name: str,
        column: str,
        expected_value: Union[int, float, Decimal],
        tolerance: float,
        *,
        min_score: float = 0.95,
        severity: Severity = Severity.HIGH,
        weight: float = 1.0,
    ) -> AccuracyRule:
        return AccuracyRule(
            name=name,
            rule_type=RuleType.NUMERIC_TOLERANCE,
            column=column,
            expected_value=expected_value,
            absolute_tolerance=tolerance,
            threshold=AccuracyThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def date_range(
        name: str,
        column: str,
        *,
        min_value: Optional[Any] = None,
        max_value: Optional[Any] = None,
        min_score: float = 0.99,
        severity: Severity = Severity.HIGH,
        weight: float = 1.0,
    ) -> AccuracyRule:
        return AccuracyRule(
            name=name,
            rule_type=RuleType.DATE_RANGE,
            column=column,
            min_value=min_value,
            max_value=max_value,
            threshold=AccuracyThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def reference_lookup(
        name: str,
        column: str,
        reference_dataset: str,
        reference_column: str,
        key_columns: Sequence[str],
        *,
        min_score: float = 0.995,
        severity: Severity = Severity.CRITICAL,
        weight: float = 2.0,
    ) -> AccuracyRule:
        return AccuracyRule(
            name=name,
            rule_type=RuleType.REFERENCE_LOOKUP,
            column=column,
            reference_dataset=reference_dataset,
            reference_column=reference_column,
            key_columns=key_columns,
            threshold=AccuracyThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
        )

    @staticmethod
    def custom(
        name: str,
        function: CustomRuleFunction,
        *,
        min_score: float = 0.95,
        severity: Severity = Severity.HIGH,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AccuracyRule:
        return AccuracyRule(
            name=name,
            rule_type=RuleType.CUSTOM,
            custom_function=function,
            threshold=AccuracyThreshold(min_score=min_score),
            severity=severity,
            weight=weight,
            metadata=metadata or {},
        )


# =============================================================================
# CLI / Local Smoke Example
# =============================================================================


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if pd is None:
        raise SystemExit("pandas is required to run the local example: pip install pandas")

    dataset = pd.DataFrame(
        [
            {"id": 1, "status": "active", "email": "ana@example.com", "amount": 100.00, "country": "BR"},
            {"id": 2, "status": "inactive", "email": "bruno@example.com", "amount": 100.01, "country": "BR"},
            {"id": 3, "status": "blocked", "email": "invalid-email", "amount": 97.00, "country": "AR"},
        ]
    )

    reference = pd.DataFrame(
        [
            {"id": 1, "country": "BR"},
            {"id": 2, "country": "BR"},
            {"id": 3, "country": "BR"},
        ]
    )

    rules = [
        AccuracyRuleFactory.domain(
            "status_domain",
            "status",
            ["active", "inactive"],
            min_score=0.95,
            severity=Severity.HIGH,
        ),
        AccuracyRuleFactory.regex(
            "email_format",
            "email",
            r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
            min_score=0.95,
        ),
        AccuracyRuleFactory.numeric_tolerance(
            "amount_close_to_expected",
            "amount",
            100.00,
            0.05,
            min_score=0.90,
        ),
        AccuracyRuleFactory.reference_lookup(
            "country_matches_reference",
            column="country",
            reference_dataset="customer_master",
            reference_column="country",
            key_columns=["id"],
            min_score=0.99,
        ),
        AccuracyRuleFactory.custom(
            "amount_positive",
            lambda row: Decimal(str(row["amount"])) > 0,
            min_score=1.0,
        ),
    ]

    checker = AccuracyChecker(
        rules,
        reference_provider=StaticReferenceDataProvider({"customer_master": reference}),
        audit_sink=InMemoryAuditSink(),
        max_findings_per_rule=100,
    )

    report = checker.run(dataset, dataset_name="customer_transactions")
    print(report.to_json(include_findings=True))

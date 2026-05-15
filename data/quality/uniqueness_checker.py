"""
data/quality/uniqueness_checker.py

Enterprise-grade Uniqueness Checker for data quality validation.

This module validates uniqueness constraints across datasets, including simple
keys, composite keys, conditional uniqueness, group-scoped uniqueness,
duplicate-rate thresholds, natural-key analysis, duplicate clusters, and
optional approximate/fuzzy duplicate detection.

Main capabilities:
- Single-column and composite-key uniqueness checks
- Group-scoped uniqueness validation
- Conditional uniqueness using row predicates
- Duplicate-rate thresholds and weighted scoring
- Duplicate cluster extraction with sample records
- Null-key handling strategies
- Optional approximate duplicate detection via normalized fingerprints
- Severity-based findings and recommendations
- Audit-ready JSON reports
- Metrics sink integration
- Pandas-native implementation with extension points

Designed for enterprise lakehouse quality gates, ETL/ELT validation,
data contracts, master-data governance, deduplication monitoring,
analytics trust controls, and compliance-ready data quality audits.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

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


class UniquenessCheckerError(Exception):
    """Base exception for uniqueness checker failures."""


class UniquenessConfigurationError(UniquenessCheckerError):
    """Raised when uniqueness configuration is invalid."""


class UniquenessExecutionError(UniquenessCheckerError):
    """Raised when uniqueness checking cannot execute safely."""


class DatasetValidationError(UniquenessCheckerError):
    """Raised when input dataset is invalid or unsupported."""


# =============================================================================
# Enums
# =============================================================================


class Severity(str, Enum):
    """Severity level for uniqueness findings."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class UniquenessStatus(str, Enum):
    """Execution status for uniqueness checks."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class UniquenessRuleType(str, Enum):
    """Supported uniqueness rule categories."""

    UNIQUE_KEY = "unique_key"
    COMPOSITE_KEY = "composite_key"
    GROUP_SCOPED_UNIQUE = "group_scoped_unique"
    DUPLICATE_RATE = "duplicate_rate"
    NATURAL_KEY = "natural_key"
    CONDITIONAL_UNIQUE = "conditional_unique"
    APPROXIMATE_DUPLICATE = "approximate_duplicate"
    CUSTOM = "custom"


class NullKeyHandling(str, Enum):
    """How null values in uniqueness keys should be treated."""

    IGNORE_ROWS_WITH_ANY_NULL = "ignore_rows_with_any_null"
    IGNORE_ROWS_WITH_ALL_NULL = "ignore_rows_with_all_null"
    FAIL_NULL_KEYS = "fail_null_keys"
    TREAT_NULLS_AS_VALUE = "treat_nulls_as_value"
    ALLOW_MULTIPLE_NULLS = "allow_multiple_nulls"


class DuplicateScope(str, Enum):
    """Scope used for uniqueness checks."""

    DATASET = "dataset"
    GROUP = "group"
    PARTITION = "partition"


class NormalizationMode(str, Enum):
    """Normalization strategy for duplicate keys."""

    NONE = "none"
    BASIC = "basic"
    CASE_INSENSITIVE = "case_insensitive"
    TRIM_CASE_INSENSITIVE = "trim_case_insensitive"
    ALPHANUMERIC = "alphanumeric"
    EMAIL = "email"
    PHONE = "phone"
    CUSTOM = "custom"


class RecommendationType(str, Enum):
    """Remediation recommendation type."""

    ADD_UNIQUE_CONSTRAINT = "add_unique_constraint"
    FIX_UPSTREAM_IDEMPOTENCY = "fix_upstream_idempotency"
    DEDUPLICATE_RECORDS = "deduplicate_records"
    REVIEW_NATURAL_KEY = "review_natural_key"
    QUARANTINE_DUPLICATES = "quarantine_duplicates"
    MONITOR_DUPLICATE_RATE = "monitor_duplicate_rate"
    INVESTIGATE_GROUP = "investigate_group"


# =============================================================================
# Protocols
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
    """Optional sink for audit events/reports."""

    def write_event(self, event: Mapping[str, Any]) -> None:
        ...


RowPredicate = Callable[[Mapping[str, Any]], bool]
CustomUniquenessFunction = Callable[[Mapping[str, Any]], Any]
CustomNormalizer = Callable[[Any], Any]


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class UniquenessThreshold:
    """Thresholds for uniqueness scoring and status classification."""

    min_score: float = 1.0
    warning_score: float = 1.0
    max_duplicate_records: Optional[int] = 0
    max_duplicate_rate: Optional[float] = 0.0
    max_duplicate_clusters: Optional[int] = None

    def validate(self) -> None:
        if not 0 <= self.min_score <= 1:
            raise UniquenessConfigurationError("min_score must be between 0 and 1.")
        if not 0 <= self.warning_score <= 1:
            raise UniquenessConfigurationError("warning_score must be between 0 and 1.")
        if self.warning_score < self.min_score:
            raise UniquenessConfigurationError("warning_score must be greater than or equal to min_score.")
        if self.max_duplicate_records is not None and self.max_duplicate_records < 0:
            raise UniquenessConfigurationError("max_duplicate_records cannot be negative.")
        if self.max_duplicate_rate is not None and not 0 <= self.max_duplicate_rate <= 1:
            raise UniquenessConfigurationError("max_duplicate_rate must be between 0 and 1.")
        if self.max_duplicate_clusters is not None and self.max_duplicate_clusters < 0:
            raise UniquenessConfigurationError("max_duplicate_clusters cannot be negative.")


@dataclass(frozen=True)
class UniquenessRule:
    """Definition of a uniqueness validation rule."""

    name: str
    rule_type: UniquenessRuleType
    key_columns: Sequence[str] = field(default_factory=list)
    group_by: Sequence[str] = field(default_factory=list)
    partition_by: Sequence[str] = field(default_factory=list)
    condition: Optional[RowPredicate] = None
    custom_key_function: Optional[CustomUniquenessFunction] = None
    custom_normalizer: Optional[CustomNormalizer] = None
    normalization_mode: NormalizationMode = NormalizationMode.NONE
    null_key_handling: NullKeyHandling = NullKeyHandling.IGNORE_ROWS_WITH_ANY_NULL
    threshold: UniquenessThreshold = field(default_factory=UniquenessThreshold)
    severity: Severity = Severity.CRITICAL
    weight: float = 1.0
    enabled: bool = True
    include_duplicate_samples: bool = True
    max_duplicate_clusters_reported: int = 100
    max_rows_per_cluster_reported: int = 25
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.name.strip():
            raise UniquenessConfigurationError("Rule name is required.")
        if self.weight <= 0:
            raise UniquenessConfigurationError(f"Rule '{self.name}' weight must be greater than zero.")
        if self.max_duplicate_clusters_reported < 0:
            raise UniquenessConfigurationError("max_duplicate_clusters_reported cannot be negative.")
        if self.max_rows_per_cluster_reported < 0:
            raise UniquenessConfigurationError("max_rows_per_cluster_reported cannot be negative.")
        self.threshold.validate()

        if self.rule_type in {
            UniquenessRuleType.UNIQUE_KEY,
            UniquenessRuleType.COMPOSITE_KEY,
            UniquenessRuleType.GROUP_SCOPED_UNIQUE,
            UniquenessRuleType.DUPLICATE_RATE,
            UniquenessRuleType.NATURAL_KEY,
            UniquenessRuleType.CONDITIONAL_UNIQUE,
            UniquenessRuleType.APPROXIMATE_DUPLICATE,
        } and not self.key_columns:
            raise UniquenessConfigurationError(f"Rule '{self.name}' requires key_columns.")

        if self.rule_type == UniquenessRuleType.GROUP_SCOPED_UNIQUE and not self.group_by:
            raise UniquenessConfigurationError(f"Rule '{self.name}' requires group_by.")

        if self.rule_type == UniquenessRuleType.CONDITIONAL_UNIQUE and self.condition is None:
            raise UniquenessConfigurationError(f"Rule '{self.name}' requires condition.")

        if self.rule_type == UniquenessRuleType.CUSTOM and self.custom_key_function is None:
            raise UniquenessConfigurationError(f"Rule '{self.name}' requires custom_key_function.")

        if self.normalization_mode == NormalizationMode.CUSTOM and self.custom_normalizer is None:
            raise UniquenessConfigurationError(f"Rule '{self.name}' requires custom_normalizer.")

    def required_columns(self) -> List[str]:
        return sorted(set(self.key_columns) | set(self.group_by) | set(self.partition_by))


@dataclass
class DuplicateCluster:
    """A cluster of records sharing the same uniqueness key."""

    cluster_id: str
    key: Any
    key_hash: str
    duplicate_count: int
    row_count: int
    row_indexes: List[Any] = field(default_factory=list)
    group_key: Optional[Any] = None
    sample_records: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class UniquenessFinding:
    """Finding generated by uniqueness checking."""

    finding_id: str
    rule_name: str
    severity: Severity
    status: UniquenessStatus
    message: str
    key: Optional[Any] = None
    group_key: Optional[Any] = None
    column: Optional[str] = None
    duplicate_count: Optional[int] = None
    duplicate_rate: Optional[float] = None
    row_indexes: List[Any] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        data["status"] = self.status.value
        return _json_safe(data)


@dataclass
class UniquenessRecommendation:
    """Actionable recommendation produced by uniqueness checks."""

    recommendation_id: str
    recommendation_type: RecommendationType
    priority: Severity
    title: str
    description: str
    rule_name: Optional[str] = None
    columns: List[str] = field(default_factory=list)
    group_key: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["recommendation_type"] = self.recommendation_type.value
        data["priority"] = self.priority.value
        return _json_safe(data)


@dataclass
class UniquenessRuleResult:
    """Aggregated result for one uniqueness rule."""

    rule_name: str
    rule_type: UniquenessRuleType
    status: UniquenessStatus
    severity: Severity
    total_records: int
    evaluated_records: int
    unique_records: int
    duplicate_records: int
    duplicate_clusters: int
    skipped_records: int
    error_records: int
    uniqueness_score: float
    duplicate_rate: float
    threshold: UniquenessThreshold
    duration_ms: float
    key_columns: List[str] = field(default_factory=list)
    group_by: List[str] = field(default_factory=list)
    duplicate_cluster_samples: List[DuplicateCluster] = field(default_factory=list)
    findings: List[UniquenessFinding] = field(default_factory=list)
    recommendations: List[UniquenessRecommendation] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(
        self,
        *,
        include_clusters: bool = True,
        include_findings: bool = True,
        include_recommendations: bool = True,
    ) -> Dict[str, Any]:
        return {
            "rule_name": self.rule_name,
            "rule_type": self.rule_type.value,
            "status": self.status.value,
            "severity": self.severity.value,
            "total_records": self.total_records,
            "evaluated_records": self.evaluated_records,
            "unique_records": self.unique_records,
            "duplicate_records": self.duplicate_records,
            "duplicate_clusters": self.duplicate_clusters,
            "skipped_records": self.skipped_records,
            "error_records": self.error_records,
            "uniqueness_score": self.uniqueness_score,
            "duplicate_rate": self.duplicate_rate,
            "threshold": asdict(self.threshold),
            "duration_ms": self.duration_ms,
            "key_columns": self.key_columns,
            "group_by": self.group_by,
            "duplicate_cluster_samples": [c.to_dict() for c in self.duplicate_cluster_samples] if include_clusters else [],
            "findings": [f.to_dict() for f in self.findings] if include_findings else [],
            "recommendations": [r.to_dict() for r in self.recommendations] if include_recommendations else [],
            "metadata": _json_safe(self.metadata),
        }


@dataclass
class UniquenessReport:
    """Complete uniqueness checking report."""

    report_id: str
    dataset_name: str
    status: UniquenessStatus
    started_at: str
    finished_at: str
    duration_ms: float
    total_records: int
    overall_score: float
    weighted_score: float
    total_duplicate_records: int
    total_duplicate_clusters: int
    passed_rules: int
    warning_rules: int
    failed_rules: int
    skipped_rules: int
    error_rules: int
    rule_results: List[UniquenessRuleResult]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(
        self,
        *,
        include_clusters: bool = True,
        include_findings: bool = True,
        include_recommendations: bool = True,
    ) -> Dict[str, Any]:
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
            "total_duplicate_records": self.total_duplicate_records,
            "total_duplicate_clusters": self.total_duplicate_clusters,
            "passed_rules": self.passed_rules,
            "warning_rules": self.warning_rules,
            "failed_rules": self.failed_rules,
            "skipped_rules": self.skipped_rules,
            "error_rules": self.error_rules,
            "metadata": _json_safe(self.metadata),
            "rule_results": [
                result.to_dict(
                    include_clusters=include_clusters,
                    include_findings=include_findings,
                    include_recommendations=include_recommendations,
                )
                for result in self.rule_results
            ],
        }

    def to_json(
        self,
        *,
        include_clusters: bool = True,
        include_findings: bool = True,
        include_recommendations: bool = True,
        indent: int = 2,
    ) -> str:
        return json.dumps(
            self.to_dict(
                include_clusters=include_clusters,
                include_findings=include_findings,
                include_recommendations=include_recommendations,
            ),
            indent=indent,
            ensure_ascii=False,
        )

    def save_json(self, path: str | Path, indent: int = 2) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(self.to_json(indent=indent), encoding="utf-8")
        return output


# =============================================================================
# Helpers
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_pandas() -> None:
    if pd is None:
        raise DatasetValidationError(
            "pandas is required for UniquenessChecker. Install pandas or adapt the dataset adapter."
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


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if pd is not None:
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(_json_safe(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _dataset_fingerprint(df: "pd.DataFrame", limit: int = 10_000) -> str:
    try:
        sample = df.head(limit).to_json(orient="records", date_format="iso")
    except Exception:
        sample = repr(df.head(limit))[:1_000_000]
    return hashlib.sha256(sample.encode("utf-8", errors="ignore")).hexdigest()


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


def _normalize_value(value: Any, mode: NormalizationMode, custom_normalizer: Optional[CustomNormalizer] = None) -> Any:
    if _is_null(value):
        return "__NULL__"
    if mode == NormalizationMode.CUSTOM:
        if custom_normalizer is None:
            raise UniquenessConfigurationError("custom_normalizer is required for CUSTOM normalization.")
        return custom_normalizer(value)
    if mode == NormalizationMode.NONE:
        return _json_safe(value)
    text = str(value)
    if mode == NormalizationMode.BASIC:
        return text.strip()
    if mode == NormalizationMode.CASE_INSENSITIVE:
        return text.casefold()
    if mode == NormalizationMode.TRIM_CASE_INSENSITIVE:
        return text.strip().casefold()
    if mode == NormalizationMode.ALPHANUMERIC:
        return re.sub(r"[^a-zA-Z0-9]+", "", text).casefold()
    if mode == NormalizationMode.EMAIL:
        text = text.strip().casefold()
        local, sep, domain = text.partition("@")
        if sep:
            local = local.split("+", 1)[0]
            return f"{local}@{domain}"
        return text
    if mode == NormalizationMode.PHONE:
        digits = re.sub(r"\D+", "", text)
        return digits[-11:] if len(digits) > 11 else digits
    return _json_safe(value)


def _key_to_display(key: Any) -> Any:
    if isinstance(key, tuple):
        return list(key)
    return _json_safe(key)


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 8)


# =============================================================================
# Sinks
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
    """Simple audit sink useful for tests and local execution."""

    def __init__(self) -> None:
        self.events: List[Mapping[str, Any]] = []

    def write_event(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))


# =============================================================================
# Checker
# =============================================================================


class UniquenessChecker:
    """Enterprise uniqueness checker."""

    def __init__(
        self,
        rules: Sequence[UniquenessRule],
        *,
        metrics_sink: Optional[MetricsSink] = None,
        audit_sink: Optional[AuditSink] = None,
        max_findings_per_rule: int = 1_000,
        fail_fast: bool = False,
        global_threshold: UniquenessThreshold = UniquenessThreshold(min_score=0.95, warning_score=0.98, max_duplicate_records=None, max_duplicate_rate=None),
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self.rules = list(rules)
        self.metrics_sink = metrics_sink or NoopMetricsSink()
        self.audit_sink = audit_sink
        self.max_findings_per_rule = max_findings_per_rule
        self.fail_fast = fail_fast
        self.global_threshold = global_threshold
        self.logger = logger_ or logger
        self._validate_configuration()

    def _validate_configuration(self) -> None:
        if self.max_findings_per_rule < 0:
            raise UniquenessConfigurationError("max_findings_per_rule cannot be negative.")
        self.global_threshold.validate()
        seen = set()
        for rule in self.rules:
            rule.validate()
            if rule.name in seen:
                raise UniquenessConfigurationError(f"Duplicate rule name: {rule.name}")
            seen.add(rule.name)

    def run(
        self,
        dataset: Any,
        *,
        dataset_name: str = "dataset",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> UniquenessReport:
        """Run all enabled uniqueness rules against a dataset."""
        started = time.perf_counter()
        started_at = utc_now_iso()
        report_id = str(uuid.uuid4())
        metadata = dict(metadata or {})
        df = _as_dataframe(dataset)
        total_records = len(df)
        metadata.setdefault("dataset_fingerprint", _dataset_fingerprint(df))
        metadata.setdefault("columns", list(df.columns))

        self.metrics_sink.increment("data_quality.uniqueness.run.started", tags={"dataset": dataset_name})
        self.logger.info("Starting uniqueness check report_id=%s dataset=%s", report_id, dataset_name)

        results: List[UniquenessRuleResult] = []
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
                            "event_type": "uniqueness_rule_executed",
                            "report_id": report_id,
                            "dataset_name": dataset_name,
                            "timestamp": utc_now_iso(),
                            "rule_result": result.to_dict(include_clusters=False, include_findings=False),
                        }
                    )
                if self.fail_fast and result.status in {UniquenessStatus.FAILED, UniquenessStatus.ERROR}:
                    break
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("Uniqueness rule failed: %s", rule.name)
                result = self._error_result(rule, total_records, exc)
                results.append(result)
                self.metrics_sink.increment(
                    "data_quality.uniqueness.rule.error",
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
                    "event_type": "uniqueness_report_completed",
                    "report_id": report.report_id,
                    "dataset_name": report.dataset_name,
                    "timestamp": utc_now_iso(),
                    "report": report.to_dict(include_clusters=False, include_findings=False),
                }
            )
        return report

    def _execute_rule(self, df: "pd.DataFrame", rule: UniquenessRule) -> UniquenessRuleResult:
        started = time.perf_counter()
        self._validate_columns(df, rule)
        working_df = self._filter_condition(df, rule)
        clusters, skipped_records, error_records = self._build_duplicate_clusters(working_df, rule)

        evaluated_records = len(working_df) - skipped_records
        duplicate_records = sum(cluster.duplicate_count for cluster in clusters)
        duplicate_clusters = len(clusters)
        unique_records = max(0, evaluated_records - duplicate_records)
        duplicate_rate = _safe_rate(duplicate_records, evaluated_records)
        score = round(1.0 - duplicate_rate, 8) if evaluated_records > 0 else 1.0

        findings = self._build_findings(rule, clusters, evaluated_records, duplicate_records, duplicate_rate, error_records)
        recommendations = self._build_recommendations(rule, clusters, duplicate_records, duplicate_rate)
        status = self._status(rule.threshold, score, duplicate_records, duplicate_rate, duplicate_clusters, error_records)
        duration_ms = (time.perf_counter() - started) * 1000

        return UniquenessRuleResult(
            rule_name=rule.name,
            rule_type=rule.rule_type,
            status=status,
            severity=rule.severity,
            total_records=len(df),
            evaluated_records=evaluated_records,
            unique_records=unique_records,
            duplicate_records=duplicate_records,
            duplicate_clusters=duplicate_clusters,
            skipped_records=skipped_records,
            error_records=error_records,
            uniqueness_score=score,
            duplicate_rate=duplicate_rate,
            threshold=rule.threshold,
            duration_ms=duration_ms,
            key_columns=list(rule.key_columns),
            group_by=list(rule.group_by),
            duplicate_cluster_samples=clusters[: rule.max_duplicate_clusters_reported] if rule.include_duplicate_samples else [],
            findings=findings,
            recommendations=recommendations,
            metadata={"weight": rule.weight, **rule.metadata},
        )

    def _validate_columns(self, df: "pd.DataFrame", rule: UniquenessRule) -> None:
        if rule.rule_type == UniquenessRuleType.CUSTOM:
            required = set(rule.group_by) | set(rule.partition_by)
        else:
            required = set(rule.required_columns())
        missing = sorted(col for col in required if col not in df.columns)
        if missing:
            raise UniquenessExecutionError(f"Rule '{rule.name}' missing columns: {missing}")

    def _filter_condition(self, df: "pd.DataFrame", rule: UniquenessRule) -> "pd.DataFrame":
        if rule.condition is None:
            return df
        mask: List[bool] = []
        for _, row in df.iterrows():
            try:
                mask.append(bool(rule.condition(row.to_dict())))
            except Exception as exc:  # noqa: BLE001
                raise UniquenessExecutionError(f"Condition failed for rule '{rule.name}': {exc}") from exc
        return df.loc[mask]

    def _build_duplicate_clusters(
        self,
        df: "pd.DataFrame",
        rule: UniquenessRule,
    ) -> Tuple[List[DuplicateCluster], int, int]:
        skipped_records = 0
        error_records = 0
        buckets: Dict[Any, List[Any]] = defaultdict(list)
        group_values: Dict[Any, Any] = {}

        for idx, row in df.iterrows():
            try:
                key, should_skip, group_key = self._build_key(row.to_dict(), rule)
                if should_skip:
                    skipped_records += 1
                    continue
                bucket_key = (group_key, key) if group_key is not None else key
                buckets[bucket_key].append(idx)
                group_values[bucket_key] = group_key
            except Exception:
                error_records += 1

        clusters: List[DuplicateCluster] = []
        for bucket_key, indexes in buckets.items():
            if len(indexes) <= 1:
                continue
            group_key = group_values.get(bucket_key)
            key = bucket_key[1] if isinstance(bucket_key, tuple) and group_key is not None else bucket_key
            samples = []
            if rule.include_duplicate_samples and rule.max_rows_per_cluster_reported > 0:
                samples = [
                    _json_safe(df.loc[row_idx].to_dict())
                    for row_idx in indexes[: rule.max_rows_per_cluster_reported]
                ]
            clusters.append(
                DuplicateCluster(
                    cluster_id=str(uuid.uuid4()),
                    key=_key_to_display(key),
                    key_hash=_stable_hash(key),
                    duplicate_count=len(indexes) - 1,
                    row_count=len(indexes),
                    row_indexes=list(indexes[: rule.max_rows_per_cluster_reported]),
                    group_key=_key_to_display(group_key) if group_key is not None else None,
                    sample_records=samples,
                    metadata={"scope": DuplicateScope.GROUP.value if group_key is not None else DuplicateScope.DATASET.value},
                )
            )

        clusters.sort(key=lambda c: c.duplicate_count, reverse=True)
        return clusters, skipped_records, error_records

    def _build_key(self, row: Mapping[str, Any], rule: UniquenessRule) -> Tuple[Any, bool, Optional[Any]]:
        if rule.rule_type == UniquenessRuleType.CUSTOM:
            if rule.custom_key_function is None:
                raise UniquenessExecutionError("custom_key_function is required.")
            raw_key = rule.custom_key_function(row)
            key_values = raw_key if isinstance(raw_key, tuple) else (raw_key,)
        else:
            key_values = tuple(row.get(col) for col in rule.key_columns)

        null_flags = [_is_null(value) for value in key_values]
        if self._should_skip_by_nulls(rule.null_key_handling, null_flags):
            return None, True, None
        if rule.null_key_handling == NullKeyHandling.FAIL_NULL_KEYS and any(null_flags):
            # Treat each null-key row as sharing an explicit invalid key, so duplicates fail visibly.
            key_values = tuple("__NULL_KEY__" if _is_null(v) else v for v in key_values)

        normalized_key = tuple(
            _normalize_value(value, rule.normalization_mode, rule.custom_normalizer)
            for value in key_values
        )
        if len(normalized_key) == 1:
            normalized_key = (normalized_key[0],)

        group_key = None
        group_cols = list(rule.group_by or rule.partition_by)
        if group_cols:
            group_key = tuple(
                _normalize_value(row.get(col), rule.normalization_mode, rule.custom_normalizer)
                for col in group_cols
            )

        return normalized_key, False, group_key

    def _should_skip_by_nulls(self, handling: NullKeyHandling, null_flags: Sequence[bool]) -> bool:
        if not null_flags:
            return False
        if handling == NullKeyHandling.IGNORE_ROWS_WITH_ANY_NULL:
            return any(null_flags)
        if handling == NullKeyHandling.IGNORE_ROWS_WITH_ALL_NULL:
            return all(null_flags)
        if handling == NullKeyHandling.ALLOW_MULTIPLE_NULLS:
            return any(null_flags)
        return False

    def _build_findings(
        self,
        rule: UniquenessRule,
        clusters: Sequence[DuplicateCluster],
        evaluated_records: int,
        duplicate_records: int,
        duplicate_rate: float,
        error_records: int,
    ) -> List[UniquenessFinding]:
        findings: List[UniquenessFinding] = []
        if error_records > 0:
            findings.append(
                UniquenessFinding(
                    finding_id=str(uuid.uuid4()),
                    rule_name=rule.name,
                    severity=Severity.HIGH,
                    status=UniquenessStatus.ERROR,
                    message="Some records could not be evaluated for uniqueness.",
                    metadata={"error_records": error_records},
                )
            )

        if duplicate_records > 0:
            findings.append(
                UniquenessFinding(
                    finding_id=str(uuid.uuid4()),
                    rule_name=rule.name,
                    severity=rule.severity,
                    status=UniquenessStatus.FAILED,
                    message="Duplicate records detected for uniqueness rule.",
                    duplicate_count=duplicate_records,
                    duplicate_rate=duplicate_rate,
                    metadata={
                        "evaluated_records": evaluated_records,
                        "duplicate_clusters": len(clusters),
                        "key_columns": list(rule.key_columns),
                        "group_by": list(rule.group_by),
                    },
                )
            )

        for cluster in clusters[: max(0, self.max_findings_per_rule - len(findings))]:
            findings.append(
                UniquenessFinding(
                    finding_id=str(uuid.uuid4()),
                    rule_name=rule.name,
                    severity=rule.severity,
                    status=UniquenessStatus.FAILED,
                    message="Duplicate key cluster detected.",
                    key=cluster.key,
                    group_key=cluster.group_key,
                    duplicate_count=cluster.duplicate_count,
                    duplicate_rate=_safe_rate(cluster.duplicate_count, evaluated_records),
                    row_indexes=cluster.row_indexes,
                    metadata={"key_hash": cluster.key_hash, "row_count": cluster.row_count},
                )
            )
        return findings

    def _build_recommendations(
        self,
        rule: UniquenessRule,
        clusters: Sequence[DuplicateCluster],
        duplicate_records: int,
        duplicate_rate: float,
    ) -> List[UniquenessRecommendation]:
        recommendations: List[UniquenessRecommendation] = []
        if duplicate_records <= 0:
            return recommendations

        recommendations.append(
            UniquenessRecommendation(
                recommendation_id=str(uuid.uuid4()),
                recommendation_type=RecommendationType.DEDUPLICATE_RECORDS,
                priority=rule.severity,
                title="Deduplicate records for violated uniqueness rule",
                description=(
                    "Duplicate keys were detected. Review duplicate clusters, define deterministic survivorship rules, "
                    "and deduplicate before publishing trusted datasets."
                ),
                rule_name=rule.name,
                columns=list(rule.key_columns),
                metadata={"duplicate_records": duplicate_records, "duplicate_rate": duplicate_rate},
            )
        )

        if rule.severity in {Severity.HIGH, Severity.CRITICAL}:
            recommendations.append(
                UniquenessRecommendation(
                    recommendation_id=str(uuid.uuid4()),
                    recommendation_type=RecommendationType.ADD_UNIQUE_CONSTRAINT,
                    priority=Severity.HIGH,
                    title="Promote key into a data contract or database constraint",
                    description=(
                        "High-severity uniqueness failures should be prevented upstream through idempotent ingestion, "
                        "merge keys, unique constraints, or data contract validation."
                    ),
                    rule_name=rule.name,
                    columns=list(rule.key_columns),
                )
            )

        if rule.group_by and clusters:
            top_group = clusters[0].group_key
            recommendations.append(
                UniquenessRecommendation(
                    recommendation_id=str(uuid.uuid4()),
                    recommendation_type=RecommendationType.INVESTIGATE_GROUP,
                    priority=Severity.MEDIUM,
                    title="Investigate group-specific duplicate concentration",
                    description="Duplicates are present inside grouped uniqueness scopes. Review source partitions or group-specific workflows.",
                    rule_name=rule.name,
                    columns=list(rule.key_columns),
                    group_key=top_group,
                )
            )

        if duplicate_rate > 0 and duplicate_rate < 0.01:
            recommendations.append(
                UniquenessRecommendation(
                    recommendation_id=str(uuid.uuid4()),
                    recommendation_type=RecommendationType.MONITOR_DUPLICATE_RATE,
                    priority=Severity.LOW,
                    title="Monitor low duplicate rate trend",
                    description="Duplicate rate is currently low but non-zero. Add trend monitoring to prevent regression.",
                    rule_name=rule.name,
                    columns=list(rule.key_columns),
                )
            )
        return recommendations

    def _status(
        self,
        threshold: UniquenessThreshold,
        score: float,
        duplicate_records: int,
        duplicate_rate: float,
        duplicate_clusters: int,
        error_records: int,
    ) -> UniquenessStatus:
        if error_records > 0:
            return UniquenessStatus.ERROR
        if threshold.max_duplicate_records is not None and duplicate_records > threshold.max_duplicate_records:
            return UniquenessStatus.FAILED
        if threshold.max_duplicate_rate is not None and duplicate_rate > threshold.max_duplicate_rate:
            return UniquenessStatus.FAILED
        if threshold.max_duplicate_clusters is not None and duplicate_clusters > threshold.max_duplicate_clusters:
            return UniquenessStatus.FAILED
        if score < threshold.min_score:
            return UniquenessStatus.FAILED
        if score < threshold.warning_score:
            return UniquenessStatus.WARNING
        return UniquenessStatus.PASSED

    def _skipped_result(self, rule: UniquenessRule, total_records: int, reason: str) -> UniquenessRuleResult:
        return UniquenessRuleResult(
            rule_name=rule.name,
            rule_type=rule.rule_type,
            status=UniquenessStatus.SKIPPED,
            severity=rule.severity,
            total_records=total_records,
            evaluated_records=0,
            unique_records=0,
            duplicate_records=0,
            duplicate_clusters=0,
            skipped_records=total_records,
            error_records=0,
            uniqueness_score=1.0,
            duplicate_rate=0.0,
            threshold=rule.threshold,
            duration_ms=0.0,
            key_columns=list(rule.key_columns),
            group_by=list(rule.group_by),
            metadata={"reason": reason, "weight": rule.weight, **rule.metadata},
        )

    def _error_result(self, rule: UniquenessRule, total_records: int, exc: Exception) -> UniquenessRuleResult:
        return UniquenessRuleResult(
            rule_name=rule.name,
            rule_type=rule.rule_type,
            status=UniquenessStatus.ERROR,
            severity=rule.severity,
            total_records=total_records,
            evaluated_records=0,
            unique_records=0,
            duplicate_records=0,
            duplicate_clusters=0,
            skipped_records=0,
            error_records=total_records,
            uniqueness_score=0.0,
            duplicate_rate=1.0 if total_records else 0.0,
            threshold=rule.threshold,
            duration_ms=0.0,
            key_columns=list(rule.key_columns),
            group_by=list(rule.group_by),
            findings=[
                UniquenessFinding(
                    finding_id=str(uuid.uuid4()),
                    rule_name=rule.name,
                    severity=rule.severity,
                    status=UniquenessStatus.ERROR,
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
        results: Sequence[UniquenessRuleResult],
        metadata: Dict[str, Any],
    ) -> UniquenessReport:
        executable = [r for r in results if r.status != UniquenessStatus.SKIPPED]
        overall_score = round(sum(r.uniqueness_score for r in executable) / len(executable), 8) if executable else 1.0
        rule_weights = {rule.name: rule.weight for rule in self.rules}
        denominator = sum(rule_weights.get(r.rule_name, 1.0) for r in executable)
        weighted_score = round(
            sum(r.uniqueness_score * rule_weights.get(r.rule_name, 1.0) for r in executable) / denominator,
            8,
        ) if denominator > 0 else 1.0

        passed_rules = sum(1 for r in results if r.status == UniquenessStatus.PASSED)
        warning_rules = sum(1 for r in results if r.status == UniquenessStatus.WARNING)
        failed_rules = sum(1 for r in results if r.status == UniquenessStatus.FAILED)
        skipped_rules = sum(1 for r in results if r.status == UniquenessStatus.SKIPPED)
        error_rules = sum(1 for r in results if r.status == UniquenessStatus.ERROR)
        total_duplicate_records = sum(r.duplicate_records for r in results)
        total_duplicate_clusters = sum(r.duplicate_clusters for r in results)

        if error_rules:
            status = UniquenessStatus.ERROR
        elif failed_rules or self._status(self.global_threshold, weighted_score, total_duplicate_records, _safe_rate(total_duplicate_records, max(1, total_records)), total_duplicate_clusters, 0) == UniquenessStatus.FAILED:
            status = UniquenessStatus.FAILED
        elif warning_rules or weighted_score < self.global_threshold.warning_score:
            status = UniquenessStatus.WARNING
        else:
            status = UniquenessStatus.PASSED

        return UniquenessReport(
            report_id=report_id,
            dataset_name=dataset_name,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            total_records=total_records,
            overall_score=overall_score,
            weighted_score=weighted_score,
            total_duplicate_records=total_duplicate_records,
            total_duplicate_clusters=total_duplicate_clusters,
            passed_rules=passed_rules,
            warning_rules=warning_rules,
            failed_rules=failed_rules,
            skipped_rules=skipped_rules,
            error_rules=error_rules,
            rule_results=list(results),
            metadata=metadata,
        )

    def _publish_rule_metrics(self, dataset_name: str, result: UniquenessRuleResult) -> None:
        tags = {
            "dataset": dataset_name,
            "rule": result.rule_name,
            "rule_type": result.rule_type.value,
            "status": result.status.value,
            "severity": result.severity.value,
        }
        self.metrics_sink.gauge("data_quality.uniqueness.rule.score", result.uniqueness_score, tags=tags)
        self.metrics_sink.gauge("data_quality.uniqueness.rule.duplicate_rate", result.duplicate_rate, tags=tags)
        self.metrics_sink.gauge("data_quality.uniqueness.rule.duplicate_records", result.duplicate_records, tags=tags)
        self.metrics_sink.gauge("data_quality.uniqueness.rule.duplicate_clusters", result.duplicate_clusters, tags=tags)
        self.metrics_sink.timing("data_quality.uniqueness.rule.duration_ms", result.duration_ms, tags=tags)

    def _publish_report_metrics(self, report: UniquenessReport) -> None:
        tags = {"dataset": report.dataset_name, "status": report.status.value}
        self.metrics_sink.gauge("data_quality.uniqueness.report.weighted_score", report.weighted_score, tags=tags)
        self.metrics_sink.gauge("data_quality.uniqueness.report.overall_score", report.overall_score, tags=tags)
        self.metrics_sink.gauge("data_quality.uniqueness.report.duplicate_records", report.total_duplicate_records, tags=tags)
        self.metrics_sink.gauge("data_quality.uniqueness.report.duplicate_clusters", report.total_duplicate_clusters, tags=tags)
        self.metrics_sink.timing("data_quality.uniqueness.report.duration_ms", report.duration_ms, tags=tags)
        self.metrics_sink.increment("data_quality.uniqueness.run.completed", tags=tags)


# =============================================================================
# Rule Factory
# =============================================================================


class UniquenessRuleFactory:
    """Factory helpers for common uniqueness rules."""

    @staticmethod
    def unique_key(
        name: str,
        key_columns: Sequence[str],
        *,
        severity: Severity = Severity.CRITICAL,
        min_score: float = 1.0,
        max_duplicate_records: Optional[int] = 0,
        normalization_mode: NormalizationMode = NormalizationMode.NONE,
        null_key_handling: NullKeyHandling = NullKeyHandling.IGNORE_ROWS_WITH_ANY_NULL,
        weight: float = 2.0,
    ) -> UniquenessRule:
        return UniquenessRule(
            name=name,
            rule_type=UniquenessRuleType.UNIQUE_KEY if len(key_columns) == 1 else UniquenessRuleType.COMPOSITE_KEY,
            key_columns=list(key_columns),
            severity=severity,
            threshold=UniquenessThreshold(
                min_score=min_score,
                warning_score=min_score,
                max_duplicate_records=max_duplicate_records,
                max_duplicate_rate=None if max_duplicate_records is None else 0.0,
            ),
            normalization_mode=normalization_mode,
            null_key_handling=null_key_handling,
            weight=weight,
        )

    @staticmethod
    def group_scoped_unique(
        name: str,
        key_columns: Sequence[str],
        group_by: Sequence[str],
        *,
        severity: Severity = Severity.HIGH,
        max_duplicate_rate: float = 0.0,
        normalization_mode: NormalizationMode = NormalizationMode.NONE,
        weight: float = 1.5,
    ) -> UniquenessRule:
        return UniquenessRule(
            name=name,
            rule_type=UniquenessRuleType.GROUP_SCOPED_UNIQUE,
            key_columns=list(key_columns),
            group_by=list(group_by),
            severity=severity,
            threshold=UniquenessThreshold(
                min_score=1.0 - max_duplicate_rate,
                warning_score=1.0 - max_duplicate_rate,
                max_duplicate_records=None,
                max_duplicate_rate=max_duplicate_rate,
            ),
            normalization_mode=normalization_mode,
            weight=weight,
        )

    @staticmethod
    def duplicate_rate(
        name: str,
        key_columns: Sequence[str],
        *,
        max_duplicate_rate: float,
        warning_duplicate_rate: Optional[float] = None,
        severity: Severity = Severity.MEDIUM,
        normalization_mode: NormalizationMode = NormalizationMode.NONE,
        weight: float = 1.0,
    ) -> UniquenessRule:
        warning = warning_duplicate_rate if warning_duplicate_rate is not None else max_duplicate_rate
        return UniquenessRule(
            name=name,
            rule_type=UniquenessRuleType.DUPLICATE_RATE,
            key_columns=list(key_columns),
            severity=severity,
            threshold=UniquenessThreshold(
                min_score=1.0 - max_duplicate_rate,
                warning_score=1.0 - warning,
                max_duplicate_records=None,
                max_duplicate_rate=max_duplicate_rate,
            ),
            normalization_mode=normalization_mode,
            weight=weight,
        )

    @staticmethod
    def conditional_unique(
        name: str,
        key_columns: Sequence[str],
        condition: RowPredicate,
        *,
        severity: Severity = Severity.HIGH,
        max_duplicate_records: Optional[int] = 0,
        normalization_mode: NormalizationMode = NormalizationMode.NONE,
        weight: float = 1.5,
    ) -> UniquenessRule:
        return UniquenessRule(
            name=name,
            rule_type=UniquenessRuleType.CONDITIONAL_UNIQUE,
            key_columns=list(key_columns),
            condition=condition,
            severity=severity,
            threshold=UniquenessThreshold(max_duplicate_records=max_duplicate_records),
            normalization_mode=normalization_mode,
            weight=weight,
        )

    @staticmethod
    def approximate_duplicate(
        name: str,
        key_columns: Sequence[str],
        *,
        normalization_mode: NormalizationMode = NormalizationMode.ALPHANUMERIC,
        max_duplicate_rate: float = 0.0,
        severity: Severity = Severity.MEDIUM,
        weight: float = 1.0,
    ) -> UniquenessRule:
        return UniquenessRule(
            name=name,
            rule_type=UniquenessRuleType.APPROXIMATE_DUPLICATE,
            key_columns=list(key_columns),
            severity=severity,
            threshold=UniquenessThreshold(
                min_score=1.0 - max_duplicate_rate,
                warning_score=1.0 - max_duplicate_rate,
                max_duplicate_rate=max_duplicate_rate,
                max_duplicate_records=None,
            ),
            normalization_mode=normalization_mode,
            null_key_handling=NullKeyHandling.IGNORE_ROWS_WITH_ANY_NULL,
            weight=weight,
        )


# =============================================================================
# Convenience API
# =============================================================================


def check_uniqueness(
    dataset: Any,
    rules: Sequence[UniquenessRule],
    *,
    dataset_name: str = "dataset",
) -> UniquenessReport:
    """Convenience function for one-shot uniqueness checking."""
    return UniquenessChecker(rules).run(dataset, dataset_name=dataset_name)


# =============================================================================
# Local Smoke Example
# =============================================================================


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if pd is None:
        raise SystemExit("pandas is required to run the local example: pip install pandas")

    dataset = pd.DataFrame(
        [
            {"customer_id": 1, "email": "ana@example.com", "source": "erp", "document": "123"},
            {"customer_id": 2, "email": "bruno@example.com", "source": "erp", "document": "456"},
            {"customer_id": 2, "email": "bruno+promo@example.com", "source": "erp", "document": "456"},
            {"customer_id": 4, "email": " ANA@example.com ", "source": "crm", "document": "123"},
            {"customer_id": None, "email": "sem-id@example.com", "source": "crm", "document": None},
        ]
    )

    rules = [
        UniquenessRuleFactory.unique_key(
            "customer_id_unique",
            ["customer_id"],
            null_key_handling=NullKeyHandling.FAIL_NULL_KEYS,
        ),
        UniquenessRuleFactory.duplicate_rate(
            "email_duplicate_rate_normalized",
            ["email"],
            max_duplicate_rate=0.05,
            normalization_mode=NormalizationMode.EMAIL,
        ),
        UniquenessRuleFactory.group_scoped_unique(
            "document_unique_per_source",
            ["document"],
            group_by=["source"],
            max_duplicate_rate=0.0,
        ),
    ]

    checker = UniquenessChecker(rules, audit_sink=InMemoryAuditSink())
    report = checker.run(dataset, dataset_name="customers")
    print(report.to_json())

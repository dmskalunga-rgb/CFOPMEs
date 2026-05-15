"""
data/quality/null_analysis.py

Enterprise-grade Null Analysis module for data quality platforms.

This module profiles, scores, explains, and reports missing/null values across
enterprise datasets. It is designed to complement completeness checks by
providing deeper diagnostics about null patterns, structural gaps, correlated
missingness, group-level null behavior, and remediation recommendations.

Main capabilities:
- Column-level null profiling
- Row-level null density analysis
- Group/segment-level null analysis
- Null pattern mining
- Pairwise null correlation analysis
- Critical-column null risk scoring
- Threshold-based findings
- Remediation recommendations
- Audit-ready reports
- Metrics sink integration
- Pandas-native implementation with extension points

Typical use cases:
- Data quality observability
- Lakehouse quality profiling
- ETL/ELT quality gates
- ML feature readiness analysis
- Master-data quality diagnostics
- Regulatory/reporting dataset audits
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
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


class NullAnalysisError(Exception):
    """Base exception for null analysis failures."""


class NullAnalysisConfigurationError(NullAnalysisError):
    """Raised when configuration is invalid."""


class NullAnalysisExecutionError(NullAnalysisError):
    """Raised when analysis cannot be executed safely."""


class DatasetValidationError(NullAnalysisError):
    """Raised when input dataset is invalid or unsupported."""


# =============================================================================
# Enums
# =============================================================================


class Severity(str, Enum):
    """Severity level for null-analysis findings."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class NullAnalysisStatus(str, Enum):
    """Overall analysis status."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    ERROR = "error"


class NullSemantics(str, Enum):
    """Rules used to classify a value as null/missing."""

    STRICT_NULL_ONLY = "strict_null_only"
    NULL_AND_NAN = "null_and_nan"
    NULL_EMPTY_STRING = "null_empty_string"
    NULL_EMPTY_WHITESPACE = "null_empty_whitespace"
    NULL_EMPTY_ZERO = "null_empty_zero"
    NULL_EMPTY_COMMON_TOKENS = "null_empty_common_tokens"
    CUSTOM = "custom"


class NullRiskLevel(str, Enum):
    """Risk level for null density or null behavior."""

    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    SEVERE = "severe"


class RecommendationType(str, Enum):
    """Types of remediation recommendations."""

    INVESTIGATE_SOURCE = "investigate_source"
    ADD_VALIDATION_RULE = "add_validation_rule"
    BACKFILL_VALUES = "backfill_values"
    IMPUTE_VALUES = "impute_values"
    EXCLUDE_COLUMN = "exclude_column"
    REVIEW_BUSINESS_PROCESS = "review_business_process"
    MONITOR_SEGMENT = "monitor_segment"
    ENFORCE_CONTRACT = "enforce_contract"


# =============================================================================
# Protocols
# =============================================================================


class MetricsSink(Protocol):
    """Optional sink for publishing analysis metrics."""

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


MissingValueFunction = Callable[[Any], bool]


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class NullThresholds:
    """Thresholds used to classify null risk and report status."""

    warning_null_rate: float = 0.05
    failure_null_rate: float = 0.20
    critical_null_rate: float = 0.50
    max_allowed_null_rate_for_critical_columns: float = 0.0
    warning_row_null_rate: float = 0.25
    failure_row_null_rate: float = 0.50
    high_correlation_threshold: float = 0.80

    def validate(self) -> None:
        values = [
            self.warning_null_rate,
            self.failure_null_rate,
            self.critical_null_rate,
            self.max_allowed_null_rate_for_critical_columns,
            self.warning_row_null_rate,
            self.failure_row_null_rate,
            self.high_correlation_threshold,
        ]
        if any(value < 0 or value > 1 for value in values):
            raise NullAnalysisConfigurationError("All threshold values must be between 0 and 1.")
        if self.warning_null_rate > self.failure_null_rate:
            raise NullAnalysisConfigurationError("warning_null_rate cannot exceed failure_null_rate.")
        if self.failure_null_rate > self.critical_null_rate:
            raise NullAnalysisConfigurationError("failure_null_rate cannot exceed critical_null_rate.")
        if self.warning_row_null_rate > self.failure_row_null_rate:
            raise NullAnalysisConfigurationError("warning_row_null_rate cannot exceed failure_row_null_rate.")


@dataclass(frozen=True)
class NullAnalysisConfig:
    """Configuration for enterprise null analysis."""

    columns: Optional[Sequence[str]] = None
    exclude_columns: Sequence[str] = field(default_factory=list)
    critical_columns: Sequence[str] = field(default_factory=list)
    group_by: Sequence[str] = field(default_factory=list)
    null_semantics: NullSemantics = NullSemantics.NULL_EMPTY_COMMON_TOKENS
    custom_missing_function: Optional[MissingValueFunction] = None
    common_null_tokens: Sequence[str] = field(
        default_factory=lambda: (
            "",
            " ",
            "null",
            "none",
            "nan",
            "na",
            "n/a",
            "not available",
            "unknown",
            "undefined",
            "-",
            "--",
            "sem informação",
            "não informado",
            "nao informado",
        )
    )
    thresholds: NullThresholds = field(default_factory=NullThresholds)
    max_patterns: int = 50
    max_findings: int = 1_000
    max_recommendations: int = 100
    compute_pairwise_correlation: bool = True
    compute_group_profile: bool = True
    sample_size_for_examples: int = 5

    def validate(self) -> None:
        self.thresholds.validate()
        if self.max_patterns < 1:
            raise NullAnalysisConfigurationError("max_patterns must be at least 1.")
        if self.max_findings < 0:
            raise NullAnalysisConfigurationError("max_findings cannot be negative.")
        if self.max_recommendations < 0:
            raise NullAnalysisConfigurationError("max_recommendations cannot be negative.")
        if self.sample_size_for_examples < 0:
            raise NullAnalysisConfigurationError("sample_size_for_examples cannot be negative.")
        if self.null_semantics == NullSemantics.CUSTOM and self.custom_missing_function is None:
            raise NullAnalysisConfigurationError(
                "custom_missing_function is required when null_semantics is CUSTOM."
            )


@dataclass
class ColumnNullProfile:
    """Column-level null profile."""

    column: str
    dtype: str
    total_records: int
    null_count: int
    non_null_count: int
    null_rate: float
    distinct_non_null_count: int
    risk_level: NullRiskLevel
    is_critical: bool = False
    sample_null_row_indexes: List[Any] = field(default_factory=list)
    sample_non_null_values: List[Any] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["risk_level"] = self.risk_level.value
        return _json_safe(data)


@dataclass
class RowNullProfile:
    """Row-level null profile summary."""

    total_rows: int
    total_columns_analyzed: int
    rows_with_any_null: int
    rows_with_all_null: int
    average_nulls_per_row: float
    median_nulls_per_row: float
    max_nulls_in_row: int
    max_row_null_rate: float
    high_null_density_rows: int
    failed_null_density_rows: int
    top_rows_by_null_count: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class NullPattern:
    """Recurring null pattern across rows."""

    pattern_id: str
    missing_columns: List[str]
    present_columns: List[str]
    row_count: int
    row_rate: float
    sample_row_indexes: List[Any] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class GroupNullProfile:
    """Group/segment-level null profile."""

    group_key: Any
    group_size: int
    null_count: int
    possible_values: int
    null_rate: float
    highest_null_columns: Dict[str, float] = field(default_factory=dict)
    risk_level: NullRiskLevel = NullRiskLevel.NONE

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["risk_level"] = self.risk_level.value
        return _json_safe(data)


@dataclass
class PairwiseNullCorrelation:
    """Pairwise null co-occurrence/correlation profile."""

    left_column: str
    right_column: str
    both_null_count: int
    either_null_count: int
    left_null_count: int
    right_null_count: int
    jaccard_similarity: float
    phi_correlation: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class NullFinding:
    """A diagnostic finding produced by null analysis."""

    finding_id: str
    severity: Severity
    message: str
    column: Optional[str] = None
    group_key: Optional[Any] = None
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    threshold: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        return _json_safe(data)


@dataclass
class NullRecommendation:
    """Actionable remediation recommendation."""

    recommendation_id: str
    recommendation_type: RecommendationType
    priority: Severity
    title: str
    description: str
    columns: List[str] = field(default_factory=list)
    group_key: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["recommendation_type"] = self.recommendation_type.value
        data["priority"] = self.priority.value
        return _json_safe(data)


@dataclass
class NullAnalysisReport:
    """Complete null analysis report."""

    report_id: str
    dataset_name: str
    status: NullAnalysisStatus
    started_at: str
    finished_at: str
    duration_ms: float
    total_records: int
    total_columns: int
    analyzed_columns: List[str]
    overall_null_count: int
    overall_possible_values: int
    overall_null_rate: float
    critical_column_null_rate: float
    column_profiles: List[ColumnNullProfile]
    row_profile: RowNullProfile
    null_patterns: List[NullPattern]
    group_profiles: List[GroupNullProfile]
    pairwise_correlations: List[PairwiseNullCorrelation]
    findings: List[NullFinding]
    recommendations: List[NullRecommendation]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(
        self,
        *,
        include_patterns: bool = True,
        include_correlations: bool = True,
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
            "total_columns": self.total_columns,
            "analyzed_columns": self.analyzed_columns,
            "overall_null_count": self.overall_null_count,
            "overall_possible_values": self.overall_possible_values,
            "overall_null_rate": self.overall_null_rate,
            "critical_column_null_rate": self.critical_column_null_rate,
            "metadata": _json_safe(self.metadata),
            "column_profiles": [profile.to_dict() for profile in self.column_profiles],
            "row_profile": self.row_profile.to_dict(),
            "null_patterns": [p.to_dict() for p in self.null_patterns] if include_patterns else [],
            "group_profiles": [g.to_dict() for g in self.group_profiles],
            "pairwise_correlations": (
                [c.to_dict() for c in self.pairwise_correlations] if include_correlations else []
            ),
            "findings": [f.to_dict() for f in self.findings] if include_findings else [],
            "recommendations": (
                [r.to_dict() for r in self.recommendations] if include_recommendations else []
            ),
        }

    def to_json(
        self,
        *,
        include_patterns: bool = True,
        include_correlations: bool = True,
        include_findings: bool = True,
        include_recommendations: bool = True,
        indent: int = 2,
    ) -> str:
        return json.dumps(
            self.to_dict(
                include_patterns=include_patterns,
                include_correlations=include_correlations,
                include_findings=include_findings,
                include_recommendations=include_recommendations,
            ),
            indent=indent,
            ensure_ascii=False,
        )


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
            "pandas is required for NullAnalyzer. Install pandas or adapt the dataset adapter."
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


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 8)


def _risk_level(null_rate: float, thresholds: NullThresholds) -> NullRiskLevel:
    if null_rate <= 0:
        return NullRiskLevel.NONE
    if null_rate >= thresholds.critical_null_rate:
        return NullRiskLevel.SEVERE
    if null_rate >= thresholds.failure_null_rate:
        return NullRiskLevel.HIGH
    if null_rate >= thresholds.warning_null_rate:
        return NullRiskLevel.MODERATE
    return NullRiskLevel.LOW


def _severity_for_risk(risk: NullRiskLevel, *, critical: bool = False) -> Severity:
    if critical and risk != NullRiskLevel.NONE:
        return Severity.CRITICAL
    if risk == NullRiskLevel.SEVERE:
        return Severity.CRITICAL
    if risk == NullRiskLevel.HIGH:
        return Severity.HIGH
    if risk == NullRiskLevel.MODERATE:
        return Severity.MEDIUM
    if risk == NullRiskLevel.LOW:
        return Severity.LOW
    return Severity.INFO


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _phi_correlation(a: Sequence[bool], b: Sequence[bool]) -> Optional[float]:
    if len(a) != len(b) or not a:
        return None
    n11 = sum(1 for x, y in zip(a, b) if x and y)
    n10 = sum(1 for x, y in zip(a, b) if x and not y)
    n01 = sum(1 for x, y in zip(a, b) if not x and y)
    n00 = sum(1 for x, y in zip(a, b) if not x and not y)
    denominator = math.sqrt((n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00))
    if denominator == 0:
        return None
    return round(((n11 * n00) - (n10 * n01)) / denominator, 8)


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
    """Simple audit sink useful for tests, local execution, and debugging."""

    def __init__(self) -> None:
        self.events: List[Mapping[str, Any]] = []

    def write_event(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))


# =============================================================================
# Null Analyzer
# =============================================================================


class NullAnalyzer:
    """
    Enterprise null/missing-value analyzer.

    Example:
        analyzer = NullAnalyzer(
            NullAnalysisConfig(
                critical_columns=["customer_id", "document"],
                group_by=["source_system"],
            )
        )
        report = analyzer.analyze(dataset, dataset_name="customers")
    """

    def __init__(
        self,
        config: Optional[NullAnalysisConfig] = None,
        *,
        metrics_sink: Optional[MetricsSink] = None,
        audit_sink: Optional[AuditSink] = None,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or NullAnalysisConfig()
        self.metrics_sink = metrics_sink or NoopMetricsSink()
        self.audit_sink = audit_sink
        self.logger = logger_ or logger
        self.config.validate()

        self._common_null_tokens = {str(token).strip().casefold() for token in self.config.common_null_tokens}

    def analyze(
        self,
        dataset: Any,
        *,
        dataset_name: str = "dataset",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> NullAnalysisReport:
        """Run full null analysis against a dataset."""
        started_at = utc_now_iso()
        started = time.perf_counter()
        report_id = str(uuid.uuid4())
        metadata = dict(metadata or {})

        df = _as_dataframe(dataset)
        analyzed_columns = self._resolve_columns(df)
        self._validate_columns(df, analyzed_columns)

        metadata.setdefault("dataset_fingerprint", _dataset_fingerprint(df))
        metadata.setdefault("all_columns", list(df.columns))
        metadata.setdefault("null_semantics", self.config.null_semantics.value)

        self.logger.info("Starting null analysis report_id=%s dataset=%s", report_id, dataset_name)
        self.metrics_sink.increment("data_quality.null_analysis.run.started", tags={"dataset": dataset_name})

        try:
            null_mask = self._build_null_mask(df, analyzed_columns)
            column_profiles = self._profile_columns(df, null_mask, analyzed_columns)
            row_profile = self._profile_rows(null_mask)
            null_patterns = self._mine_null_patterns(null_mask)
            group_profiles = self._profile_groups(df, null_mask) if self.config.compute_group_profile else []
            pairwise = (
                self._compute_pairwise_correlations(null_mask)
                if self.config.compute_pairwise_correlation
                else []
            )
            findings = self._build_findings(column_profiles, row_profile, group_profiles, pairwise)
            recommendations = self._build_recommendations(column_profiles, row_profile, group_profiles, pairwise)

            total_records = len(df)
            total_columns = len(analyzed_columns)
            overall_possible_values = total_records * total_columns
            overall_null_count = int(null_mask.sum().sum()) if total_columns else 0
            overall_null_rate = _safe_rate(overall_null_count, overall_possible_values)
            critical_rate = self._critical_column_null_rate(column_profiles)
            status = self._determine_status(column_profiles, row_profile, findings)

            finished_at = utc_now_iso()
            duration_ms = (time.perf_counter() - started) * 1000

            report = NullAnalysisReport(
                report_id=report_id,
                dataset_name=dataset_name,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                total_records=total_records,
                total_columns=total_columns,
                analyzed_columns=analyzed_columns,
                overall_null_count=overall_null_count,
                overall_possible_values=overall_possible_values,
                overall_null_rate=overall_null_rate,
                critical_column_null_rate=critical_rate,
                column_profiles=column_profiles,
                row_profile=row_profile,
                null_patterns=null_patterns,
                group_profiles=group_profiles,
                pairwise_correlations=pairwise,
                findings=findings,
                recommendations=recommendations,
                metadata=metadata,
            )

            self._publish_metrics(report)
            self._write_audit(report)

            self.logger.info(
                "Completed null analysis report_id=%s dataset=%s status=%s null_rate=%.5f duration_ms=%.2f",
                report_id,
                dataset_name,
                report.status.value,
                report.overall_null_rate,
                duration_ms,
            )
            return report

        except Exception as exc:  # noqa: BLE001 - enterprise boundary handling
            self.logger.exception("Null analysis failed dataset=%s", dataset_name)
            self.metrics_sink.increment("data_quality.null_analysis.run.error", tags={"dataset": dataset_name})
            raise NullAnalysisExecutionError(str(exc)) from exc

    def _resolve_columns(self, df: "pd.DataFrame") -> List[str]:
        if self.config.columns is None:
            columns = list(df.columns)
        else:
            columns = list(self.config.columns)
        excluded = set(self.config.exclude_columns)
        return [column for column in columns if column not in excluded]

    def _validate_columns(self, df: "pd.DataFrame", analyzed_columns: Sequence[str]) -> None:
        missing = sorted(column for column in analyzed_columns if column not in df.columns)
        if missing:
            raise DatasetValidationError(f"Configured analysis columns are missing: {missing}")
        missing_groups = sorted(column for column in self.config.group_by if column not in df.columns)
        if missing_groups:
            raise DatasetValidationError(f"Configured group_by columns are missing: {missing_groups}")
        missing_critical = sorted(column for column in self.config.critical_columns if column not in df.columns)
        if missing_critical:
            raise DatasetValidationError(f"Configured critical columns are missing: {missing_critical}")

    def _is_missing(self, value: Any) -> bool:
        semantics = self.config.null_semantics

        if semantics == NullSemantics.CUSTOM:
            if self.config.custom_missing_function is None:
                raise NullAnalysisConfigurationError("custom_missing_function is required.")
            return bool(self.config.custom_missing_function(value))

        if value is None:
            return True
        if semantics in {
            NullSemantics.NULL_AND_NAN,
            NullSemantics.NULL_EMPTY_STRING,
            NullSemantics.NULL_EMPTY_WHITESPACE,
            NullSemantics.NULL_EMPTY_ZERO,
            NullSemantics.NULL_EMPTY_COMMON_TOKENS,
        }:
            if isinstance(value, float) and math.isnan(value):
                return True
            if pd is not None:
                try:
                    if pd.isna(value):
                        return True
                except Exception:
                    pass

        if semantics == NullSemantics.STRICT_NULL_ONLY:
            return False

        if isinstance(value, str):
            if semantics in {
                NullSemantics.NULL_EMPTY_STRING,
                NullSemantics.NULL_EMPTY_WHITESPACE,
                NullSemantics.NULL_EMPTY_ZERO,
                NullSemantics.NULL_EMPTY_COMMON_TOKENS,
            } and value == "":
                return True

            if semantics in {
                NullSemantics.NULL_EMPTY_WHITESPACE,
                NullSemantics.NULL_EMPTY_ZERO,
                NullSemantics.NULL_EMPTY_COMMON_TOKENS,
            } and value.strip() == "":
                return True

            if semantics == NullSemantics.NULL_EMPTY_COMMON_TOKENS:
                return value.strip().casefold() in self._common_null_tokens

        if semantics == NullSemantics.NULL_EMPTY_ZERO:
            if value == 0 or value == 0.0:
                return True
            if isinstance(value, str) and value.strip() in {"0", "0.0", "0,0"}:
                return True

        return False

    def _build_null_mask(self, df: "pd.DataFrame", columns: Sequence[str]) -> "pd.DataFrame":
        mask_data: Dict[str, List[bool]] = {}
        for column in columns:
            mask_data[column] = [self._is_missing(value) for value in df[column].tolist()]
        return pd.DataFrame(mask_data, index=df.index)

    def _profile_columns(
        self,
        df: "pd.DataFrame",
        null_mask: "pd.DataFrame",
        columns: Sequence[str],
    ) -> List[ColumnNullProfile]:
        profiles: List[ColumnNullProfile] = []
        total_records = len(df)
        critical = set(self.config.critical_columns)

        for column in columns:
            column_mask = null_mask[column]
            null_count = int(column_mask.sum())
            non_null_count = total_records - null_count
            null_rate = _safe_rate(null_count, total_records)
            non_null_series = df.loc[~column_mask, column]
            distinct_non_null_count = int(non_null_series.nunique(dropna=True)) if non_null_count else 0
            risk = _risk_level(null_rate, self.config.thresholds)

            sample_null_indexes = list(df.index[column_mask])[: self.config.sample_size_for_examples]
            sample_non_null_values = [
                _json_safe(value)
                for value in non_null_series.head(self.config.sample_size_for_examples).tolist()
            ]

            profiles.append(
                ColumnNullProfile(
                    column=column,
                    dtype=str(df[column].dtype),
                    total_records=total_records,
                    null_count=null_count,
                    non_null_count=non_null_count,
                    null_rate=null_rate,
                    distinct_non_null_count=distinct_non_null_count,
                    risk_level=risk,
                    is_critical=column in critical,
                    sample_null_row_indexes=sample_null_indexes,
                    sample_non_null_values=sample_non_null_values,
                )
            )

        return sorted(profiles, key=lambda profile: (profile.null_rate, profile.is_critical), reverse=True)

    def _profile_rows(self, null_mask: "pd.DataFrame") -> RowNullProfile:
        total_rows = len(null_mask)
        total_columns = len(null_mask.columns)

        if total_rows == 0 or total_columns == 0:
            return RowNullProfile(
                total_rows=total_rows,
                total_columns_analyzed=total_columns,
                rows_with_any_null=0,
                rows_with_all_null=0,
                average_nulls_per_row=0.0,
                median_nulls_per_row=0.0,
                max_nulls_in_row=0,
                max_row_null_rate=0.0,
                high_null_density_rows=0,
                failed_null_density_rows=0,
                top_rows_by_null_count=[],
            )

        row_counts = null_mask.sum(axis=1).astype(int)
        row_rates = row_counts / total_columns
        rows_with_any_null = int((row_counts > 0).sum())
        rows_with_all_null = int((row_counts == total_columns).sum())
        high_density = int((row_rates >= self.config.thresholds.warning_row_null_rate).sum())
        failed_density = int((row_rates >= self.config.thresholds.failure_row_null_rate).sum())

        top_rows: List[Dict[str, Any]] = []
        top_n = min(25, total_rows)
        for idx, count in row_counts.sort_values(ascending=False).head(top_n).items():
            missing_columns = list(null_mask.columns[null_mask.loc[idx].tolist()])
            top_rows.append(
                {
                    "row_index": idx,
                    "null_count": int(count),
                    "null_rate": round(float(count) / total_columns, 8),
                    "missing_columns": missing_columns,
                }
            )

        return RowNullProfile(
            total_rows=total_rows,
            total_columns_analyzed=total_columns,
            rows_with_any_null=rows_with_any_null,
            rows_with_all_null=rows_with_all_null,
            average_nulls_per_row=round(float(row_counts.mean()), 8),
            median_nulls_per_row=round(float(row_counts.median()), 8),
            max_nulls_in_row=int(row_counts.max()),
            max_row_null_rate=round(float(row_rates.max()), 8),
            high_null_density_rows=high_density,
            failed_null_density_rows=failed_density,
            top_rows_by_null_count=top_rows,
        )

    def _mine_null_patterns(self, null_mask: "pd.DataFrame") -> List[NullPattern]:
        if null_mask.empty:
            return []

        pattern_counter: Counter[Tuple[str, ...]] = Counter()
        sample_indexes: Dict[Tuple[str, ...], List[Any]] = defaultdict(list)
        columns = list(null_mask.columns)
        total_rows = len(null_mask)

        for idx, row in null_mask.iterrows():
            missing_columns = tuple(col for col in columns if bool(row[col]))
            pattern_counter[missing_columns] += 1
            if len(sample_indexes[missing_columns]) < self.config.sample_size_for_examples:
                sample_indexes[missing_columns].append(idx)

        patterns: List[NullPattern] = []
        for missing_columns, row_count in pattern_counter.most_common(self.config.max_patterns):
            present_columns = [col for col in columns if col not in set(missing_columns)]
            patterns.append(
                NullPattern(
                    pattern_id=_stable_id("pattern", missing_columns),
                    missing_columns=list(missing_columns),
                    present_columns=present_columns,
                    row_count=row_count,
                    row_rate=_safe_rate(row_count, total_rows),
                    sample_row_indexes=sample_indexes[missing_columns],
                )
            )
        return patterns

    def _profile_groups(self, df: "pd.DataFrame", null_mask: "pd.DataFrame") -> List[GroupNullProfile]:
        if not self.config.group_by:
            return []

        profiles: List[GroupNullProfile] = []
        group_by = list(self.config.group_by)
        grouped = df.groupby(group_by, dropna=False)
        columns = list(null_mask.columns)

        for group_key, group_df in grouped:
            group_indexes = group_df.index
            group_mask = null_mask.loc[group_indexes]
            possible_values = len(group_mask) * len(columns)
            null_count = int(group_mask.sum().sum())
            null_rate = _safe_rate(null_count, possible_values)

            column_rates = {
                column: _safe_rate(int(group_mask[column].sum()), len(group_mask))
                for column in columns
            }
            highest = dict(sorted(column_rates.items(), key=lambda item: item[1], reverse=True)[:10])
            risk = _risk_level(null_rate, self.config.thresholds)

            profiles.append(
                GroupNullProfile(
                    group_key=group_key,
                    group_size=len(group_mask),
                    null_count=null_count,
                    possible_values=possible_values,
                    null_rate=null_rate,
                    highest_null_columns=highest,
                    risk_level=risk,
                )
            )

        return sorted(profiles, key=lambda profile: profile.null_rate, reverse=True)

    def _compute_pairwise_correlations(self, null_mask: "pd.DataFrame") -> List[PairwiseNullCorrelation]:
        columns = list(null_mask.columns)
        correlations: List[PairwiseNullCorrelation] = []
        if len(columns) < 2:
            return correlations

        for i, left in enumerate(columns):
            left_values = [bool(v) for v in null_mask[left].tolist()]
            left_count = sum(left_values)
            for right in columns[i + 1 :]:
                right_values = [bool(v) for v in null_mask[right].tolist()]
                right_count = sum(right_values)
                both_null = sum(1 for a, b in zip(left_values, right_values) if a and b)
                either_null = sum(1 for a, b in zip(left_values, right_values) if a or b)
                jaccard = _safe_rate(both_null, either_null)
                phi = _phi_correlation(left_values, right_values)
                correlations.append(
                    PairwiseNullCorrelation(
                        left_column=left,
                        right_column=right,
                        both_null_count=both_null,
                        either_null_count=either_null,
                        left_null_count=left_count,
                        right_null_count=right_count,
                        jaccard_similarity=jaccard,
                        phi_correlation=phi,
                    )
                )

        return sorted(
            correlations,
            key=lambda item: (
                item.jaccard_similarity,
                abs(item.phi_correlation) if item.phi_correlation is not None else 0,
            ),
            reverse=True,
        )

    def _build_findings(
        self,
        column_profiles: Sequence[ColumnNullProfile],
        row_profile: RowNullProfile,
        group_profiles: Sequence[GroupNullProfile],
        pairwise_correlations: Sequence[PairwiseNullCorrelation],
    ) -> List[NullFinding]:
        findings: List[NullFinding] = []
        thresholds = self.config.thresholds

        def add(finding: NullFinding) -> None:
            if len(findings) < self.config.max_findings:
                findings.append(finding)

        for profile in column_profiles:
            if profile.is_critical and profile.null_rate > thresholds.max_allowed_null_rate_for_critical_columns:
                add(
                    NullFinding(
                        finding_id=_stable_id("finding", "critical_column", profile.column),
                        severity=Severity.CRITICAL,
                        message="Critical column contains null values above allowed threshold.",
                        column=profile.column,
                        metric_name="null_rate",
                        metric_value=profile.null_rate,
                        threshold=thresholds.max_allowed_null_rate_for_critical_columns,
                        metadata={"null_count": profile.null_count, "total_records": profile.total_records},
                    )
                )
            elif profile.null_rate >= thresholds.failure_null_rate:
                add(
                    NullFinding(
                        finding_id=_stable_id("finding", "column_failure", profile.column),
                        severity=_severity_for_risk(profile.risk_level),
                        message="Column null rate exceeds failure threshold.",
                        column=profile.column,
                        metric_name="null_rate",
                        metric_value=profile.null_rate,
                        threshold=thresholds.failure_null_rate,
                        metadata={"null_count": profile.null_count, "risk_level": profile.risk_level.value},
                    )
                )
            elif profile.null_rate >= thresholds.warning_null_rate:
                add(
                    NullFinding(
                        finding_id=_stable_id("finding", "column_warning", profile.column),
                        severity=Severity.MEDIUM,
                        message="Column null rate exceeds warning threshold.",
                        column=profile.column,
                        metric_name="null_rate",
                        metric_value=profile.null_rate,
                        threshold=thresholds.warning_null_rate,
                        metadata={"null_count": profile.null_count, "risk_level": profile.risk_level.value},
                    )
                )

        if row_profile.max_row_null_rate >= thresholds.failure_row_null_rate:
            add(
                NullFinding(
                    finding_id=_stable_id("finding", "row_density_failure"),
                    severity=Severity.HIGH,
                    message="One or more rows exceed failure null-density threshold.",
                    metric_name="max_row_null_rate",
                    metric_value=row_profile.max_row_null_rate,
                    threshold=thresholds.failure_row_null_rate,
                    metadata={"failed_rows": row_profile.failed_null_density_rows},
                )
            )
        elif row_profile.max_row_null_rate >= thresholds.warning_row_null_rate:
            add(
                NullFinding(
                    finding_id=_stable_id("finding", "row_density_warning"),
                    severity=Severity.MEDIUM,
                    message="One or more rows exceed warning null-density threshold.",
                    metric_name="max_row_null_rate",
                    metric_value=row_profile.max_row_null_rate,
                    threshold=thresholds.warning_row_null_rate,
                    metadata={"high_density_rows": row_profile.high_null_density_rows},
                )
            )

        for group in group_profiles[:50]:
            if group.null_rate >= thresholds.failure_null_rate:
                add(
                    NullFinding(
                        finding_id=_stable_id("finding", "group_failure", group.group_key),
                        severity=_severity_for_risk(group.risk_level),
                        message="Group/segment null rate exceeds failure threshold.",
                        group_key=group.group_key,
                        metric_name="group_null_rate",
                        metric_value=group.null_rate,
                        threshold=thresholds.failure_null_rate,
                        metadata={"group_size": group.group_size, "highest_null_columns": group.highest_null_columns},
                    )
                )

        for corr in pairwise_correlations[:100]:
            if corr.jaccard_similarity >= thresholds.high_correlation_threshold and corr.both_null_count > 0:
                add(
                    NullFinding(
                        finding_id=_stable_id("finding", "pairwise", corr.left_column, corr.right_column),
                        severity=Severity.MEDIUM,
                        message="Two columns show high null co-occurrence.",
                        metric_name="jaccard_similarity",
                        metric_value=corr.jaccard_similarity,
                        threshold=thresholds.high_correlation_threshold,
                        metadata={
                            "left_column": corr.left_column,
                            "right_column": corr.right_column,
                            "both_null_count": corr.both_null_count,
                            "phi_correlation": corr.phi_correlation,
                        },
                    )
                )

        return findings

    def _build_recommendations(
        self,
        column_profiles: Sequence[ColumnNullProfile],
        row_profile: RowNullProfile,
        group_profiles: Sequence[GroupNullProfile],
        pairwise_correlations: Sequence[PairwiseNullCorrelation],
    ) -> List[NullRecommendation]:
        recommendations: List[NullRecommendation] = []
        thresholds = self.config.thresholds

        def add(recommendation: NullRecommendation) -> None:
            if len(recommendations) < self.config.max_recommendations:
                recommendations.append(recommendation)

        critical_with_nulls = [p for p in column_profiles if p.is_critical and p.null_rate > 0]
        if critical_with_nulls:
            add(
                NullRecommendation(
                    recommendation_id=_stable_id("rec", "critical_contract", [p.column for p in critical_with_nulls]),
                    recommendation_type=RecommendationType.ENFORCE_CONTRACT,
                    priority=Severity.CRITICAL,
                    title="Enforce non-null contract for critical columns",
                    description=(
                        "Critical columns contain missing values. Add upstream contract validation, "
                        "reject invalid records, or backfill from trusted source systems."
                    ),
                    columns=[p.column for p in critical_with_nulls],
                    metadata={"max_allowed_null_rate": thresholds.max_allowed_null_rate_for_critical_columns},
                )
            )

        severe_columns = [p for p in column_profiles if p.risk_level == NullRiskLevel.SEVERE and not p.is_critical]
        if severe_columns:
            add(
                NullRecommendation(
                    recommendation_id=_stable_id("rec", "severe_columns", [p.column for p in severe_columns[:20]]),
                    recommendation_type=RecommendationType.INVESTIGATE_SOURCE,
                    priority=Severity.HIGH,
                    title="Investigate columns with severe missingness",
                    description=(
                        "Some columns have very high null rates. Confirm whether the fields are still "
                        "produced by the source system or whether schema drift/business process changes occurred."
                    ),
                    columns=[p.column for p in severe_columns[:20]],
                )
            )

        medium_columns = [
            p
            for p in column_profiles
            if thresholds.warning_null_rate <= p.null_rate < thresholds.failure_null_rate
        ]
        if medium_columns:
            add(
                NullRecommendation(
                    recommendation_id=_stable_id("rec", "monitor_columns", [p.column for p in medium_columns[:20]]),
                    recommendation_type=RecommendationType.ADD_VALIDATION_RULE,
                    priority=Severity.MEDIUM,
                    title="Add monitoring rules for moderate null-rate columns",
                    description=(
                        "Moderate missingness was detected. Add trend monitoring and alerts before "
                        "the null rate reaches failure thresholds."
                    ),
                    columns=[p.column for p in medium_columns[:20]],
                )
            )

        if row_profile.failed_null_density_rows > 0:
            add(
                NullRecommendation(
                    recommendation_id=_stable_id("rec", "row_density", row_profile.failed_null_density_rows),
                    recommendation_type=RecommendationType.REVIEW_BUSINESS_PROCESS,
                    priority=Severity.HIGH,
                    title="Review records with high null density",
                    description=(
                        "Rows with high missing-value density may represent incomplete extraction, "
                        "bad joins, optional workflow branches, or malformed source events."
                    ),
                    metadata={
                        "failed_null_density_rows": row_profile.failed_null_density_rows,
                        "max_row_null_rate": row_profile.max_row_null_rate,
                    },
                )
            )

        high_risk_groups = [g for g in group_profiles if g.risk_level in {NullRiskLevel.HIGH, NullRiskLevel.SEVERE}]
        if high_risk_groups:
            add(
                NullRecommendation(
                    recommendation_id=_stable_id("rec", "segments", [g.group_key for g in high_risk_groups[:10]]),
                    recommendation_type=RecommendationType.MONITOR_SEGMENT,
                    priority=Severity.HIGH,
                    title="Monitor high-null segments separately",
                    description=(
                        "Some groups/segments have significantly worse null behavior. Create segment-level "
                        "quality dashboards and investigate source-specific failures."
                    ),
                    group_key=[g.group_key for g in high_risk_groups[:10]],
                    metadata={"group_by": list(self.config.group_by)},
                )
            )

        strong_pairs = [
            c
            for c in pairwise_correlations
            if c.jaccard_similarity >= thresholds.high_correlation_threshold and c.both_null_count > 0
        ]
        if strong_pairs:
            columns = sorted(
                set(
                    col
                    for pair in strong_pairs[:20]
                    for col in [pair.left_column, pair.right_column]
                )
            )
            add(
                NullRecommendation(
                    recommendation_id=_stable_id("rec", "pairwise_missingness", columns),
                    recommendation_type=RecommendationType.INVESTIGATE_SOURCE,
                    priority=Severity.MEDIUM,
                    title="Investigate correlated missingness",
                    description=(
                        "Columns show strong null co-occurrence, which may indicate upstream join failures, "
                        "conditional capture logic, or source-system outages."
                    ),
                    columns=columns,
                    metadata={"pair_count": len(strong_pairs)},
                )
            )

        imputation_candidates = [
            p
            for p in column_profiles
            if 0 < p.null_rate < thresholds.failure_null_rate and p.distinct_non_null_count > 0
        ]
        if imputation_candidates:
            add(
                NullRecommendation(
                    recommendation_id=_stable_id("rec", "imputation", [p.column for p in imputation_candidates[:20]]),
                    recommendation_type=RecommendationType.IMPUTE_VALUES,
                    priority=Severity.LOW,
                    title="Evaluate safe imputation for low/moderate null columns",
                    description=(
                        "For analytics or ML use cases, consider documented imputation strategies for "
                        "non-critical fields with manageable null rates."
                    ),
                    columns=[p.column for p in imputation_candidates[:20]],
                )
            )

        return recommendations

    def _critical_column_null_rate(self, profiles: Sequence[ColumnNullProfile]) -> float:
        critical_profiles = [p for p in profiles if p.is_critical]
        if not critical_profiles:
            return 0.0
        nulls = sum(p.null_count for p in critical_profiles)
        possible = sum(p.total_records for p in critical_profiles)
        return _safe_rate(nulls, possible)

    def _determine_status(
        self,
        column_profiles: Sequence[ColumnNullProfile],
        row_profile: RowNullProfile,
        findings: Sequence[NullFinding],
    ) -> NullAnalysisStatus:
        if any(f.severity == Severity.CRITICAL for f in findings):
            return NullAnalysisStatus.FAILED
        if any(p.null_rate >= self.config.thresholds.failure_null_rate for p in column_profiles):
            return NullAnalysisStatus.FAILED
        if row_profile.failed_null_density_rows > 0:
            return NullAnalysisStatus.FAILED
        if any(f.severity in {Severity.HIGH, Severity.MEDIUM} for f in findings):
            return NullAnalysisStatus.WARNING
        return NullAnalysisStatus.PASSED

    def _publish_metrics(self, report: NullAnalysisReport) -> None:
        tags = {"dataset": report.dataset_name, "status": report.status.value}
        self.metrics_sink.gauge("data_quality.null_analysis.overall_null_rate", report.overall_null_rate, tags=tags)
        self.metrics_sink.gauge(
            "data_quality.null_analysis.critical_column_null_rate",
            report.critical_column_null_rate,
            tags=tags,
        )
        self.metrics_sink.gauge("data_quality.null_analysis.overall_null_count", report.overall_null_count, tags=tags)
        self.metrics_sink.gauge("data_quality.null_analysis.finding_count", len(report.findings), tags=tags)
        self.metrics_sink.timing("data_quality.null_analysis.duration_ms", report.duration_ms, tags=tags)
        self.metrics_sink.increment("data_quality.null_analysis.run.completed", tags=tags)

        for profile in report.column_profiles:
            column_tags = {**tags, "column": profile.column, "risk_level": profile.risk_level.value}
            self.metrics_sink.gauge("data_quality.null_analysis.column_null_rate", profile.null_rate, tags=column_tags)
            self.metrics_sink.gauge("data_quality.null_analysis.column_null_count", profile.null_count, tags=column_tags)

    def _write_audit(self, report: NullAnalysisReport) -> None:
        if not self.audit_sink:
            return
        self.audit_sink.write_event(
            {
                "event_type": "null_analysis_completed",
                "report_id": report.report_id,
                "dataset_name": report.dataset_name,
                "timestamp": utc_now_iso(),
                "report": report.to_dict(
                    include_patterns=False,
                    include_correlations=False,
                    include_findings=True,
                    include_recommendations=True,
                ),
            }
        )


# =============================================================================
# Convenience API
# =============================================================================


def analyze_nulls(
    dataset: Any,
    *,
    dataset_name: str = "dataset",
    critical_columns: Optional[Sequence[str]] = None,
    group_by: Optional[Sequence[str]] = None,
    columns: Optional[Sequence[str]] = None,
    exclude_columns: Optional[Sequence[str]] = None,
    null_semantics: NullSemantics = NullSemantics.NULL_EMPTY_COMMON_TOKENS,
) -> NullAnalysisReport:
    """Convenience function for one-shot null analysis."""
    config = NullAnalysisConfig(
        columns=columns,
        exclude_columns=list(exclude_columns or []),
        critical_columns=list(critical_columns or []),
        group_by=list(group_by or []),
        null_semantics=null_semantics,
    )
    return NullAnalyzer(config).analyze(dataset, dataset_name=dataset_name)


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
                "source_system": "erp",
                "name": "Ana",
                "email": "ana@example.com",
                "phone": "51999990000",
                "document": "123",
                "birth_date": "1990-01-01",
            },
            {
                "customer_id": 2,
                "source_system": "erp",
                "name": "Bruno",
                "email": "",
                "phone": None,
                "document": "456",
                "birth_date": "unknown",
            },
            {
                "customer_id": 3,
                "source_system": "crm",
                "name": "",
                "email": None,
                "phone": None,
                "document": None,
                "birth_date": None,
            },
            {
                "customer_id": 4,
                "source_system": "crm",
                "name": "Carla",
                "email": "nao informado",
                "phone": "",
                "document": "789",
                "birth_date": "1988-03-10",
            },
        ]
    )

    config = NullAnalysisConfig(
        critical_columns=["customer_id", "document"],
        group_by=["source_system"],
        thresholds=NullThresholds(
            warning_null_rate=0.10,
            failure_null_rate=0.30,
            critical_null_rate=0.60,
            max_allowed_null_rate_for_critical_columns=0.0,
            warning_row_null_rate=0.30,
            failure_row_null_rate=0.60,
            high_correlation_threshold=0.70,
        ),
    )

    analyzer = NullAnalyzer(config, audit_sink=InMemoryAuditSink())
    report = analyzer.analyze(dataset, dataset_name="customers")
    print(report.to_json(include_patterns=True, include_correlations=True))

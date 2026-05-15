"""
data/quality/profiling_engine.py

Enterprise-grade Data Profiling Engine.

This module provides a robust profiling foundation for enterprise data quality,
observability, governance, analytics readiness, and ML feature diagnostics.

Main capabilities:
- Dataset-level profiling
- Column-level statistical profiling
- Type/schema inference and validation hints
- Cardinality and uniqueness analysis
- Null/missing-value profiling
- Numeric distribution profiling
- Categorical distribution profiling
- Datetime range/frequency profiling
- Text length and token profiling
- Outlier detection using IQR and z-score
- Pairwise correlation profiling
- Quality score synthesis
- Audit-ready reports
- Metrics sink integration
- Pandas-native implementation with extension points

Designed for lakehouse quality gates, ETL/ELT validation, data observability,
data catalog enrichment, governance workflows, and ML feature readiness checks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import statistics
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

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


class ProfilingEngineError(Exception):
    """Base exception for profiling engine failures."""


class ProfilingConfigurationError(ProfilingEngineError):
    """Raised when profiling configuration is invalid."""


class ProfilingExecutionError(ProfilingEngineError):
    """Raised when profiling execution fails."""


class DatasetValidationError(ProfilingEngineError):
    """Raised when input dataset is invalid or unsupported."""


# =============================================================================
# Enums
# =============================================================================


class ProfileStatus(str, Enum):
    """Status of a profiling report."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    ERROR = "error"


class Severity(str, Enum):
    """Severity for profiling findings."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SemanticType(str, Enum):
    """Best-effort semantic type inference."""

    UNKNOWN = "unknown"
    IDENTIFIER = "identifier"
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    TEXT = "text"
    EMAIL = "email"
    URL = "url"
    PHONE = "phone"
    CURRENCY = "currency"
    PERCENTAGE = "percentage"


class ColumnRole(str, Enum):
    """Likely role of a column in a dataset."""

    PRIMARY_KEY_CANDIDATE = "primary_key_candidate"
    FOREIGN_KEY_CANDIDATE = "foreign_key_candidate"
    DIMENSION = "dimension"
    MEASURE = "measure"
    TIMESTAMP = "timestamp"
    FREE_TEXT = "free_text"
    FLAG = "flag"
    UNKNOWN = "unknown"


class DistributionShape(str, Enum):
    """Best-effort numeric distribution shape classification."""

    UNKNOWN = "unknown"
    CONSTANT = "constant"
    SPARSE = "sparse"
    APPROX_NORMAL = "approx_normal"
    RIGHT_SKEWED = "right_skewed"
    LEFT_SKEWED = "left_skewed"
    HEAVY_TAILED = "heavy_tailed"
    MULTIMODAL_CANDIDATE = "multimodal_candidate"


class MissingSemantics(str, Enum):
    """Rules used to classify missing values."""

    STRICT_NULL_ONLY = "strict_null_only"
    NULL_NAN_EMPTY = "null_nan_empty"
    COMMON_TOKENS = "common_tokens"
    CUSTOM = "custom"


# =============================================================================
# Protocols
# =============================================================================


class MetricsSink(Protocol):
    """Optional sink for publishing profiling metrics."""

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
# Config Models
# =============================================================================


@dataclass(frozen=True)
class ProfilingThresholds:
    """Thresholds for warnings, findings, and quality scoring."""

    high_null_rate: float = 0.20
    critical_null_rate: float = 0.50
    high_duplicate_rate: float = 0.10
    high_cardinality_rate: float = 0.90
    low_cardinality_max_distinct: int = 20
    outlier_rate_warning: float = 0.05
    outlier_rate_failure: float = 0.20
    strong_correlation_threshold: float = 0.85
    constant_column_distinct_count: int = 1

    def validate(self) -> None:
        rate_fields = [
            self.high_null_rate,
            self.critical_null_rate,
            self.high_duplicate_rate,
            self.high_cardinality_rate,
            self.outlier_rate_warning,
            self.outlier_rate_failure,
            self.strong_correlation_threshold,
        ]
        if any(value < 0 or value > 1 for value in rate_fields):
            raise ProfilingConfigurationError("All rate thresholds must be between 0 and 1.")
        if self.high_null_rate > self.critical_null_rate:
            raise ProfilingConfigurationError("high_null_rate cannot exceed critical_null_rate.")
        if self.outlier_rate_warning > self.outlier_rate_failure:
            raise ProfilingConfigurationError("outlier_rate_warning cannot exceed outlier_rate_failure.")
        if self.low_cardinality_max_distinct < 1:
            raise ProfilingConfigurationError("low_cardinality_max_distinct must be at least 1.")
        if self.constant_column_distinct_count < 1:
            raise ProfilingConfigurationError("constant_column_distinct_count must be at least 1.")


@dataclass(frozen=True)
class ProfilingConfig:
    """Configuration for the profiling engine."""

    columns: Optional[Sequence[str]] = None
    exclude_columns: Sequence[str] = field(default_factory=list)
    key_candidate_columns: Sequence[str] = field(default_factory=list)
    missing_semantics: MissingSemantics = MissingSemantics.COMMON_TOKENS
    custom_missing_function: Optional[MissingValueFunction] = None
    common_missing_tokens: Sequence[str] = field(
        default_factory=lambda: (
            "",
            " ",
            "null",
            "none",
            "nan",
            "na",
            "n/a",
            "unknown",
            "undefined",
            "not available",
            "sem informação",
            "não informado",
            "nao informado",
            "-",
            "--",
        )
    )
    thresholds: ProfilingThresholds = field(default_factory=ProfilingThresholds)
    top_n_values: int = 20
    sample_values: int = 10
    compute_correlations: bool = True
    compute_outliers: bool = True
    max_correlation_columns: int = 100
    max_findings: int = 1_000
    max_recommendations: int = 100

    def validate(self) -> None:
        self.thresholds.validate()
        if self.top_n_values < 1:
            raise ProfilingConfigurationError("top_n_values must be at least 1.")
        if self.sample_values < 0:
            raise ProfilingConfigurationError("sample_values cannot be negative.")
        if self.max_correlation_columns < 2:
            raise ProfilingConfigurationError("max_correlation_columns must be at least 2.")
        if self.max_findings < 0:
            raise ProfilingConfigurationError("max_findings cannot be negative.")
        if self.max_recommendations < 0:
            raise ProfilingConfigurationError("max_recommendations cannot be negative.")
        if self.missing_semantics == MissingSemantics.CUSTOM and self.custom_missing_function is None:
            raise ProfilingConfigurationError("custom_missing_function is required for CUSTOM missing semantics.")


# =============================================================================
# Profile Models
# =============================================================================


@dataclass
class NumericProfile:
    """Numeric distribution profile."""

    count: int
    mean: Optional[float]
    stddev: Optional[float]
    min: Optional[float]
    p01: Optional[float]
    p05: Optional[float]
    p25: Optional[float]
    median: Optional[float]
    p75: Optional[float]
    p95: Optional[float]
    p99: Optional[float]
    max: Optional[float]
    skewness: Optional[float]
    kurtosis: Optional[float]
    zero_count: int
    negative_count: int
    positive_count: int
    iqr: Optional[float]
    distribution_shape: DistributionShape
    outlier_count_iqr: int = 0
    outlier_rate_iqr: float = 0.0
    outlier_count_zscore: int = 0
    outlier_rate_zscore: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["distribution_shape"] = self.distribution_shape.value
        return _json_safe(data)


@dataclass
class CategoricalProfile:
    """Categorical/cardinality profile."""

    distinct_count: int
    cardinality_rate: float
    top_values: List[Dict[str, Any]] = field(default_factory=list)
    rare_value_count: int = 0
    rare_value_rate: float = 0.0
    entropy: Optional[float] = None
    mode: Optional[Any] = None
    mode_frequency: int = 0
    mode_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class DatetimeProfile:
    """Datetime profile."""

    count: int
    min: Optional[str]
    max: Optional[str]
    range_seconds: Optional[float]
    earliest_age_seconds: Optional[float]
    latest_age_seconds: Optional[float]
    inferred_frequency_hint: Optional[str]
    weekend_count: int
    future_count: int
    past_count: int

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class TextProfile:
    """Text/string profile."""

    count: int
    min_length: Optional[int]
    max_length: Optional[int]
    mean_length: Optional[float]
    median_length: Optional[float]
    empty_string_count: int
    whitespace_only_count: int
    numeric_string_count: int
    uppercase_count: int
    lowercase_count: int
    contains_digit_count: int
    contains_special_char_count: int
    sample_values: List[Any] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class ColumnProfile:
    """Full column profile."""

    column: str
    physical_dtype: str
    inferred_semantic_type: SemanticType
    inferred_role: ColumnRole
    total_records: int
    non_missing_count: int
    missing_count: int
    missing_rate: float
    distinct_count: int
    distinct_rate: float
    duplicate_count: int
    duplicate_rate: float
    constant: bool
    unique: bool
    nullable: bool
    quality_score: float
    numeric_profile: Optional[NumericProfile] = None
    categorical_profile: Optional[CategoricalProfile] = None
    datetime_profile: Optional[DatetimeProfile] = None
    text_profile: Optional[TextProfile] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["inferred_semantic_type"] = self.inferred_semantic_type.value
        data["inferred_role"] = self.inferred_role.value
        data["numeric_profile"] = self.numeric_profile.to_dict() if self.numeric_profile else None
        data["categorical_profile"] = self.categorical_profile.to_dict() if self.categorical_profile else None
        data["datetime_profile"] = self.datetime_profile.to_dict() if self.datetime_profile else None
        data["text_profile"] = self.text_profile.to_dict() if self.text_profile else None
        return _json_safe(data)


@dataclass
class CorrelationProfile:
    """Pairwise numeric correlation profile."""

    left_column: str
    right_column: str
    method: str
    correlation: float
    absolute_correlation: float
    sample_size: int

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class DatasetProfile:
    """Dataset-level profile."""

    row_count: int
    column_count: int
    analyzed_column_count: int
    total_cells: int
    missing_cells: int
    missing_rate: float
    duplicate_row_count: int
    duplicate_row_rate: float
    memory_usage_bytes: Optional[int]
    physical_schema: Dict[str, str]
    inferred_schema: Dict[str, str]
    key_candidates: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class ProfilingFinding:
    """Diagnostic finding from profiling."""

    finding_id: str
    severity: Severity
    message: str
    column: Optional[str] = None
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    threshold: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        return _json_safe(data)


@dataclass
class ProfilingRecommendation:
    """Actionable recommendation produced by profiling."""

    recommendation_id: str
    priority: Severity
    title: str
    description: str
    columns: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["priority"] = self.priority.value
        return _json_safe(data)


@dataclass
class ProfilingReport:
    """Complete profiling report."""

    report_id: str
    dataset_name: str
    status: ProfileStatus
    started_at: str
    finished_at: str
    duration_ms: float
    dataset_profile: DatasetProfile
    column_profiles: List[ColumnProfile]
    correlations: List[CorrelationProfile]
    findings: List[ProfilingFinding]
    recommendations: List[ProfilingRecommendation]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(
        self,
        *,
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
            "metadata": _json_safe(self.metadata),
            "dataset_profile": self.dataset_profile.to_dict(),
            "column_profiles": [profile.to_dict() for profile in self.column_profiles],
            "correlations": [c.to_dict() for c in self.correlations] if include_correlations else [],
            "findings": [f.to_dict() for f in self.findings] if include_findings else [],
            "recommendations": (
                [r.to_dict() for r in self.recommendations] if include_recommendations else []
            ),
        }

    def to_json(
        self,
        *,
        include_correlations: bool = True,
        include_findings: bool = True,
        include_recommendations: bool = True,
        indent: int = 2,
    ) -> str:
        return json.dumps(
            self.to_dict(
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
            "pandas is required for ProfilingEngine. Install pandas or adapt the dataset adapter."
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


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 8)


def _entropy(values: Sequence[Any]) -> Optional[float]:
    if not values:
        return None
    counts = Counter(values)
    total = len(values)
    entropy = 0.0
    for count in counts.values():
        p = count / total
        entropy -= p * math.log2(p)
    return round(entropy, 8)


def _to_datetime_series(series: "pd.Series") -> "pd.Series":
    return pd.to_datetime(series, errors="coerce", utc=True)


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
# Profiling Engine
# =============================================================================


class ProfilingEngine:
    """
    Enterprise data profiling engine.

    Example:
        engine = ProfilingEngine()
        report = engine.profile(dataset, dataset_name="customers")
    """

    def __init__(
        self,
        config: Optional[ProfilingConfig] = None,
        *,
        metrics_sink: Optional[MetricsSink] = None,
        audit_sink: Optional[AuditSink] = None,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or ProfilingConfig()
        self.metrics_sink = metrics_sink or NoopMetricsSink()
        self.audit_sink = audit_sink
        self.logger = logger_ or logger
        self.config.validate()
        self._missing_tokens = {str(token).strip().casefold() for token in self.config.common_missing_tokens}

    def profile(
        self,
        dataset: Any,
        *,
        dataset_name: str = "dataset",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProfilingReport:
        """Run full dataset profiling."""
        started_at = utc_now_iso()
        started = time.perf_counter()
        report_id = str(uuid.uuid4())
        metadata = dict(metadata or {})

        self.metrics_sink.increment("data_quality.profiling.run.started", tags={"dataset": dataset_name})
        self.logger.info("Starting profiling report_id=%s dataset=%s", report_id, dataset_name)

        try:
            df = _as_dataframe(dataset)
            columns = self._resolve_columns(df)
            self._validate_columns(df, columns)
            metadata.setdefault("dataset_fingerprint", _dataset_fingerprint(df))
            metadata.setdefault("profiling_config", self._config_summary())

            missing_mask = self._build_missing_mask(df, columns)
            column_profiles = [self._profile_column(df, missing_mask, column) for column in columns]
            dataset_profile = self._profile_dataset(df, columns, missing_mask, column_profiles)
            correlations = self._profile_correlations(df, column_profiles) if self.config.compute_correlations else []
            findings = self._build_findings(dataset_profile, column_profiles, correlations)
            recommendations = self._build_recommendations(dataset_profile, column_profiles, correlations, findings)
            status = self._determine_status(findings)

            finished_at = utc_now_iso()
            duration_ms = (time.perf_counter() - started) * 1000

            report = ProfilingReport(
                report_id=report_id,
                dataset_name=dataset_name,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                dataset_profile=dataset_profile,
                column_profiles=column_profiles,
                correlations=correlations,
                findings=findings,
                recommendations=recommendations,
                metadata=metadata,
            )

            self._publish_metrics(report)
            self._write_audit(report)
            self.logger.info(
                "Completed profiling report_id=%s dataset=%s status=%s duration_ms=%.2f",
                report_id,
                dataset_name,
                report.status.value,
                duration_ms,
            )
            return report

        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Profiling failed dataset=%s", dataset_name)
            self.metrics_sink.increment("data_quality.profiling.run.error", tags={"dataset": dataset_name})
            raise ProfilingExecutionError(str(exc)) from exc

    def _config_summary(self) -> Dict[str, Any]:
        return {
            "missing_semantics": self.config.missing_semantics.value,
            "compute_correlations": self.config.compute_correlations,
            "compute_outliers": self.config.compute_outliers,
            "thresholds": asdict(self.config.thresholds),
        }

    def _resolve_columns(self, df: "pd.DataFrame") -> List[str]:
        columns = list(df.columns) if self.config.columns is None else list(self.config.columns)
        excluded = set(self.config.exclude_columns)
        return [column for column in columns if column not in excluded]

    def _validate_columns(self, df: "pd.DataFrame", columns: Sequence[str]) -> None:
        missing = sorted(column for column in columns if column not in df.columns)
        if missing:
            raise DatasetValidationError(f"Configured profiling columns are missing: {missing}")

    def _is_missing(self, value: Any) -> bool:
        semantics = self.config.missing_semantics
        if semantics == MissingSemantics.CUSTOM:
            if self.config.custom_missing_function is None:
                raise ProfilingConfigurationError("custom_missing_function is required.")
            return bool(self.config.custom_missing_function(value))
        if value is None:
            return True
        if semantics in {MissingSemantics.NULL_NAN_EMPTY, MissingSemantics.COMMON_TOKENS}:
            if isinstance(value, float) and math.isnan(value):
                return True
            if pd is not None:
                try:
                    if pd.isna(value):
                        return True
                except Exception:
                    pass
        if semantics == MissingSemantics.STRICT_NULL_ONLY:
            return False
        if isinstance(value, str):
            if semantics == MissingSemantics.NULL_NAN_EMPTY and value.strip() == "":
                return True
            if semantics == MissingSemantics.COMMON_TOKENS:
                return value.strip().casefold() in self._missing_tokens
        return False

    def _build_missing_mask(self, df: "pd.DataFrame", columns: Sequence[str]) -> "pd.DataFrame":
        return pd.DataFrame(
            {column: [self._is_missing(value) for value in df[column].tolist()] for column in columns},
            index=df.index,
        )

    def _profile_dataset(
        self,
        df: "pd.DataFrame",
        columns: Sequence[str],
        missing_mask: "pd.DataFrame",
        column_profiles: Sequence[ColumnProfile],
    ) -> DatasetProfile:
        row_count = len(df)
        column_count = len(df.columns)
        analyzed_column_count = len(columns)
        total_cells = row_count * analyzed_column_count
        missing_cells = int(missing_mask.sum().sum()) if analyzed_column_count else 0
        duplicate_row_count = int(df.duplicated().sum()) if row_count else 0
        memory_usage = int(df.memory_usage(deep=True).sum()) if hasattr(df, "memory_usage") else None
        physical_schema = {column: str(df[column].dtype) for column in df.columns}
        inferred_schema = {profile.column: profile.inferred_semantic_type.value for profile in column_profiles}
        key_candidates = [
            profile.column
            for profile in column_profiles
            if profile.inferred_role == ColumnRole.PRIMARY_KEY_CANDIDATE
        ]
        return DatasetProfile(
            row_count=row_count,
            column_count=column_count,
            analyzed_column_count=analyzed_column_count,
            total_cells=total_cells,
            missing_cells=missing_cells,
            missing_rate=_safe_rate(missing_cells, total_cells),
            duplicate_row_count=duplicate_row_count,
            duplicate_row_rate=_safe_rate(duplicate_row_count, row_count),
            memory_usage_bytes=memory_usage,
            physical_schema=physical_schema,
            inferred_schema=inferred_schema,
            key_candidates=key_candidates,
        )

    def _profile_column(self, df: "pd.DataFrame", missing_mask: "pd.DataFrame", column: str) -> ColumnProfile:
        series = df[column]
        mask = missing_mask[column]
        non_missing = series.loc[~mask]
        total = len(series)
        missing_count = int(mask.sum())
        non_missing_count = total - missing_count
        distinct_count = int(non_missing.nunique(dropna=True)) if non_missing_count else 0
        missing_rate = _safe_rate(missing_count, total)
        distinct_rate = _safe_rate(distinct_count, non_missing_count)
        duplicate_count = max(0, non_missing_count - distinct_count)
        duplicate_rate = _safe_rate(duplicate_count, non_missing_count)
        constant = distinct_count <= self.config.thresholds.constant_column_distinct_count and non_missing_count > 0
        unique = distinct_count == non_missing_count and non_missing_count > 0

        semantic_type = self._infer_semantic_type(series, non_missing)
        role = self._infer_column_role(column, semantic_type, distinct_rate, unique, constant, non_missing_count)

        numeric_profile = self._profile_numeric(non_missing) if semantic_type in {SemanticType.NUMERIC, SemanticType.CURRENCY, SemanticType.PERCENTAGE} else None
        datetime_profile = self._profile_datetime(non_missing) if semantic_type == SemanticType.DATETIME else None
        text_profile = self._profile_text(non_missing) if semantic_type in {SemanticType.TEXT, SemanticType.EMAIL, SemanticType.URL, SemanticType.PHONE, SemanticType.IDENTIFIER, SemanticType.CATEGORICAL} else None
        categorical_profile = self._profile_categorical(non_missing) if semantic_type in {SemanticType.CATEGORICAL, SemanticType.BOOLEAN, SemanticType.IDENTIFIER, SemanticType.TEXT, SemanticType.EMAIL, SemanticType.URL, SemanticType.PHONE} else None

        warnings = self._column_warnings(missing_rate, duplicate_rate, constant, numeric_profile)
        quality_score = self._column_quality_score(missing_rate, duplicate_rate, constant, numeric_profile)

        return ColumnProfile(
            column=column,
            physical_dtype=str(series.dtype),
            inferred_semantic_type=semantic_type,
            inferred_role=role,
            total_records=total,
            non_missing_count=non_missing_count,
            missing_count=missing_count,
            missing_rate=missing_rate,
            distinct_count=distinct_count,
            distinct_rate=distinct_rate,
            duplicate_count=duplicate_count,
            duplicate_rate=duplicate_rate,
            constant=constant,
            unique=unique,
            nullable=missing_count > 0,
            quality_score=quality_score,
            numeric_profile=numeric_profile,
            categorical_profile=categorical_profile,
            datetime_profile=datetime_profile,
            text_profile=text_profile,
            warnings=warnings,
        )

    def _infer_semantic_type(self, series: "pd.Series", non_missing: "pd.Series") -> SemanticType:
        if len(non_missing) == 0:
            return SemanticType.UNKNOWN
        dtype = str(series.dtype).lower()
        column_name = str(series.name).lower()
        sample = non_missing.head(min(1000, len(non_missing)))

        if "bool" in dtype:
            return SemanticType.BOOLEAN
        if "datetime" in dtype or "date" in column_name or column_name.endswith("_at") or column_name.endswith("_dt"):
            parsed = _to_datetime_series(sample)
            if parsed.notna().mean() >= 0.80:
                return SemanticType.DATETIME
        if "int" in dtype or "float" in dtype or "decimal" in dtype:
            if any(token in column_name for token in ["price", "amount", "total", "cost", "revenue", "valor"]):
                return SemanticType.CURRENCY
            if any(token in column_name for token in ["percent", "pct", "rate", "ratio"]):
                return SemanticType.PERCENTAGE
            return SemanticType.NUMERIC

        as_str = sample.astype(str).str.strip()
        lower = as_str.str.casefold()
        if lower.isin(["true", "false", "yes", "no", "y", "n", "sim", "não", "nao", "0", "1"]).mean() >= 0.90:
            return SemanticType.BOOLEAN
        if as_str.str.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", na=False).mean() >= 0.80:
            return SemanticType.EMAIL
        if as_str.str.match(r"^https?://", na=False).mean() >= 0.80:
            return SemanticType.URL
        if as_str.str.replace(r"\D+", "", regex=True).str.len().between(8, 15).mean() >= 0.80 and "phone" in column_name:
            return SemanticType.PHONE
        numeric_parse = pd.to_numeric(as_str.str.replace(",", ".", regex=False), errors="coerce")
        if numeric_parse.notna().mean() >= 0.90:
            return SemanticType.NUMERIC
        parsed_dt = _to_datetime_series(as_str)
        if parsed_dt.notna().mean() >= 0.90:
            return SemanticType.DATETIME

        distinct_rate = _safe_rate(int(sample.nunique(dropna=True)), len(sample))
        avg_len = float(as_str.str.len().mean()) if len(as_str) else 0.0
        if any(token in column_name for token in ["id", "uuid", "key", "codigo", "code"]) and distinct_rate > 0.70:
            return SemanticType.IDENTIFIER
        if distinct_rate <= 0.20 or int(sample.nunique(dropna=True)) <= self.config.thresholds.low_cardinality_max_distinct:
            return SemanticType.CATEGORICAL
        if avg_len >= 30:
            return SemanticType.TEXT
        return SemanticType.CATEGORICAL

    def _infer_column_role(
        self,
        column: str,
        semantic_type: SemanticType,
        distinct_rate: float,
        unique: bool,
        constant: bool,
        non_missing_count: int,
    ) -> ColumnRole:
        name = column.lower()
        if constant:
            return ColumnRole.UNKNOWN
        if unique and non_missing_count > 0 and (
            column in self.config.key_candidate_columns
            or name.endswith("id")
            or name.endswith("_id")
            or "uuid" in name
            or "key" in name
        ):
            return ColumnRole.PRIMARY_KEY_CANDIDATE
        if semantic_type == SemanticType.DATETIME:
            return ColumnRole.TIMESTAMP
        if semantic_type == SemanticType.BOOLEAN:
            return ColumnRole.FLAG
        if semantic_type in {SemanticType.NUMERIC, SemanticType.CURRENCY, SemanticType.PERCENTAGE}:
            return ColumnRole.MEASURE
        if semantic_type == SemanticType.TEXT:
            return ColumnRole.FREE_TEXT
        if distinct_rate > self.config.thresholds.high_cardinality_rate and ("id" in name or "key" in name):
            return ColumnRole.FOREIGN_KEY_CANDIDATE
        if semantic_type in {SemanticType.CATEGORICAL, SemanticType.EMAIL, SemanticType.URL, SemanticType.PHONE, SemanticType.IDENTIFIER}:
            return ColumnRole.DIMENSION
        return ColumnRole.UNKNOWN

    def _profile_numeric(self, series: "pd.Series") -> Optional[NumericProfile]:
        numeric = pd.to_numeric(series, errors="coerce").dropna()
        if len(numeric) == 0:
            return None
        count = len(numeric)
        q = numeric.quantile([0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99])
        mean = float(numeric.mean())
        stddev = float(numeric.std(ddof=0)) if count > 1 else 0.0
        p25 = float(q.loc[0.25])
        p75 = float(q.loc[0.75])
        iqr = p75 - p25
        skewness = float(numeric.skew()) if count > 2 else None
        kurtosis = float(numeric.kurtosis()) if count > 3 else None
        zero_count = int((numeric == 0).sum())
        negative_count = int((numeric < 0).sum())
        positive_count = int((numeric > 0).sum())

        outlier_iqr_count = 0
        outlier_z_count = 0
        if self.config.compute_outliers:
            if iqr > 0:
                lower = p25 - 1.5 * iqr
                upper = p75 + 1.5 * iqr
                outlier_iqr_count = int(((numeric < lower) | (numeric > upper)).sum())
            if stddev > 0:
                z = (numeric - mean).abs() / stddev
                outlier_z_count = int((z > 3).sum())

        return NumericProfile(
            count=count,
            mean=round(mean, 8),
            stddev=round(stddev, 8),
            min=float(numeric.min()),
            p01=float(q.loc[0.01]),
            p05=float(q.loc[0.05]),
            p25=p25,
            median=float(q.loc[0.50]),
            p75=p75,
            p95=float(q.loc[0.95]),
            p99=float(q.loc[0.99]),
            max=float(numeric.max()),
            skewness=round(skewness, 8) if skewness is not None and not math.isnan(skewness) else None,
            kurtosis=round(kurtosis, 8) if kurtosis is not None and not math.isnan(kurtosis) else None,
            zero_count=zero_count,
            negative_count=negative_count,
            positive_count=positive_count,
            iqr=round(iqr, 8),
            distribution_shape=self._classify_distribution(count, stddev, skewness, kurtosis, zero_count),
            outlier_count_iqr=outlier_iqr_count,
            outlier_rate_iqr=_safe_rate(outlier_iqr_count, count),
            outlier_count_zscore=outlier_z_count,
            outlier_rate_zscore=_safe_rate(outlier_z_count, count),
        )

    def _classify_distribution(
        self,
        count: int,
        stddev: float,
        skewness: Optional[float],
        kurtosis: Optional[float],
        zero_count: int,
    ) -> DistributionShape:
        if count <= 1 or stddev == 0:
            return DistributionShape.CONSTANT
        if _safe_rate(zero_count, count) >= 0.80:
            return DistributionShape.SPARSE
        if skewness is not None:
            if skewness >= 1:
                return DistributionShape.RIGHT_SKEWED
            if skewness <= -1:
                return DistributionShape.LEFT_SKEWED
            if abs(skewness) < 0.5 and (kurtosis is None or abs(kurtosis) < 2):
                return DistributionShape.APPROX_NORMAL
        if kurtosis is not None and kurtosis > 5:
            return DistributionShape.HEAVY_TAILED
        return DistributionShape.UNKNOWN

    def _profile_categorical(self, series: "pd.Series") -> Optional[CategoricalProfile]:
        if len(series) == 0:
            return None
        values = [_json_safe(v) for v in series.tolist()]
        counts = Counter(values)
        total = len(values)
        distinct = len(counts)
        top = [
            {"value": value, "count": count, "rate": _safe_rate(count, total)}
            for value, count in counts.most_common(self.config.top_n_values)
        ]
        rare_count = sum(1 for count in counts.values() if count == 1)
        mode_value, mode_freq = counts.most_common(1)[0]
        return CategoricalProfile(
            distinct_count=distinct,
            cardinality_rate=_safe_rate(distinct, total),
            top_values=top,
            rare_value_count=rare_count,
            rare_value_rate=_safe_rate(rare_count, distinct),
            entropy=_entropy(values),
            mode=mode_value,
            mode_frequency=mode_freq,
            mode_rate=_safe_rate(mode_freq, total),
        )

    def _profile_datetime(self, series: "pd.Series") -> Optional[DatetimeProfile]:
        parsed = _to_datetime_series(series).dropna()
        if len(parsed) == 0:
            return None
        now = pd.Timestamp.now(tz="UTC")
        min_dt = parsed.min()
        max_dt = parsed.max()
        range_seconds = float((max_dt - min_dt).total_seconds())
        latest_age = float((now - max_dt).total_seconds())
        earliest_age = float((now - min_dt).total_seconds())
        weekend_count = int(parsed.dt.weekday.isin([5, 6]).sum())
        future_count = int((parsed > now).sum())
        past_count = int((parsed <= now).sum())
        return DatetimeProfile(
            count=len(parsed),
            min=min_dt.isoformat(),
            max=max_dt.isoformat(),
            range_seconds=range_seconds,
            earliest_age_seconds=earliest_age,
            latest_age_seconds=latest_age,
            inferred_frequency_hint=self._infer_datetime_frequency(parsed),
            weekend_count=weekend_count,
            future_count=future_count,
            past_count=past_count,
        )

    def _infer_datetime_frequency(self, parsed: "pd.Series") -> Optional[str]:
        if len(parsed) < 3:
            return None
        values = parsed.sort_values().drop_duplicates()
        if len(values) < 3:
            return None
        deltas = values.diff().dropna().dt.total_seconds()
        if len(deltas) == 0:
            return None
        median_delta = float(deltas.median())
        if median_delta <= 3600:
            return "sub_hourly_or_hourly"
        if median_delta <= 86400:
            return "daily_or_intraday"
        if median_delta <= 604800:
            return "weekly"
        if median_delta <= 2678400:
            return "monthly"
        return "irregular_or_sparse"

    def _profile_text(self, series: "pd.Series") -> Optional[TextProfile]:
        if len(series) == 0:
            return None
        text = series.astype(str)
        lengths = text.str.len()
        return TextProfile(
            count=len(text),
            min_length=int(lengths.min()) if len(lengths) else None,
            max_length=int(lengths.max()) if len(lengths) else None,
            mean_length=round(float(lengths.mean()), 8) if len(lengths) else None,
            median_length=round(float(lengths.median()), 8) if len(lengths) else None,
            empty_string_count=int((text == "").sum()),
            whitespace_only_count=int(text.str.match(r"^\s+$", na=False).sum()),
            numeric_string_count=int(text.str.match(r"^[+-]?\d+(?:[\.,]\d+)?$", na=False).sum()),
            uppercase_count=int((text == text.str.upper()).sum()),
            lowercase_count=int((text == text.str.lower()).sum()),
            contains_digit_count=int(text.str.contains(r"\d", regex=True, na=False).sum()),
            contains_special_char_count=int(text.str.contains(r"[^\w\s]", regex=True, na=False).sum()),
            sample_values=[_json_safe(v) for v in text.head(self.config.sample_values).tolist()],
        )

    def _column_warnings(
        self,
        missing_rate: float,
        duplicate_rate: float,
        constant: bool,
        numeric_profile: Optional[NumericProfile],
    ) -> List[str]:
        warnings: List[str] = []
        t = self.config.thresholds
        if missing_rate >= t.critical_null_rate:
            warnings.append("critical_missing_rate")
        elif missing_rate >= t.high_null_rate:
            warnings.append("high_missing_rate")
        if duplicate_rate >= t.high_duplicate_rate:
            warnings.append("high_duplicate_rate")
        if constant:
            warnings.append("constant_column")
        if numeric_profile:
            if numeric_profile.outlier_rate_iqr >= t.outlier_rate_failure:
                warnings.append("high_outlier_rate_iqr")
            elif numeric_profile.outlier_rate_iqr >= t.outlier_rate_warning:
                warnings.append("moderate_outlier_rate_iqr")
        return warnings

    def _column_quality_score(
        self,
        missing_rate: float,
        duplicate_rate: float,
        constant: bool,
        numeric_profile: Optional[NumericProfile],
    ) -> float:
        score = 1.0
        score -= min(0.60, missing_rate * 0.70)
        score -= min(0.20, duplicate_rate * 0.20)
        if constant:
            score -= 0.15
        if numeric_profile:
            score -= min(0.15, numeric_profile.outlier_rate_iqr * 0.30)
        return round(max(0.0, min(1.0, score)), 8)

    def _profile_correlations(self, df: "pd.DataFrame", column_profiles: Sequence[ColumnProfile]) -> List[CorrelationProfile]:
        numeric_columns = [
            p.column
            for p in column_profiles
            if p.inferred_semantic_type in {SemanticType.NUMERIC, SemanticType.CURRENCY, SemanticType.PERCENTAGE}
        ][: self.config.max_correlation_columns]
        if len(numeric_columns) < 2:
            return []
        numeric_df = df[numeric_columns].apply(pd.to_numeric, errors="coerce")
        corr = numeric_df.corr(method="pearson")
        profiles: List[CorrelationProfile] = []
        for i, left in enumerate(numeric_columns):
            for right in numeric_columns[i + 1 :]:
                value = corr.loc[left, right]
                if pd.isna(value):
                    continue
                sample_size = int(numeric_df[[left, right]].dropna().shape[0])
                profiles.append(
                    CorrelationProfile(
                        left_column=left,
                        right_column=right,
                        method="pearson",
                        correlation=round(float(value), 8),
                        absolute_correlation=round(abs(float(value)), 8),
                        sample_size=sample_size,
                    )
                )
        return sorted(profiles, key=lambda c: c.absolute_correlation, reverse=True)

    def _build_findings(
        self,
        dataset_profile: DatasetProfile,
        column_profiles: Sequence[ColumnProfile],
        correlations: Sequence[CorrelationProfile],
    ) -> List[ProfilingFinding]:
        findings: List[ProfilingFinding] = []
        t = self.config.thresholds

        def add(finding: ProfilingFinding) -> None:
            if len(findings) < self.config.max_findings:
                findings.append(finding)

        if dataset_profile.duplicate_row_rate >= t.high_duplicate_rate:
            add(
                ProfilingFinding(
                    finding_id=_stable_id("finding", "duplicate_rows"),
                    severity=Severity.HIGH,
                    message="Dataset duplicate row rate exceeds threshold.",
                    metric_name="duplicate_row_rate",
                    metric_value=dataset_profile.duplicate_row_rate,
                    threshold=t.high_duplicate_rate,
                    metadata={"duplicate_row_count": dataset_profile.duplicate_row_count},
                )
            )

        for profile in column_profiles:
            if profile.missing_rate >= t.critical_null_rate:
                add(
                    ProfilingFinding(
                        finding_id=_stable_id("finding", "critical_null", profile.column),
                        severity=Severity.CRITICAL,
                        message="Column has critical missing rate.",
                        column=profile.column,
                        metric_name="missing_rate",
                        metric_value=profile.missing_rate,
                        threshold=t.critical_null_rate,
                    )
                )
            elif profile.missing_rate >= t.high_null_rate:
                add(
                    ProfilingFinding(
                        finding_id=_stable_id("finding", "high_null", profile.column),
                        severity=Severity.HIGH,
                        message="Column has high missing rate.",
                        column=profile.column,
                        metric_name="missing_rate",
                        metric_value=profile.missing_rate,
                        threshold=t.high_null_rate,
                    )
                )

            if profile.constant:
                add(
                    ProfilingFinding(
                        finding_id=_stable_id("finding", "constant", profile.column),
                        severity=Severity.MEDIUM,
                        message="Column appears to be constant.",
                        column=profile.column,
                        metric_name="distinct_count",
                        metric_value=float(profile.distinct_count),
                        threshold=float(t.constant_column_distinct_count),
                    )
                )

            if profile.numeric_profile:
                outlier_rate = profile.numeric_profile.outlier_rate_iqr
                if outlier_rate >= t.outlier_rate_failure:
                    severity = Severity.HIGH
                elif outlier_rate >= t.outlier_rate_warning:
                    severity = Severity.MEDIUM
                else:
                    severity = None
                if severity:
                    add(
                        ProfilingFinding(
                            finding_id=_stable_id("finding", "outliers", profile.column),
                            severity=severity,
                            message="Numeric column has elevated outlier rate.",
                            column=profile.column,
                            metric_name="outlier_rate_iqr",
                            metric_value=outlier_rate,
                            threshold=t.outlier_rate_warning,
                            metadata={"outlier_count_iqr": profile.numeric_profile.outlier_count_iqr},
                        )
                    )

        for corr in correlations:
            if corr.absolute_correlation >= t.strong_correlation_threshold:
                add(
                    ProfilingFinding(
                        finding_id=_stable_id("finding", "correlation", corr.left_column, corr.right_column),
                        severity=Severity.MEDIUM,
                        message="Strong numeric correlation detected.",
                        metric_name="absolute_correlation",
                        metric_value=corr.absolute_correlation,
                        threshold=t.strong_correlation_threshold,
                        metadata={
                            "left_column": corr.left_column,
                            "right_column": corr.right_column,
                            "correlation": corr.correlation,
                            "sample_size": corr.sample_size,
                        },
                    )
                )

        return findings

    def _build_recommendations(
        self,
        dataset_profile: DatasetProfile,
        column_profiles: Sequence[ColumnProfile],
        correlations: Sequence[CorrelationProfile],
        findings: Sequence[ProfilingFinding],
    ) -> List[ProfilingRecommendation]:
        recommendations: List[ProfilingRecommendation] = []

        def add(rec: ProfilingRecommendation) -> None:
            if len(recommendations) < self.config.max_recommendations:
                recommendations.append(rec)

        high_null_columns = [p.column for p in column_profiles if p.missing_rate >= self.config.thresholds.high_null_rate]
        if high_null_columns:
            add(
                ProfilingRecommendation(
                    recommendation_id=_stable_id("rec", "missing", high_null_columns[:30]),
                    priority=Severity.HIGH,
                    title="Investigate high-missing columns",
                    description="Several columns have high missing rates. Validate upstream extraction, joins, and source contracts.",
                    columns=high_null_columns[:30],
                )
            )

        constant_columns = [p.column for p in column_profiles if p.constant]
        if constant_columns:
            add(
                ProfilingRecommendation(
                    recommendation_id=_stable_id("rec", "constant", constant_columns[:30]),
                    priority=Severity.MEDIUM,
                    title="Review constant columns",
                    description="Constant columns may be metadata, broken mappings, or candidates for exclusion from analytics/ML.",
                    columns=constant_columns[:30],
                )
            )

        outlier_columns = [
            p.column
            for p in column_profiles
            if p.numeric_profile and p.numeric_profile.outlier_rate_iqr >= self.config.thresholds.outlier_rate_warning
        ]
        if outlier_columns:
            add(
                ProfilingRecommendation(
                    recommendation_id=_stable_id("rec", "outliers", outlier_columns[:30]),
                    priority=Severity.MEDIUM,
                    title="Validate numeric outliers",
                    description="Numeric columns contain outliers. Confirm whether they are valid business extremes or data errors.",
                    columns=outlier_columns[:30],
                )
            )

        key_candidates = dataset_profile.key_candidates
        if key_candidates:
            add(
                ProfilingRecommendation(
                    recommendation_id=_stable_id("rec", "keys", key_candidates),
                    priority=Severity.LOW,
                    title="Promote key candidates into data contracts",
                    description="Unique identifier-like columns were detected. Consider declaring them as keys in schema/data contracts.",
                    columns=key_candidates,
                )
            )

        strong_corr = [c for c in correlations if c.absolute_correlation >= self.config.thresholds.strong_correlation_threshold]
        if strong_corr:
            corr_cols = sorted({col for c in strong_corr[:20] for col in [c.left_column, c.right_column]})
            add(
                ProfilingRecommendation(
                    recommendation_id=_stable_id("rec", "correlation", corr_cols),
                    priority=Severity.LOW,
                    title="Review strongly correlated measures",
                    description="Strongly correlated numeric fields may indicate redundant features, derived measures, or reconciliation opportunities.",
                    columns=corr_cols,
                    metadata={"pair_count": len(strong_corr)},
                )
            )

        if dataset_profile.duplicate_row_rate >= self.config.thresholds.high_duplicate_rate:
            add(
                ProfilingRecommendation(
                    recommendation_id=_stable_id("rec", "duplicate_rows"),
                    priority=Severity.HIGH,
                    title="Investigate duplicate rows",
                    description="Dataset duplicate row rate is high. Review extraction idempotency, merge keys, and deduplication rules.",
                    metadata={"duplicate_row_rate": dataset_profile.duplicate_row_rate},
                )
            )

        return recommendations

    def _determine_status(self, findings: Sequence[ProfilingFinding]) -> ProfileStatus:
        if any(f.severity == Severity.CRITICAL for f in findings):
            return ProfileStatus.FAILED
        if any(f.severity == Severity.HIGH for f in findings):
            return ProfileStatus.WARNING
        if any(f.severity == Severity.MEDIUM for f in findings):
            return ProfileStatus.WARNING
        return ProfileStatus.PASSED

    def _publish_metrics(self, report: ProfilingReport) -> None:
        tags = {"dataset": report.dataset_name, "status": report.status.value}
        dp = report.dataset_profile
        self.metrics_sink.gauge("data_quality.profiling.dataset_rows", dp.row_count, tags=tags)
        self.metrics_sink.gauge("data_quality.profiling.dataset_columns", dp.column_count, tags=tags)
        self.metrics_sink.gauge("data_quality.profiling.missing_rate", dp.missing_rate, tags=tags)
        self.metrics_sink.gauge("data_quality.profiling.duplicate_row_rate", dp.duplicate_row_rate, tags=tags)
        self.metrics_sink.gauge("data_quality.profiling.finding_count", len(report.findings), tags=tags)
        self.metrics_sink.timing("data_quality.profiling.duration_ms", report.duration_ms, tags=tags)
        self.metrics_sink.increment("data_quality.profiling.run.completed", tags=tags)

        for profile in report.column_profiles:
            column_tags = {
                **tags,
                "column": profile.column,
                "semantic_type": profile.inferred_semantic_type.value,
                "role": profile.inferred_role.value,
            }
            self.metrics_sink.gauge("data_quality.profiling.column_missing_rate", profile.missing_rate, tags=column_tags)
            self.metrics_sink.gauge("data_quality.profiling.column_distinct_rate", profile.distinct_rate, tags=column_tags)
            self.metrics_sink.gauge("data_quality.profiling.column_quality_score", profile.quality_score, tags=column_tags)

    def _write_audit(self, report: ProfilingReport) -> None:
        if not self.audit_sink:
            return
        self.audit_sink.write_event(
            {
                "event_type": "profiling_completed",
                "report_id": report.report_id,
                "dataset_name": report.dataset_name,
                "timestamp": utc_now_iso(),
                "report": report.to_dict(
                    include_correlations=False,
                    include_findings=True,
                    include_recommendations=True,
                ),
            }
        )


# =============================================================================
# Convenience API
# =============================================================================


def profile_dataset(
    dataset: Any,
    *,
    dataset_name: str = "dataset",
    columns: Optional[Sequence[str]] = None,
    exclude_columns: Optional[Sequence[str]] = None,
    compute_correlations: bool = True,
    compute_outliers: bool = True,
) -> ProfilingReport:
    """Convenience function for one-shot profiling."""
    config = ProfilingConfig(
        columns=columns,
        exclude_columns=list(exclude_columns or []),
        compute_correlations=compute_correlations,
        compute_outliers=compute_outliers,
    )
    return ProfilingEngine(config).profile(dataset, dataset_name=dataset_name)


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
                "name": "Ana Silva",
                "email": "ana@example.com",
                "source": "erp",
                "amount": 100.0,
                "discount": 10.0,
                "created_at": "2026-05-01T10:00:00Z",
                "active": True,
            },
            {
                "customer_id": 2,
                "name": "Bruno Souza",
                "email": "bruno@example.com",
                "source": "erp",
                "amount": 120.0,
                "discount": 12.0,
                "created_at": "2026-05-02T10:00:00Z",
                "active": True,
            },
            {
                "customer_id": 3,
                "name": "",
                "email": "nao informado",
                "source": "crm",
                "amount": 9999.0,
                "discount": 0.0,
                "created_at": "2026-05-03T10:00:00Z",
                "active": False,
            },
            {
                "customer_id": 4,
                "name": "Carla Lima",
                "email": "carla@example.com",
                "source": "crm",
                "amount": 80.0,
                "discount": 8.0,
                "created_at": "2026-05-04T10:00:00Z",
                "active": True,
            },
        ]
    )

    engine = ProfilingEngine(
        ProfilingConfig(
            key_candidate_columns=["customer_id"],
            thresholds=ProfilingThresholds(
                high_null_rate=0.10,
                critical_null_rate=0.40,
                high_duplicate_rate=0.10,
                outlier_rate_warning=0.10,
                outlier_rate_failure=0.30,
            ),
        ),
        audit_sink=InMemoryAuditSink(),
    )

    report = engine.profile(dataset, dataset_name="customers")
    print(report.to_json(include_correlations=True, include_findings=True, include_recommendations=True))

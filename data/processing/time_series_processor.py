"""
time_series_processor.py
========================

Enterprise-grade time series processing module for data pipelines.

Core capabilities
-----------------
- Time column parsing, timezone handling and index normalization.
- Frequency inference and resampling.
- Gap detection, missing timestamp generation and gap filling.
- Duplicate timestamp handling.
- Lag features, lead features, rolling statistics and expanding statistics.
- Calendar/date-part features.
- Outlier detection and treatment with configurable strategies.
- Stationarity-friendly transformations: diff, pct_change, log, z-score.
- Multi-entity/grouped time series processing.
- Data quality audit report with temporal health metrics.
- Forecasting-ready dataset generation.
- Optional pandas-first implementation with dependency-light public API.

This module is designed for production ETL/ELT, feature engineering and ML pipelines.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

try:
    import pandas as pd  # type: ignore
    import numpy as np  # type: ignore
except Exception as exc:  # pragma: no cover
    pd = None  # type: ignore
    np = None  # type: ignore
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]


class TimeSeriesProcessingError(Exception):
    """Base exception for time series processor failures."""


class MissingDependencyError(TimeSeriesProcessingError):
    """Raised when pandas/numpy are required but unavailable."""


class DuplicateTimestampPolicy(str, enum.Enum):
    KEEP_FIRST = "keep_first"
    KEEP_LAST = "keep_last"
    AGGREGATE = "aggregate"
    FAIL = "fail"


class GapFillStrategy(str, enum.Enum):
    NONE = "none"
    FORWARD_FILL = "ffill"
    BACKWARD_FILL = "bfill"
    ZERO = "zero"
    MEAN = "mean"
    MEDIAN = "median"
    INTERPOLATE = "interpolate"
    CONSTANT = "constant"


class OutlierMethod(str, enum.Enum):
    NONE = "none"
    ZSCORE = "zscore"
    IQR = "iqr"
    MAD = "mad"
    QUANTILE = "quantile"


class OutlierTreatment(str, enum.Enum):
    FLAG_ONLY = "flag_only"
    REMOVE = "remove"
    CLIP = "clip"
    NULLIFY = "nullify"


class ResampleAggregation(str, enum.Enum):
    SUM = "sum"
    MEAN = "mean"
    MEDIAN = "median"
    MIN = "min"
    MAX = "max"
    FIRST = "first"
    LAST = "last"
    COUNT = "count"


@dataclass(frozen=True)
class OutlierConfig:
    method: OutlierMethod = OutlierMethod.NONE
    treatment: OutlierTreatment = OutlierTreatment.FLAG_ONLY
    zscore_threshold: float = 3.0
    iqr_multiplier: float = 1.5
    mad_threshold: float = 3.5
    lower_quantile: float = 0.01
    upper_quantile: float = 0.99
    columns: Sequence[str] = field(default_factory=list)


@dataclass(frozen=True)
class RollingFeatureConfig:
    windows: Sequence[int] = field(default_factory=lambda: (3, 7, 14, 30))
    stats: Sequence[str] = field(default_factory=lambda: ("mean", "std", "min", "max"))
    columns: Sequence[str] = field(default_factory=list)
    min_periods: int = 1


@dataclass(frozen=True)
class LagFeatureConfig:
    lags: Sequence[int] = field(default_factory=lambda: (1, 2, 3, 7, 14))
    leads: Sequence[int] = field(default_factory=tuple)
    columns: Sequence[str] = field(default_factory=list)


@dataclass(frozen=True)
class CalendarFeatureConfig:
    enabled: bool = True
    include_cyclical: bool = True
    include_weekend: bool = True
    include_quarter: bool = True
    include_month_start_end: bool = True


@dataclass(frozen=True)
class TransformConfig:
    add_diff: bool = False
    diff_periods: Sequence[int] = field(default_factory=lambda: (1,))
    add_pct_change: bool = False
    pct_periods: Sequence[int] = field(default_factory=lambda: (1,))
    add_log: bool = False
    add_zscore: bool = False
    columns: Sequence[str] = field(default_factory=list)


@dataclass(frozen=True)
class TimeSeriesProcessorConfig:
    time_column: str
    value_columns: Sequence[str]
    entity_columns: Sequence[str] = field(default_factory=tuple)
    timezone: Optional[str] = "UTC"
    target_frequency: Optional[str] = None
    infer_frequency: bool = True
    resample: bool = False
    resample_aggregation: Union[ResampleAggregation, Mapping[str, str]] = ResampleAggregation.MEAN
    duplicate_policy: DuplicateTimestampPolicy = DuplicateTimestampPolicy.AGGREGATE
    gap_fill_strategy: GapFillStrategy = GapFillStrategy.NONE
    gap_fill_constant: Any = None
    sort: bool = True
    enforce_monotonic: bool = True
    calendar_features: CalendarFeatureConfig = field(default_factory=CalendarFeatureConfig)
    lag_features: LagFeatureConfig = field(default_factory=LagFeatureConfig)
    rolling_features: RollingFeatureConfig = field(default_factory=RollingFeatureConfig)
    transforms: TransformConfig = field(default_factory=TransformConfig)
    outliers: OutlierConfig = field(default_factory=OutlierConfig)
    drop_original_index: bool = True
    metadata: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.time_column:
            raise ValueError("time_column is required")
        if not self.value_columns:
            raise ValueError("value_columns cannot be empty")


@dataclass
class TimeSeriesAuditReport:
    processor_name: str = "time_series_processor"
    total_rows_input: int = 0
    total_rows_output: int = 0
    duplicate_timestamps: int = 0
    missing_timestamps_added: int = 0
    null_counts_before: JsonDict = field(default_factory=dict)
    null_counts_after: JsonDict = field(default_factory=dict)
    inferred_frequency: Optional[str] = None
    target_frequency: Optional[str] = None
    entity_count: int = 1
    outlier_counts: JsonDict = field(default_factory=dict)
    generated_features: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def duration_ms(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at) * 1000, 3)

    def finish(self) -> None:
        self.finished_at = time.time()

    def to_dict(self) -> JsonDict:
        return {
            "processor_name": self.processor_name,
            "total_rows_input": self.total_rows_input,
            "total_rows_output": self.total_rows_output,
            "duplicate_timestamps": self.duplicate_timestamps,
            "missing_timestamps_added": self.missing_timestamps_added,
            "null_counts_before": dict(self.null_counts_before),
            "null_counts_after": dict(self.null_counts_after),
            "inferred_frequency": self.inferred_frequency,
            "target_frequency": self.target_frequency,
            "entity_count": self.entity_count,
            "outlier_counts": dict(self.outlier_counts),
            "generated_features": list(self.generated_features),
            "warnings": list(self.warnings),
            "duration_ms": self.duration_ms,
        }


@dataclass
class TimeSeriesProcessingResult:
    dataframe: Any
    audit_report: TimeSeriesAuditReport

    def to_dict(self) -> JsonDict:
        return {
            "audit_report": self.audit_report.to_dict(),
            "rows": int(len(self.dataframe)),
            "columns": list(self.dataframe.columns),
        }


def _require_pandas() -> None:
    if pd is None or np is None:
        raise MissingDependencyError(f"pandas and numpy are required. Original import error: {_IMPORT_ERROR}")


def _as_list(value: Union[str, Sequence[str]]) -> List[str]:
    if isinstance(value, str):
        return [value]
    return list(value)


class TimeSeriesProcessor:
    """Production-ready time series processing engine."""

    def __init__(self, config: TimeSeriesProcessorConfig, *, log: Optional[logging.Logger] = None) -> None:
        _require_pandas()
        self.config = config
        self.log = log or logger

    def process(self, dataframe: Any) -> TimeSeriesProcessingResult:
        """Process a pandas DataFrame and return enriched time series data plus audit report."""
        self._validate_dataframe(dataframe)
        report = TimeSeriesAuditReport(total_rows_input=int(len(dataframe)))
        df = dataframe.copy()

        report.null_counts_before = self._null_counts(df)
        df = self._normalize_time_column(df, report)

        if self.config.sort:
            sort_cols = list(self.config.entity_columns) + [self.config.time_column]
            df = df.sort_values(sort_cols).reset_index(drop=True)

        report.entity_count = self._entity_count(df)
        report.duplicate_timestamps = self._count_duplicates(df)
        df = self._handle_duplicates(df, report)

        if self.config.infer_frequency:
            report.inferred_frequency = self._infer_frequency(df, report)

        target_freq = self.config.target_frequency or report.inferred_frequency
        report.target_frequency = target_freq

        if self.config.resample and target_freq:
            df = self._resample(df, target_freq, report)

        if target_freq and self.config.gap_fill_strategy != GapFillStrategy.NONE:
            before = len(df)
            df = self._fill_time_gaps(df, target_freq, report)
            report.missing_timestamps_added += max(0, len(df) - before)

        if self.config.enforce_monotonic:
            self._check_monotonic(df, report)

        df = self._apply_outlier_processing(df, report)
        df = self._add_calendar_features(df, report)
        df = self._add_lag_features(df, report)
        df = self._add_rolling_features(df, report)
        df = self._add_transforms(df, report)

        report.null_counts_after = self._null_counts(df)
        report.total_rows_output = int(len(df))
        report.finish()
        df.attrs["time_series_audit_report"] = report.to_dict()
        return TimeSeriesProcessingResult(dataframe=df, audit_report=report)

    def _validate_dataframe(self, df: Any) -> None:
        if pd is None or not isinstance(df, pd.DataFrame):
            raise TypeError("dataframe must be a pandas DataFrame")
        required = [self.config.time_column, *self.config.value_columns, *self.config.entity_columns]
        missing = [col for col in required if col not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

    def _normalize_time_column(self, df: Any, report: TimeSeriesAuditReport) -> Any:
        time_col = self.config.time_column
        df[time_col] = pd.to_datetime(df[time_col], errors="coerce", utc=False)
        invalid = int(df[time_col].isna().sum())
        if invalid:
            report.warnings.append(f"Dropped {invalid} rows with invalid timestamps")
            df = df.dropna(subset=[time_col])

        if self.config.timezone:
            if df[time_col].dt.tz is None:
                df[time_col] = df[time_col].dt.tz_localize(self.config.timezone)
            else:
                df[time_col] = df[time_col].dt.tz_convert(self.config.timezone)
        return df

    def _null_counts(self, df: Any) -> JsonDict:
        return {col: int(df[col].isna().sum()) for col in df.columns}

    def _entity_count(self, df: Any) -> int:
        if not self.config.entity_columns:
            return 1
        return int(df[list(self.config.entity_columns)].drop_duplicates().shape[0])

    def _group_keys(self) -> List[str]:
        return list(self.config.entity_columns)

    def _count_duplicates(self, df: Any) -> int:
        keys = self._group_keys() + [self.config.time_column]
        return int(df.duplicated(subset=keys).sum())

    def _handle_duplicates(self, df: Any, report: TimeSeriesAuditReport) -> Any:
        if report.duplicate_timestamps == 0:
            return df

        keys = self._group_keys() + [self.config.time_column]
        policy = self.config.duplicate_policy

        if policy == DuplicateTimestampPolicy.FAIL:
            raise TimeSeriesProcessingError(f"Duplicate timestamps found: {report.duplicate_timestamps}")
        if policy == DuplicateTimestampPolicy.KEEP_FIRST:
            return df.drop_duplicates(subset=keys, keep="first")
        if policy == DuplicateTimestampPolicy.KEEP_LAST:
            return df.drop_duplicates(subset=keys, keep="last")

        aggregations: Dict[str, str] = {}
        for col in df.columns:
            if col in keys:
                continue
            if col in self.config.value_columns:
                aggregations[col] = self._aggregation_name(self.config.resample_aggregation, col)
            else:
                aggregations[col] = "last"
        return df.groupby(keys, as_index=False).agg(aggregations)

    def _aggregation_name(self, aggregation: Union[ResampleAggregation, Mapping[str, str]], column: str) -> str:
        if isinstance(aggregation, Mapping):
            return aggregation.get(column, "mean")
        if isinstance(aggregation, ResampleAggregation):
            return aggregation.value
        return str(aggregation)

    def _infer_frequency(self, df: Any, report: TimeSeriesAuditReport) -> Optional[str]:
        frequencies: List[Optional[str]] = []
        if self.config.entity_columns:
            grouped = df.groupby(self._group_keys(), dropna=False)
            for _, group in grouped:
                values = group[self.config.time_column].sort_values().drop_duplicates()
                if len(values) >= 3:
                    frequencies.append(pd.infer_freq(values))
        else:
            values = df[self.config.time_column].sort_values().drop_duplicates()
            if len(values) >= 3:
                frequencies.append(pd.infer_freq(values))

        valid = [freq for freq in frequencies if freq]
        if not valid:
            report.warnings.append("Could not infer time frequency")
            return None
        mode = max(set(valid), key=valid.count)
        if len(set(valid)) > 1:
            report.warnings.append(f"Multiple frequencies inferred. Using most common: {mode}")
        return mode

    def _resample(self, df: Any, freq: str, report: TimeSeriesAuditReport) -> Any:
        time_col = self.config.time_column
        agg_map: Dict[str, str] = {}
        for col in df.columns:
            if col == time_col or col in self.config.entity_columns:
                continue
            if col in self.config.value_columns:
                agg_map[col] = self._aggregation_name(self.config.resample_aggregation, col)
            else:
                agg_map[col] = "last"

        if self.config.entity_columns:
            parts = []
            for entity_values, group in df.groupby(self._group_keys(), dropna=False):
                if not isinstance(entity_values, tuple):
                    entity_values = (entity_values,)
                resampled = group.set_index(time_col).resample(freq).agg(agg_map).reset_index()
                for col, value in zip(self.config.entity_columns, entity_values):
                    resampled[col] = value
                parts.append(resampled)
            return pd.concat(parts, ignore_index=True) if parts else df.iloc[0:0]
        return df.set_index(time_col).resample(freq).agg(agg_map).reset_index()

    def _fill_time_gaps(self, df: Any, freq: str, report: TimeSeriesAuditReport) -> Any:
        if self.config.entity_columns:
            parts = []
            for entity_values, group in df.groupby(self._group_keys(), dropna=False):
                if not isinstance(entity_values, tuple):
                    entity_values = (entity_values,)
                filled = self._fill_single_series(group, freq)
                for col, value in zip(self.config.entity_columns, entity_values):
                    filled[col] = value
                parts.append(filled)
            return pd.concat(parts, ignore_index=True) if parts else df.iloc[0:0]
        return self._fill_single_series(df, freq)

    def _fill_single_series(self, df: Any, freq: str) -> Any:
        time_col = self.config.time_column
        group = df.sort_values(time_col).set_index(time_col)
        if group.empty:
            return df
        full_index = pd.date_range(start=group.index.min(), end=group.index.max(), freq=freq, tz=group.index.tz)
        group = group.reindex(full_index)
        group.index.name = time_col
        group = self._apply_gap_fill(group)
        return group.reset_index()

    def _apply_gap_fill(self, df: Any) -> Any:
        strategy = self.config.gap_fill_strategy
        value_cols = list(self.config.value_columns)
        if strategy == GapFillStrategy.FORWARD_FILL:
            df[value_cols] = df[value_cols].ffill()
        elif strategy == GapFillStrategy.BACKWARD_FILL:
            df[value_cols] = df[value_cols].bfill()
        elif strategy == GapFillStrategy.ZERO:
            df[value_cols] = df[value_cols].fillna(0)
        elif strategy == GapFillStrategy.MEAN:
            df[value_cols] = df[value_cols].fillna(df[value_cols].mean(numeric_only=True))
        elif strategy == GapFillStrategy.MEDIAN:
            df[value_cols] = df[value_cols].fillna(df[value_cols].median(numeric_only=True))
        elif strategy == GapFillStrategy.INTERPOLATE:
            df[value_cols] = df[value_cols].interpolate(method="time", limit_direction="both")
        elif strategy == GapFillStrategy.CONSTANT:
            df[value_cols] = df[value_cols].fillna(self.config.gap_fill_constant)
        return df

    def _check_monotonic(self, df: Any, report: TimeSeriesAuditReport) -> None:
        time_col = self.config.time_column
        if self.config.entity_columns:
            for entity, group in df.groupby(self._group_keys(), dropna=False):
                if not group[time_col].is_monotonic_increasing:
                    report.warnings.append(f"Non-monotonic timestamps detected for entity={entity}")
        elif not df[time_col].is_monotonic_increasing:
            report.warnings.append("Non-monotonic timestamps detected")

    def _target_columns(self, configured: Sequence[str]) -> List[str]:
        return list(configured) if configured else list(self.config.value_columns)

    def _apply_outlier_processing(self, df: Any, report: TimeSeriesAuditReport) -> Any:
        cfg = self.config.outliers
        if cfg.method == OutlierMethod.NONE:
            return df

        for col in self._target_columns(cfg.columns):
            if col not in df.columns:
                continue
            mask, lower, upper = self._outlier_mask(df[col], cfg)
            flag_col = f"{col}_is_outlier"
            df[flag_col] = mask.fillna(False)
            report.generated_features.append(flag_col)
            report.outlier_counts[col] = int(mask.sum())

            if cfg.treatment == OutlierTreatment.REMOVE:
                df = df.loc[~mask].copy()
            elif cfg.treatment == OutlierTreatment.CLIP:
                df[col] = df[col].clip(lower=lower, upper=upper)
            elif cfg.treatment == OutlierTreatment.NULLIFY:
                df.loc[mask, col] = np.nan
        return df

    def _outlier_mask(self, series: Any, cfg: OutlierConfig) -> Tuple[Any, Optional[float], Optional[float]]:
        numeric = pd.to_numeric(series, errors="coerce")
        if cfg.method == OutlierMethod.ZSCORE:
            mean = numeric.mean()
            std = numeric.std(ddof=0)
            if std == 0 or pd.isna(std):
                return pd.Series(False, index=series.index), None, None
            z = (numeric - mean).abs() / std
            return z > cfg.zscore_threshold, mean - cfg.zscore_threshold * std, mean + cfg.zscore_threshold * std

        if cfg.method == OutlierMethod.IQR:
            q1 = numeric.quantile(0.25)
            q3 = numeric.quantile(0.75)
            iqr = q3 - q1
            lower = q1 - cfg.iqr_multiplier * iqr
            upper = q3 + cfg.iqr_multiplier * iqr
            return (numeric < lower) | (numeric > upper), float(lower), float(upper)

        if cfg.method == OutlierMethod.MAD:
            median = numeric.median()
            mad = (numeric - median).abs().median()
            if mad == 0 or pd.isna(mad):
                return pd.Series(False, index=series.index), None, None
            modified_z = 0.6745 * (numeric - median).abs() / mad
            lower = median - (cfg.mad_threshold * mad / 0.6745)
            upper = median + (cfg.mad_threshold * mad / 0.6745)
            return modified_z > cfg.mad_threshold, float(lower), float(upper)

        if cfg.method == OutlierMethod.QUANTILE:
            lower = numeric.quantile(cfg.lower_quantile)
            upper = numeric.quantile(cfg.upper_quantile)
            return (numeric < lower) | (numeric > upper), float(lower), float(upper)

        return pd.Series(False, index=series.index), None, None

    def _add_calendar_features(self, df: Any, report: TimeSeriesAuditReport) -> Any:
        cfg = self.config.calendar_features
        if not cfg.enabled:
            return df
        time = df[self.config.time_column]
        features = {
            "ts_year": time.dt.year,
            "ts_month": time.dt.month,
            "ts_day": time.dt.day,
            "ts_dayofweek": time.dt.dayofweek,
            "ts_dayofyear": time.dt.dayofyear,
            "ts_weekofyear": time.dt.isocalendar().week.astype(int),
            "ts_hour": time.dt.hour,
        }
        if cfg.include_quarter:
            features["ts_quarter"] = time.dt.quarter
        if cfg.include_weekend:
            features["ts_is_weekend"] = time.dt.dayofweek.isin([5, 6]).astype(int)
        if cfg.include_month_start_end:
            features["ts_is_month_start"] = time.dt.is_month_start.astype(int)
            features["ts_is_month_end"] = time.dt.is_month_end.astype(int)
        if cfg.include_cyclical:
            features["ts_month_sin"] = np.sin(2 * np.pi * time.dt.month / 12)
            features["ts_month_cos"] = np.cos(2 * np.pi * time.dt.month / 12)
            features["ts_dow_sin"] = np.sin(2 * np.pi * time.dt.dayofweek / 7)
            features["ts_dow_cos"] = np.cos(2 * np.pi * time.dt.dayofweek / 7)
            features["ts_hour_sin"] = np.sin(2 * np.pi * time.dt.hour / 24)
            features["ts_hour_cos"] = np.cos(2 * np.pi * time.dt.hour / 24)

        for name, values in features.items():
            df[name] = values
            report.generated_features.append(name)
        return df

    def _add_lag_features(self, df: Any, report: TimeSeriesAuditReport) -> Any:
        cfg = self.config.lag_features
        columns = self._target_columns(cfg.columns)
        if not cfg.lags and not cfg.leads:
            return df

        group_keys = self._group_keys()
        for col in columns:
            if col not in df.columns:
                continue
            for lag in cfg.lags:
                name = f"{col}_lag_{lag}"
                df[name] = self._shift(df, col, lag, group_keys)
                report.generated_features.append(name)
            for lead in cfg.leads:
                name = f"{col}_lead_{lead}"
                df[name] = self._shift(df, col, -lead, group_keys)
                report.generated_features.append(name)
        return df

    def _shift(self, df: Any, column: str, periods: int, group_keys: Sequence[str]) -> Any:
        if group_keys:
            return df.groupby(list(group_keys), dropna=False)[column].shift(periods)
        return df[column].shift(periods)

    def _add_rolling_features(self, df: Any, report: TimeSeriesAuditReport) -> Any:
        cfg = self.config.rolling_features
        columns = self._target_columns(cfg.columns)
        if not cfg.windows or not cfg.stats:
            return df

        group_keys = self._group_keys()
        for col in columns:
            if col not in df.columns:
                continue
            for window in cfg.windows:
                for stat in cfg.stats:
                    name = f"{col}_rolling_{window}_{stat}"
                    df[name] = self._rolling_stat(df, col, window, stat, cfg.min_periods, group_keys)
                    report.generated_features.append(name)
        return df

    def _rolling_stat(self, df: Any, column: str, window: int, stat: str, min_periods: int, group_keys: Sequence[str]) -> Any:
        if group_keys:
            rolling = df.groupby(list(group_keys), dropna=False)[column].rolling(window=window, min_periods=min_periods)
            result = getattr(rolling, stat)()
            return result.reset_index(level=list(range(len(group_keys))), drop=True)
        rolling = df[column].rolling(window=window, min_periods=min_periods)
        return getattr(rolling, stat)()

    def _add_transforms(self, df: Any, report: TimeSeriesAuditReport) -> Any:
        cfg = self.config.transforms
        columns = self._target_columns(cfg.columns)
        group_keys = self._group_keys()

        for col in columns:
            if col not in df.columns:
                continue
            if cfg.add_diff:
                for period in cfg.diff_periods:
                    name = f"{col}_diff_{period}"
                    df[name] = self._diff(df, col, period, group_keys)
                    report.generated_features.append(name)
            if cfg.add_pct_change:
                for period in cfg.pct_periods:
                    name = f"{col}_pct_change_{period}"
                    df[name] = self._pct_change(df, col, period, group_keys)
                    report.generated_features.append(name)
            if cfg.add_log:
                name = f"{col}_log1p"
                df[name] = np.log1p(pd.to_numeric(df[col], errors="coerce").clip(lower=0))
                report.generated_features.append(name)
            if cfg.add_zscore:
                name = f"{col}_zscore"
                numeric = pd.to_numeric(df[col], errors="coerce")
                if group_keys:
                    mean = numeric.groupby([df[k] for k in group_keys]).transform("mean")
                    std = numeric.groupby([df[k] for k in group_keys]).transform("std")
                else:
                    mean = numeric.mean()
                    std = numeric.std()
                df[name] = (numeric - mean) / std.replace(0, np.nan) if hasattr(std, "replace") else (numeric - mean) / (std or np.nan)
                report.generated_features.append(name)
        return df

    def _diff(self, df: Any, column: str, periods: int, group_keys: Sequence[str]) -> Any:
        if group_keys:
            return df.groupby(list(group_keys), dropna=False)[column].diff(periods)
        return df[column].diff(periods)

    def _pct_change(self, df: Any, column: str, periods: int, group_keys: Sequence[str]) -> Any:
        if group_keys:
            return df.groupby(list(group_keys), dropna=False)[column].pct_change(periods=periods)
        return df[column].pct_change(periods=periods)

    def make_supervised_dataset(
        self,
        dataframe: Any,
        *,
        target_column: str,
        horizon: int = 1,
        drop_na_target: bool = True,
    ) -> Any:
        """Create a forecasting-ready supervised dataset with future target label."""
        self._validate_dataframe(dataframe)
        df = dataframe.copy()
        group_keys = self._group_keys()
        label = f"{target_column}_target_h{horizon}"
        if group_keys:
            df[label] = df.groupby(list(group_keys), dropna=False)[target_column].shift(-horizon)
        else:
            df[label] = df[target_column].shift(-horizon)
        if drop_na_target:
            df = df.dropna(subset=[label])
        df.attrs["target_column"] = label
        df.attrs["forecast_horizon"] = horizon
        return df

    def profile(self, dataframe: Any) -> JsonDict:
        """Return a lightweight temporal profile without mutating data."""
        self._validate_dataframe(dataframe)
        df = dataframe.copy()
        df[self.config.time_column] = pd.to_datetime(df[self.config.time_column], errors="coerce")
        profile: JsonDict = {
            "rows": int(len(df)),
            "time_min": None,
            "time_max": None,
            "null_counts": self._null_counts(df),
            "duplicate_timestamps": self._count_duplicates(df),
            "entity_count": self._entity_count(df),
        }
        valid_time = df[self.config.time_column].dropna()
        if not valid_time.empty:
            profile["time_min"] = valid_time.min().isoformat()
            profile["time_max"] = valid_time.max().isoformat()
            profile["duration_seconds"] = float((valid_time.max() - valid_time.min()).total_seconds())
        return profile


# -----------------------------------------------------------------------------
# Convenience functions
# -----------------------------------------------------------------------------


def process_time_series(dataframe: Any, config: TimeSeriesProcessorConfig) -> TimeSeriesProcessingResult:
    return TimeSeriesProcessor(config).process(dataframe)


def build_retail_daily_sales_processor(
    *,
    time_column: str = "date",
    sales_column: str = "sales",
    entity_columns: Sequence[str] = ("store_id", "product_id"),
) -> TimeSeriesProcessor:
    """Example factory for daily retail/supermarket sales time series."""
    config = TimeSeriesProcessorConfig(
        time_column=time_column,
        value_columns=[sales_column],
        entity_columns=entity_columns,
        timezone="UTC",
        target_frequency="D",
        resample=True,
        resample_aggregation=ResampleAggregation.SUM,
        duplicate_policy=DuplicateTimestampPolicy.AGGREGATE,
        gap_fill_strategy=GapFillStrategy.ZERO,
        lag_features=LagFeatureConfig(lags=(1, 7, 14, 28), columns=[sales_column]),
        rolling_features=RollingFeatureConfig(windows=(7, 14, 28), stats=("mean", "std", "min", "max"), columns=[sales_column]),
        transforms=TransformConfig(add_diff=True, diff_periods=(1, 7), add_pct_change=True, pct_periods=(1, 7), columns=[sales_column]),
        outliers=OutlierConfig(method=OutlierMethod.IQR, treatment=OutlierTreatment.FLAG_ONLY, columns=[sales_column]),
        metadata={"domain": "retail", "grain": "daily_store_product"},
    )
    return TimeSeriesProcessor(config)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    _require_pandas()

    sample = pd.DataFrame(
        {
            "date": [
                "2026-01-01",
                "2026-01-02",
                "2026-01-04",
                "2026-01-04",
                "2026-01-01",
                "2026-01-03",
            ],
            "store_id": [1, 1, 1, 1, 2, 2],
            "product_id": ["A", "A", "A", "A", "A", "A"],
            "sales": [10, 12, 500, 14, 7, 9],
        }
    )

    processor = build_retail_daily_sales_processor()
    result = processor.process(sample)
    print(json.dumps(result.audit_report.to_dict(), indent=2, ensure_ascii=False, default=str))
    print(result.dataframe.head(20).to_string(index=False))

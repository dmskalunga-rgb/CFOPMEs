"""
ml/utils/preprocessing.py

Enterprise-grade preprocessing utilities for ML pipelines.

Features:
- DataFrame validation
- Missing value handling
- Duplicate removal
- Type coercion
- Categorical encoding
- Numeric scaling
- Outlier treatment
- Train/test split helpers
- Feature/target separation
- Pipeline-ready transformers
- Structured metadata reports
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Literal, Mapping, Sequence

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("numpy is required for preprocessing utilities.") from exc

try:
    import pandas as pd
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("pandas is required for preprocessing utilities.") from exc

try:
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import LabelEncoder, MinMaxScaler, OneHotEncoder, RobustScaler, StandardScaler
except ImportError:  # pragma: no cover
    train_test_split = None
    LabelEncoder = None
    MinMaxScaler = None
    OneHotEncoder = None
    RobustScaler = None
    StandardScaler = None


logger = logging.getLogger(__name__)


class PreprocessingError(Exception):
    """Base error for preprocessing failures."""


class SchemaValidationError(PreprocessingError):
    """Raised when a DataFrame does not match expected schema."""


class UnsupportedEncodingError(PreprocessingError):
    """Raised when an unsupported encoding strategy is used."""


class UnsupportedScalingError(PreprocessingError):
    """Raised when an unsupported scaling strategy is used."""


class MissingDependencyError(PreprocessingError):
    """Raised when an optional dependency is required but unavailable."""


class MissingValueStrategy(str, Enum):
    DROP_ROWS = "drop_rows"
    DROP_COLUMNS = "drop_columns"
    MEAN = "mean"
    MEDIAN = "median"
    MODE = "mode"
    CONSTANT = "constant"
    FORWARD_FILL = "forward_fill"
    BACKWARD_FILL = "backward_fill"


class EncodingStrategy(str, Enum):
    NONE = "none"
    ONE_HOT = "one_hot"
    LABEL = "label"
    FREQUENCY = "frequency"
    TARGET = "target"


class ScalingStrategy(str, Enum):
    NONE = "none"
    STANDARD = "standard"
    MINMAX = "minmax"
    ROBUST = "robust"


class OutlierStrategy(str, Enum):
    NONE = "none"
    CLIP_IQR = "clip_iqr"
    CLIP_ZSCORE = "clip_zscore"
    REMOVE_IQR = "remove_iqr"
    REMOVE_ZSCORE = "remove_zscore"


@dataclass(frozen=True)
class SchemaRule:
    column: str
    dtype: str | None = None
    required: bool = True
    nullable: bool = True
    min_value: float | None = None
    max_value: float | None = None
    allowed_values: set[Any] | None = None


@dataclass(frozen=True)
class PreprocessingConfig:
    target_column: str | None = None
    drop_columns: tuple[str, ...] = ()
    id_columns: tuple[str, ...] = ()
    missing_strategy: MissingValueStrategy = MissingValueStrategy.MEDIAN
    missing_constant_value: Any = 0
    missing_threshold: float = 0.95
    encoding_strategy: EncodingStrategy = EncodingStrategy.ONE_HOT
    scaling_strategy: ScalingStrategy = ScalingStrategy.STANDARD
    outlier_strategy: OutlierStrategy = OutlierStrategy.CLIP_IQR
    outlier_iqr_factor: float = 1.5
    outlier_zscore_threshold: float = 3.0
    lowercase_columns: bool = True
    normalize_column_names: bool = True
    remove_duplicates: bool = True
    random_state: int = 42
    test_size: float = 0.2
    validation_size: float | None = None


@dataclass
class PreprocessingReport:
    initial_rows: int
    initial_columns: int
    final_rows: int = 0
    final_columns: int = 0
    removed_rows: int = 0
    removed_columns: list[str] = field(default_factory=list)
    encoded_columns: list[str] = field(default_factory=list)
    scaled_columns: list[str] = field(default_factory=list)
    imputed_columns: list[str] = field(default_factory=list)
    outlier_columns: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_rows": self.initial_rows,
            "initial_columns": self.initial_columns,
            "final_rows": self.final_rows,
            "final_columns": self.final_columns,
            "removed_rows": self.removed_rows,
            "removed_columns": self.removed_columns,
            "encoded_columns": self.encoded_columns,
            "scaled_columns": self.scaled_columns,
            "imputed_columns": self.imputed_columns,
            "outlier_columns": self.outlier_columns,
            "warnings": self.warnings,
        }


@dataclass
class PreprocessingArtifacts:
    encoders: dict[str, Any] = field(default_factory=dict)
    scalers: dict[str, Any] = field(default_factory=dict)
    imputers: dict[str, Any] = field(default_factory=dict)
    target_encoder: Any | None = None
    feature_columns: list[str] = field(default_factory=list)


@dataclass
class PreprocessingResult:
    dataframe: pd.DataFrame
    report: PreprocessingReport
    artifacts: PreprocessingArtifacts


def require_sklearn() -> None:
    if train_test_split is None:
        raise MissingDependencyError("scikit-learn is required for this operation.")


def normalize_column_name(name: str) -> str:
    normalized = name.strip()

    normalized = re.sub(r"[\s\-]+", "_", normalized)
    normalized = re.sub(r"[^a-zA-Z0-9_]", "", normalized)
    normalized = re.sub(r"_+", "_", normalized)

    return normalized.strip("_").lower()


def normalize_columns(df: pd.DataFrame, *, lowercase: bool = True) -> pd.DataFrame:
    result = df.copy()

    if lowercase:
        result.columns = [normalize_column_name(str(col)) for col in result.columns]
    else:
        result.columns = [str(col).strip() for col in result.columns]

    return result


def validate_dataframe(df: pd.DataFrame, *, min_rows: int = 1, min_columns: int = 1) -> None:
    if not isinstance(df, pd.DataFrame):
        raise PreprocessingError("Input must be a pandas DataFrame.")

    if df.shape[0] < min_rows:
        raise PreprocessingError(f"DataFrame must have at least {min_rows} rows.")

    if df.shape[1] < min_columns:
        raise PreprocessingError(f"DataFrame must have at least {min_columns} columns.")


def validate_schema(df: pd.DataFrame, rules: Sequence[SchemaRule]) -> None:
    errors: list[str] = []

    for rule in rules:
        if rule.required and rule.column not in df.columns:
            errors.append(f"Missing required column: {rule.column}")
            continue

        if rule.column not in df.columns:
            continue

        series = df[rule.column]

        if not rule.nullable and series.isna().any():
            errors.append(f"Column '{rule.column}' contains null values.")

        if rule.dtype:
            try:
                if rule.dtype in {"int", "integer"}:
                    pd.to_numeric(series.dropna(), errors="raise").astype("int64")
                elif rule.dtype in {"float", "number", "numeric"}:
                    pd.to_numeric(series.dropna(), errors="raise")
                elif rule.dtype in {"str", "string", "object"}:
                    series.dropna().astype(str)
                elif rule.dtype in {"bool", "boolean"}:
                    series.dropna().astype(bool)
                elif rule.dtype in {"datetime", "date"}:
                    pd.to_datetime(series.dropna(), errors="raise")
                else:
                    errors.append(f"Unsupported dtype rule '{rule.dtype}' for column '{rule.column}'.")
            except Exception as exc:
                errors.append(f"Column '{rule.column}' failed dtype validation: {exc}")

        if rule.min_value is not None:
            numeric = pd.to_numeric(series, errors="coerce")
            if (numeric.dropna() < rule.min_value).any():
                errors.append(f"Column '{rule.column}' has values below {rule.min_value}.")

        if rule.max_value is not None:
            numeric = pd.to_numeric(series, errors="coerce")
            if (numeric.dropna() > rule.max_value).any():
                errors.append(f"Column '{rule.column}' has values above {rule.max_value}.")

        if rule.allowed_values is not None:
            invalid = set(series.dropna().unique()) - rule.allowed_values
            if invalid:
                errors.append(f"Column '{rule.column}' has invalid values: {sorted(invalid)}.")

    if errors:
        raise SchemaValidationError("; ".join(errors))


def infer_numeric_columns(df: pd.DataFrame, exclude: Iterable[str] = ()) -> list[str]:
    excluded = set(exclude)
    return [
        col for col in df.select_dtypes(include=["number"]).columns
        if col not in excluded
    ]


def infer_categorical_columns(df: pd.DataFrame, exclude: Iterable[str] = ()) -> list[str]:
    excluded = set(exclude)
    return [
        col for col in df.select_dtypes(include=["object", "category", "bool"]).columns
        if col not in excluded
    ]


def remove_duplicate_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    before = len(df)
    result = df.drop_duplicates().reset_index(drop=True)
    return result, before - len(result)


def drop_columns(
    df: pd.DataFrame,
    columns: Iterable[str],
    *,
    ignore_missing: bool = True,
) -> tuple[pd.DataFrame, list[str]]:
    existing = [col for col in columns if col in df.columns]

    if not ignore_missing:
        missing = [col for col in columns if col not in df.columns]
        if missing:
            raise SchemaValidationError(f"Columns not found: {missing}")

    return df.drop(columns=existing), existing


def drop_high_missing_columns(
    df: pd.DataFrame,
    threshold: float,
    *,
    exclude: Iterable[str] = (),
) -> tuple[pd.DataFrame, list[str]]:
    excluded = set(exclude)
    missing_ratio = df.isna().mean()

    to_drop = [
        col for col, ratio in missing_ratio.items()
        if ratio >= threshold and col not in excluded
    ]

    return df.drop(columns=to_drop), to_drop


def handle_missing_values(
    df: pd.DataFrame,
    strategy: MissingValueStrategy = MissingValueStrategy.MEDIAN,
    *,
    constant_value: Any = 0,
    exclude: Iterable[str] = (),
) -> tuple[pd.DataFrame, dict[str, Any]]:
    result = df.copy()
    excluded = set(exclude)
    imputers: dict[str, Any] = {}

    target_columns = [col for col in result.columns if col not in excluded]

    if strategy == MissingValueStrategy.DROP_ROWS:
        result = result.dropna(subset=target_columns).reset_index(drop=True)
        return result, {"strategy": strategy.value}

    if strategy == MissingValueStrategy.DROP_COLUMNS:
        columns_to_drop = [col for col in target_columns if result[col].isna().any()]
        result = result.drop(columns=columns_to_drop)
        return result, {"strategy": strategy.value, "dropped_columns": columns_to_drop}

    for col in target_columns:
        if not result[col].isna().any():
            continue

        if strategy == MissingValueStrategy.MEAN:
            value = pd.to_numeric(result[col], errors="coerce").mean()
        elif strategy == MissingValueStrategy.MEDIAN:
            if pd.api.types.is_numeric_dtype(result[col]):
                value = result[col].median()
            else:
                value = result[col].mode(dropna=True).iloc[0] if not result[col].mode(dropna=True).empty else constant_value
        elif strategy == MissingValueStrategy.MODE:
            value = result[col].mode(dropna=True).iloc[0] if not result[col].mode(dropna=True).empty else constant_value
        elif strategy == MissingValueStrategy.CONSTANT:
            value = constant_value
        elif strategy == MissingValueStrategy.FORWARD_FILL:
            result[col] = result[col].ffill()
            value = "ffill"
        elif strategy == MissingValueStrategy.BACKWARD_FILL:
            result[col] = result[col].bfill()
            value = "bfill"
        else:
            raise PreprocessingError(f"Unsupported missing value strategy: {strategy}")

        if strategy not in {
            MissingValueStrategy.FORWARD_FILL,
            MissingValueStrategy.BACKWARD_FILL,
        }:
            result[col] = result[col].fillna(value)

        imputers[col] = value

    return result, imputers


def coerce_numeric_columns(
    df: pd.DataFrame,
    columns: Iterable[str],
    *,
    errors: Literal["raise", "coerce", "ignore"] = "coerce",
) -> pd.DataFrame:
    result = df.copy()

    for col in columns:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors=errors)

    return result


def coerce_datetime_columns(
    df: pd.DataFrame,
    columns: Iterable[str],
    *,
    errors: Literal["raise", "coerce", "ignore"] = "coerce",
) -> pd.DataFrame:
    result = df.copy()

    for col in columns:
        if col in result.columns:
            result[col] = pd.to_datetime(result[col], errors=errors)

    return result


def encode_categorical_features(
    df: pd.DataFrame,
    columns: Iterable[str] | None = None,
    *,
    strategy: EncodingStrategy = EncodingStrategy.ONE_HOT,
    target: pd.Series | None = None,
    artifacts: PreprocessingArtifacts | None = None,
) -> tuple[pd.DataFrame, PreprocessingArtifacts]:
    if artifacts is None:
        artifacts = PreprocessingArtifacts()

    result = df.copy()
    categorical_columns = list(columns or infer_categorical_columns(result))

    if not categorical_columns or strategy == EncodingStrategy.NONE:
        return result, artifacts

    if strategy == EncodingStrategy.ONE_HOT:
        require_sklearn()

        for col in categorical_columns:
            if col not in result.columns:
                continue

            encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
            values = result[[col]].astype(str)
            encoded = encoder.fit_transform(values)

            encoded_columns = [
                f"{col}__{category}"
                for category in encoder.categories_[0]
            ]

            encoded_df = pd.DataFrame(
                encoded,
                columns=encoded_columns,
                index=result.index,
            )

            result = pd.concat([result.drop(columns=[col]), encoded_df], axis=1)
            artifacts.encoders[col] = encoder

        return result, artifacts

    if strategy == EncodingStrategy.LABEL:
        require_sklearn()

        for col in categorical_columns:
            if col not in result.columns:
                continue

            encoder = LabelEncoder()
            result[col] = encoder.fit_transform(result[col].astype(str))
            artifacts.encoders[col] = encoder

        return result, artifacts

    if strategy == EncodingStrategy.FREQUENCY:
        for col in categorical_columns:
            if col not in result.columns:
                continue

            mapping = result[col].value_counts(normalize=True).to_dict()
            result[col] = result[col].map(mapping).fillna(0.0)
            artifacts.encoders[col] = mapping

        return result, artifacts

    if strategy == EncodingStrategy.TARGET:
        if target is None:
            raise PreprocessingError("Target encoding requires target series.")

        for col in categorical_columns:
            if col not in result.columns:
                continue

            mapping = target.groupby(result[col]).mean().to_dict()
            global_mean = float(target.mean())
            result[col] = result[col].map(mapping).fillna(global_mean)
            artifacts.encoders[col] = {
                "mapping": mapping,
                "global_mean": global_mean,
            }

        return result, artifacts

    raise UnsupportedEncodingError(f"Unsupported encoding strategy: {strategy}")


def scale_numeric_features(
    df: pd.DataFrame,
    columns: Iterable[str] | None = None,
    *,
    strategy: ScalingStrategy = ScalingStrategy.STANDARD,
    artifacts: PreprocessingArtifacts | None = None,
) -> tuple[pd.DataFrame, PreprocessingArtifacts]:
    if artifacts is None:
        artifacts = PreprocessingArtifacts()

    result = df.copy()
    numeric_columns = list(columns or infer_numeric_columns(result))

    if not numeric_columns or strategy == ScalingStrategy.NONE:
        return result, artifacts

    require_sklearn()

    if strategy == ScalingStrategy.STANDARD:
        scaler = StandardScaler()
    elif strategy == ScalingStrategy.MINMAX:
        scaler = MinMaxScaler()
    elif strategy == ScalingStrategy.ROBUST:
        scaler = RobustScaler()
    else:
        raise UnsupportedScalingError(f"Unsupported scaling strategy: {strategy}")

    result[numeric_columns] = scaler.fit_transform(result[numeric_columns])
    artifacts.scalers["numeric"] = {
        "columns": numeric_columns,
        "scaler": scaler,
        "strategy": strategy.value,
    }

    return result, artifacts


def _iqr_bounds(series: pd.Series, factor: float) -> tuple[float, float]:
    q1 = float(series.quantile(0.25))
    q3 = float(series.quantile(0.75))
    iqr = q3 - q1

    return q1 - factor * iqr, q3 + factor * iqr


def treat_outliers(
    df: pd.DataFrame,
    columns: Iterable[str] | None = None,
    *,
    strategy: OutlierStrategy = OutlierStrategy.CLIP_IQR,
    iqr_factor: float = 1.5,
    zscore_threshold: float = 3.0,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    result = df.copy()
    numeric_columns = list(columns or infer_numeric_columns(result))
    report: dict[str, Any] = {}

    if not numeric_columns or strategy == OutlierStrategy.NONE:
        return result, report

    rows_to_keep = pd.Series(True, index=result.index)

    for col in numeric_columns:
        series = pd.to_numeric(result[col], errors="coerce")
        original = series.copy()

        if strategy in {OutlierStrategy.CLIP_IQR, OutlierStrategy.REMOVE_IQR}:
            lower, upper = _iqr_bounds(series.dropna(), iqr_factor)
            mask = series.between(lower, upper) | series.isna()

            if strategy == OutlierStrategy.CLIP_IQR:
                result[col] = series.clip(lower=lower, upper=upper)
            else:
                rows_to_keep &= mask

            affected = int((~mask).sum())
            report[col] = {
                "strategy": strategy.value,
                "lower": lower,
                "upper": upper,
                "affected_rows": affected,
            }

        elif strategy in {OutlierStrategy.CLIP_ZSCORE, OutlierStrategy.REMOVE_ZSCORE}:
            mean = float(series.mean())
            std = float(series.std(ddof=0))

            if math.isclose(std, 0.0) or np.isnan(std):
                continue

            zscores = (series - mean) / std
            mask = zscores.abs() <= zscore_threshold

            lower = mean - zscore_threshold * std
            upper = mean + zscore_threshold * std

            if strategy == OutlierStrategy.CLIP_ZSCORE:
                result[col] = series.clip(lower=lower, upper=upper)
            else:
                rows_to_keep &= mask | series.isna()

            affected = int((original != result[col]).sum()) if "CLIP" in strategy.name else int((~mask).sum())
            report[col] = {
                "strategy": strategy.value,
                "mean": mean,
                "std": std,
                "lower": lower,
                "upper": upper,
                "affected_rows": affected,
            }

        else:
            raise PreprocessingError(f"Unsupported outlier strategy: {strategy}")

    if strategy in {OutlierStrategy.REMOVE_IQR, OutlierStrategy.REMOVE_ZSCORE}:
        result = result.loc[rows_to_keep].reset_index(drop=True)

    return result, report


def split_features_target(
    df: pd.DataFrame,
    target_column: str,
) -> tuple[pd.DataFrame, pd.Series]:
    if target_column not in df.columns:
        raise SchemaValidationError(f"Target column not found: {target_column}")

    return df.drop(columns=[target_column]), df[target_column]


def train_test_split_data(
    X: pd.DataFrame,
    y: pd.Series | None = None,
    *,
    test_size: float = 0.2,
    random_state: int = 42,
    stratify: pd.Series | None = None,
) -> tuple[Any, ...]:
    require_sklearn()

    if y is None:
        return train_test_split(
            X,
            test_size=test_size,
            random_state=random_state,
        )

    return train_test_split(
        X,
        y,
        test_size=test_size,
        random_state=random_state,
        stratify=stratify,
    )


def train_validation_test_split(
    X: pd.DataFrame,
    y: pd.Series,
    *,
    test_size: float = 0.2,
    validation_size: float = 0.1,
    random_state: int = 42,
    stratify: pd.Series | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Series, pd.Series, pd.Series]:
    require_sklearn()

    X_train_val, X_test, y_train_val, y_test = train_test_split(
        X,
        y,
        test_size=test_size,
        random_state=random_state,
        stratify=stratify,
    )

    adjusted_validation_size = validation_size / (1 - test_size)

    stratify_train_val = y_train_val if stratify is not None else None

    X_train, X_val, y_train, y_val = train_test_split(
        X_train_val,
        y_train_val,
        test_size=adjusted_validation_size,
        random_state=random_state,
        stratify=stratify_train_val,
    )

    return X_train, X_val, X_test, y_train, y_val, y_test


def reduce_memory_usage(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()

    for col in result.columns:
        col_type = result[col].dtype

        if pd.api.types.is_integer_dtype(col_type):
            result[col] = pd.to_numeric(result[col], downcast="integer")
        elif pd.api.types.is_float_dtype(col_type):
            result[col] = pd.to_numeric(result[col], downcast="float")
        elif pd.api.types.is_object_dtype(col_type):
            unique_ratio = result[col].nunique(dropna=False) / max(len(result), 1)
            if unique_ratio < 0.5:
                result[col] = result[col].astype("category")

    return result


def create_feature_report(df: pd.DataFrame) -> dict[str, Any]:
    return {
        "rows": int(df.shape[0]),
        "columns": int(df.shape[1]),
        "missing_values": df.isna().sum().to_dict(),
        "missing_ratio": df.isna().mean().round(4).to_dict(),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "numeric_columns": infer_numeric_columns(df),
        "categorical_columns": infer_categorical_columns(df),
        "duplicate_rows": int(df.duplicated().sum()),
    }


def preprocess_dataframe(
    df: pd.DataFrame,
    config: PreprocessingConfig | None = None,
    *,
    schema_rules: Sequence[SchemaRule] | None = None,
) -> PreprocessingResult:
    cfg = config or PreprocessingConfig()
    validate_dataframe(df)

    work = df.copy()

    report = PreprocessingReport(
        initial_rows=int(work.shape[0]),
        initial_columns=int(work.shape[1]),
    )

    if cfg.normalize_column_names:
        work = normalize_columns(work, lowercase=cfg.lowercase_columns)

    if schema_rules:
        validate_schema(work, schema_rules)

    if cfg.remove_duplicates:
        work, removed_duplicates = remove_duplicate_rows(work)
        report.removed_rows += removed_duplicates

    protected_columns = {
        *(cfg.id_columns or ()),
        *([cfg.target_column] if cfg.target_column else []),
    }

    if cfg.drop_columns:
        work, removed = drop_columns(work, cfg.drop_columns)
        report.removed_columns.extend(removed)

    work, high_missing_removed = drop_high_missing_columns(
        work,
        cfg.missing_threshold,
        exclude=protected_columns,
    )
    report.removed_columns.extend(high_missing_removed)

    before_missing_rows = len(work)
    work, imputers = handle_missing_values(
        work,
        cfg.missing_strategy,
        constant_value=cfg.missing_constant_value,
        exclude=protected_columns,
    )
    report.removed_rows += before_missing_rows - len(work)
    report.imputed_columns.extend(list(imputers.keys()))

    target: pd.Series | None = None
    features = work

    if cfg.target_column:
        features, target = split_features_target(work, cfg.target_column)

    features, outlier_report = treat_outliers(
        features,
        strategy=cfg.outlier_strategy,
        iqr_factor=cfg.outlier_iqr_factor,
        zscore_threshold=cfg.outlier_zscore_threshold,
    )
    report.outlier_columns.extend(list(outlier_report.keys()))

    if target is not None and len(features) != len(target):
        target = target.loc[features.index].reset_index(drop=True)
        features = features.reset_index(drop=True)

    artifacts = PreprocessingArtifacts()
    categorical_columns = infer_categorical_columns(features, exclude=cfg.id_columns)

    features, artifacts = encode_categorical_features(
        features,
        categorical_columns,
        strategy=cfg.encoding_strategy,
        target=target,
        artifacts=artifacts,
    )
    report.encoded_columns.extend(categorical_columns)

    numeric_columns = infer_numeric_columns(features, exclude=cfg.id_columns)

    features, artifacts = scale_numeric_features(
        features,
        numeric_columns,
        strategy=cfg.scaling_strategy,
        artifacts=artifacts,
    )
    report.scaled_columns.extend(numeric_columns)

    if cfg.target_column and target is not None:
        work = pd.concat(
            [
                features.reset_index(drop=True),
                target.reset_index(drop=True).rename(cfg.target_column),
            ],
            axis=1,
        )
    else:
        work = features

    artifacts.feature_columns = [
        col for col in work.columns if col != cfg.target_column
    ]

    report.final_rows = int(work.shape[0])
    report.final_columns = int(work.shape[1])
    report.removed_rows += report.initial_rows - report.final_rows - report.removed_rows

    logger.info(
        "preprocessing.completed",
        extra=report.to_dict(),
    )

    return PreprocessingResult(
        dataframe=work,
        report=report,
        artifacts=artifacts,
    )


__all__ = [
    "EncodingStrategy",
    "MissingValueStrategy",
    "OutlierStrategy",
    "PreprocessingArtifacts",
    "PreprocessingConfig",
    "PreprocessingError",
    "PreprocessingReport",
    "PreprocessingResult",
    "ScalingStrategy",
    "SchemaRule",
    "SchemaValidationError",
    "UnsupportedEncodingError",
    "UnsupportedScalingError",
    "clean_column_name",
    "normalize_column_name",
    "normalize_columns",
    "validate_dataframe",
    "validate_schema",
    "infer_numeric_columns",
    "infer_categorical_columns",
    "remove_duplicate_rows",
    "drop_columns",
    "drop_high_missing_columns",
    "handle_missing_values",
    "coerce_numeric_columns",
    "coerce_datetime_columns",
    "encode_categorical_features",
    "scale_numeric_features",
    "treat_outliers",
    "split_features_target",
    "train_test_split_data",
    "train_validation_test_split",
    "reduce_memory_usage",
    "create_feature_report",
    "preprocess_dataframe",
]
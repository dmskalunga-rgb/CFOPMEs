"""
ml/pipelines/classifier.py

Enterprise-grade classification pipeline.

Responsabilidades:
- Treinar classificadores sklearn-like
- Validar dados e target
- Pré-processar dataset
- Split estratificado
- Avaliar métricas de classificação
- Ajustar threshold para classificação binária
- Gerar predições, probabilidades e decisões
- Persistir modelo, métricas, config e manifesto
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

import numpy as np
import pandas as pd

from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split

try:
    from ml.utils.loaders import load_by_extension
    from ml.utils.preprocessing import (
        PreprocessingConfig,
        PreprocessingResult,
        SchemaRule,
        create_feature_report,
        preprocess_dataframe,
        split_features_target,
    )
    from ml.utils.serializers import (
        ArtifactRegistry,
        SerializerOptions,
        save_joblib,
        save_json,
    )
except ImportError:  # pragma: no cover
    from ..utils.loaders import load_by_extension
    from ..utils.preprocessing import (
        PreprocessingConfig,
        PreprocessingResult,
        SchemaRule,
        create_feature_report,
        preprocess_dataframe,
        split_features_target,
    )
    from ..utils.serializers import (
        ArtifactRegistry,
        SerializerOptions,
        save_joblib,
        save_json,
    )


logger = logging.getLogger(__name__)


class ClassifierPipelineError(Exception):
    """Erro base do pipeline de classificação."""


class ClassifierValidationError(ClassifierPipelineError):
    """Erro de validação do pipeline de classificação."""


class ClassifierProtocol(Protocol):
    def fit(self, X: Any, y: Any) -> Any:
        ...

    def predict(self, X: Any) -> Any:
        ...


@dataclass(frozen=True)
class ClassifierPaths:
    input_path: Path
    output_dir: Path
    model_filename: str = "classifier.joblib"
    metrics_filename: str = "classifier_metrics.json"
    report_filename: str = "classifier_report.json"
    config_filename: str = "classifier_config.json"
    manifest_filename: str = "manifest.json"
    feature_report_filename: str = "feature_report.json"
    threshold_filename: str = "threshold.json"


@dataclass(frozen=True)
class ClassifierSplitConfig:
    test_size: float = 0.2
    validation_size: float = 0.1
    random_state: int = 42
    stratify: bool = True
    shuffle: bool = True


@dataclass(frozen=True)
class ThresholdTuningConfig:
    enabled: bool = True
    metric: str = "f1"
    min_precision: float | None = None
    min_recall: float | None = None
    default_threshold: float = 0.5


@dataclass(frozen=True)
class CalibrationConfig:
    enabled: bool = False
    method: str = "sigmoid"
    cv: int | str = "prefit"


@dataclass(frozen=True)
class ClassifierPipelineConfig:
    pipeline_name: str = "classifier_pipeline"
    environment: str = "dev"
    experiment_name: str = "default"
    run_id: str | None = None
    model_name: str = "classifier"
    model_version: str = "0.0.1"
    target_column: str = "target"
    positive_label: Any = 1
    random_state: int = 42
    fail_fast: bool = True
    preprocessing: PreprocessingConfig = field(default_factory=PreprocessingConfig)
    split: ClassifierSplitConfig = field(default_factory=ClassifierSplitConfig)
    threshold_tuning: ThresholdTuningConfig = field(default_factory=ThresholdTuningConfig)
    calibration: CalibrationConfig = field(default_factory=CalibrationConfig)
    schema_rules: tuple[SchemaRule, ...] = ()
    serializer_options: SerializerOptions = field(default_factory=SerializerOptions)
    extra_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ClassifierPipelineReport:
    run_id: str
    pipeline_name: str
    environment: str
    experiment_name: str
    model_name: str
    model_version: str
    status: str
    started_at: str
    finished_at: str | None = None
    input_rows: int = 0
    input_columns: int = 0
    train_rows: int = 0
    validation_rows: int = 0
    test_rows: int = 0
    duration_ms: int = 0
    feature_report: dict[str, Any] | None = None
    preprocessing_report: dict[str, Any] | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    threshold: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClassifierPipelineResult:
    model: Any
    threshold: float
    report: ClassifierPipelineReport
    preprocessing: PreprocessingResult


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_run_id() -> str:
    return str(uuid.uuid4())


def elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def dataframe_from_artifact(artifact: Any) -> pd.DataFrame:
    data = getattr(artifact, "data", artifact)

    if isinstance(data, pd.DataFrame):
        return data.copy()

    if isinstance(data, list):
        return pd.DataFrame(data)

    if isinstance(data, Mapping):
        if "data" in data and isinstance(data["data"], list):
            return pd.DataFrame(data["data"])
        return pd.DataFrame([data])

    raise ClassifierValidationError(
        f"Não foi possível converter entrada para DataFrame: {type(data).__name__}"
    )


def validate_classifier_dataframe(df: pd.DataFrame, target_column: str) -> None:
    if df.empty:
        raise ClassifierValidationError("Dataset vazio.")

    if target_column not in df.columns:
        raise ClassifierValidationError(f"Coluna target não encontrada: {target_column}")

    if df[target_column].isna().any():
        raise ClassifierValidationError("Target contém valores nulos.")

    if df[target_column].nunique() < 2:
        raise ClassifierValidationError("Classificação exige pelo menos 2 classes.")

    if df.columns.duplicated().any():
        duplicated = df.columns[df.columns.duplicated()].tolist()
        raise ClassifierValidationError(f"Colunas duplicadas: {duplicated}")


def split_classifier_dataset(
    X: pd.DataFrame,
    y: pd.Series,
    config: ClassifierSplitConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Series, pd.Series, pd.Series]:
    stratify = y if config.stratify and y.value_counts().min() >= 2 else None

    X_train_val, X_test, y_train_val, y_test = train_test_split(
        X,
        y,
        test_size=config.test_size,
        random_state=config.random_state,
        shuffle=config.shuffle,
        stratify=stratify,
    )

    adjusted_val = config.validation_size / (1 - config.test_size)

    stratify_train_val = (
        y_train_val
        if config.stratify and y_train_val.value_counts().min() >= 2
        else None
    )

    X_train, X_val, y_train, y_val = train_test_split(
        X_train_val,
        y_train_val,
        test_size=adjusted_val,
        random_state=config.random_state,
        shuffle=config.shuffle,
        stratify=stratify_train_val,
    )

    return (
        X_train.reset_index(drop=True),
        X_val.reset_index(drop=True),
        X_test.reset_index(drop=True),
        y_train.reset_index(drop=True),
        y_val.reset_index(drop=True),
        y_test.reset_index(drop=True),
    )


def predict_proba_positive(
    model: Any,
    X: pd.DataFrame,
    positive_label: Any,
) -> np.ndarray | None:
    if not hasattr(model, "predict_proba"):
        return None

    probabilities = model.predict_proba(X)

    classes = getattr(model, "classes_", None)

    if classes is None:
        return probabilities[:, -1]

    classes_list = list(classes)

    if positive_label in classes_list:
        positive_index = classes_list.index(positive_label)
    else:
        positive_index = -1

    return probabilities[:, positive_index]


def evaluate_classifier(
    model: Any,
    X: pd.DataFrame,
    y: pd.Series,
    *,
    positive_label: Any = 1,
    threshold: float = 0.5,
) -> dict[str, Any]:
    y_pred = model.predict(X)

    proba = predict_proba_positive(model, X, positive_label)

    if proba is not None and y.nunique() == 2:
        y_pred_threshold = np.where(proba >= threshold, positive_label, _negative_label(y, positive_label))
    else:
        y_pred_threshold = y_pred

    metrics: dict[str, Any] = {
        "accuracy": float(accuracy_score(y, y_pred_threshold)),
        "balanced_accuracy": float(balanced_accuracy_score(y, y_pred_threshold)),
        "precision_macro": float(precision_score(y, y_pred_threshold, average="macro", zero_division=0)),
        "recall_macro": float(recall_score(y, y_pred_threshold, average="macro", zero_division=0)),
        "f1_macro": float(f1_score(y, y_pred_threshold, average="macro", zero_division=0)),
        "confusion_matrix": confusion_matrix(y, y_pred_threshold).tolist(),
        "classification_report": classification_report(
            y,
            y_pred_threshold,
            output_dict=True,
            zero_division=0,
        ),
    }

    if proba is not None:
        try:
            metrics["roc_auc"] = float(roc_auc_score(y, proba))
        except Exception as exc:
            metrics["roc_auc_error"] = str(exc)

        try:
            metrics["average_precision"] = float(average_precision_score(y, proba))
        except Exception as exc:
            metrics["average_precision_error"] = str(exc)

        try:
            metrics["log_loss"] = float(log_loss(y, model.predict_proba(X)))
        except Exception as exc:
            metrics["log_loss_error"] = str(exc)

    return metrics


def _negative_label(y: pd.Series, positive_label: Any) -> Any:
    labels = [label for label in sorted(y.unique()) if label != positive_label]
    return labels[0] if labels else 0


def tune_threshold(
    y_true: pd.Series,
    proba: np.ndarray | None,
    *,
    positive_label: Any,
    config: ThresholdTuningConfig,
) -> dict[str, Any]:
    if not config.enabled or proba is None or y_true.nunique() != 2:
        return {
            "enabled": False,
            "threshold": config.default_threshold,
            "reason": "disabled_or_not_binary_or_no_proba",
        }

    binary_true = (y_true == positive_label).astype(int)
    precision, recall, thresholds = precision_recall_curve(binary_true, proba)

    best_score = -1.0
    best_threshold = config.default_threshold
    best_precision = 0.0
    best_recall = 0.0

    for idx, threshold in enumerate(thresholds):
        p = float(precision[idx])
        r = float(recall[idx])

        if config.min_precision is not None and p < config.min_precision:
            continue

        if config.min_recall is not None and r < config.min_recall:
            continue

        if config.metric == "precision":
            score = p
        elif config.metric == "recall":
            score = r
        else:
            score = 0.0 if p + r == 0 else 2 * p * r / (p + r)

        if score > best_score:
            best_score = score
            best_threshold = float(threshold)
            best_precision = p
            best_recall = r

    return {
        "enabled": True,
        "metric": config.metric,
        "threshold": best_threshold,
        "score": best_score,
        "precision": best_precision,
        "recall": best_recall,
        "min_precision": config.min_precision,
        "min_recall": config.min_recall,
    }


def maybe_calibrate_model(
    model: Any,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    config: CalibrationConfig,
) -> Any:
    if not config.enabled:
        return model

    calibrated = CalibratedClassifierCV(
        estimator=model,
        method=config.method,
        cv=config.cv,
    )
    calibrated.fit(X_val, y_val)

    return calibrated


class ClassifierPipeline:
    def __init__(
        self,
        model: BaseEstimator | ClassifierProtocol | Callable[..., Any],
        paths: ClassifierPaths,
        *,
        config: ClassifierPipelineConfig | None = None,
        fit_params: Mapping[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.paths = paths
        self.config = config or ClassifierPipelineConfig()
        self.fit_params = dict(fit_params or {})
        self.registry = ArtifactRegistry()

    def run(self) -> ClassifierPipelineResult:
        started = time.perf_counter()
        run_id = self.config.run_id or make_run_id()
        self.paths.output_dir.mkdir(parents=True, exist_ok=True)

        report = ClassifierPipelineReport(
            run_id=run_id,
            pipeline_name=self.config.pipeline_name,
            environment=self.config.environment,
            experiment_name=self.config.experiment_name,
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            status="running",
            started_at=utc_now_iso(),
            metadata=self.config.extra_metadata,
        )

        try:
            artifact = load_by_extension(self.paths.input_path)
            df = dataframe_from_artifact(artifact)

            validate_classifier_dataframe(df, self.config.target_column)

            report.input_rows = int(df.shape[0])
            report.input_columns = int(df.shape[1])
            report.feature_report = create_feature_report(df)

            preprocessing_config = PreprocessingConfig(
                target_column=self.config.target_column,
                drop_columns=self.config.preprocessing.drop_columns,
                id_columns=self.config.preprocessing.id_columns,
                missing_strategy=self.config.preprocessing.missing_strategy,
                missing_constant_value=self.config.preprocessing.missing_constant_value,
                missing_threshold=self.config.preprocessing.missing_threshold,
                encoding_strategy=self.config.preprocessing.encoding_strategy,
                scaling_strategy=self.config.preprocessing.scaling_strategy,
                outlier_strategy=self.config.preprocessing.outlier_strategy,
                outlier_iqr_factor=self.config.preprocessing.outlier_iqr_factor,
                outlier_zscore_threshold=self.config.preprocessing.outlier_zscore_threshold,
                lowercase_columns=self.config.preprocessing.lowercase_columns,
                normalize_column_names=self.config.preprocessing.normalize_column_names,
                remove_duplicates=self.config.preprocessing.remove_duplicates,
                random_state=self.config.random_state,
            )

            preprocessing = preprocess_dataframe(
                df,
                preprocessing_config,
                schema_rules=self.config.schema_rules,
            )
            report.preprocessing_report = preprocessing.report.to_dict()

            X, y = split_features_target(
                preprocessing.dataframe,
                self.config.target_column,
            )

            X_train, X_val, X_test, y_train, y_val, y_test = split_classifier_dataset(
                X,
                y,
                self.config.split,
            )

            report.train_rows = len(X_train)
            report.validation_rows = len(X_val)
            report.test_rows = len(X_test)

            trained_model = self._fit_model(X_train, y_train)
            trained_model = maybe_calibrate_model(
                trained_model,
                X_val,
                y_val,
                self.config.calibration,
            )

            val_proba = predict_proba_positive(
                trained_model,
                X_val,
                self.config.positive_label,
            )

            threshold_info = tune_threshold(
                y_val,
                val_proba,
                positive_label=self.config.positive_label,
                config=self.config.threshold_tuning,
            )

            threshold = float(threshold_info["threshold"])
            report.threshold = threshold_info

            report.metrics = {
                "train": evaluate_classifier(
                    trained_model,
                    X_train,
                    y_train,
                    positive_label=self.config.positive_label,
                    threshold=threshold,
                ),
                "validation": evaluate_classifier(
                    trained_model,
                    X_val,
                    y_val,
                    positive_label=self.config.positive_label,
                    threshold=threshold,
                ),
                "test": evaluate_classifier(
                    trained_model,
                    X_test,
                    y_test,
                    positive_label=self.config.positive_label,
                    threshold=threshold,
                ),
            }

            self._persist(trained_model, report)

            report.status = "success"
            report.finished_at = utc_now_iso()
            report.duration_ms = elapsed_ms(started)

            self._save_report(report)

            logger.info(
                "classifier_pipeline.completed",
                extra={
                    "run_id": run_id,
                    "duration_ms": report.duration_ms,
                    "threshold": threshold,
                },
            )

            return ClassifierPipelineResult(
                model=trained_model,
                threshold=threshold,
                report=report,
                preprocessing=preprocessing,
            )

        except Exception as exc:
            report.status = "failed"
            report.finished_at = utc_now_iso()
            report.duration_ms = elapsed_ms(started)
            report.errors.append(
                {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            self._save_report(report)

            logger.exception(
                "classifier_pipeline.failed",
                extra={"run_id": run_id},
            )

            if self.config.fail_fast:
                raise

            raise ClassifierPipelineError(str(exc)) from exc

    def _fit_model(self, X_train: pd.DataFrame, y_train: pd.Series) -> Any:
        if hasattr(self.model, "fit"):
            self.model.fit(X_train, y_train, **self.fit_params)  # type: ignore[attr-defined]
            return self.model

        return self.model(X_train, y_train, **self.fit_params)

    def predict(
        self,
        model: Any,
        X: pd.DataFrame,
        *,
        threshold: float = 0.5,
    ) -> pd.DataFrame:
        predictions = model.predict(X)
        proba = predict_proba_positive(model, X, self.config.positive_label)

        result = pd.DataFrame({"prediction": predictions})

        if proba is not None:
            result["probability"] = proba
            result["decision"] = np.where(
                proba >= threshold,
                self.config.positive_label,
                "negative",
            )

        return result

    def _persist(self, model: Any, report: ClassifierPipelineReport) -> None:
        model_path = self.paths.output_dir / self.paths.model_filename
        metrics_path = self.paths.output_dir / self.paths.metrics_filename
        config_path = self.paths.output_dir / self.paths.config_filename
        threshold_path = self.paths.output_dir / self.paths.threshold_filename
        feature_report_path = self.paths.output_dir / self.paths.feature_report_filename
        manifest_path = self.paths.output_dir / self.paths.manifest_filename

        model_artifact = save_joblib(
            model,
            model_path,
            metadata_extra={
                "artifact_type": "classifier_model",
                "run_id": report.run_id,
                "model_name": self.config.model_name,
                "model_version": self.config.model_version,
            },
        )
        self.registry.register("model", model_artifact)

        metrics_artifact = save_json(
            report.metrics,
            metrics_path,
            options=self.config.serializer_options,
            metadata_extra={"artifact_type": "classifier_metrics"},
        )
        self.registry.register("metrics", metrics_artifact)

        config_artifact = save_json(
            {
                "config": asdict(self.config),
                "fit_params": self.fit_params,
            },
            config_path,
            options=self.config.serializer_options,
            metadata_extra={"artifact_type": "classifier_config"},
        )
        self.registry.register("config", config_artifact)

        threshold_artifact = save_json(
            report.threshold,
            threshold_path,
            options=self.config.serializer_options,
            metadata_extra={"artifact_type": "classifier_threshold"},
        )
        self.registry.register("threshold", threshold_artifact)

        feature_artifact = save_json(
            report.feature_report or {},
            feature_report_path,
            options=self.config.serializer_options,
            metadata_extra={"artifact_type": "feature_report"},
        )
        self.registry.register("feature_report", feature_artifact)

        manifest_artifact = self.registry.save_manifest(
            manifest_path,
            options=self.config.serializer_options,
        )
        self.registry.register("manifest", manifest_artifact)

        report.artifacts = {
            "model_path": str(model_path),
            "metrics_path": str(metrics_path),
            "config_path": str(config_path),
            "threshold_path": str(threshold_path),
            "feature_report_path": str(feature_report_path),
            "manifest_path": str(manifest_path),
        }

    def _save_report(self, report: ClassifierPipelineReport) -> None:
        report_path = self.paths.output_dir / self.paths.report_filename

        try:
            save_json(
                report.to_dict(),
                report_path,
                options=self.config.serializer_options,
                metadata_extra={
                    "artifact_type": "classifier_pipeline_report",
                    "run_id": report.run_id,
                },
            )
        except Exception:
            logger.exception(
                "classifier_pipeline.report_persist_failed",
                extra={"run_id": report.run_id},
            )


def run_classifier_pipeline(
    model: BaseEstimator | ClassifierMixin | ClassifierProtocol | Callable[..., Any],
    input_path: str | Path,
    output_dir: str | Path,
    *,
    config: ClassifierPipelineConfig | None = None,
    fit_params: Mapping[str, Any] | None = None,
) -> ClassifierPipelineResult:
    pipeline = ClassifierPipeline(
        model=model,
        paths=ClassifierPaths(
            input_path=Path(input_path),
            output_dir=Path(output_dir),
        ),
        config=config,
        fit_params=fit_params,
    )

    return pipeline.run()


__all__ = [
    "CalibrationConfig",
    "ClassifierPaths",
    "ClassifierPipeline",
    "ClassifierPipelineConfig",
    "ClassifierPipelineError",
    "ClassifierPipelineReport",
    "ClassifierPipelineResult",
    "ClassifierProtocol",
    "ClassifierSplitConfig",
    "ClassifierValidationError",
    "ThresholdTuningConfig",
    "dataframe_from_artifact",
    "elapsed_ms",
    "evaluate_classifier",
    "make_run_id",
    "maybe_calibrate_model",
    "predict_proba_positive",
    "run_classifier_pipeline",
    "split_classifier_dataset",
    "tune_threshold",
    "utc_now_iso",
    "validate_classifier_dataframe",
]
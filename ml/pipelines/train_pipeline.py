"""
ml/pipelines/train_pipeline.py

Enterprise-grade ML training pipeline.

Responsabilidades:
- Carregar dataset
- Validar schema e qualidade
- Pré-processar dados
- Separar treino/validação/teste
- Treinar modelo
- Avaliar métricas
- Persistir modelo, métricas, configs, artefatos e manifesto
- Garantir rastreabilidade, reprodutibilidade e auditoria
"""

from __future__ import annotations

import json
import logging
import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

import pandas as pd

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("numpy is required for train_pipeline.py") from exc

try:
    from sklearn.base import BaseEstimator
    from sklearn.metrics import (
        accuracy_score,
        classification_report,
        f1_score,
        mean_absolute_error,
        mean_squared_error,
        precision_score,
        r2_score,
        recall_score,
        roc_auc_score,
    )
    from sklearn.model_selection import train_test_split
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("scikit-learn is required for train_pipeline.py") from exc

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
        save_artifact,
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
        save_artifact,
        save_joblib,
        save_json,
    )


logger = logging.getLogger(__name__)


class TrainPipelineError(Exception):
    """Erro base do pipeline de treinamento."""


class TrainPipelineValidationError(TrainPipelineError):
    """Erro de validação do pipeline de treinamento."""


class TrainTaskType(str):
    CLASSIFICATION = "classification"
    REGRESSION = "regression"


class ModelProtocol(Protocol):
    def fit(self, X: Any, y: Any) -> Any:
        ...

    def predict(self, X: Any) -> Any:
        ...


@dataclass(frozen=True)
class TrainPipelinePaths:
    input_path: Path
    output_dir: Path
    model_filename: str = "model.joblib"
    metrics_filename: str = "metrics.json"
    report_filename: str = "train_pipeline_report.json"
    config_filename: str = "training_config.json"
    manifest_filename: str = "manifest.json"
    feature_report_filename: str = "feature_report.json"


@dataclass(frozen=True)
class TrainSplitConfig:
    test_size: float = 0.2
    validation_size: float | None = 0.1
    random_state: int = 42
    stratify: bool = True
    shuffle: bool = True


@dataclass(frozen=True)
class TrainPipelineConfig:
    pipeline_name: str = "train_pipeline"
    environment: str = "dev"
    experiment_name: str = "default"
    run_id: str | None = None
    model_name: str = "model"
    model_version: str = "0.0.1"
    task_type: str = TrainTaskType.CLASSIFICATION
    target_column: str = "target"
    random_state: int = 42
    fail_fast: bool = True
    persist_preprocessed: bool = False
    preprocessing: PreprocessingConfig = field(default_factory=PreprocessingConfig)
    split: TrainSplitConfig = field(default_factory=TrainSplitConfig)
    schema_rules: tuple[SchemaRule, ...] = ()
    serializer_options: SerializerOptions = field(default_factory=SerializerOptions)
    extra_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class TrainPipelineMetrics:
    input_rows: int = 0
    input_columns: int = 0
    preprocessed_rows: int = 0
    preprocessed_columns: int = 0
    train_rows: int = 0
    validation_rows: int = 0
    test_rows: int = 0
    load_duration_ms: int = 0
    preprocessing_duration_ms: int = 0
    split_duration_ms: int = 0
    train_duration_ms: int = 0
    evaluation_duration_ms: int = 0
    persistence_duration_ms: int = 0
    total_duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrainPipelineReport:
    run_id: str
    pipeline_name: str
    environment: str
    experiment_name: str
    model_name: str
    model_version: str
    task_type: str
    status: str
    started_at: str
    finished_at: str | None = None
    metrics: TrainPipelineMetrics = field(default_factory=TrainPipelineMetrics)
    input_artifact: dict[str, Any] | None = None
    feature_report: dict[str, Any] | None = None
    preprocessing_report: dict[str, Any] | None = None
    evaluation: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pipeline_name": self.pipeline_name,
            "environment": self.environment,
            "experiment_name": self.experiment_name,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "task_type": self.task_type,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metrics": self.metrics.to_dict(),
            "input_artifact": self.input_artifact,
            "feature_report": self.feature_report,
            "preprocessing_report": self.preprocessing_report,
            "evaluation": self.evaluation,
            "artifacts": self.artifacts,
            "errors": self.errors,
            "metadata": dict(self.metadata),
        }


@dataclass
class TrainArtifacts:
    model: Any
    preprocessing: PreprocessingResult
    X_train: pd.DataFrame
    X_val: pd.DataFrame | None
    X_test: pd.DataFrame
    y_train: pd.Series
    y_val: pd.Series | None
    y_test: pd.Series


@dataclass
class TrainPipelineResult:
    model: Any
    report: TrainPipelineReport
    artifacts: TrainArtifacts


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_run_id() -> str:
    return str(uuid.uuid4())


def elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def set_global_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def dataframe_from_loaded_artifact(artifact: Any) -> pd.DataFrame:
    data = getattr(artifact, "data", artifact)

    if isinstance(data, pd.DataFrame):
        return data.copy()

    if isinstance(data, list):
        return pd.DataFrame(data)

    if isinstance(data, Mapping):
        if "data" in data and isinstance(data["data"], list):
            return pd.DataFrame(data["data"])
        return pd.DataFrame([data])

    raise TrainPipelineValidationError(
        f"Não foi possível converter artefato para DataFrame: {type(data).__name__}"
    )


def validate_training_input(df: pd.DataFrame, target_column: str) -> None:
    if not isinstance(df, pd.DataFrame):
        raise TrainPipelineValidationError("Entrada precisa ser um pandas DataFrame.")

    if df.empty:
        raise TrainPipelineValidationError("Dataset de treinamento está vazio.")

    if target_column not in df.columns:
        raise TrainPipelineValidationError(f"Coluna target não encontrada: {target_column}")

    if df[target_column].isna().all():
        raise TrainPipelineValidationError("Coluna target contém apenas valores nulos.")

    if df.columns.duplicated().any():
        duplicated = df.columns[df.columns.duplicated()].tolist()
        raise TrainPipelineValidationError(f"Colunas duplicadas encontradas: {duplicated}")


def infer_stratify_y(
    y: pd.Series,
    *,
    enabled: bool,
    task_type: str,
) -> pd.Series | None:
    if not enabled:
        return None

    if task_type != TrainTaskType.CLASSIFICATION:
        return None

    value_counts = y.value_counts(dropna=False)

    if value_counts.empty or value_counts.min() < 2:
        return None

    return y


def split_dataset(
    X: pd.DataFrame,
    y: pd.Series,
    *,
    config: TrainSplitConfig,
    task_type: str,
) -> tuple[pd.DataFrame, pd.DataFrame | None, pd.DataFrame, pd.Series, pd.Series | None, pd.Series]:
    stratify = infer_stratify_y(
        y,
        enabled=config.stratify,
        task_type=task_type,
    )

    X_train_val, X_test, y_train_val, y_test = train_test_split(
        X,
        y,
        test_size=config.test_size,
        random_state=config.random_state,
        shuffle=config.shuffle,
        stratify=stratify,
    )

    if config.validation_size is None or config.validation_size <= 0:
        return (
            X_train_val.reset_index(drop=True),
            None,
            X_test.reset_index(drop=True),
            y_train_val.reset_index(drop=True),
            None,
            y_test.reset_index(drop=True),
        )

    validation_size_adjusted = config.validation_size / (1 - config.test_size)
    stratify_train_val = infer_stratify_y(
        y_train_val,
        enabled=config.stratify,
        task_type=task_type,
    )

    X_train, X_val, y_train, y_val = train_test_split(
        X_train_val,
        y_train_val,
        test_size=validation_size_adjusted,
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


def train_model(
    model: ModelProtocol | Callable[..., Any],
    X_train: pd.DataFrame,
    y_train: pd.Series,
    *,
    fit_params: Mapping[str, Any] | None = None,
) -> Any:
    params = dict(fit_params or {})

    if hasattr(model, "fit"):
        model.fit(X_train, y_train, **params)  # type: ignore[attr-defined]
        return model

    trained = model(X_train, y_train, **params)
    return trained


def safe_predict(model: Any, X: pd.DataFrame) -> Any:
    if not hasattr(model, "predict"):
        raise TrainPipelineValidationError("Modelo treinado não possui método predict().")
    return model.predict(X)


def evaluate_classification(
    model: Any,
    X: pd.DataFrame,
    y: pd.Series,
) -> dict[str, Any]:
    y_pred = safe_predict(model, X)

    metrics: dict[str, Any] = {
        "accuracy": float(accuracy_score(y, y_pred)),
        "precision_macro": float(precision_score(y, y_pred, average="macro", zero_division=0)),
        "recall_macro": float(recall_score(y, y_pred, average="macro", zero_division=0)),
        "f1_macro": float(f1_score(y, y_pred, average="macro", zero_division=0)),
        "classification_report": classification_report(
            y,
            y_pred,
            output_dict=True,
            zero_division=0,
        ),
    }

    if hasattr(model, "predict_proba"):
        try:
            proba = model.predict_proba(X)

            if proba.shape[1] == 2:
                metrics["roc_auc"] = float(roc_auc_score(y, proba[:, 1]))
            else:
                metrics["roc_auc_ovr"] = float(
                    roc_auc_score(y, proba, multi_class="ovr")
                )
        except Exception as exc:
            metrics["roc_auc_error"] = str(exc)

    return metrics


def evaluate_regression(
    model: Any,
    X: pd.DataFrame,
    y: pd.Series,
) -> dict[str, Any]:
    y_pred = safe_predict(model, X)

    mse = float(mean_squared_error(y, y_pred))
    rmse = float(np.sqrt(mse))

    return {
        "mae": float(mean_absolute_error(y, y_pred)),
        "mse": mse,
        "rmse": rmse,
        "r2": float(r2_score(y, y_pred)),
    }


def evaluate_model(
    model: Any,
    *,
    task_type: str,
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame | None,
    y_val: pd.Series | None,
    X_test: pd.DataFrame,
    y_test: pd.Series,
) -> dict[str, Any]:
    evaluation: dict[str, Any] = {}

    evaluator = (
        evaluate_classification
        if task_type == TrainTaskType.CLASSIFICATION
        else evaluate_regression
    )

    evaluation["train"] = evaluator(model, X_train, y_train)

    if X_val is not None and y_val is not None:
        evaluation["validation"] = evaluator(model, X_val, y_val)

    evaluation["test"] = evaluator(model, X_test, y_test)

    return evaluation


def persist_dataframe(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    suffix = path.suffix.lower()

    if suffix == ".parquet":
        df.to_parquet(path, index=False)
        return

    if suffix == ".csv":
        df.to_csv(path, index=False)
        return

    if suffix == ".json":
        path.write_text(
            json.dumps(df.to_dict(orient="records"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return

    raise TrainPipelineError(f"Formato não suportado para DataFrame: {suffix}")


class TrainPipeline:
    def __init__(
        self,
        model: ModelProtocol | Callable[..., Any],
        paths: TrainPipelinePaths,
        *,
        config: TrainPipelineConfig | None = None,
        fit_params: Mapping[str, Any] | None = None,
        preprocessor_override: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    ) -> None:
        self.model = model
        self.paths = paths
        self.config = config or TrainPipelineConfig()
        self.fit_params = fit_params or {}
        self.preprocessor_override = preprocessor_override
        self.registry = ArtifactRegistry()

    def run(self) -> TrainPipelineResult:
        total_started = time.perf_counter()
        run_id = self.config.run_id or make_run_id()

        ensure_output_dir(self.paths.output_dir)
        set_global_seed(self.config.random_state)

        report = TrainPipelineReport(
            run_id=run_id,
            pipeline_name=self.config.pipeline_name,
            environment=self.config.environment,
            experiment_name=self.config.experiment_name,
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            task_type=self.config.task_type,
            status="running",
            started_at=utc_now_iso(),
            metadata=self.config.extra_metadata,
        )

        try:
            logger.info(
                "train_pipeline.started",
                extra={
                    "run_id": run_id,
                    "input_path": str(self.paths.input_path),
                    "output_dir": str(self.paths.output_dir),
                },
            )

            loaded_artifact, df = self._load_input(report)
            validate_training_input(df, self.config.target_column)

            report.metrics.input_rows = int(df.shape[0])
            report.metrics.input_columns = int(df.shape[1])
            report.feature_report = create_feature_report(df)

            preprocessing = self._preprocess(df, report)

            X, y = split_features_target(
                preprocessing.dataframe,
                self.config.target_column,
            )

            X_train, X_val, X_test, y_train, y_val, y_test = self._split(
                X,
                y,
                report,
            )

            trained_model = self._train(X_train, y_train, report)

            evaluation = self._evaluate(
                trained_model,
                X_train,
                y_train,
                X_val,
                y_val,
                X_test,
                y_test,
                report,
            )
            report.evaluation = evaluation

            self._persist(
                model=trained_model,
                loaded_artifact=loaded_artifact,
                preprocessing=preprocessing,
                report=report,
            )

            report.status = "success"
            report.finished_at = utc_now_iso()
            report.metrics.total_duration_ms = elapsed_ms(total_started)

            self._save_report(report)

            logger.info(
                "train_pipeline.completed",
                extra={
                    "run_id": run_id,
                    "duration_ms": report.metrics.total_duration_ms,
                },
            )

            return TrainPipelineResult(
                model=trained_model,
                report=report,
                artifacts=TrainArtifacts(
                    model=trained_model,
                    preprocessing=preprocessing,
                    X_train=X_train,
                    X_val=X_val,
                    X_test=X_test,
                    y_train=y_train,
                    y_val=y_val,
                    y_test=y_test,
                ),
            )

        except Exception as exc:
            report.status = "failed"
            report.finished_at = utc_now_iso()
            report.metrics.total_duration_ms = elapsed_ms(total_started)
            report.errors.append(
                {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            )

            logger.exception(
                "train_pipeline.failed",
                extra={
                    "run_id": run_id,
                    "error": str(exc),
                },
            )

            self._save_report(report)

            if self.config.fail_fast:
                raise

            raise TrainPipelineError(str(exc)) from exc

    def _load_input(self, report: TrainPipelineReport) -> tuple[Any, pd.DataFrame]:
        started = time.perf_counter()

        artifact = load_by_extension(self.paths.input_path)
        df = dataframe_from_loaded_artifact(artifact)

        report.metrics.load_duration_ms = elapsed_ms(started)

        if hasattr(artifact, "path"):
            report.input_artifact = {
                "path": str(getattr(artifact, "path")),
                "size_bytes": getattr(artifact, "size_bytes", None),
                "sha256": getattr(artifact, "sha256", None),
                "metadata": getattr(artifact, "metadata", None),
            }

        logger.info(
            "train_pipeline.input_loaded",
            extra={
                "rows": int(df.shape[0]),
                "columns": int(df.shape[1]),
                "duration_ms": report.metrics.load_duration_ms,
            },
        )

        return artifact, df

    def _preprocess(
        self,
        df: pd.DataFrame,
        report: TrainPipelineReport,
    ) -> PreprocessingResult:
        started = time.perf_counter()

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
            test_size=self.config.preprocessing.test_size,
            validation_size=self.config.preprocessing.validation_size,
        )

        if self.preprocessor_override:
            processed_df = self.preprocessor_override(df)
            preprocessing = PreprocessingResult(
                dataframe=processed_df,
                report=preprocess_dataframe(
                    pd.DataFrame({"_dummy": [0]}),
                    PreprocessingConfig(),
                ).report,
                artifacts=preprocess_dataframe(
                    pd.DataFrame({"_dummy": [0]}),
                    PreprocessingConfig(),
                ).artifacts,
            )
        else:
            preprocessing = preprocess_dataframe(
                df,
                preprocessing_config,
                schema_rules=self.config.schema_rules,
            )

        report.metrics.preprocessing_duration_ms = elapsed_ms(started)
        report.metrics.preprocessed_rows = int(preprocessing.dataframe.shape[0])
        report.metrics.preprocessed_columns = int(preprocessing.dataframe.shape[1])
        report.preprocessing_report = preprocessing.report.to_dict()

        logger.info(
            "train_pipeline.preprocessing_completed",
            extra={
                "rows": report.metrics.preprocessed_rows,
                "columns": report.metrics.preprocessed_columns,
                "duration_ms": report.metrics.preprocessing_duration_ms,
            },
        )

        return preprocessing

    def _split(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        report: TrainPipelineReport,
    ) -> tuple[pd.DataFrame, pd.DataFrame | None, pd.DataFrame, pd.Series, pd.Series | None, pd.Series]:
        started = time.perf_counter()

        result = split_dataset(
            X,
            y,
            config=self.config.split,
            task_type=self.config.task_type,
        )

        X_train, X_val, X_test, y_train, y_val, y_test = result

        report.metrics.split_duration_ms = elapsed_ms(started)
        report.metrics.train_rows = int(len(X_train))
        report.metrics.validation_rows = int(len(X_val)) if X_val is not None else 0
        report.metrics.test_rows = int(len(X_test))

        return result

    def _train(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        report: TrainPipelineReport,
    ) -> Any:
        started = time.perf_counter()

        trained = train_model(
            self.model,
            X_train,
            y_train,
            fit_params=self.fit_params,
        )

        report.metrics.train_duration_ms = elapsed_ms(started)

        logger.info(
            "train_pipeline.model_trained",
            extra={
                "duration_ms": report.metrics.train_duration_ms,
                "rows": len(X_train),
            },
        )

        return trained

    def _evaluate(
        self,
        model: Any,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: pd.DataFrame | None,
        y_val: pd.Series | None,
        X_test: pd.DataFrame,
        y_test: pd.Series,
        report: TrainPipelineReport,
    ) -> dict[str, Any]:
        started = time.perf_counter()

        evaluation = evaluate_model(
            model,
            task_type=self.config.task_type,
            X_train=X_train,
            y_train=y_train,
            X_val=X_val,
            y_val=y_val,
            X_test=X_test,
            y_test=y_test,
        )

        report.metrics.evaluation_duration_ms = elapsed_ms(started)

        logger.info(
            "train_pipeline.model_evaluated",
            extra={
                "duration_ms": report.metrics.evaluation_duration_ms,
            },
        )

        return evaluation

    def _persist(
        self,
        *,
        model: Any,
        loaded_artifact: Any,
        preprocessing: PreprocessingResult,
        report: TrainPipelineReport,
    ) -> None:
        started = time.perf_counter()

        model_path = self.paths.output_dir / self.paths.model_filename
        metrics_path = self.paths.output_dir / self.paths.metrics_filename
        config_path = self.paths.output_dir / self.paths.config_filename
        feature_report_path = self.paths.output_dir / self.paths.feature_report_filename
        manifest_path = self.paths.output_dir / self.paths.manifest_filename

        model_artifact = save_joblib(
            model,
            model_path,
            metadata_extra={
                "artifact_type": "model",
                "run_id": report.run_id,
                "model_name": self.config.model_name,
                "model_version": self.config.model_version,
            },
        )
        self.registry.register("model", model_artifact)

        metrics_artifact = save_json(
            report.evaluation,
            metrics_path,
            options=self.config.serializer_options,
            metadata_extra={
                "artifact_type": "metrics",
                "run_id": report.run_id,
            },
        )
        self.registry.register("metrics", metrics_artifact)

        config_artifact = save_json(
            {
                "config": asdict(self.config),
                "fit_params": dict(self.fit_params),
            },
            config_path,
            options=self.config.serializer_options,
            metadata_extra={
                "artifact_type": "training_config",
                "run_id": report.run_id,
            },
        )
        self.registry.register("training_config", config_artifact)

        feature_artifact = save_json(
            report.feature_report or {},
            feature_report_path,
            options=self.config.serializer_options,
            metadata_extra={
                "artifact_type": "feature_report",
                "run_id": report.run_id,
            },
        )
        self.registry.register("feature_report", feature_artifact)

        if self.config.persist_preprocessed:
            preprocessed_path = self.paths.output_dir / "preprocessed.parquet"
            persist_dataframe(preprocessing.dataframe, preprocessed_path)

            preprocessed_artifact = save_artifact(
                {
                    "path": str(preprocessed_path),
                    "rows": int(preprocessing.dataframe.shape[0]),
                    "columns": list(preprocessing.dataframe.columns),
                },
                preprocessed_path.with_suffix(".metadata.json"),
                options=self.config.serializer_options,
                metadata_extra={
                    "artifact_type": "preprocessed_metadata",
                    "run_id": report.run_id,
                },
            )
            self.registry.register("preprocessed_metadata", preprocessed_artifact)

        manifest_artifact = self.registry.save_manifest(
            manifest_path,
            options=self.config.serializer_options,
        )
        self.registry.register("manifest", manifest_artifact)

        report.artifacts = {
            "input_path": str(getattr(loaded_artifact, "path", self.paths.input_path)),
            "model_path": str(model_path),
            "metrics_path": str(metrics_path),
            "config_path": str(config_path),
            "feature_report_path": str(feature_report_path),
            "manifest_path": str(manifest_path),
        }

        if self.config.persist_preprocessed:
            report.artifacts["preprocessed_path"] = str(
                self.paths.output_dir / "preprocessed.parquet"
            )

        report.metrics.persistence_duration_ms = elapsed_ms(started)

    def _save_report(self, report: TrainPipelineReport) -> None:
        report_path = self.paths.output_dir / self.paths.report_filename

        try:
            save_json(
                report.to_dict(),
                report_path,
                options=self.config.serializer_options,
                metadata_extra={
                    "artifact_type": "train_pipeline_report",
                    "run_id": report.run_id,
                },
            )
        except Exception:
            logger.exception(
                "train_pipeline.report_persist_failed",
                extra={"run_id": report.run_id},
            )


def run_train_pipeline(
    model: BaseEstimator | ModelProtocol | Callable[..., Any],
    input_path: str | Path,
    output_dir: str | Path,
    *,
    config: TrainPipelineConfig | None = None,
    fit_params: Mapping[str, Any] | None = None,
    preprocessor_override: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
) -> TrainPipelineResult:
    pipeline = TrainPipeline(
        model=model,
        paths=TrainPipelinePaths(
            input_path=Path(input_path),
            output_dir=Path(output_dir),
        ),
        config=config,
        fit_params=fit_params,
        preprocessor_override=preprocessor_override,
    )

    return pipeline.run()


__all__ = [
    "ModelProtocol",
    "TrainArtifacts",
    "TrainPipeline",
    "TrainPipelineConfig",
    "TrainPipelineError",
    "TrainPipelineMetrics",
    "TrainPipelinePaths",
    "TrainPipelineReport",
    "TrainPipelineResult",
    "TrainPipelineValidationError",
    "TrainSplitConfig",
    "TrainTaskType",
    "dataframe_from_loaded_artifact",
    "elapsed_ms",
    "ensure_output_dir",
    "evaluate_classification",
    "evaluate_model",
    "evaluate_regression",
    "infer_stratify_y",
    "make_run_id",
    "persist_dataframe",
    "run_train_pipeline",
    "set_global_seed",
    "split_dataset",
    "train_model",
    "utc_now_iso",
    "validate_training_input",
]
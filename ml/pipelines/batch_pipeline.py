"""
ml/pipelines/batch_pipeline.py

Enterprise-grade batch ML pipeline.

Responsabilidades:
- Carregar dados de entrada
- Validar schema e qualidade mínima
- Aplicar pré-processamento
- Executar inferência batch
- Persistir predições, relatórios e manifestos
- Registrar auditoria, métricas e rastreabilidade
- Suportar execução idempotente e reprodutível
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

import pandas as pd

try:
    from ml.utils.batch_serving import (
        BatchServingConfig,
        BatchServingEngine,
        BatchServingResult,
    )
    from ml.utils.loaders import load_by_extension
    from ml.utils.preprocessing import (
        PreprocessingConfig,
        PreprocessingResult,
        SchemaRule,
        create_feature_report,
        preprocess_dataframe,
    )
    from ml.utils.serializers import (
        ArtifactRegistry,
        SerializerOptions,
        save_artifact,
        save_json,
    )
except ImportError:  # pragma: no cover
    from ..utils.batch_serving import (
        BatchServingConfig,
        BatchServingEngine,
        BatchServingResult,
    )
    from ..utils.loaders import load_by_extension
    from ..utils.preprocessing import (
        PreprocessingConfig,
        PreprocessingResult,
        SchemaRule,
        create_feature_report,
        preprocess_dataframe,
    )
    from ..utils.serializers import (
        ArtifactRegistry,
        SerializerOptions,
        save_artifact,
        save_json,
    )


logger = logging.getLogger(__name__)


class BatchPipelineError(Exception):
    """Erro base do pipeline batch."""


class BatchPipelineValidationError(BatchPipelineError):
    """Erro de validação do pipeline batch."""


class ModelProtocol(Protocol):
    def predict(self, data: Any) -> Any:
        ...


@dataclass(frozen=True)
class BatchPipelinePaths:
    input_path: Path
    output_dir: Path
    predictions_filename: str = "predictions.parquet"
    report_filename: str = "batch_pipeline_report.json"
    manifest_filename: str = "manifest.json"
    feature_report_filename: str = "feature_report.json"
    preprocessed_filename: str | None = None


@dataclass(frozen=True)
class BatchPipelineConfig:
    pipeline_name: str = "batch_pipeline"
    environment: str = "dev"
    model_name: str = "model"
    model_version: str = "unknown"
    run_id: str | None = None
    fail_fast: bool = True
    persist_preprocessed: bool = False
    include_input_in_predictions: bool = False
    serializer_options: SerializerOptions = field(default_factory=SerializerOptions)
    preprocessing: PreprocessingConfig = field(default_factory=PreprocessingConfig)
    serving: BatchServingConfig = field(default_factory=BatchServingConfig)
    schema_rules: tuple[SchemaRule, ...] = ()


@dataclass
class BatchPipelineMetrics:
    input_rows: int = 0
    input_columns: int = 0
    preprocessed_rows: int = 0
    preprocessed_columns: int = 0
    prediction_rows: int = 0
    duration_ms: int = 0
    load_duration_ms: int = 0
    preprocessing_duration_ms: int = 0
    serving_duration_ms: int = 0
    persistence_duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_rows": self.input_rows,
            "input_columns": self.input_columns,
            "preprocessed_rows": self.preprocessed_rows,
            "preprocessed_columns": self.preprocessed_columns,
            "prediction_rows": self.prediction_rows,
            "duration_ms": self.duration_ms,
            "load_duration_ms": self.load_duration_ms,
            "preprocessing_duration_ms": self.preprocessing_duration_ms,
            "serving_duration_ms": self.serving_duration_ms,
            "persistence_duration_ms": self.persistence_duration_ms,
        }


@dataclass
class BatchPipelineReport:
    run_id: str
    pipeline_name: str
    environment: str
    model_name: str
    model_version: str
    status: str
    started_at: str
    finished_at: str | None = None
    metrics: BatchPipelineMetrics = field(default_factory=BatchPipelineMetrics)
    input_artifact: dict[str, Any] | None = None
    preprocessing_report: dict[str, Any] | None = None
    serving_report: dict[str, Any] | None = None
    feature_report: dict[str, Any] | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pipeline_name": self.pipeline_name,
            "environment": self.environment,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metrics": self.metrics.to_dict(),
            "input_artifact": self.input_artifact,
            "preprocessing_report": self.preprocessing_report,
            "serving_report": self.serving_report,
            "feature_report": self.feature_report,
            "artifacts": self.artifacts,
            "errors": self.errors,
        }


@dataclass
class BatchPipelineResult:
    predictions: pd.DataFrame
    report: BatchPipelineReport
    preprocessing: PreprocessingResult
    serving: BatchServingResult


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_run_id() -> str:
    return str(uuid.uuid4())


def elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def validate_pipeline_input(df: pd.DataFrame) -> None:
    if not isinstance(df, pd.DataFrame):
        raise BatchPipelineValidationError("Entrada precisa ser um pandas DataFrame.")

    if df.empty:
        raise BatchPipelineValidationError("Dataset de entrada está vazio.")

    if df.columns.duplicated().any():
        duplicated = df.columns[df.columns.duplicated()].tolist()
        raise BatchPipelineValidationError(f"Colunas duplicadas encontradas: {duplicated}")


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

    raise BatchPipelineValidationError(
        f"Não foi possível converter artefato carregado para DataFrame: {type(data).__name__}"
    )


def save_dataframe(df: pd.DataFrame, path: Path) -> None:
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

    raise BatchPipelineError(f"Formato de saída não suportado: {suffix}")


class BatchPipeline:
    def __init__(
        self,
        model: ModelProtocol | Callable[[Any], Any],
        paths: BatchPipelinePaths,
        *,
        config: BatchPipelineConfig | None = None,
        preprocessor_override: Callable[[pd.DataFrame], Any] | None = None,
        postprocessor: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    ) -> None:
        self.model = model
        self.paths = paths
        self.config = config or BatchPipelineConfig()
        self.preprocessor_override = preprocessor_override
        self.postprocessor = postprocessor
        self.registry = ArtifactRegistry()

    def run(self) -> BatchPipelineResult:
        run_started = time.perf_counter()
        run_id = self.config.run_id or make_run_id()

        ensure_output_dir(self.paths.output_dir)

        report = BatchPipelineReport(
            run_id=run_id,
            pipeline_name=self.config.pipeline_name,
            environment=self.config.environment,
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            status="running",
            started_at=utc_now_iso(),
        )

        try:
            logger.info(
                "batch_pipeline.started",
                extra={
                    "run_id": run_id,
                    "pipeline_name": self.config.pipeline_name,
                    "input_path": str(self.paths.input_path),
                    "output_dir": str(self.paths.output_dir),
                },
            )

            loaded_artifact, input_df = self._load_input(report)
            validate_pipeline_input(input_df)

            report.metrics.input_rows = int(input_df.shape[0])
            report.metrics.input_columns = int(input_df.shape[1])

            feature_report = create_feature_report(input_df)
            report.feature_report = feature_report

            preprocessing_result = self._preprocess(input_df, report)

            predictions_result = self._serve(preprocessing_result.dataframe, report)

            self._persist(
                loaded_artifact=loaded_artifact,
                preprocessing_result=preprocessing_result,
                serving_result=predictions_result,
                report=report,
            )

            report.status = "success"
            report.finished_at = utc_now_iso()
            report.metrics.duration_ms = elapsed_ms(run_started)

            self._save_report(report)

            logger.info(
                "batch_pipeline.completed",
                extra={
                    "run_id": run_id,
                    "status": report.status,
                    "duration_ms": report.metrics.duration_ms,
                },
            )

            return BatchPipelineResult(
                predictions=predictions_result.predictions,
                report=report,
                preprocessing=preprocessing_result,
                serving=predictions_result,
            )

        except Exception as exc:
            report.status = "failed"
            report.finished_at = utc_now_iso()
            report.metrics.duration_ms = elapsed_ms(run_started)
            report.errors.append(
                {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            )

            logger.exception(
                "batch_pipeline.failed",
                extra={
                    "run_id": run_id,
                    "error": str(exc),
                },
            )

            self._save_report(report)

            if self.config.fail_fast:
                raise

            empty_predictions = pd.DataFrame()
            empty_preprocessing = PreprocessingResult(
                dataframe=pd.DataFrame(),
                report=getattr(preprocess_dataframe(pd.DataFrame({"_": [0]})), "report"),
                artifacts=getattr(preprocess_dataframe(pd.DataFrame({"_": [0]})), "artifacts"),
            )
            empty_serving = BatchServingResult(
                predictions=empty_predictions,
                report=getattr(
                    BatchServingEngine(lambda x: []).predict(pd.DataFrame()),
                    "report",
                ),
            )

            return BatchPipelineResult(
                predictions=empty_predictions,
                report=report,
                preprocessing=empty_preprocessing,
                serving=empty_serving,
            )

    def _load_input(self, report: BatchPipelineReport) -> tuple[Any, pd.DataFrame]:
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
            "batch_pipeline.input_loaded",
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
        report: BatchPipelineReport,
    ) -> PreprocessingResult:
        started = time.perf_counter()

        result = preprocess_dataframe(
            df,
            self.config.preprocessing,
            schema_rules=self.config.schema_rules,
        )

        report.metrics.preprocessing_duration_ms = elapsed_ms(started)
        report.metrics.preprocessed_rows = int(result.dataframe.shape[0])
        report.metrics.preprocessed_columns = int(result.dataframe.shape[1])
        report.preprocessing_report = result.report.to_dict()

        logger.info(
            "batch_pipeline.preprocessing_completed",
            extra={
                "rows": report.metrics.preprocessed_rows,
                "columns": report.metrics.preprocessed_columns,
                "duration_ms": report.metrics.preprocessing_duration_ms,
            },
        )

        return result

    def _serve(
        self,
        df: pd.DataFrame,
        report: BatchPipelineReport,
    ) -> BatchServingResult:
        started = time.perf_counter()

        serving_config = BatchServingConfig(
            batch_size=self.config.serving.batch_size,
            max_rows=self.config.serving.max_rows,
            retry_policy=self.config.serving.retry_policy,
            include_input=self.config.include_input_in_predictions,
            prediction_column=self.config.serving.prediction_column,
            probability_column=self.config.serving.probability_column,
            persist_path=None,
            persist_format=self.config.serving.persist_format,
            fail_fast=self.config.serving.fail_fast,
        )

        engine = BatchServingEngine(
            self.model,
            config=serving_config,
            preprocessor=self.preprocessor_override,
            postprocessor=self.postprocessor,
        )

        result = engine.predict(df)

        report.metrics.serving_duration_ms = elapsed_ms(started)
        report.metrics.prediction_rows = int(result.predictions.shape[0])
        report.serving_report = result.report.to_dict()

        logger.info(
            "batch_pipeline.serving_completed",
            extra={
                "prediction_rows": report.metrics.prediction_rows,
                "duration_ms": report.metrics.serving_duration_ms,
            },
        )

        return result

    def _persist(
        self,
        *,
        loaded_artifact: Any,
        preprocessing_result: PreprocessingResult,
        serving_result: BatchServingResult,
        report: BatchPipelineReport,
    ) -> None:
        started = time.perf_counter()

        predictions_path = self.paths.output_dir / self.paths.predictions_filename
        feature_report_path = self.paths.output_dir / self.paths.feature_report_filename

        save_dataframe(serving_result.predictions, predictions_path)

        prediction_artifact = save_artifact(
            {
                "path": str(predictions_path),
                "rows": int(serving_result.predictions.shape[0]),
                "columns": list(serving_result.predictions.columns),
            },
            predictions_path.with_suffix(predictions_path.suffix + ".metadata.json"),
            options=self.config.serializer_options,
            metadata_extra={
                "artifact_type": "predictions_metadata",
                "run_id": report.run_id,
            },
        )

        self.registry.register("predictions_metadata", prediction_artifact)

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
            filename = self.paths.preprocessed_filename or "preprocessed.parquet"
            preprocessed_path = self.paths.output_dir / filename

            save_dataframe(preprocessing_result.dataframe, preprocessed_path)

            preprocessed_artifact = save_artifact(
                {
                    "path": str(preprocessed_path),
                    "rows": int(preprocessing_result.dataframe.shape[0]),
                    "columns": list(preprocessing_result.dataframe.columns),
                },
                preprocessed_path.with_suffix(preprocessed_path.suffix + ".metadata.json"),
                options=self.config.serializer_options,
                metadata_extra={
                    "artifact_type": "preprocessed_metadata",
                    "run_id": report.run_id,
                },
            )

            self.registry.register("preprocessed_metadata", preprocessed_artifact)

        manifest_path = self.paths.output_dir / self.paths.manifest_filename
        manifest_artifact = self.registry.save_manifest(
            manifest_path,
            options=self.config.serializer_options,
        )

        self.registry.register("manifest", manifest_artifact)

        report.artifacts = {
            "input_path": str(getattr(loaded_artifact, "path", self.paths.input_path)),
            "predictions_path": str(predictions_path),
            "feature_report_path": str(feature_report_path),
            "manifest_path": str(manifest_path),
        }

        if self.config.persist_preprocessed:
            report.artifacts["preprocessed_path"] = str(
                self.paths.output_dir / (self.paths.preprocessed_filename or "preprocessed.parquet")
            )

        report.metrics.persistence_duration_ms = elapsed_ms(started)

    def _save_report(self, report: BatchPipelineReport) -> None:
        report_path = self.paths.output_dir / self.paths.report_filename

        try:
            save_json(
                report.to_dict(),
                report_path,
                options=self.config.serializer_options,
                metadata_extra={
                    "artifact_type": "batch_pipeline_report",
                    "run_id": report.run_id,
                },
            )
        except Exception:
            logger.exception(
                "batch_pipeline.report_persist_failed",
                extra={"run_id": report.run_id},
            )


def run_batch_pipeline(
    model: ModelProtocol | Callable[[Any], Any],
    input_path: str | Path,
    output_dir: str | Path,
    *,
    config: BatchPipelineConfig | None = None,
    preprocessor_override: Callable[[pd.DataFrame], Any] | None = None,
    postprocessor: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
) -> BatchPipelineResult:
    pipeline = BatchPipeline(
        model=model,
        paths=BatchPipelinePaths(
            input_path=Path(input_path),
            output_dir=Path(output_dir),
        ),
        config=config,
        preprocessor_override=preprocessor_override,
        postprocessor=postprocessor,
    )

    return pipeline.run()


__all__ = [
    "BatchPipeline",
    "BatchPipelineConfig",
    "BatchPipelineError",
    "BatchPipelineMetrics",
    "BatchPipelinePaths",
    "BatchPipelineReport",
    "BatchPipelineResult",
    "BatchPipelineValidationError",
    "ModelProtocol",
    "dataframe_from_loaded_artifact",
    "elapsed_ms",
    "ensure_output_dir",
    "make_run_id",
    "run_batch_pipeline",
    "save_dataframe",
    "utc_now_iso",
    "validate_pipeline_input",
]
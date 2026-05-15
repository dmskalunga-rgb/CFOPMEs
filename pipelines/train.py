"""
kwanza-ai-core/pipelines/train.py

Enterprise-grade model training pipeline CLI.

Purpose
-------
Run reproducible ML training jobs using Kwanza AI Core TrainingService with
configurable dataset loading, split strategy, hyperparameters, quality gates,
artifact persistence, model registration and optional promotion.

Typical usage
-------------
python -m kwanza_ai_core.pipelines.train \
  --dataset-id fraud-demo \
  --dataset-uri data/fraud_train.jsonl \
  --model-name fraud-detector \
  --task classification \
  --target-column is_fraud \
  --tenant-id tenant-ao \
  --quality-gate accuracy:0.75:maximize \
  --auto-register

Supported local dataset formats
-------------------------------
- .json  : list of objects or {"rows": [...]}
- .jsonl : one JSON object per line
- .csv   : header-based CSV

Production deployments can replace the local dataset loader with adapters for
Supabase, PostgreSQL, data lake, warehouse, feature store or object storage.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence

try:
    from kwanza_ai_core.services.training_service import (
        ArtifactStore,
        DatasetReference,
        InMemoryDatasetLoader,
        InMemoryModelRegistry,
        InMemoryTrainerRegistry,
        LocalArtifactStore,
        ModelStage,
        QualityGate,
        SplitConfig,
        DatasetSplitStrategy,
        TrainingHyperparameters,
        TrainingRequest,
        TrainingService,
        TrainingServiceConfig,
        TrainingStatus,
        TrainingTask,
        MetricDirection,
        build_training_service,
    )
except Exception:  # pragma: no cover - local fallback
    try:
        from services.training_service import (  # type: ignore
            ArtifactStore,
            DatasetReference,
            InMemoryDatasetLoader,
            InMemoryModelRegistry,
            InMemoryTrainerRegistry,
            LocalArtifactStore,
            ModelStage,
            QualityGate,
            SplitConfig,
            DatasetSplitStrategy,
            TrainingHyperparameters,
            TrainingRequest,
            TrainingService,
            TrainingServiceConfig,
            TrainingStatus,
            TrainingTask,
            MetricDirection,
            build_training_service,
        )
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Unable to import TrainingService. Run inside the kwanza-ai-core project.") from exc

logger = logging.getLogger("kwanza_ai_core.pipelines.train")

JsonDict = Dict[str, Any]


# =============================================================================
# Exceptions
# =============================================================================


class TrainPipelineError(RuntimeError):
    """Base exception for train pipeline failures."""


class TrainPipelineValidationError(TrainPipelineError):
    """Raised when CLI/config validation fails."""


# =============================================================================
# Pipeline models
# =============================================================================


class DatasetFormat(str, Enum):
    AUTO = "auto"
    JSON = "json"
    JSONL = "jsonl"
    CSV = "csv"


@dataclass(frozen=True)
class TrainPipelineConfig:
    dataset_id: str
    model_name: str
    task: TrainingTask
    dataset_uri: Optional[Path] = None
    dataset_format: DatasetFormat = DatasetFormat.AUTO
    tenant_id: Optional[str] = None
    target_column: Optional[str] = None
    timestamp_column: Optional[str] = None
    feature_columns: Optional[Sequence[str]] = None
    trainer_name: Optional[str] = None
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    requested_by: Optional[str] = None
    split_strategy: DatasetSplitStrategy = DatasetSplitStrategy.RANDOM
    train_ratio: float = 0.7
    validation_ratio: float = 0.15
    test_ratio: float = 0.15
    random_seed: int = 42
    stratify_column: Optional[str] = None
    hyperparameters: Mapping[str, Any] = field(default_factory=dict)
    quality_gates: Sequence[QualityGate] = field(default_factory=tuple)
    tags: Mapping[str, str] = field(default_factory=dict)
    artifact_base_path: Path = Path("/tmp/kwanza-ai-core/training-artifacts")
    max_rows: int = 2_000_000
    min_rows: int = 10
    max_columns: int = 10_000
    timeout_seconds: int = 3_600
    auto_register: bool = True
    auto_promote: bool = False
    promote_stage: ModelStage = ModelStage.PRODUCTION
    idempotency_key: Optional[str] = None
    output_path: Optional[Path] = None
    dry_run: bool = False
    fail_on_gate_failure: bool = False

    def validate(self) -> None:
        if not self.dataset_id:
            raise TrainPipelineValidationError("dataset_id is required.")
        if not self.model_name:
            raise TrainPipelineValidationError("model_name is required.")
        if self.dataset_uri and not self.dataset_uri.exists():
            raise TrainPipelineValidationError(f"dataset_uri not found: {self.dataset_uri}")
        if self.task in {TrainingTask.CLASSIFICATION, TrainingTask.REGRESSION, TrainingTask.FORECASTING} and not self.target_column:
            raise TrainPipelineValidationError(f"target_column is recommended/required for task {self.task.value}.")
        total = self.train_ratio + self.validation_ratio + self.test_ratio
        if abs(total - 1.0) > 0.0001:
            raise TrainPipelineValidationError("train_ratio + validation_ratio + test_ratio must equal 1.0.")
        if self.timeout_seconds <= 0:
            raise TrainPipelineValidationError("timeout_seconds must be positive.")


@dataclass(frozen=True)
class TrainPipelineSummary:
    job_id: str
    status: str
    model_name: str
    task: str
    tenant_id: Optional[str]
    dataset_id: str
    model_version: Optional[str]
    model_stage: Optional[str]
    metrics: Optional[Mapping[str, Any]]
    gate_results: Mapping[str, bool]
    artifact_count: int
    warnings: Sequence[str]
    output_path: Optional[str]
    started_at: str
    completed_at: str
    processing_ms: float

    def to_dict(self) -> JsonDict:
        return asdict(self)


# =============================================================================
# Light metrics/audit
# =============================================================================


class MetricsClient(Protocol):
    def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None: ...

    def timing(self, name: str, value_ms: float, tags: Optional[Mapping[str, str]] = None) -> None: ...

    def gauge(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None: ...


class AuditSink(Protocol):
    async def write(self, event_name: str, payload: Mapping[str, Any]) -> None: ...


class NoopMetricsClient:
    def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        return None

    def timing(self, name: str, value_ms: float, tags: Optional[Mapping[str, str]] = None) -> None:
        return None

    def gauge(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        return None


class NoopAuditSink:
    async def write(self, event_name: str, payload: Mapping[str, Any]) -> None:
        return None


# =============================================================================
# Dataset loader helpers
# =============================================================================


def infer_dataset_format(path: Path, configured: DatasetFormat) -> DatasetFormat:
    if configured != DatasetFormat.AUTO:
        return configured
    suffix = path.suffix.lower()
    if suffix == ".json":
        return DatasetFormat.JSON
    if suffix == ".jsonl":
        return DatasetFormat.JSONL
    if suffix == ".csv":
        return DatasetFormat.CSV
    raise TrainPipelineValidationError(f"Cannot infer dataset format from extension: {path.suffix}")


def read_dataset(path: Path, dataset_format: DatasetFormat) -> Sequence[Mapping[str, Any]]:
    fmt = infer_dataset_format(path, dataset_format)
    if fmt == DatasetFormat.JSON:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, Mapping) and isinstance(payload.get("rows"), list):
            payload = payload["rows"]
        elif isinstance(payload, Mapping):
            payload = [payload]
        if not isinstance(payload, list) or not all(isinstance(row, Mapping) for row in payload):
            raise TrainPipelineValidationError("JSON dataset must be an object, list of objects, or {'rows': [...]}.")
        return [dict(row) for row in payload]
    if fmt == DatasetFormat.JSONL:
        rows: List[Mapping[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise TrainPipelineValidationError(f"Invalid JSONL at line {line_number}: {exc}") from exc
                if not isinstance(row, Mapping):
                    raise TrainPipelineValidationError(f"JSONL line {line_number} must be an object.")
                rows.append(dict(row))
        return rows
    if fmt == DatasetFormat.CSV:
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            return [dict(row) for row in reader]
    raise TrainPipelineValidationError(f"Unsupported dataset format: {fmt}")


def parse_json_value(value: Optional[str], default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise TrainPipelineValidationError(f"Invalid JSON value: {value}") from exc


def parse_tags(items: Sequence[str]) -> Mapping[str, str]:
    tags: Dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise TrainPipelineValidationError(f"Invalid tag '{item}'. Expected key=value.")
        key, value = item.split("=", 1)
        tags[key.strip()] = value.strip()
    return tags


def parse_quality_gate(raw: str) -> QualityGate:
    """Parse metric:threshold:direction[:required]. Example: accuracy:0.8:maximize:true"""
    parts = raw.split(":")
    if len(parts) < 2:
        raise TrainPipelineValidationError(f"Invalid quality gate '{raw}'. Expected metric:threshold[:direction][:required].")
    metric = parts[0].strip()
    threshold = float(parts[1])
    direction = MetricDirection(parts[2]) if len(parts) >= 3 and parts[2] else MetricDirection.MAXIMIZE
    required = True if len(parts) < 4 else parts[3].lower() in {"1", "true", "yes", "y"}
    return QualityGate(metric_name=metric, threshold=threshold, direction=direction, required=required)


# =============================================================================
# Pipeline
# =============================================================================


class TrainPipeline:
    def __init__(
        self,
        config: TrainPipelineConfig,
        training_service: Optional[TrainingService] = None,
        metrics: Optional[MetricsClient] = None,
        audit_sink: Optional[AuditSink] = None,
    ) -> None:
        config.validate()
        self.config = config
        self.metrics = metrics or NoopMetricsClient()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.training_service = training_service or self._build_default_service(config)

    async def run(self) -> TrainPipelineSummary:
        started = datetime.now(UTC)
        self.metrics.increment("train_pipeline.started", tags=self._tags())
        await self._audit("train_pipeline.started", {"config": self._safe_config()})

        if self.config.dry_run:
            summary = TrainPipelineSummary(
                job_id=self.config.job_id,
                status="dry_run",
                model_name=self.config.model_name,
                task=self.config.task.value,
                tenant_id=self.config.tenant_id,
                dataset_id=self.config.dataset_id,
                model_version=None,
                model_stage=None,
                metrics=None,
                gate_results={},
                artifact_count=0,
                warnings=("Dry run completed. Training was not executed.",),
                output_path=str(self.config.output_path) if self.config.output_path else None,
                started_at=started.isoformat(),
                completed_at=datetime.now(UTC).isoformat(),
                processing_ms=0.0,
            )
            await self._write_output(summary.to_dict())
            await self._audit("train_pipeline.dry_run.completed", summary.to_dict())
            return summary

        request = self._build_training_request()
        result = await self.training_service.train(request)

        if self.config.fail_on_gate_failure and result.gate_results and not all(result.gate_results.values()):
            raise TrainPipelineError(f"Quality gates failed: {result.gate_results}")

        if self.config.auto_promote and result.model_version and result.model_version.stage != self.config.promote_stage:
            promoted = await self.training_service.promote_model(result.model_version, self.config.promote_stage)
            result = await self.training_service.get_job(result.tenant_id, result.job_id) or result
            model_version = promoted
        else:
            model_version = result.model_version

        completed = datetime.now(UTC)
        metrics_payload = asdict(result.metrics) if result.metrics else None
        summary = TrainPipelineSummary(
            job_id=result.job_id,
            status=result.status.value,
            model_name=result.model_name,
            task=result.task.value,
            tenant_id=result.tenant_id,
            dataset_id=self.config.dataset_id,
            model_version=model_version.version if model_version else None,
            model_stage=model_version.stage.value if model_version else None,
            metrics=metrics_payload,
            gate_results=result.gate_results,
            artifact_count=len(result.artifacts),
            warnings=result.warnings,
            output_path=str(self.config.output_path) if self.config.output_path else None,
            started_at=result.started_at.isoformat(),
            completed_at=completed.isoformat(),
            processing_ms=result.processing_ms,
        )
        await self._write_output({"summary": summary.to_dict(), "result": result.to_dict()})
        self.metrics.increment("train_pipeline.completed", tags={**self._tags(), "status": result.status.value})
        self.metrics.timing("train_pipeline.processing_ms", result.processing_ms, tags=self._tags())
        await self._audit("train_pipeline.completed", summary.to_dict())
        return summary

    def _build_default_service(self, config: TrainPipelineConfig) -> TrainingService:
        loader = InMemoryDatasetLoader()
        if config.dataset_uri:
            rows = read_dataset(config.dataset_uri, config.dataset_format)
            loader.add_dataset(config.dataset_id, rows)
        service_config = TrainingServiceConfig(
            max_rows=config.max_rows,
            min_rows=config.min_rows,
            max_columns=config.max_columns,
            default_timeout_seconds=config.timeout_seconds,
            artifact_base_path=str(config.artifact_base_path),
        )
        return build_training_service(
            dataset_loader=loader,
            trainer_registry=InMemoryTrainerRegistry(),
            artifact_store=LocalArtifactStore(str(config.artifact_base_path)),
            model_registry=InMemoryModelRegistry(),
            config=service_config,
            metrics=self.metrics,
            audit_sink=self.audit_sink,
        )

    def _build_training_request(self) -> TrainingRequest:
        return TrainingRequest(
            model_name=self.config.model_name,
            dataset=DatasetReference(
                dataset_id=self.config.dataset_id,
                tenant_id=self.config.tenant_id,
                uri=str(self.config.dataset_uri) if self.config.dataset_uri else None,
                format=self.config.dataset_format.value,
                target_column=self.config.target_column,
                timestamp_column=self.config.timestamp_column,
                feature_columns=tuple(self.config.feature_columns or ()) or None,
            ),
            task=self.config.task,
            tenant_id=self.config.tenant_id,
            job_id=self.config.job_id,
            requested_by=self.config.requested_by,
            trainer_name=self.config.trainer_name,
            split_config=SplitConfig(
                strategy=self.config.split_strategy,
                train_ratio=self.config.train_ratio,
                validation_ratio=self.config.validation_ratio,
                test_ratio=self.config.test_ratio,
                random_seed=self.config.random_seed,
                stratify_column=self.config.stratify_column,
                timestamp_column=self.config.timestamp_column,
            ),
            hyperparameters=TrainingHyperparameters(self.config.hyperparameters),
            quality_gates=tuple(self.config.quality_gates),
            tags=self.config.tags,
            idempotency_key=self.config.idempotency_key,
            auto_register=self.config.auto_register,
            auto_promote=self.config.auto_promote,
            metadata={"pipeline": "train.py"},
        )

    async def _write_output(self, payload: Mapping[str, Any]) -> None:
        if not self.config.output_path:
            print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
            return
        self.config.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    def _tags(self) -> Dict[str, str]:
        return {
            "tenant_id": self.config.tenant_id or "global",
            "task": self.config.task.value,
            "model_name": self.config.model_name,
            "job_id": self.config.job_id,
        }

    def _safe_config(self) -> Mapping[str, Any]:
        payload = asdict(self.config)
        payload["dataset_uri"] = str(self.config.dataset_uri) if self.config.dataset_uri else None
        payload["artifact_base_path"] = str(self.config.artifact_base_path)
        payload["output_path"] = str(self.config.output_path) if self.config.output_path else None
        payload["task"] = self.config.task.value
        payload["dataset_format"] = self.config.dataset_format.value
        payload["split_strategy"] = self.config.split_strategy.value
        payload["promote_stage"] = self.config.promote_stage.value
        payload["quality_gates"] = [asdict(gate) for gate in self.config.quality_gates]
        return payload

    async def _audit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        try:
            await self.audit_sink.write(event_name, payload)
        except Exception:
            logger.exception("Failed to write train pipeline audit event", extra={"event_name": event_name})


# =============================================================================
# CLI
# =============================================================================


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enterprise training pipeline")
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--dataset-uri", default=None)
    parser.add_argument("--dataset-format", default=DatasetFormat.AUTO.value, choices=[x.value for x in DatasetFormat])
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--task", default=TrainingTask.GENERIC.value, choices=[x.value for x in TrainingTask])
    parser.add_argument("--tenant-id", default=None)
    parser.add_argument("--target-column", default=None)
    parser.add_argument("--timestamp-column", default=None)
    parser.add_argument("--feature-column", action="append", default=[])
    parser.add_argument("--trainer-name", default=None)
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--requested-by", default=None)
    parser.add_argument("--split-strategy", default=DatasetSplitStrategy.RANDOM.value, choices=[x.value for x in DatasetSplitStrategy])
    parser.add_argument("--train-ratio", type=float, default=0.7)
    parser.add_argument("--validation-ratio", type=float, default=0.15)
    parser.add_argument("--test-ratio", type=float, default=0.15)
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument("--stratify-column", default=None)
    parser.add_argument("--hyperparameters-json", default=None, help='JSON object, e.g. {"n_estimators":100}')
    parser.add_argument("--quality-gate", action="append", default=[], help="metric:threshold[:maximize|minimize][:required]")
    parser.add_argument("--tag", action="append", default=[], help="key=value")
    parser.add_argument("--artifact-base-path", default="/tmp/kwanza-ai-core/training-artifacts")
    parser.add_argument("--max-rows", type=int, default=2_000_000)
    parser.add_argument("--min-rows", type=int, default=10)
    parser.add_argument("--max-columns", type=int, default=10_000)
    parser.add_argument("--timeout-seconds", type=int, default=3_600)
    parser.add_argument("--auto-register", action="store_true")
    parser.add_argument("--no-auto-register", action="store_true")
    parser.add_argument("--auto-promote", action="store_true")
    parser.add_argument("--promote-stage", default=ModelStage.PRODUCTION.value, choices=[x.value for x in ModelStage])
    parser.add_argument("--idempotency-key", default=None)
    parser.add_argument("--output", dest="output_path", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-on-gate-failure", action="store_true")
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> TrainPipelineConfig:
    auto_register = True
    if args.no_auto_register:
        auto_register = False
    if args.auto_register:
        auto_register = True

    return TrainPipelineConfig(
        dataset_id=args.dataset_id,
        dataset_uri=Path(args.dataset_uri) if args.dataset_uri else None,
        dataset_format=DatasetFormat(args.dataset_format),
        model_name=args.model_name,
        task=TrainingTask(args.task),
        tenant_id=args.tenant_id,
        target_column=args.target_column,
        timestamp_column=args.timestamp_column,
        feature_columns=tuple(args.feature_column or ()) or None,
        trainer_name=args.trainer_name,
        job_id=args.job_id or str(uuid.uuid4()),
        requested_by=args.requested_by,
        split_strategy=DatasetSplitStrategy(args.split_strategy),
        train_ratio=args.train_ratio,
        validation_ratio=args.validation_ratio,
        test_ratio=args.test_ratio,
        random_seed=args.random_seed,
        stratify_column=args.stratify_column,
        hyperparameters=parse_json_value(args.hyperparameters_json, {}),
        quality_gates=tuple(parse_quality_gate(raw) for raw in args.quality_gate),
        tags=parse_tags(args.tag),
        artifact_base_path=Path(args.artifact_base_path),
        max_rows=args.max_rows,
        min_rows=args.min_rows,
        max_columns=args.max_columns,
        timeout_seconds=args.timeout_seconds,
        auto_register=auto_register,
        auto_promote=args.auto_promote,
        promote_stage=ModelStage(args.promote_stage),
        idempotency_key=args.idempotency_key,
        output_path=Path(args.output_path) if args.output_path else None,
        dry_run=args.dry_run,
        fail_on_gate_failure=args.fail_on_gate_failure,
    )


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def async_main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level)
    try:
        config = config_from_args(args)
        pipeline = TrainPipeline(config)
        summary = await pipeline.run()
        if summary.status in {TrainingStatus.FAILED.value, TrainingStatus.REJECTED.value}:
            return 2
        return 0
    except Exception as exc:
        logger.exception("Training pipeline failed")
        print(json.dumps({"status": "failed", "error": f"{exc.__class__.__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 1


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()

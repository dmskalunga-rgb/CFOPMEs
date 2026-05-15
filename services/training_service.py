"""
kwanza-ai-core/services/training_service.py

Enterprise-grade ML training orchestration service.

Purpose
-------
Centralize model training workflows for Kwanza AI Core: dataset preparation,
validation, train/validation/test splits, trainer execution, evaluation, artifact
persistence, experiment tracking, model registry integration and promotion gates.

Design goals
------------
- Async-first and framework-agnostic service API.
- Pluggable trainers, dataset loaders, artifact stores and model registries.
- Idempotent training jobs and reproducible run metadata.
- Safe retries, cancellation and status lifecycle.
- Validation gates for quality, drift, fairness and business metrics.
- Metrics, audit and structured logs.
- Self-contained local implementations for development/tests.

This service does not require a specific ML framework. Production adapters can
wrap sklearn, PyTorch, TensorFlow, XGBoost, LightGBM, ONNX, MLflow, BentoML,
Vertex AI, SageMaker or internal model platforms.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import random
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
MetricTags = Mapping[str, str]
DatasetRow = Mapping[str, Any]


# =============================================================================
# Exceptions
# =============================================================================


class TrainingServiceError(RuntimeError):
    """Base exception for training service failures."""


class TrainingValidationError(TrainingServiceError):
    """Raised when training request or dataset validation fails."""


class TrainingConflictError(TrainingServiceError):
    """Raised when a training job conflicts with an existing state."""


class TrainingDependencyError(TrainingServiceError):
    """Raised when a trainer/store/registry dependency fails."""


class TrainingGateError(TrainingServiceError):
    """Raised when model quality gates fail."""


# =============================================================================
# Enums and data models
# =============================================================================


class TrainingTask(str, Enum):
    CLASSIFICATION = "classification"
    REGRESSION = "regression"
    FORECASTING = "forecasting"
    ANOMALY_DETECTION = "anomaly_detection"
    FRAUD_DETECTION = "fraud_detection"
    EMBEDDING = "embedding"
    RANKING = "ranking"
    GENERIC = "generic"


class TrainingStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PROMOTED = "promoted"
    REJECTED = "rejected"


class DatasetSplitStrategy(str, Enum):
    RANDOM = "random"
    STRATIFIED = "stratified"
    TEMPORAL = "temporal"
    CUSTOM = "custom"


class ModelStage(str, Enum):
    EXPERIMENT = "experiment"
    STAGING = "staging"
    PRODUCTION = "production"
    ARCHIVED = "archived"


class MetricDirection(str, Enum):
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"


@dataclass(frozen=True)
class DatasetReference:
    dataset_id: str
    tenant_id: Optional[str] = None
    uri: Optional[str] = None
    version: Optional[str] = None
    format: str = "json"
    target_column: Optional[str] = None
    timestamp_column: Optional[str] = None
    feature_columns: Optional[Sequence[str]] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetProfile:
    dataset_id: str
    row_count: int
    column_count: int
    columns: Sequence[str]
    missing_by_column: Mapping[str, int]
    numeric_columns: Sequence[str]
    categorical_columns: Sequence[str]
    target_distribution: Mapping[str, int] = field(default_factory=dict)
    fingerprint: str = ""
    warnings: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class SplitConfig:
    strategy: DatasetSplitStrategy = DatasetSplitStrategy.RANDOM
    train_ratio: float = 0.7
    validation_ratio: float = 0.15
    test_ratio: float = 0.15
    random_seed: int = 42
    stratify_column: Optional[str] = None
    timestamp_column: Optional[str] = None

    def validate(self) -> None:
        total = self.train_ratio + self.validation_ratio + self.test_ratio
        if not math.isclose(total, 1.0, rel_tol=0.0001):
            raise TrainingValidationError("Split ratios must sum to 1.0.")
        if min(self.train_ratio, self.validation_ratio, self.test_ratio) < 0:
            raise TrainingValidationError("Split ratios cannot be negative.")


@dataclass(frozen=True)
class TrainingHyperparameters:
    values: Mapping[str, Any] = field(default_factory=dict)

    def stable_hash(self) -> str:
        return _stable_hash(self.values)


@dataclass(frozen=True)
class QualityGate:
    metric_name: str
    threshold: float
    direction: MetricDirection = MetricDirection.MAXIMIZE
    required: bool = True

    def passes(self, metrics: Mapping[str, float]) -> bool:
        if self.metric_name not in metrics:
            return not self.required
        value = metrics[self.metric_name]
        if self.direction == MetricDirection.MAXIMIZE:
            return value >= self.threshold
        return value <= self.threshold


@dataclass(frozen=True)
class TrainingRequest:
    model_name: str
    dataset: DatasetReference
    task: TrainingTask = TrainingTask.GENERIC
    tenant_id: Optional[str] = None
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    requested_by: Optional[str] = None
    trainer_name: Optional[str] = None
    split_config: SplitConfig = field(default_factory=SplitConfig)
    hyperparameters: TrainingHyperparameters = field(default_factory=TrainingHyperparameters)
    quality_gates: Sequence[QualityGate] = field(default_factory=tuple)
    tags: Mapping[str, str] = field(default_factory=dict)
    idempotency_key: Optional[str] = None
    auto_register: bool = True
    auto_promote: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetSplit:
    train: Sequence[DatasetRow]
    validation: Sequence[DatasetRow]
    test: Sequence[DatasetRow]
    profile: DatasetProfile
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrainingArtifact:
    artifact_id: str
    name: str
    uri: str
    artifact_type: str
    size_bytes: Optional[int] = None
    checksum: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrainingMetrics:
    values: Mapping[str, float]
    validation_values: Mapping[str, float] = field(default_factory=dict)
    test_values: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def primary(self, metric_name: str) -> Optional[float]:
        if metric_name in self.test_values:
            return self.test_values[metric_name]
        if metric_name in self.validation_values:
            return self.validation_values[metric_name]
        return self.values.get(metric_name)


@dataclass(frozen=True)
class ModelVersion:
    model_id: str
    model_name: str
    version: str
    stage: ModelStage
    task: TrainingTask
    tenant_id: Optional[str]
    artifact_uri: str
    metrics: TrainingMetrics
    created_at: datetime
    tags: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrainerResult:
    model_object: Any
    metrics: TrainingMetrics
    artifacts: Sequence[TrainingArtifact] = field(default_factory=tuple)
    explanation: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrainingJobResult:
    job_id: str
    tenant_id: Optional[str]
    model_name: str
    task: TrainingTask
    status: TrainingStatus
    dataset_profile: Optional[DatasetProfile]
    metrics: Optional[TrainingMetrics]
    artifacts: Sequence[TrainingArtifact]
    model_version: Optional[ModelVersion]
    gate_results: Mapping[str, bool]
    warnings: Sequence[str]
    error: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime]
    processing_ms: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["task"] = self.task.value
        payload["status"] = self.status.value
        payload["started_at"] = self.started_at.isoformat()
        payload["completed_at"] = self.completed_at.isoformat() if self.completed_at else None
        if payload.get("model_version"):
            payload["model_version"]["stage"] = self.model_version.stage.value if self.model_version else None
            payload["model_version"]["task"] = self.model_version.task.value if self.model_version else None
            payload["model_version"]["created_at"] = self.model_version.created_at.isoformat() if self.model_version else None
        return payload


@dataclass(frozen=True)
class TrainingServiceConfig:
    max_rows: int = 2_000_000
    min_rows: int = 10
    max_columns: int = 10_000
    default_timeout_seconds: int = 3_600
    idempotency_ttl_seconds: int = 86_400
    artifact_base_path: str = "/tmp/kwanza-ai-artifacts"
    audit_enabled: bool = True
    fail_fast: bool = False
    privacy_hash_salt: str = "change-me-in-production"

    def validate(self) -> None:
        if self.max_rows <= 0 or self.min_rows <= 0:
            raise TrainingValidationError("max_rows and min_rows must be positive.")
        if self.min_rows > self.max_rows:
            raise TrainingValidationError("min_rows cannot be greater than max_rows.")
        if self.default_timeout_seconds <= 0:
            raise TrainingValidationError("default_timeout_seconds must be positive.")


# =============================================================================
# Protocols
# =============================================================================


class DatasetLoader(Protocol):
    async def load(self, reference: DatasetReference) -> Sequence[DatasetRow]: ...


class ModelTrainer(Protocol):
    name: str

    async def train(self, request: TrainingRequest, split: DatasetSplit) -> TrainerResult: ...


class TrainerRegistry(Protocol):
    async def resolve(self, task: TrainingTask, trainer_name: Optional[str] = None) -> ModelTrainer: ...


class ArtifactStore(Protocol):
    async def save_model(self, job_id: str, model_name: str, model_object: Any, metadata: Mapping[str, Any]) -> TrainingArtifact: ...

    async def save_json(self, job_id: str, name: str, payload: Mapping[str, Any]) -> TrainingArtifact: ...


class ModelRegistry(Protocol):
    async def register(self, request: TrainingRequest, artifact: TrainingArtifact, metrics: TrainingMetrics) -> ModelVersion: ...

    async def promote(self, model_version: ModelVersion, stage: ModelStage) -> ModelVersion: ...


class TrainingRepository(Protocol):
    async def save_job_result(self, result: TrainingJobResult) -> None: ...

    async def get_job_result(self, tenant_id: Optional[str], job_id: str) -> Optional[TrainingJobResult]: ...


class MetricsClient(Protocol):
    def increment(self, name: str, value: int = 1, tags: Optional[MetricTags] = None) -> None: ...

    def timing(self, name: str, value_ms: float, tags: Optional[MetricTags] = None) -> None: ...

    def gauge(self, name: str, value: float, tags: Optional[MetricTags] = None) -> None: ...


class AuditSink(Protocol):
    async def write(self, event_name: str, payload: Mapping[str, Any]) -> None: ...


# =============================================================================
# No-op/in-memory implementations
# =============================================================================


class NoopMetricsClient:
    def increment(self, name: str, value: int = 1, tags: Optional[MetricTags] = None) -> None:
        return None

    def timing(self, name: str, value_ms: float, tags: Optional[MetricTags] = None) -> None:
        return None

    def gauge(self, name: str, value: float, tags: Optional[MetricTags] = None) -> None:
        return None


class NoopAuditSink:
    async def write(self, event_name: str, payload: Mapping[str, Any]) -> None:
        return None


class InMemoryTrainingRepository:
    def __init__(self) -> None:
        self._jobs: Dict[Tuple[Optional[str], str], TrainingJobResult] = {}

    async def save_job_result(self, result: TrainingJobResult) -> None:
        self._jobs[(result.tenant_id, result.job_id)] = result

    async def get_job_result(self, tenant_id: Optional[str], job_id: str) -> Optional[TrainingJobResult]:
        return self._jobs.get((tenant_id, job_id))


class InMemoryDatasetLoader:
    def __init__(self) -> None:
        self._datasets: Dict[str, Sequence[DatasetRow]] = {}

    def add_dataset(self, dataset_id: str, rows: Sequence[DatasetRow]) -> None:
        self._datasets[dataset_id] = tuple(dict(row) for row in rows)

    async def load(self, reference: DatasetReference) -> Sequence[DatasetRow]:
        if reference.dataset_id in self._datasets:
            return self._datasets[reference.dataset_id]
        if reference.uri:
            path = Path(reference.uri)
            if path.exists() and path.suffix.lower() == ".json":
                return json.loads(path.read_text(encoding="utf-8"))
            if path.exists() and path.suffix.lower() == ".jsonl":
                return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        raise TrainingValidationError(f"Dataset not found: {reference.dataset_id}")


class LocalArtifactStore:
    def __init__(self, base_path: str) -> None:
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    async def save_model(self, job_id: str, model_name: str, model_object: Any, metadata: Mapping[str, Any]) -> TrainingArtifact:
        job_dir = self.base_path / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        path = job_dir / f"{model_name}.model.json"
        payload = {"model_repr": repr(model_object), "metadata": dict(metadata), "saved_at": _utc_now().isoformat()}
        content = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        path.write_text(content, encoding="utf-8")
        return TrainingArtifact(
            artifact_id=str(uuid.uuid4()),
            name=f"{model_name}.model.json",
            uri=str(path),
            artifact_type="model",
            size_bytes=len(content.encode("utf-8")),
            checksum=_sha256_bytes(content.encode("utf-8")),
            metadata=dict(metadata),
        )

    async def save_json(self, job_id: str, name: str, payload: Mapping[str, Any]) -> TrainingArtifact:
        job_dir = self.base_path / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        path = job_dir / name
        content = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        path.write_text(content, encoding="utf-8")
        return TrainingArtifact(
            artifact_id=str(uuid.uuid4()),
            name=name,
            uri=str(path),
            artifact_type="json",
            size_bytes=len(content.encode("utf-8")),
            checksum=_sha256_bytes(content.encode("utf-8")),
        )


class InMemoryModelRegistry:
    def __init__(self) -> None:
        self._versions: Dict[str, ModelVersion] = {}

    async def register(self, request: TrainingRequest, artifact: TrainingArtifact, metrics: TrainingMetrics) -> ModelVersion:
        version = f"v{int(time.time())}-{uuid.uuid4().hex[:8]}"
        model = ModelVersion(
            model_id=str(uuid.uuid4()),
            model_name=request.model_name,
            version=version,
            stage=ModelStage.EXPERIMENT,
            task=request.task,
            tenant_id=request.tenant_id,
            artifact_uri=artifact.uri,
            metrics=metrics,
            created_at=_utc_now(),
            tags=request.tags,
            metadata={"job_id": request.job_id, "trainer_name": request.trainer_name},
        )
        self._versions[model.model_id] = model
        return model

    async def promote(self, model_version: ModelVersion, stage: ModelStage) -> ModelVersion:
        promoted = ModelVersion(
            model_id=model_version.model_id,
            model_name=model_version.model_name,
            version=model_version.version,
            stage=stage,
            task=model_version.task,
            tenant_id=model_version.tenant_id,
            artifact_uri=model_version.artifact_uri,
            metrics=model_version.metrics,
            created_at=model_version.created_at,
            tags=model_version.tags,
            metadata={**dict(model_version.metadata), "promoted_at": _utc_now().isoformat()},
        )
        self._versions[promoted.model_id] = promoted
        return promoted


class AsyncIdempotencyStore:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: MutableMapping[str, Tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any:
        now = time.monotonic()
        async with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at < now:
                self._items.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._items[key] = (time.monotonic() + self.ttl_seconds, value)


# =============================================================================
# Local trainer
# =============================================================================


class LocalBaselineTrainer:
    """Deterministic baseline trainer for tests, demos and fallback workflows."""

    name = "local_baseline"

    async def train(self, request: TrainingRequest, split: DatasetSplit) -> TrainerResult:
        target = request.dataset.target_column
        model: Dict[str, Any] = {
            "type": "local_baseline",
            "task": request.task.value,
            "target_column": target,
            "hyperparameters": dict(request.hyperparameters.values),
        }

        if request.task == TrainingTask.CLASSIFICATION and target:
            labels = [str(row.get(target)) for row in split.train if row.get(target) is not None]
            most_common = Counter(labels).most_common(1)[0][0] if labels else None
            model["prediction"] = most_common
            metrics = self._classification_metrics(split, target, most_common)
        elif request.task in {TrainingTask.REGRESSION, TrainingTask.FORECASTING, TrainingTask.CASHFLOW_FORECAST if hasattr(TrainingTask, 'CASHFLOW_FORECAST') else TrainingTask.FORECASTING} and target:
            values = [_safe_float(row.get(target), math.nan) for row in split.train]
            values = [v for v in values if not math.isnan(v)]
            mean_value = statistics.mean(values) if values else 0.0
            model["prediction"] = mean_value
            metrics = self._regression_metrics(split, target, mean_value)
        else:
            model["prediction"] = None
            metrics = TrainingMetrics(values={"baseline_score": 0.0}, validation_values={}, test_values={})

        return TrainerResult(
            model_object=model,
            metrics=metrics,
            artifacts=tuple(),
            explanation={"trainer": self.name, "strategy": "constant_baseline"},
            metadata={"train_rows": len(split.train), "validation_rows": len(split.validation), "test_rows": len(split.test)},
        )

    def _classification_metrics(self, split: DatasetSplit, target: str, prediction: Optional[str]) -> TrainingMetrics:
        def accuracy(rows: Sequence[DatasetRow]) -> float:
            labels = [str(row.get(target)) for row in rows if row.get(target) is not None]
            if not labels:
                return 0.0
            return sum(1 for label in labels if label == prediction) / len(labels)

        return TrainingMetrics(
            values={"train_accuracy": accuracy(split.train)},
            validation_values={"accuracy": accuracy(split.validation)},
            test_values={"accuracy": accuracy(split.test)},
        )

    def _regression_metrics(self, split: DatasetSplit, target: str, prediction: float) -> TrainingMetrics:
        def rmse(rows: Sequence[DatasetRow]) -> float:
            values = [_safe_float(row.get(target), math.nan) for row in rows]
            values = [v for v in values if not math.isnan(v)]
            if not values:
                return 0.0
            return math.sqrt(statistics.mean((v - prediction) ** 2 for v in values))

        return TrainingMetrics(
            values={"train_rmse": rmse(split.train)},
            validation_values={"rmse": rmse(split.validation)},
            test_values={"rmse": rmse(split.test)},
        )


class InMemoryTrainerRegistry:
    def __init__(self) -> None:
        self._trainers: Dict[str, ModelTrainer] = {LocalBaselineTrainer.name: LocalBaselineTrainer()}

    def register(self, trainer: ModelTrainer) -> None:
        self._trainers[trainer.name] = trainer

    async def resolve(self, task: TrainingTask, trainer_name: Optional[str] = None) -> ModelTrainer:
        if trainer_name:
            trainer = self._trainers.get(trainer_name)
            if not trainer:
                raise TrainingValidationError(f"Trainer not found: {trainer_name}")
            return trainer
        return self._trainers[LocalBaselineTrainer.name]


# =============================================================================
# Utility functions
# =============================================================================


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_value(value: Optional[str], salt: str) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()[:20]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _jsonable_rows(rows: Sequence[DatasetRow], limit: Optional[int] = None) -> List[Dict[str, Any]]:
    selected = rows if limit is None else rows[:limit]
    return [dict(row) for row in selected]


# =============================================================================
# Dataset profiler/splitter
# =============================================================================


class DatasetProfiler:
    def profile(self, reference: DatasetReference, rows: Sequence[DatasetRow]) -> DatasetProfile:
        if not rows:
            raise TrainingValidationError("Dataset is empty.")
        columns = sorted({str(key) for row in rows for key in row.keys()})
        missing = {col: 0 for col in columns}
        numeric_counts = Counter()
        categorical_counts = Counter()

        for row in rows:
            for col in columns:
                value = row.get(col)
                if value is None or value == "":
                    missing[col] += 1
                    continue
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    numeric_counts[col] += 1
                else:
                    try:
                        float(value)
                        numeric_counts[col] += 1
                    except (TypeError, ValueError):
                        categorical_counts[col] += 1

        numeric_columns = [col for col in columns if numeric_counts[col] >= categorical_counts[col] and numeric_counts[col] > 0]
        categorical_columns = [col for col in columns if col not in numeric_columns]
        target_distribution: Dict[str, int] = {}
        if reference.target_column:
            target_distribution = dict(Counter(str(row.get(reference.target_column)) for row in rows if row.get(reference.target_column) is not None))

        warnings: List[str] = []
        if reference.target_column and reference.target_column not in columns:
            warnings.append(f"Target column '{reference.target_column}' not found in dataset.")
        high_missing = [col for col, count in missing.items() if count / len(rows) > 0.5]
        if high_missing:
            warnings.append(f"Columns with >50% missing values: {', '.join(high_missing[:20])}")

        fingerprint = _stable_hash({"dataset_id": reference.dataset_id, "rows": _jsonable_rows(rows, limit=5000), "row_count": len(rows)})
        return DatasetProfile(
            dataset_id=reference.dataset_id,
            row_count=len(rows),
            column_count=len(columns),
            columns=tuple(columns),
            missing_by_column=missing,
            numeric_columns=tuple(numeric_columns),
            categorical_columns=tuple(categorical_columns),
            target_distribution=target_distribution,
            fingerprint=fingerprint,
            warnings=tuple(warnings),
        )


class DatasetSplitter:
    def split(self, rows: Sequence[DatasetRow], profile: DatasetProfile, config: SplitConfig) -> DatasetSplit:
        config.validate()
        if config.strategy == DatasetSplitStrategy.TEMPORAL:
            ordered = sorted(rows, key=lambda row: str(row.get(config.timestamp_column or "timestamp", "")))
        elif config.strategy == DatasetSplitStrategy.STRATIFIED and config.stratify_column:
            ordered = self._stratified_order(rows, config.stratify_column, config.random_seed)
        else:
            ordered = list(rows)
            random.Random(config.random_seed).shuffle(ordered)

        n = len(ordered)
        train_end = int(n * config.train_ratio)
        validation_end = train_end + int(n * config.validation_ratio)
        return DatasetSplit(
            train=tuple(ordered[:train_end]),
            validation=tuple(ordered[train_end:validation_end]),
            test=tuple(ordered[validation_end:]),
            profile=profile,
            metadata={"strategy": config.strategy.value, "random_seed": config.random_seed},
        )

    def _stratified_order(self, rows: Sequence[DatasetRow], column: str, seed: int) -> List[DatasetRow]:
        groups: Dict[str, List[DatasetRow]] = defaultdict(list)
        for row in rows:
            groups[str(row.get(column))].append(row)
        rng = random.Random(seed)
        for group in groups.values():
            rng.shuffle(group)
        ordered: List[DatasetRow] = []
        while any(groups.values()):
            for key in sorted(groups):
                if groups[key]:
                    ordered.append(groups[key].pop())
        return ordered


# =============================================================================
# Main service
# =============================================================================


class TrainingService:
    def __init__(
        self,
        dataset_loader: DatasetLoader,
        trainer_registry: TrainerRegistry,
        artifact_store: ArtifactStore,
        model_registry: ModelRegistry,
        repository: Optional[TrainingRepository] = None,
        config: Optional[TrainingServiceConfig] = None,
        metrics: Optional[MetricsClient] = None,
        audit_sink: Optional[AuditSink] = None,
        idempotency_store: Optional[AsyncIdempotencyStore] = None,
    ) -> None:
        self.config = config or TrainingServiceConfig()
        self.config.validate()
        self.dataset_loader = dataset_loader
        self.trainer_registry = trainer_registry
        self.artifact_store = artifact_store
        self.model_registry = model_registry
        self.repository = repository or InMemoryTrainingRepository()
        self.metrics = metrics or NoopMetricsClient()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.idempotency_store = idempotency_store or AsyncIdempotencyStore(self.config.idempotency_ttl_seconds)
        self.profiler = DatasetProfiler()
        self.splitter = DatasetSplitter()

    async def train(self, request: TrainingRequest) -> TrainingJobResult:
        started_at = _utc_now()
        started = time.perf_counter()
        self._validate_request(request)
        tags = {"tenant_id": request.tenant_id or "global", "task": request.task.value, "model_name": request.model_name}
        self.metrics.increment("training.job.started", tags=tags)

        idem_key = self._idempotency_key(request)
        cached = await self.idempotency_store.get(idem_key)
        if cached is not None:
            self.metrics.increment("training.job.idempotency_hit", tags=tags)
            return cached

        try:
            rows = await asyncio.wait_for(self.dataset_loader.load(request.dataset), timeout=self.config.default_timeout_seconds)
            self._validate_dataset_rows(rows)
            profile = self.profiler.profile(request.dataset, rows)
            split = self.splitter.split(rows, profile, request.split_config)
            trainer = await self.trainer_registry.resolve(request.task, request.trainer_name)

            self.metrics.gauge("training.dataset.rows", profile.row_count, tags=tags)
            self.metrics.gauge("training.dataset.columns", profile.column_count, tags=tags)
            await self._audit("training.dataset.profiled", request, {"profile": asdict(profile)})

            trainer_result = await asyncio.wait_for(trainer.train(request, split), timeout=self.config.default_timeout_seconds)
            gate_results = self._evaluate_gates(request.quality_gates, trainer_result.metrics)
            gates_passed = all(gate_results.values()) if gate_results else True

            artifacts: List[TrainingArtifact] = list(trainer_result.artifacts)
            model_artifact = await self.artifact_store.save_model(
                request.job_id,
                request.model_name,
                trainer_result.model_object,
                {
                    "job_id": request.job_id,
                    "task": request.task.value,
                    "trainer": trainer.name,
                    "dataset_fingerprint": profile.fingerprint,
                    "hyperparameters_hash": request.hyperparameters.stable_hash(),
                },
            )
            artifacts.append(model_artifact)
            artifacts.append(await self.artifact_store.save_json(request.job_id, "metrics.json", asdict(trainer_result.metrics)))
            artifacts.append(await self.artifact_store.save_json(request.job_id, "dataset_profile.json", asdict(profile)))

            model_version: Optional[ModelVersion] = None
            status = TrainingStatus.COMPLETED
            if request.auto_register and gates_passed:
                model_version = await self.model_registry.register(request, model_artifact, trainer_result.metrics)
                if request.auto_promote:
                    model_version = await self.model_registry.promote(model_version, ModelStage.PRODUCTION)
                    status = TrainingStatus.PROMOTED
            elif request.auto_register and not gates_passed:
                status = TrainingStatus.REJECTED

            warnings = list(profile.warnings)
            if not gates_passed:
                warnings.append("One or more quality gates failed.")

            result = TrainingJobResult(
                job_id=request.job_id,
                tenant_id=request.tenant_id,
                model_name=request.model_name,
                task=request.task,
                status=status,
                dataset_profile=profile,
                metrics=trainer_result.metrics,
                artifacts=tuple(artifacts),
                model_version=model_version,
                gate_results=gate_results,
                warnings=tuple(warnings),
                error=None,
                started_at=started_at,
                completed_at=_utc_now(),
                processing_ms=round((time.perf_counter() - started) * 1000, 4),
                metadata={
                    "trainer": trainer.name,
                    "dataset_fingerprint": profile.fingerprint,
                    "hyperparameters_hash": request.hyperparameters.stable_hash(),
                    "requested_by_hash": _hash_value(request.requested_by, self.config.privacy_hash_salt),
                },
            )
            await self.repository.save_job_result(result)
            await self.idempotency_store.set(idem_key, result)
            self.metrics.increment("training.job.completed", tags={**tags, "status": result.status.value})
            self.metrics.timing("training.job.processing_ms", result.processing_ms, tags=tags)
            self._emit_metric_gauges(trainer_result.metrics, tags)
            await self._audit("training.job.completed", request, result.to_dict())
            return result
        except Exception as exc:
            logger.exception("Training job failed", extra={"job_id": request.job_id, "model_name": request.model_name})
            self.metrics.increment("training.job.failed", tags={**tags, "error": exc.__class__.__name__})
            result = TrainingJobResult(
                job_id=request.job_id,
                tenant_id=request.tenant_id,
                model_name=request.model_name,
                task=request.task,
                status=TrainingStatus.FAILED,
                dataset_profile=None,
                metrics=None,
                artifacts=tuple(),
                model_version=None,
                gate_results={},
                warnings=tuple(),
                error=f"{exc.__class__.__name__}: {exc}",
                started_at=started_at,
                completed_at=_utc_now(),
                processing_ms=round((time.perf_counter() - started) * 1000, 4),
                metadata={"requested_by_hash": _hash_value(request.requested_by, self.config.privacy_hash_salt)},
            )
            await self.repository.save_job_result(result)
            await self._audit("training.job.failed", request, result.to_dict())
            raise

    async def get_job(self, tenant_id: Optional[str], job_id: str) -> Optional[TrainingJobResult]:
        return await self.repository.get_job_result(tenant_id, job_id)

    async def promote_model(self, model_version: ModelVersion, stage: ModelStage = ModelStage.PRODUCTION) -> ModelVersion:
        promoted = await self.model_registry.promote(model_version, stage)
        await self._audit_generic(
            "training.model.promoted",
            {
                "tenant_id": promoted.tenant_id,
                "model_name": promoted.model_name,
                "model_id": promoted.model_id,
                "version": promoted.version,
                "stage": promoted.stage.value,
            },
        )
        self.metrics.increment("training.model.promoted", tags={"tenant_id": promoted.tenant_id or "global", "stage": stage.value})
        return promoted

    def _validate_request(self, request: TrainingRequest) -> None:
        if not request.model_name:
            raise TrainingValidationError("model_name is required.")
        if not request.dataset.dataset_id:
            raise TrainingValidationError("dataset.dataset_id is required.")
        request.split_config.validate()
        for gate in request.quality_gates:
            if not gate.metric_name:
                raise TrainingValidationError("quality gate metric_name is required.")

    def _validate_dataset_rows(self, rows: Sequence[DatasetRow]) -> None:
        if len(rows) < self.config.min_rows:
            raise TrainingValidationError(f"Dataset has {len(rows)} rows; minimum is {self.config.min_rows}.")
        if len(rows) > self.config.max_rows:
            raise TrainingValidationError(f"Dataset has {len(rows)} rows; maximum is {self.config.max_rows}.")
        if rows:
            columns = {key for row in rows for key in row.keys()}
            if len(columns) > self.config.max_columns:
                raise TrainingValidationError(f"Dataset has {len(columns)} columns; maximum is {self.config.max_columns}.")

    def _evaluate_gates(self, gates: Sequence[QualityGate], metrics: TrainingMetrics) -> Dict[str, bool]:
        combined = {**metrics.values, **metrics.validation_values, **metrics.test_values}
        return {gate.metric_name: gate.passes(combined) for gate in gates}

    def _emit_metric_gauges(self, metrics: TrainingMetrics, tags: MetricTags) -> None:
        for namespace, values in [
            ("training.metric", metrics.values),
            ("training.metric.validation", metrics.validation_values),
            ("training.metric.test", metrics.test_values),
        ]:
            for name, value in values.items():
                self.metrics.gauge(namespace, float(value), tags={**dict(tags), "metric": name})

    def _idempotency_key(self, request: TrainingRequest) -> str:
        if request.idempotency_key:
            return f"training:{request.tenant_id or 'global'}:{request.idempotency_key}"
        return "training:" + _stable_hash(
            {
                "tenant_id": request.tenant_id,
                "model_name": request.model_name,
                "dataset": asdict(request.dataset),
                "task": request.task.value,
                "trainer_name": request.trainer_name,
                "split": asdict(request.split_config),
                "hyperparameters": dict(request.hyperparameters.values),
            }
        )

    async def _audit(self, event_name: str, request: TrainingRequest, payload: Mapping[str, Any]) -> None:
        await self._audit_generic(
            event_name,
            {
                "job_id": request.job_id,
                "tenant_id": request.tenant_id,
                "model_name": request.model_name,
                "task": request.task.value,
                "requested_by_hash": _hash_value(request.requested_by, self.config.privacy_hash_salt),
                "payload": payload,
                "created_at": _utc_now().isoformat(),
            },
        )

    async def _audit_generic(self, event_name: str, payload: Mapping[str, Any]) -> None:
        if not self.config.audit_enabled:
            return
        try:
            await self.audit_sink.write(event_name, payload)
        except Exception:
            logger.exception("Failed to write training audit event", extra={"event_name": event_name})

    @classmethod
    def request_from_payload(cls, payload: Mapping[str, Any]) -> TrainingRequest:
        dataset_payload = payload.get("dataset") or {}
        split_payload = payload.get("split_config") or {}
        gates = tuple(
            QualityGate(
                metric_name=str(gate["metric_name"]),
                threshold=float(gate["threshold"]),
                direction=MetricDirection(gate.get("direction", MetricDirection.MAXIMIZE.value)),
                required=bool(gate.get("required", True)),
            )
            for gate in payload.get("quality_gates", [])
        )
        return TrainingRequest(
            model_name=str(payload["model_name"]),
            dataset=DatasetReference(
                dataset_id=str(dataset_payload["dataset_id"]),
                tenant_id=dataset_payload.get("tenant_id") or payload.get("tenant_id"),
                uri=dataset_payload.get("uri"),
                version=dataset_payload.get("version"),
                format=dataset_payload.get("format", "json"),
                target_column=dataset_payload.get("target_column"),
                timestamp_column=dataset_payload.get("timestamp_column"),
                feature_columns=tuple(dataset_payload.get("feature_columns") or ()) or None,
                metadata=dataset_payload.get("metadata") or {},
            ),
            task=TrainingTask(payload.get("task", TrainingTask.GENERIC.value)),
            tenant_id=payload.get("tenant_id"),
            job_id=str(payload.get("job_id") or uuid.uuid4()),
            requested_by=payload.get("requested_by"),
            trainer_name=payload.get("trainer_name"),
            split_config=SplitConfig(
                strategy=DatasetSplitStrategy(split_payload.get("strategy", DatasetSplitStrategy.RANDOM.value)),
                train_ratio=float(split_payload.get("train_ratio", 0.7)),
                validation_ratio=float(split_payload.get("validation_ratio", 0.15)),
                test_ratio=float(split_payload.get("test_ratio", 0.15)),
                random_seed=int(split_payload.get("random_seed", 42)),
                stratify_column=split_payload.get("stratify_column"),
                timestamp_column=split_payload.get("timestamp_column"),
            ),
            hyperparameters=TrainingHyperparameters(payload.get("hyperparameters") or {}),
            quality_gates=gates,
            tags=payload.get("tags") or {},
            idempotency_key=payload.get("idempotency_key"),
            auto_register=bool(payload.get("auto_register", True)),
            auto_promote=bool(payload.get("auto_promote", False)),
            metadata=payload.get("metadata") or {},
        )


# =============================================================================
# Factory
# =============================================================================


def build_training_service(
    dataset_loader: Optional[DatasetLoader] = None,
    trainer_registry: Optional[TrainerRegistry] = None,
    artifact_store: Optional[ArtifactStore] = None,
    model_registry: Optional[ModelRegistry] = None,
    repository: Optional[TrainingRepository] = None,
    config: Optional[TrainingServiceConfig] = None,
    metrics: Optional[MetricsClient] = None,
    audit_sink: Optional[AuditSink] = None,
) -> TrainingService:
    cfg = config or TrainingServiceConfig()
    return TrainingService(
        dataset_loader=dataset_loader or InMemoryDatasetLoader(),
        trainer_registry=trainer_registry or InMemoryTrainerRegistry(),
        artifact_store=artifact_store or LocalArtifactStore(cfg.artifact_base_path),
        model_registry=model_registry or InMemoryModelRegistry(),
        repository=repository,
        config=cfg,
        metrics=metrics,
        audit_sink=audit_sink,
    )


# =============================================================================
# Manual smoke test
# =============================================================================


async def _demo() -> None:
    logging.basicConfig(level=logging.INFO)
    loader = InMemoryDatasetLoader()
    rows = []
    for idx in range(120):
        rows.append(
            {
                "amount": idx * 10 + 100,
                "recent_count": idx % 9,
                "is_risky": "yes" if idx % 7 == 0 else "no",
            }
        )
    loader.add_dataset("fraud-demo", rows)

    service = build_training_service(
        dataset_loader=loader,
        config=TrainingServiceConfig(artifact_base_path="/tmp/kwanza-ai-training-demo", privacy_hash_salt="local-dev-salt"),
    )
    result = await service.train(
        TrainingRequest(
            tenant_id="tenant-ao",
            model_name="fraud-baseline",
            task=TrainingTask.CLASSIFICATION,
            dataset=DatasetReference(dataset_id="fraud-demo", target_column="is_risky"),
            requested_by="admin-1",
            quality_gates=(QualityGate(metric_name="accuracy", threshold=0.5),),
            auto_register=True,
            auto_promote=False,
        )
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(_demo())

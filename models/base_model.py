#!/usr/bin/env python3
"""
models/base_model.py

Enterprise-grade Base Model Framework.

Objetivo:
- Fornecer uma base sólida e padronizada para todos os modelos do projeto.
- Padronizar configuração, metadados, ciclo de vida, validação, treino, predição, avaliação e persistência.
- Dar suporte a modelos rule-based, estatísticos, ML, scoring, forecasting, risk, fraud, NLP e intelligence.
- Incluir registry local, checksum, auditoria, logging estruturado e contrato extensível.

Exemplo de uso:
    class MyModel(BaseModel):
        def _fit(self, records):
            self.state["count"] = len(records)
            return self

        def _predict_one(self, record):
            return {"score": 1.0}

    model = MyModel(ModelConfig(name="my_model"))
    model.fit([{"x": 1}])
    print(model.predict_one({"x": 2}))
    model.save("models/artifacts/my_model.json")

CLI:
    python models/base_model.py inspect --model models/artifacts/my_model.json
    python models/base_model.py registry-list --registry models/artifacts/registry.json
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import platform
import sys
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Generic, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple, Type, TypeVar


APP_NAME = "base_model"
FRAMEWORK_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc

TInput = TypeVar("TInput")
TPrediction = TypeVar("TPrediction")


class ModelStage(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    ARCHIVED = "archived"


class ModelStatus(str, Enum):
    CREATED = "created"
    TRAINING = "training"
    TRAINED = "trained"
    EVALUATED = "evaluated"
    SAVED = "saved"
    LOADED = "loaded"
    FAILED = "failed"


class PredictionMode(str, Enum):
    SINGLE = "single"
    BATCH = "batch"


@dataclass(frozen=True)
class ModelConfig:
    name: str
    version: str = "1.0.0"
    stage: ModelStage = ModelStage.DEVELOPMENT
    model_type: str = "generic"
    description: str = ""
    owner: str = "unknown"
    tags: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    random_seed: Optional[int] = None
    strict_validation: bool = True
    created_by: str = "system"

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ModelValidationError("ModelConfig.name is required")
        if not self.version or not self.version.strip():
            raise ModelValidationError("ModelConfig.version is required")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "version", self.version.strip())
        if isinstance(self.stage, str):
            object.__setattr__(self, "stage", ModelStage(self.stage))

    def to_dict(self) -> Dict[str, Any]:
        payload = dataclasses.asdict(self)
        payload["stage"] = self.stage.value
        return payload

    @staticmethod
    def from_dict(payload: Mapping[str, Any]) -> "ModelConfig":
        data = dict(payload)
        data["stage"] = ModelStage(data.get("stage", ModelStage.DEVELOPMENT.value))
        return ModelConfig(**data)


@dataclass
class ModelMetadata:
    model_id: str
    name: str
    version: str
    framework_version: str
    status: ModelStatus
    stage: ModelStage
    created_at: str
    updated_at: str
    trained_at: Optional[str] = None
    evaluated_at: Optional[str] = None
    saved_at: Optional[str] = None
    loaded_at: Optional[str] = None
    training_records: int = 0
    prediction_count: int = 0
    checksum: Optional[str] = None
    python_version: str = field(default_factory=lambda: sys.version.split()[0])
    platform: str = field(default_factory=platform.platform)

    def touch(self) -> None:
        self.updated_at = utc_now_iso()

    def set_status(self, status: ModelStatus) -> None:
        self.status = status
        self.touch()

    def to_dict(self) -> Dict[str, Any]:
        payload = dataclasses.asdict(self)
        payload["status"] = self.status.value
        payload["stage"] = self.stage.value
        return payload

    @staticmethod
    def from_dict(payload: Mapping[str, Any]) -> "ModelMetadata":
        data = dict(payload)
        data["status"] = ModelStatus(data.get("status", ModelStatus.CREATED.value))
        data["stage"] = ModelStage(data.get("stage", ModelStage.DEVELOPMENT.value))
        return ModelMetadata(**data)


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def raise_if_invalid(self) -> None:
        if not self.valid:
            raise ModelValidationError("; ".join(self.errors))

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class EvaluationResult:
    metrics: Dict[str, Any]
    sample_count: int
    evaluated_at: str = field(default_factory=utc_now_iso)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class PredictionResult(Generic[TPrediction]):
    model_id: str
    model_name: str
    model_version: str
    prediction: TPrediction
    mode: PredictionMode
    created_at: str = field(default_factory=utc_now_iso)
    latency_ms: float = 0.0
    explanations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = dataclasses.asdict(self)
        payload["mode"] = self.mode.value
        return payload


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    event_type: str
    model_id: str
    model_name: str
    created_at: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class ModelError(Exception):
    """Base exception for model framework."""


class ModelValidationError(ModelError):
    """Raised when model validation fails."""


class ModelStateError(ModelError):
    """Raised when the model state is invalid for the requested operation."""


class ModelPersistenceError(ModelError):
    """Raised when save/load fails."""


class SerializableModel(Protocol):
    def to_artifact(self) -> Dict[str, Any]:
        ...


class BaseModel(ABC, Generic[TInput, TPrediction]):
    """Base class for enterprise model implementations."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        now = utc_now_iso()
        self.metadata = ModelMetadata(
            model_id=self._build_model_id(config),
            name=config.name,
            version=config.version,
            framework_version=FRAMEWORK_VERSION,
            status=ModelStatus.CREATED,
            stage=config.stage,
            created_at=now,
            updated_at=now,
        )
        self.state: Dict[str, Any] = {}
        self.evaluation: Optional[EvaluationResult] = None
        self.audit_log: List[AuditEvent] = []
        self.logger = logging.getLogger(f"{APP_NAME}.{self.__class__.__name__}.{config.name}")
        self._audit("model_created", {"config": self.config.to_dict()})

    @property
    def is_trained(self) -> bool:
        return self.metadata.status in {ModelStatus.TRAINED, ModelStatus.EVALUATED, ModelStatus.SAVED, ModelStatus.LOADED}

    def fit(self, records: Sequence[TInput]) -> "BaseModel[TInput, TPrediction]":
        validation = self.validate_training_data(records)
        validation.raise_if_invalid()
        self.metadata.set_status(ModelStatus.TRAINING)
        self._audit("training_started", {"records": len(records), "warnings": validation.warnings})
        started = time.perf_counter()
        try:
            self._fit(records)
            self.metadata.training_records = len(records)
            self.metadata.trained_at = utc_now_iso()
            self.metadata.set_status(ModelStatus.TRAINED)
            self.metadata.checksum = self.compute_checksum()
            self._audit(
                "training_completed",
                {
                    "records": len(records),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 4),
                    "checksum": self.metadata.checksum,
                },
            )
            return self
        except Exception as exc:  # noqa: BLE001
            self.metadata.set_status(ModelStatus.FAILED)
            self._audit("training_failed", {"error": str(exc)})
            raise

    def predict_one(self, record: TInput) -> PredictionResult[TPrediction]:
        self.ensure_ready_for_prediction()
        validation = self.validate_prediction_input(record)
        validation.raise_if_invalid()
        started = time.perf_counter()
        prediction = self._predict_one(record)
        latency_ms = round((time.perf_counter() - started) * 1000, 4)
        self.metadata.prediction_count += 1
        self.metadata.touch()
        self._audit("prediction_completed", {"mode": PredictionMode.SINGLE.value, "latency_ms": latency_ms})
        return PredictionResult(
            model_id=self.metadata.model_id,
            model_name=self.config.name,
            model_version=self.config.version,
            prediction=prediction,
            mode=PredictionMode.SINGLE,
            latency_ms=latency_ms,
            metadata={"status": self.metadata.status.value},
        )

    def predict_many(self, records: Sequence[TInput]) -> PredictionResult[List[TPrediction]]:
        self.ensure_ready_for_prediction()
        if not records:
            raise ModelValidationError("records cannot be empty")
        started = time.perf_counter()
        predictions = [self._predict_one(record) for record in records]
        latency_ms = round((time.perf_counter() - started) * 1000, 4)
        self.metadata.prediction_count += len(records)
        self.metadata.touch()
        self._audit(
            "prediction_completed",
            {"mode": PredictionMode.BATCH.value, "records": len(records), "latency_ms": latency_ms},
        )
        return PredictionResult(
            model_id=self.metadata.model_id,
            model_name=self.config.name,
            model_version=self.config.version,
            prediction=predictions,
            mode=PredictionMode.BATCH,
            latency_ms=latency_ms,
            metadata={"records": len(records), "status": self.metadata.status.value},
        )

    def evaluate(self, records: Sequence[TInput], labels: Optional[Sequence[Any]] = None) -> EvaluationResult:
        self.ensure_ready_for_prediction()
        if not records:
            raise ModelValidationError("records cannot be empty")
        started = time.perf_counter()
        metrics = self._evaluate(records, labels)
        metrics["duration_ms"] = round((time.perf_counter() - started) * 1000, 4)
        result = EvaluationResult(metrics=metrics, sample_count=len(records))
        self.evaluation = result
        self.metadata.evaluated_at = result.evaluated_at
        self.metadata.set_status(ModelStatus.EVALUATED)
        self.metadata.checksum = self.compute_checksum()
        self._audit("evaluation_completed", result.to_dict())
        return result

    def validate_training_data(self, records: Sequence[TInput]) -> ValidationResult:
        errors: List[str] = []
        warnings: List[str] = []
        if records is None:
            errors.append("records cannot be None")
        elif len(records) == 0:
            errors.append("records cannot be empty")
        elif len(records) < 5:
            warnings.append("training data has fewer than 5 records")
        return ValidationResult(valid=not errors, errors=errors, warnings=warnings)

    def validate_prediction_input(self, record: TInput) -> ValidationResult:
        errors: List[str] = []
        if record is None:
            errors.append("record cannot be None")
        return ValidationResult(valid=not errors, errors=errors)

    def ensure_ready_for_prediction(self) -> None:
        if not self.is_trained:
            raise ModelStateError(f"model is not trained; current status={self.metadata.status.value}")

    def to_artifact(self) -> Dict[str, Any]:
        return {
            "artifact_type": "enterprise_model",
            "framework_version": FRAMEWORK_VERSION,
            "class_name": self.__class__.__name__,
            "module": self.__class__.__module__,
            "config": self.config.to_dict(),
            "metadata": self.metadata.to_dict(),
            "state": self.state,
            "evaluation": None if self.evaluation is None else self.evaluation.to_dict(),
            "audit_log": [event.to_dict() for event in self.audit_log],
        }

    def compute_checksum(self) -> str:
        artifact = self.to_artifact()
        metadata = dict(artifact["metadata"])
        metadata["checksum"] = None
        artifact["metadata"] = metadata
        payload = canonical_json(artifact)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def save(self, path: str | Path, registry_path: Optional[str | Path] = None) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        self.metadata.saved_at = utc_now_iso()
        self.metadata.set_status(ModelStatus.SAVED)
        self.metadata.checksum = self.compute_checksum()
        artifact = self.to_artifact()
        artifact["metadata"]["checksum"] = self.metadata.checksum
        try:
            output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            raise ModelPersistenceError(f"failed to save model: {exc}") from exc
        self._audit("model_saved", {"path": str(output), "checksum": self.metadata.checksum})
        if registry_path:
            ModelRegistry(Path(registry_path)).register(self.metadata, output, self.config)
        return output

    @classmethod
    def load(cls: Type["BaseModel"], path: str | Path) -> "BaseModel":
        input_path = Path(path)
        if not input_path.exists():
            raise ModelPersistenceError(f"model artifact not found: {input_path}")
        try:
            payload = json.loads(input_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ModelPersistenceError(f"failed to read model artifact: {exc}") from exc

        config = ModelConfig.from_dict(payload.get("config", {}))
        model = cls(config)  # type: ignore[call-arg]
        model.metadata = ModelMetadata.from_dict(payload.get("metadata", {}))
        model.state = payload.get("state", {})
        evaluation = payload.get("evaluation")
        model.evaluation = EvaluationResult(**evaluation) if evaluation else None
        model.audit_log = [AuditEvent(**event) for event in payload.get("audit_log", [])]
        model.metadata.loaded_at = utc_now_iso()
        model.metadata.set_status(ModelStatus.LOADED)
        expected_checksum = payload.get("metadata", {}).get("checksum")
        actual_checksum = model.compute_checksum()
        if expected_checksum and expected_checksum != actual_checksum:
            model._audit("checksum_warning", {"expected": expected_checksum, "actual": actual_checksum})
        model._audit("model_loaded", {"path": str(input_path)})
        return model

    def _audit(self, event_type: str, details: Optional[Dict[str, Any]] = None) -> None:
        self.audit_log.append(
            AuditEvent(
                event_id=str(uuid.uuid4()),
                event_type=event_type,
                model_id=self.metadata.model_id,
                model_name=self.config.name,
                created_at=utc_now_iso(),
                details=details or {},
            )
        )

    @staticmethod
    def _build_model_id(config: ModelConfig) -> str:
        raw = f"{config.name}:{config.version}:{utc_now_iso()}:{uuid.uuid4()}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        return f"mdl_{digest}"

    @abstractmethod
    def _fit(self, records: Sequence[TInput]) -> "BaseModel[TInput, TPrediction]":
        """Model-specific training implementation."""

    @abstractmethod
    def _predict_one(self, record: TInput) -> TPrediction:
        """Model-specific single prediction implementation."""

    def _evaluate(self, records: Sequence[TInput], labels: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
        predictions = [self._predict_one(record) for record in records]
        metrics: Dict[str, Any] = {
            "prediction_count": len(predictions),
            "has_labels": labels is not None,
        }
        if labels is not None:
            metrics["label_count"] = len(labels)
            metrics["label_prediction_count_match"] = len(labels) == len(predictions)
        return metrics


class RuleBasedModel(BaseModel[TInput, TPrediction], ABC):
    """Base class for deterministic rule-based models."""

    def _fit(self, records: Sequence[TInput]) -> "RuleBasedModel[TInput, TPrediction]":
        self.state["training_records_seen"] = len(records)
        self.state["model_family"] = "rule_based"
        return self


class ScoringModel(BaseModel[Mapping[str, Any], Dict[str, Any]], ABC):
    """Base class for 0-100 scoring models."""

    @staticmethod
    def clamp_score(value: float | int) -> float:
        return max(0.0, min(float(value), 100.0))

    @staticmethod
    def risk_level(score: float) -> str:
        if score >= 85:
            return "critical"
        if score >= 65:
            return "high"
        if score >= 35:
            return "medium"
        return "low"


class ForecastingModel(BaseModel[Mapping[str, Any], Dict[str, Any]], ABC):
    """Base class for forecasting models."""

    def validate_horizon(self, horizon: int) -> None:
        if horizon <= 0:
            raise ModelValidationError("horizon must be greater than zero")
        if horizon > 10_000:
            raise ModelValidationError("horizon is unreasonably large")


class ModelRegistry:
    """Simple local JSON registry for model artifacts."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def register(self, metadata: ModelMetadata, artifact_path: Path, config: ModelConfig) -> None:
        registry = self._read()
        entry = {
            "model_id": metadata.model_id,
            "name": metadata.name,
            "version": metadata.version,
            "stage": metadata.stage.value,
            "status": metadata.status.value,
            "artifact_path": str(artifact_path),
            "checksum": metadata.checksum,
            "updated_at": utc_now_iso(),
            "config": config.to_dict(),
        }
        registry[metadata.model_id] = entry
        self._write(registry)

    def list(self, name: Optional[str] = None) -> List[Dict[str, Any]]:
        registry = self._read()
        values = list(registry.values())
        if name:
            values = [item for item in values if item.get("name") == name]
        return sorted(values, key=lambda item: item.get("updated_at", ""), reverse=True)

    def get(self, model_id: str) -> Optional[Dict[str, Any]]:
        return self._read().get(model_id)

    def remove(self, model_id: str) -> bool:
        registry = self._read()
        if model_id not in registry:
            return False
        registry.pop(model_id)
        self._write(registry)
        return True

    def _read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ModelPersistenceError(f"invalid registry JSON: {self.path}: {exc}") from exc

    def _write(self, registry: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


class EchoModel(BaseModel[Mapping[str, Any], Dict[str, Any]]):
    """Small concrete model useful for tests and smoke checks."""

    def _fit(self, records: Sequence[Mapping[str, Any]]) -> "EchoModel":
        keys = sorted({key for record in records for key in record.keys()})
        self.state["keys"] = keys
        self.state["training_records_seen"] = len(records)
        return self

    def _predict_one(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "echo": dict(record),
            "known_keys": self.state.get("keys", []),
            "unknown_keys": sorted(set(record.keys()) - set(self.state.get("keys", []))),
        }


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()


def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def load_json_records(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise ModelPersistenceError(f"file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [dict(item) for item in payload]
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        return [dict(item) for item in payload["records"]]
    raise ModelValidationError("expected JSON list or object with 'records' list")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Enterprise base model framework utilities.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_cmd = subparsers.add_parser("inspect", help="Inspeciona um artefato de modelo JSON.")
    inspect_cmd.add_argument("--model", required=True, type=Path)

    registry_list = subparsers.add_parser("registry-list", help="Lista modelos em um registry JSON local.")
    registry_list.add_argument("--registry", required=True, type=Path)
    registry_list.add_argument("--name")

    registry_get = subparsers.add_parser("registry-get", help="Busca um modelo no registry por ID.")
    registry_get.add_argument("--registry", required=True, type=Path)
    registry_get.add_argument("--model-id", required=True)

    smoke = subparsers.add_parser("smoke-test", help="Treina e salva um EchoModel para validar o framework.")
    smoke.add_argument("--input", required=True, type=Path)
    smoke.add_argument("--output", required=True, type=Path)
    smoke.add_argument("--registry", type=Path)
    smoke.add_argument("--name", default="echo_model")

    return parser.parse_args(argv)


def inspect_model(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise ModelPersistenceError(f"model artifact not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "artifact_type": payload.get("artifact_type"),
        "framework_version": payload.get("framework_version"),
        "class_name": payload.get("class_name"),
        "config": payload.get("config"),
        "metadata": payload.get("metadata"),
        "evaluation": payload.get("evaluation"),
        "state_keys": sorted(list((payload.get("state") or {}).keys())),
        "audit_events": len(payload.get("audit_log", [])),
    }


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)
    logger = logging.getLogger(APP_NAME)

    try:
        if args.command == "inspect":
            print_json(inspect_model(args.model))
            return 0

        if args.command == "registry-list":
            print_json(ModelRegistry(args.registry).list(name=args.name))
            return 0

        if args.command == "registry-get":
            item = ModelRegistry(args.registry).get(args.model_id)
            print_json(item or {"error": "model_id_not_found"})
            return 0 if item else 2

        if args.command == "smoke-test":
            records = load_json_records(args.input)
            model = EchoModel(ModelConfig(name=args.name, model_type="echo", description="Smoke test model"))
            model.fit(records)
            model.evaluate(records)
            model.save(args.output, registry_path=args.registry)
            print_json({"saved": str(args.output), "metadata": model.metadata.to_dict()})
            return 0

        raise ModelError(f"Comando não suportado: {args.command}")

    except ModelError as exc:
        logger.error("Erro no framework de modelos: %s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erro inesperado: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(run())

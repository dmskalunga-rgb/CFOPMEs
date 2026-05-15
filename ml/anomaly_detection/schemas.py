# ml/anomaly_detection/schemas.py
"""
Enterprise Anomaly Detection Schemas.

Contratos para:
- treino
- predição
- batch prediction
- avaliação
- auditoria
- API responses
- erros estruturados
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence


class SchemaError(ValueError):
    pass


class AnomalyDecision(str, Enum):
    NORMAL = "normal"
    REVIEW = "review"
    ANOMALY = "anomaly"


class AnomalySeverity(str, Enum):
    NORMAL = "normal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RequestSource(str, Enum):
    API = "api"
    BATCH = "batch"
    STREAM = "stream"
    SCHEDULER = "scheduler"
    INTERNAL = "internal"


class DatasetFormat(str, Enum):
    JSON = "json"
    JSONL = "jsonl"
    CSV = "csv"
    PARQUET = "parquet"


@dataclass(frozen=True)
class ErrorDetail:
    field: str
    message: str
    code: str = "validation_error"


@dataclass(frozen=True)
class APIErrorResponse:
    request_id: str
    error_id: str
    message: str
    details: List[ErrorDetail]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


@dataclass(frozen=True)
class RequestContext:
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    source: RequestSource = RequestSource.API
    user_id: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AnomalyRecord:
    record_id: str
    timestamp: Optional[str] = None
    entity_id: Optional[str] = None
    features: Optional[List[float]] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        errors: List[ErrorDetail] = []

        if not self.record_id:
            errors.append(ErrorDetail("record_id", "record_id é obrigatório."))

        if self.features is None and not self.payload:
            errors.append(ErrorDetail("features|payload", "Informe features ou payload."))

        if self.features is not None:
            if len(self.features) == 0:
                errors.append(ErrorDetail("features", "features não pode ser vazio."))

            for i, value in enumerate(self.features):
                if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    errors.append(ErrorDetail(f"features[{i}]", "Feature precisa ser número finito."))

        if errors:
            raise SchemaError(json.dumps([asdict(e) for e in errors], ensure_ascii=False))


@dataclass(frozen=True)
class PredictionRequest:
    context: RequestContext
    record: AnomalyRecord

    def validate(self) -> None:
        self.record.validate()


@dataclass(frozen=True)
class BatchPredictionRequest:
    context: RequestContext
    records: List[AnomalyRecord]

    def validate(self, max_batch_size: int = 5000) -> None:
        if not self.records:
            raise SchemaError("records não pode ser vazio.")

        if len(self.records) > max_batch_size:
            raise SchemaError(f"Batch excede limite máximo: {len(self.records)} > {max_batch_size}")

        seen = set()
        for record in self.records:
            record.validate()
            if record.record_id in seen:
                raise SchemaError(f"record_id duplicado: {record.record_id}")
            seen.add(record.record_id)


@dataclass(frozen=True)
class FeatureContribution:
    feature: str
    contribution: float
    value: Optional[float] = None
    rank: int = 0


@dataclass(frozen=True)
class DetectorScore:
    detector: str
    score: float
    weight: Optional[float] = None


@dataclass(frozen=True)
class PredictionResponse:
    request_id: str
    prediction_id: str
    record_id: str
    anomaly_score: float
    decision: AnomalyDecision
    severity: AnomalySeverity
    threshold: float
    model_name: str
    model_version: str
    detector_scores: List[DetectorScore] = field(default_factory=list)
    feature_contributions: List[FeatureContribution] = field(default_factory=list)
    latency_ms: Optional[float] = None
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not 0 <= self.anomaly_score <= 1:
            raise SchemaError("anomaly_score precisa estar entre 0 e 1.")

        if not 0 <= self.threshold <= 1:
            raise SchemaError("threshold precisa estar entre 0 e 1.")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        self.validate()
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class BatchPredictionResponse:
    request_id: str
    total: int
    succeeded: int
    failed: int
    predictions: List[PredictionResponse]
    errors: List[APIErrorResponse] = field(default_factory=list)
    latency_ms: Optional[float] = None
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class TrainingDatasetSchema:
    dataset_id: str
    records: List[AnomalyRecord]
    labels: Optional[List[int]] = None
    format: DatasetFormat = DatasetFormat.JSON
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.dataset_id:
            raise SchemaError("dataset_id obrigatório.")

        if not self.records:
            raise SchemaError("records não pode ser vazio.")

        for record in self.records:
            record.validate()

        if self.labels is not None:
            if len(self.labels) != len(self.records):
                raise SchemaError("labels precisa ter o mesmo tamanho de records.")

            invalid = [x for x in self.labels if x not in (0, 1)]
            if invalid:
                raise SchemaError("labels aceita somente 0 ou 1.")


@dataclass(frozen=True)
class TrainingRequest:
    context: RequestContext
    dataset: TrainingDatasetSchema
    model_name: str = "enterprise_anomaly_detector"
    model_version: str = "1.0.0"
    contamination: float = 0.05
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        self.dataset.validate()

        if not 0 < self.contamination < 0.5:
            raise SchemaError("contamination precisa estar entre 0 e 0.5.")


@dataclass(frozen=True)
class TrainingResponse:
    request_id: str
    run_id: str
    status: str
    model_name: str
    model_version: str
    samples: int
    features: int
    artifact_uri: Optional[str] = None
    metrics: Dict[str, float] = field(default_factory=dict)
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class EvaluationRequest:
    context: RequestContext
    scores: List[float]
    labels: Optional[List[int]] = None
    threshold: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.scores:
            raise SchemaError("scores não pode ser vazio.")

        for i, score in enumerate(self.scores):
            if not 0 <= float(score) <= 1:
                raise SchemaError(f"scores[{i}] precisa estar entre 0 e 1.")

        if self.labels is not None and len(self.labels) != len(self.scores):
            raise SchemaError("labels precisa ter o mesmo tamanho de scores.")


@dataclass(frozen=True)
class EvaluationResponse:
    request_id: str
    evaluation_id: str
    samples: int
    metrics: Dict[str, float]
    threshold: Optional[float] = None
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class AuditEventSchema:
    event_id: str
    event_type: str
    request_id: Optional[str]
    tenant_id: Optional[str]
    record_id: Optional[str]
    actor_id: Optional[str]
    action: str
    decision: Optional[str] = None
    severity: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)


class SchemaFactory:
    @staticmethod
    def record_from_dict(data: Mapping[str, Any]) -> AnomalyRecord:
        return AnomalyRecord(
            record_id=str(data.get("record_id") or data.get("id") or uuid.uuid4()),
            timestamp=data.get("timestamp"),
            entity_id=data.get("entity_id"),
            features=[float(x) for x in data["features"]] if "features" in data and data["features"] is not None else None,
            payload=dict(data.get("payload", {})),
            metadata=dict(data.get("metadata", {})),
        )

    @staticmethod
    def prediction_request_from_dict(data: Mapping[str, Any]) -> PredictionRequest:
        context = SchemaFactory.context_from_dict(data.get("context", data))
        record_raw = data.get("record", data)
        request = PredictionRequest(
            context=context,
            record=SchemaFactory.record_from_dict(record_raw),
        )
        request.validate()
        return request

    @staticmethod
    def batch_prediction_request_from_dict(data: Mapping[str, Any]) -> BatchPredictionRequest:
        context = SchemaFactory.context_from_dict(data.get("context", data))
        records = [SchemaFactory.record_from_dict(r) for r in data.get("records", [])]

        request = BatchPredictionRequest(context=context, records=records)
        request.validate()
        return request

    @staticmethod
    def context_from_dict(data: Mapping[str, Any]) -> RequestContext:
        source = data.get("source", RequestSource.API)

        return RequestContext(
            request_id=str(data.get("request_id") or uuid.uuid4()),
            tenant_id=data.get("tenant_id"),
            source=RequestSource(source),
            user_id=data.get("user_id"),
            correlation_id=data.get("correlation_id"),
            metadata=dict(data.get("metadata", {})),
        )


def schema_to_dict(obj: Any) -> Dict[str, Any]:
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, Mapping):
        return dict(obj)
    raise SchemaError(f"Objeto não serializável: {type(obj)!r}")


if __name__ == "__main__":
    payload = {
        "request_id": "req-001",
        "tenant_id": "digital-meta",
        "record": {
            "record_id": "rec-001",
            "features": [0.1, 0.8, 0.3],
            "metadata": {"source": "demo"},
        },
    }

    request = SchemaFactory.prediction_request_from_dict(payload)
    print(json.dumps(schema_to_dict(request), indent=2, ensure_ascii=False, default=str))

    response = PredictionResponse(
        request_id=request.context.request_id,
        prediction_id=str(uuid.uuid4()),
        record_id=request.record.record_id,
        anomaly_score=0.91,
        decision=AnomalyDecision.ANOMALY,
        severity=AnomalySeverity.HIGH,
        threshold=0.85,
        model_name="enterprise_anomaly_detector",
        model_version="1.0.0",
    )

    print(response.to_json())
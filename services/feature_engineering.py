# kwanza-ai-core/services/feature_engineering.py
"""
Enterprise Feature Engineering Service.

Responsável por:
- padronizar transformação de dados brutos em features
- registrar pipelines de features por domínio/modelo
- executar transformação realtime e batch
- validar schemas de entrada/saída
- cache TTL
- auditoria
- métricas operacionais
- persistência local de artefatos
- integração opcional com feature engineers de ML
"""

from __future__ import annotations

import hashlib
import json
import pickle
import time
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

import numpy as np


class FeatureServiceError(RuntimeError):
    pass


class FeaturePipelineStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"
    TESTING = "testing"


class FeatureExecutionMode(str, Enum):
    REALTIME = "realtime"
    BATCH = "batch"
    STREAM = "stream"


class FeatureValueType(str, Enum):
    FLOAT = "float"
    INT = "int"
    STRING = "string"
    BOOLEAN = "boolean"
    VECTOR = "vector"
    JSON = "json"


@dataclass(frozen=True)
class FeatureServiceConfig:
    service_name: str = "feature-engineering-service"
    environment: str = "production"
    artifact_dir: str = "artifacts/feature_engineering"
    cache_enabled: bool = True
    cache_ttl_seconds: int = 300
    audit_enabled: bool = True
    max_batch_size: int = 10_000
    fail_on_validation_error: bool = True


@dataclass(frozen=True)
class FeatureFieldSchema:
    name: str
    value_type: FeatureValueType
    required: bool = True
    default: Optional[Any] = None
    description: Optional[str] = None


@dataclass(frozen=True)
class FeatureSchema:
    schema_id: str
    version: str
    fields: List[FeatureFieldSchema]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeaturePipelineDefinition:
    pipeline_id: str
    name: str
    version: str
    status: FeaturePipelineStatus
    input_schema: Optional[FeatureSchema] = None
    output_schema: Optional[FeatureSchema] = None
    domain: Optional[str] = None
    model_name: Optional[str] = None
    owner: Optional[str] = None
    tags: Dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeatureRequest:
    request_id: str
    pipeline_id: str
    records: List[Mapping[str, Any]]
    mode: FeatureExecutionMode = FeatureExecutionMode.REALTIME
    tenant_id: Optional[str] = None
    update_state: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeatureRecord:
    record_id: str
    features: Dict[str, float]
    feature_names: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeatureResponse:
    request_id: str
    pipeline_id: str
    pipeline_version: str
    total: int
    succeeded: int
    failed: int
    feature_names: List[str]
    records: List[FeatureRecord]
    errors: List[Dict[str, Any]]
    cached: bool
    latency_ms: float
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_matrix(self) -> np.ndarray:
        return np.asarray(
            [
                [record.features.get(name, 0.0) for name in self.feature_names]
                for record in self.records
            ],
            dtype=float,
        )

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=indent, default=str)


@dataclass
class FeatureServiceMetrics:
    total_requests: int = 0
    total_records_processed: int = 0
    total_errors: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    latency_ms_sum: float = 0.0
    latency_ms_max: float = 0.0

    def snapshot(self) -> Dict[str, float]:
        avg = self.latency_ms_sum / max(self.total_requests, 1)
        return {
            "total_requests": float(self.total_requests),
            "total_records_processed": float(self.total_records_processed),
            "total_errors": float(self.total_errors),
            "cache_hits": float(self.cache_hits),
            "cache_misses": float(self.cache_misses),
            "latency_ms_avg": float(avg),
            "latency_ms_max": float(self.latency_ms_max),
        }


class FeatureTransformer(Protocol):
    feature_names: Sequence[str]

    def transform(self, records: Sequence[Mapping[str, Any]], *, update_state: bool = True) -> Any:
        ...


@dataclass
class CacheEntry:
    value: FeatureResponse
    expires_at: float


class TTLFeatureCache:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._data: Dict[str, CacheEntry] = {}
        self._lock = RLock()

    def get(self, key: str) -> Optional[FeatureResponse]:
        now = time.time()

        with self._lock:
            entry = self._data.get(key)

            if entry is None:
                return None

            if entry.expires_at < now:
                self._data.pop(key, None)
                return None

            return entry.value

    def set(self, key: str, value: FeatureResponse) -> None:
        with self._lock:
            self._data[key] = CacheEntry(
                value=value,
                expires_at=time.time() + self.ttl_seconds,
            )

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class FeatureAuditSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def write(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(dict(event), ensure_ascii=False, default=str) + "\n")


class FeatureSchemaValidator:
    @staticmethod
    def validate_record(record: Mapping[str, Any], schema: FeatureSchema) -> List[Dict[str, Any]]:
        errors: List[Dict[str, Any]] = []

        for field_schema in schema.fields:
            value = record.get(field_schema.name)

            if value is None:
                if field_schema.required and field_schema.default is None:
                    errors.append(
                        {
                            "field": field_schema.name,
                            "message": "Campo obrigatório ausente.",
                            "code": "required",
                        }
                    )
                continue

            if not FeatureSchemaValidator._matches_type(value, field_schema.value_type):
                errors.append(
                    {
                        "field": field_schema.name,
                        "message": f"Tipo inválido. Esperado: {field_schema.value_type.value}",
                        "code": "type_error",
                    }
                )

        return errors

    @staticmethod
    def _matches_type(value: Any, value_type: FeatureValueType) -> bool:
        if value_type == FeatureValueType.FLOAT:
            return FeatureSchemaValidator._is_float(value)

        if value_type == FeatureValueType.INT:
            return isinstance(value, int) and not isinstance(value, bool)

        if value_type == FeatureValueType.STRING:
            return isinstance(value, str)

        if value_type == FeatureValueType.BOOLEAN:
            return isinstance(value, bool)

        if value_type == FeatureValueType.VECTOR:
            return isinstance(value, Sequence) and not isinstance(value, (str, bytes, Mapping))

        if value_type == FeatureValueType.JSON:
            return isinstance(value, Mapping)

        return True

    @staticmethod
    def _is_float(value: Any) -> bool:
        try:
            float(value)
            return True
        except Exception:
            return False


class IdentityFeatureTransformer:
    """
    Transformer padrão para payloads que já vêm com features prontas.

    Aceita:
    - {"features": [1, 2, 3]}
    - {"features": {"a": 1, "b": 2}}
    - campos numéricos diretamente no record
    """

    def __init__(self, feature_names: Optional[Sequence[str]] = None) -> None:
        self.feature_names = list(feature_names or [])

    def transform(self, records: Sequence[Mapping[str, Any]], *, update_state: bool = True) -> Any:
        vectors: List[FeatureRecord] = []
        names = list(self.feature_names)

        if not names:
            names = self._infer_names(records)

        for i, record in enumerate(records):
            record_id = str(record.get("record_id") or record.get("id") or f"record-{i}")
            features = self._extract_features(record, names)

            vectors.append(
                FeatureRecord(
                    record_id=record_id,
                    features=features,
                    feature_names=names,
                    metadata={"transformer": "identity"},
                )
            )

        return FeatureTransformResult(feature_names=names, records=vectors)

    def _infer_names(self, records: Sequence[Mapping[str, Any]]) -> List[str]:
        if not records:
            return []

        first = records[0]

        if isinstance(first.get("features"), Mapping):
            return sorted(str(k) for k in first["features"].keys())

        if isinstance(first.get("features"), Sequence) and not isinstance(first.get("features"), (str, bytes)):
            return [f"feature_{i}" for i in range(len(first["features"]))]

        ignored = {"record_id", "id", "timestamp", "entity_id", "metadata", "label", "target"}
        names = []

        for key, value in first.items():
            if key in ignored:
                continue
            if self._is_numeric(value):
                names.append(str(key))

        return sorted(names)

    def _extract_features(self, record: Mapping[str, Any], names: Sequence[str]) -> Dict[str, float]:
        raw = record.get("features")

        if isinstance(raw, Mapping):
            return {name: float(raw.get(name, 0.0)) for name in names}

        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            return {
                name: float(raw[i]) if i < len(raw) else 0.0
                for i, name in enumerate(names)
            }

        return {
            name: float(record.get(name, 0.0))
            for name in names
        }

    @staticmethod
    def _is_numeric(value: Any) -> bool:
        try:
            float(value)
            return True
        except Exception:
            return False


@dataclass(frozen=True)
class FeatureTransformResult:
    feature_names: List[str]
    records: List[FeatureRecord]


class FeaturePipelineRegistry:
    def __init__(self) -> None:
        self._definitions: Dict[str, FeaturePipelineDefinition] = {}
        self._transformers: Dict[str, FeatureTransformer] = {}
        self._lock = RLock()

    def register(
        self,
        definition: FeaturePipelineDefinition,
        transformer: FeatureTransformer,
    ) -> None:
        with self._lock:
            self._definitions[definition.pipeline_id] = definition
            self._transformers[definition.pipeline_id] = transformer

    def unregister(self, pipeline_id: str) -> None:
        with self._lock:
            self._definitions.pop(pipeline_id, None)
            self._transformers.pop(pipeline_id, None)

    def get_definition(self, pipeline_id: str) -> FeaturePipelineDefinition:
        with self._lock:
            definition = self._definitions.get(pipeline_id)

        if definition is None:
            raise FeatureServiceError(f"Pipeline não registrado: {pipeline_id}")

        return definition

    def get_transformer(self, pipeline_id: str) -> FeatureTransformer:
        with self._lock:
            transformer = self._transformers.get(pipeline_id)

        if transformer is None:
            raise FeatureServiceError(f"Transformer não registrado: {pipeline_id}")

        return transformer

    def list_pipelines(self) -> List[FeaturePipelineDefinition]:
        with self._lock:
            return sorted(self._definitions.values(), key=lambda x: x.created_at, reverse=True)


class FeatureEngineeringService:
    def __init__(
        self,
        config: Optional[FeatureServiceConfig] = None,
        registry: Optional[FeaturePipelineRegistry] = None,
        audit_sink: Optional[FeatureAuditSink] = None,
    ) -> None:
        self.config = config or FeatureServiceConfig()
        self.registry = registry or FeaturePipelineRegistry()
        self.cache = TTLFeatureCache(self.config.cache_ttl_seconds)
        self.audit_sink = audit_sink or FeatureAuditSink(
            Path(self.config.artifact_dir) / "audit.jsonl"
        )
        self.metrics = FeatureServiceMetrics()
        self._lock = RLock()

    def register_pipeline(
        self,
        definition: FeaturePipelineDefinition,
        transformer: FeatureTransformer,
    ) -> None:
        self.registry.register(definition, transformer)

        self._audit(
            "feature_pipeline_registered",
            {
                "pipeline_id": definition.pipeline_id,
                "name": definition.name,
                "version": definition.version,
                "status": definition.status.value,
            },
        )

    def transform(self, request: FeatureRequest) -> FeatureResponse:
        started = time.perf_counter()

        self._validate_request(request)

        cache_key = self._cache_key(request)

        if self.config.cache_enabled:
            cached = self.cache.get(cache_key)
            if cached:
                with self._lock:
                    self.metrics.cache_hits += 1
                return FeatureResponse(
                    **{
                        **asdict(cached),
                        "cached": True,
                        "latency_ms": (time.perf_counter() - started) * 1000,
                    }
                )

            with self._lock:
                self.metrics.cache_misses += 1

        try:
            definition = self.registry.get_definition(request.pipeline_id)

            if definition.status not in {FeaturePipelineStatus.ACTIVE, FeaturePipelineStatus.TESTING}:
                raise FeatureServiceError(
                    f"Pipeline {request.pipeline_id} não está ativo: {definition.status.value}"
                )

            validated_records, validation_errors = self._validate_records(request.records, definition)

            if validation_errors and self.config.fail_on_validation_error:
                raise FeatureServiceError(f"Erros de validação: {validation_errors[:5]}")

            transformer = self.registry.get_transformer(request.pipeline_id)
            transformed = transformer.transform(
                validated_records,
                update_state=request.update_state,
            )

            feature_names, feature_records = self._normalize_transform_result(transformed)

            latency_ms = (time.perf_counter() - started) * 1000

            response = FeatureResponse(
                request_id=request.request_id,
                pipeline_id=request.pipeline_id,
                pipeline_version=definition.version,
                total=len(request.records),
                succeeded=len(feature_records),
                failed=len(validation_errors),
                feature_names=feature_names,
                records=feature_records,
                errors=validation_errors,
                cached=False,
                latency_ms=latency_ms,
                metadata={
                    "tenant_id": request.tenant_id,
                    "mode": request.mode.value,
                    **request.metadata,
                },
            )

            if self.config.cache_enabled:
                self.cache.set(cache_key, response)

            with self._lock:
                self.metrics.total_requests += 1
                self.metrics.total_records_processed += len(feature_records)
                self.metrics.total_errors += len(validation_errors)
                self.metrics.latency_ms_sum += latency_ms
                self.metrics.latency_ms_max = max(self.metrics.latency_ms_max, latency_ms)

            self._audit(
                "feature_transform_completed",
                {
                    "request_id": request.request_id,
                    "pipeline_id": request.pipeline_id,
                    "tenant_id": request.tenant_id,
                    "records": len(request.records),
                    "succeeded": len(feature_records),
                    "failed": len(validation_errors),
                    "latency_ms": latency_ms,
                },
            )

            return response

        except Exception as exc:
            with self._lock:
                self.metrics.total_errors += 1

            self._audit(
                "feature_transform_error",
                {
                    "request_id": request.request_id,
                    "pipeline_id": request.pipeline_id,
                    "tenant_id": request.tenant_id,
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                },
            )

            raise

    def transform_one(
        self,
        pipeline_id: str,
        record: Mapping[str, Any],
        *,
        tenant_id: Optional[str] = None,
        update_state: bool = True,
    ) -> FeatureRecord:
        response = self.transform(
            FeatureRequest(
                request_id=str(uuid.uuid4()),
                pipeline_id=pipeline_id,
                records=[record],
                tenant_id=tenant_id,
                update_state=update_state,
            )
        )

        if not response.records:
            raise FeatureServiceError("Nenhum feature record retornado.")

        return response.records[0]

    def list_pipelines(self) -> List[FeaturePipelineDefinition]:
        return self.registry.list_pipelines()

    def save_registry(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        payload = [asdict(p) for p in self.registry.list_pipelines()]
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        return target

    def save_transformer(self, pipeline_id: str, path: str | Path) -> Path:
        transformer = self.registry.get_transformer(pipeline_id)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        with target.open("wb") as file:
            pickle.dump(transformer, file)

        return target

    def health(self) -> Dict[str, Any]:
        return {
            "service": self.config.service_name,
            "environment": self.config.environment,
            "status": "ok",
            "pipelines": len(self.registry.list_pipelines()),
            "cache_enabled": self.config.cache_enabled,
            "metrics": self.metrics.snapshot(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def metrics_snapshot(self) -> Dict[str, float]:
        return self.metrics.snapshot()

    def _validate_request(self, request: FeatureRequest) -> None:
        if not request.request_id:
            raise FeatureServiceError("request_id obrigatório.")

        if not request.pipeline_id:
            raise FeatureServiceError("pipeline_id obrigatório.")

        if not request.records:
            raise FeatureServiceError("records não pode ser vazio.")

        if len(request.records) > self.config.max_batch_size:
            raise FeatureServiceError(
                f"Batch excede limite máximo: {len(request.records)} > {self.config.max_batch_size}"
            )

    def _validate_records(
        self,
        records: Sequence[Mapping[str, Any]],
        definition: FeaturePipelineDefinition,
    ) -> Tuple[List[Mapping[str, Any]], List[Dict[str, Any]]]:
        if definition.input_schema is None:
            return list(records), []

        valid: List[Mapping[str, Any]] = []
        errors: List[Dict[str, Any]] = []

        for i, record in enumerate(records):
            record_errors = FeatureSchemaValidator.validate_record(record, definition.input_schema)

            if record_errors:
                errors.append(
                    {
                        "index": i,
                        "record_id": record.get("record_id") or record.get("id"),
                        "errors": record_errors,
                    }
                )
            else:
                valid.append(record)

        return valid, errors

    def _normalize_transform_result(self, result: Any) -> Tuple[List[str], List[FeatureRecord]]:
        if isinstance(result, FeatureTransformResult):
            return result.feature_names, result.records

        if hasattr(result, "feature_names") and hasattr(result, "vectors"):
            feature_names = list(result.feature_names)
            records = []

            for vector in result.vectors:
                if is_dataclass(vector):
                    raw = asdict(vector)
                else:
                    raw = dict(vector)

                records.append(
                    FeatureRecord(
                        record_id=str(raw.get("record_id") or uuid.uuid4()),
                        features={str(k): float(v) for k, v in raw.get("features", {}).items()},
                        feature_names=feature_names,
                        metadata=dict(raw.get("metadata", {})),
                    )
                )

            return feature_names, records

        if isinstance(result, Mapping):
            feature_names = list(result.get("feature_names", []))
            records = [
                FeatureRecord(
                    record_id=str(r.get("record_id") or uuid.uuid4()),
                    features={str(k): float(v) for k, v in r.get("features", {}).items()},
                    feature_names=feature_names,
                    metadata=dict(r.get("metadata", {})),
                )
                for r in result.get("records", [])
            ]
            return feature_names, records

        raise FeatureServiceError(f"Resultado de transformação não suportado: {type(result)!r}")

    def _audit(self, event: str, payload: Mapping[str, Any]) -> None:
        if not self.config.audit_enabled:
            return

        self.audit_sink.write(
            {
                "event_id": str(uuid.uuid4()),
                "event": event,
                "service": self.config.service_name,
                "environment": self.config.environment,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **dict(payload),
            }
        )

    @staticmethod
    def _cache_key(request: FeatureRequest) -> str:
        payload = {
            "pipeline_id": request.pipeline_id,
            "records": request.records,
            "mode": request.mode.value,
            "tenant_id": request.tenant_id,
            "update_state": request.update_state,
        }

        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def create_default_identity_pipeline(
    pipeline_id: str = "identity-features",
    feature_names: Optional[Sequence[str]] = None,
) -> Tuple[FeaturePipelineDefinition, IdentityFeatureTransformer]:
    definition = FeaturePipelineDefinition(
        pipeline_id=pipeline_id,
        name="Identity Feature Pipeline",
        version="1.0.0",
        status=FeaturePipelineStatus.ACTIVE,
        domain="generic",
        tags={"type": "identity"},
    )

    transformer = IdentityFeatureTransformer(feature_names=feature_names)
    return definition, transformer


if __name__ == "__main__":
    service = FeatureEngineeringService(
        FeatureServiceConfig(environment="development")
    )

    definition, transformer = create_default_identity_pipeline(
        feature_names=["amount", "latency_ms", "error_count"]
    )

    service.register_pipeline(definition, transformer)

    response = service.transform(
        FeatureRequest(
            request_id="req-001",
            pipeline_id="identity-features",
            tenant_id="digital-meta",
            records=[
                {
                    "record_id": "rec-001",
                    "amount": 1500,
                    "latency_ms": 120,
                    "error_count": 2,
                },
                {
                    "record_id": "rec-002",
                    "amount": 9000,
                    "latency_ms": 450,
                    "error_count": 8,
                },
            ],
        )
    )

    print(response.to_json())
    print(response.to_matrix())
    print(json.dumps(service.health(), indent=2, ensure_ascii=False))
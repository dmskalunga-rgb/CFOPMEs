#!/usr/bin/env python3
"""
data/ingestion/__init__.py

Enterprise-grade ingestion package initializer.

Objetivo:
- Centralizar exports públicos da camada de ingestão.
- Fornecer contratos base para API, batch, stream, Kafka, RabbitMQ e arquivos.
- Disponibilizar registry/factory de handlers de ingestão sem acoplamento forte.
- Carregar módulos opcionais com segurança, sem quebrar o pacote quando dependências externas
  como kafka-python, pika, requests ou pandas ainda não estiverem instaladas.

Uso:
    from data.ingestion import IngestionRecord, IngestionResult, get_ingestion_registry

    registry = get_ingestion_registry()
    handler_cls = registry.get("api")

Padrões enterprise:
- Idempotência por record_id/source/hash.
- Metadados, tenant_id, correlation_id e traceability.
- Contrato de resultado padronizado.
- Imports opcionais seguros.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Type


LOGGER = logging.getLogger(__name__)
INGESTION_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc


class IngestionMode(str, Enum):
    API = "api"
    BATCH = "batch"
    STREAM = "stream"
    KAFKA_CONSUMER = "kafka_consumer"
    KAFKA_PRODUCER = "kafka_producer"
    RABBITMQ_CONSUMER = "rabbitmq_consumer"
    RABBITMQ_PRODUCER = "rabbitmq_producer"
    FILE = "file"
    CUSTOM = "custom"


class IngestionStatus(str, Enum):
    ACCEPTED = "accepted"
    PROCESSED = "processed"
    SKIPPED = "skipped"
    FAILED = "failed"
    PARTIAL = "partial"


class PayloadFormat(str, Enum):
    JSON = "json"
    CSV = "csv"
    PARQUET = "parquet"
    AVRO = "avro"
    XML = "xml"
    TEXT = "text"
    BINARY = "binary"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IngestionRecord:
    payload: Any
    source: str
    record_id: str = field(default_factory=lambda: f"ing_{uuid.uuid4().hex[:20]}")
    tenant_id: Optional[str] = None
    correlation_id: str = field(default_factory=lambda: f"corr_{uuid.uuid4().hex[:16]}")
    format: PayloadFormat = PayloadFormat.JSON
    occurred_at: str = field(default_factory=lambda: datetime.now(tz=DEFAULT_TIMEZONE).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def payload_hash(self) -> str:
        return hash_payload(self.payload)

    @property
    def idempotency_key(self) -> str:
        base = f"{self.tenant_id or '-'}|{self.source}|{self.record_id}|{self.payload_hash}"
        return hashlib.sha256(base.encode("utf-8")).hexdigest()

    def safe_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "source": self.source,
            "tenant_id": self.tenant_id,
            "correlation_id": self.correlation_id,
            "format": self.format.value,
            "occurred_at": self.occurred_at,
            "payload_hash": self.payload_hash,
            "idempotency_key": self.idempotency_key,
            "metadata": sanitize_metadata(self.metadata),
        }


@dataclass(frozen=True)
class IngestionResult:
    status: IngestionStatus
    accepted: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    started_at: Optional[str] = None
    finished_at: str = field(default_factory=lambda: datetime.now(tz=DEFAULT_TIMEZONE).isoformat())

    @property
    def ok(self) -> bool:
        return self.status in {IngestionStatus.ACCEPTED, IngestionStatus.PROCESSED, IngestionStatus.SKIPPED, IngestionStatus.PARTIAL} and self.failed == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "accepted": self.accepted,
            "processed": self.processed,
            "skipped": self.skipped,
            "failed": self.failed,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "metadata": sanitize_metadata(self.metadata),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class IngestionModuleInfo:
    name: str
    import_path: str
    loaded: bool
    error: Optional[str] = None
    exported_symbols: List[str] = field(default_factory=list)


class IngestionHandler(Protocol):
    mode: IngestionMode

    def ingest(self, records: Sequence[IngestionRecord]) -> IngestionResult:
        ...


class BaseIngestionHandler:
    mode: IngestionMode = IngestionMode.CUSTOM

    def ingest(self, records: Sequence[IngestionRecord]) -> IngestionResult:
        started = datetime.now(tz=DEFAULT_TIMEZONE).isoformat()
        return IngestionResult(
            status=IngestionStatus.ACCEPTED,
            accepted=len(records),
            processed=0,
            skipped=0,
            failed=0,
            started_at=started,
            metadata={"handler": self.__class__.__name__, "mode": self.mode.value},
        )


class IngestionRegistry:
    def __init__(self) -> None:
        self._handlers: Dict[str, Type[BaseIngestionHandler]] = {}
        self._modules: Dict[str, IngestionModuleInfo] = {}

    def register(self, name: str, handler: Type[BaseIngestionHandler], overwrite: bool = False) -> None:
        key = normalize_key(name)
        if key in self._handlers and not overwrite:
            raise ValueError(f"Handler de ingestão já registrado: {key}")
        self._handlers[key] = handler

    def get(self, name: str) -> Optional[Type[BaseIngestionHandler]]:
        return self._handlers.get(normalize_key(name))

    def create(self, name: str, *args: Any, **kwargs: Any) -> BaseIngestionHandler:
        handler_cls = self.get(name)
        if handler_cls is None:
            raise KeyError(f"Handler de ingestão não encontrado: {name}")
        return handler_cls(*args, **kwargs)

    def names(self) -> List[str]:
        return sorted(self._handlers.keys())

    def modules(self) -> Dict[str, IngestionModuleInfo]:
        return dict(self._modules)

    def set_module_info(self, info: IngestionModuleInfo) -> None:
        self._modules[info.name] = info

    def metadata(self) -> Dict[str, Any]:
        return {
            "version": INGESTION_VERSION,
            "handlers": self.names(),
            "modules": {name: info.__dict__ for name, info in self._modules.items()},
        }


registry = IngestionRegistry()
registry.register("base", BaseIngestionHandler, overwrite=True)
registry.register("custom", BaseIngestionHandler, overwrite=True)


OPTIONAL_INGESTION_MODULES = {
    "api": "data.ingestion.api_ingestion",
    "batch": "data.ingestion.batch_ingestion",
    "stream": "data.ingestion.stream_ingestion",
    "kafka_consumer": "data.ingestion.kafka_consumer",
    "kafka_producer": "data.ingestion.kafka_producer",
    "rabbitmq_consumer": "data.ingestion.rabbitmq_consumer",
    "rabbitmq_producer": "data.ingestion.rabbitmq_producer",
}


def load_optional_ingestion_modules() -> Dict[str, IngestionModuleInfo]:
    for name, import_path in OPTIONAL_INGESTION_MODULES.items():
        try:
            module = importlib.import_module(import_path)
            exported = list(getattr(module, "__all__", []))
            for symbol in exported:
                value = getattr(module, symbol, None)
                if isinstance(value, type) and issubclass(value, BaseIngestionHandler):
                    registry.register(name, value, overwrite=True)
            registry.set_module_info(IngestionModuleInfo(name=name, import_path=import_path, loaded=True, exported_symbols=exported))
        except ModuleNotFoundError as exc:
            registry.set_module_info(IngestionModuleInfo(name=name, import_path=import_path, loaded=False, error=str(exc)))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Falha ao carregar módulo de ingestão %s: %s", import_path, exc)
            registry.set_module_info(IngestionModuleInfo(name=name, import_path=import_path, loaded=False, error=str(exc)))
    return registry.modules()


def get_ingestion_registry() -> IngestionRegistry:
    return registry


def create_ingestion_handler(name: str, *args: Any, **kwargs: Any) -> BaseIngestionHandler:
    return registry.create(name, *args, **kwargs)


def build_records(
    payloads: Iterable[Any],
    source: str,
    tenant_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    payload_format: PayloadFormat = PayloadFormat.JSON,
    metadata: Optional[Mapping[str, Any]] = None,
) -> List[IngestionRecord]:
    corr = correlation_id or f"corr_{uuid.uuid4().hex[:16]}"
    return [
        IngestionRecord(
            payload=payload,
            source=source,
            tenant_id=tenant_id,
            correlation_id=corr,
            format=payload_format,
            metadata=dict(metadata or {}),
        )
        for payload in payloads
    ]


def hash_payload(payload: Any) -> str:
    try:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    except TypeError:
        raw = repr(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sanitize_metadata(metadata: Mapping[str, Any]) -> Dict[str, Any]:
    sensitive = {"password", "secret", "token", "api_key", "apikey", "authorization", "cookie"}
    result: Dict[str, Any] = {}
    for key, value in metadata.items():
        key_text = str(key)
        lower = key_text.lower()
        if any(item in lower for item in sensitive):
            result[key_text] = "[REDACTED]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key_text] = value
        else:
            result[key_text] = str(value)[:500]
    return result


def normalize_key(value: str) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def ingestion_package_metadata() -> Dict[str, Any]:
    return registry.metadata()


load_optional_ingestion_modules()


__all__ = [
    "INGESTION_VERSION",
    "IngestionMode",
    "IngestionStatus",
    "PayloadFormat",
    "IngestionRecord",
    "IngestionResult",
    "IngestionModuleInfo",
    "IngestionHandler",
    "BaseIngestionHandler",
    "IngestionRegistry",
    "OPTIONAL_INGESTION_MODULES",
    "load_optional_ingestion_modules",
    "get_ingestion_registry",
    "create_ingestion_handler",
    "build_records",
    "hash_payload",
    "sanitize_metadata",
    "ingestion_package_metadata",
]

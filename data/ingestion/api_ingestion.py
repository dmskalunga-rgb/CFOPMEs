# data/ingestion/api_ingestion.py

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from data.ingestion.ingestion_pipeline import (
    IngestionContext,
    IngestionPipeline,
    IngestionResult,
    SinkFn,
)

logger = logging.getLogger(__name__)


class ApiPayloadMode(str, Enum):
    SINGLE = "single"
    BULK = "bulk"
    AUTO = "auto"


class ApiIngestionPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(slots=True)
class ApiIngestionRequest:
    payload: Mapping[str, Any] | Sequence[Mapping[str, Any]]
    source: str = "api"
    tenant_id: str | None = None
    requested_by: str | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None
    payload_mode: ApiPayloadMode = ApiPayloadMode.AUTO
    priority: ApiIngestionPriority = ApiIngestionPriority.NORMAL
    headers: Mapping[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ApiIngestionConfig:
    max_records_per_request: int = 10_000
    max_payload_size_bytes: int = 25 * 1024 * 1024
    reject_empty_payload: bool = True
    enable_idempotency: bool = True
    timeout_seconds: float = 60.0
    enrich_with_request_metadata: bool = True


class ApiIngestionError(Exception):
    pass


class PayloadTooLargeError(ApiIngestionError):
    pass


class InvalidApiPayloadError(ApiIngestionError):
    pass


class DuplicateIngestionError(ApiIngestionError):
    pass


IdempotencyChecker = Callable[[str], bool]
IdempotencyRecorder = Callable[[str, IngestionResult], None]


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._keys: dict[str, IngestionResult] = {}

    def exists(self, key: str) -> bool:
        return key in self._keys

    def record(self, key: str, result: IngestionResult) -> None:
        self._keys[key] = result

    def get(self, key: str) -> IngestionResult | None:
        return self._keys.get(key)


class ApiIngestionService:
    """
    Serviço enterprise para ingestão via API.

    Recursos:
    - Payload unitário ou bulk
    - Idempotência
    - Correlação distribuída
    - Limite de tamanho
    - Limite de registros
    - Timeout
    - Logs estruturados
    - Enriquecimento de metadados
    - Integração com IngestionPipeline
    """

    def __init__(
        self,
        *,
        pipeline: IngestionPipeline | None = None,
        config: ApiIngestionConfig | None = None,
        idempotency_store: InMemoryIdempotencyStore | None = None,
    ) -> None:
        self.pipeline = pipeline or IngestionPipeline()
        self.config = config or ApiIngestionConfig()
        self.idempotency_store = idempotency_store or InMemoryIdempotencyStore()

    async def ingest(
        self,
        *,
        request: ApiIngestionRequest,
        sink: SinkFn,
    ) -> IngestionResult:
        started = time.perf_counter()

        correlation_id = request.correlation_id or self._extract_correlation_id(request.headers)
        idempotency_key = request.idempotency_key or self._extract_idempotency_key(request.headers)

        if not correlation_id:
            correlation_id = str(uuid.uuid4())

        records = self._normalize_payload(request)

        self._validate_limits(records)

        payload_hash = self._payload_hash(records)

        if self.config.enable_idempotency:
            effective_key = idempotency_key or payload_hash

            if self.idempotency_store.exists(effective_key):
                existing = self.idempotency_store.get(effective_key)
                if existing:
                    logger.warning(
                        "Duplicate API ingestion ignored.",
                        extra={
                            "correlation_id": correlation_id,
                            "idempotency_key": effective_key,
                            "source": request.source,
                            "tenant_id": request.tenant_id,
                        },
                    )
                    return existing

        context = IngestionContext(
            source=request.source,
            tenant_id=request.tenant_id,
            requested_by=request.requested_by,
            correlation_id=correlation_id,
            metadata=self._build_metadata(
                request=request,
                record_count=len(records),
                payload_hash=payload_hash,
                idempotency_key=idempotency_key,
            ),
        )

        logger.info(
            "API ingestion started.",
            extra={
                "source": context.source,
                "tenant_id": context.tenant_id,
                "requested_by": context.requested_by,
                "correlation_id": context.correlation_id,
                "idempotency_key": idempotency_key,
                "record_count": len(records),
                "priority": request.priority.value,
            },
        )

        try:
            result = await asyncio.wait_for(
                self.pipeline.run(
                    records=records,
                    context=context,
                    sink=sink,
                ),
                timeout=self.config.timeout_seconds,
            )

            if self.config.enable_idempotency:
                effective_key = idempotency_key or payload_hash
                self.idempotency_store.record(effective_key, result)

            logger.info(
                "API ingestion finished.",
                extra={
                    "source": result.source,
                    "status": result.status,
                    "total_records": result.total_records,
                    "accepted_records": result.accepted_records,
                    "rejected_records": result.rejected_records,
                    "correlation_id": result.correlation_id,
                    "duration_ms": result.duration_ms,
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                },
            )

            return result

        except asyncio.TimeoutError as exc:
            logger.exception(
                "API ingestion timeout.",
                extra={
                    "source": request.source,
                    "tenant_id": request.tenant_id,
                    "correlation_id": correlation_id,
                    "timeout_seconds": self.config.timeout_seconds,
                },
            )
            raise ApiIngestionError(
                f"API ingestion exceeded timeout of {self.config.timeout_seconds} seconds."
            ) from exc

        except Exception:
            logger.exception(
                "API ingestion failed.",
                extra={
                    "source": request.source,
                    "tenant_id": request.tenant_id,
                    "correlation_id": correlation_id,
                },
            )
            raise

    async def ingest_payload(
        self,
        *,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        sink: SinkFn,
        source: str = "api",
        tenant_id: str | None = None,
        requested_by: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
        headers: Mapping[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
        priority: ApiIngestionPriority = ApiIngestionPriority.NORMAL,
    ) -> IngestionResult:
        request = ApiIngestionRequest(
            payload=payload,
            source=source,
            tenant_id=tenant_id,
            requested_by=requested_by,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            headers=headers or {},
            metadata=metadata or {},
            priority=priority,
        )

        return await self.ingest(request=request, sink=sink)

    def _normalize_payload(
        self,
        request: ApiIngestionRequest,
    ) -> list[Mapping[str, Any]]:
        payload = request.payload

        if isinstance(payload, Mapping):
            if request.payload_mode == ApiPayloadMode.BULK:
                raise InvalidApiPayloadError("Payload mode BULK requires a sequence of records.")

            records = [payload]

        elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
            if request.payload_mode == ApiPayloadMode.SINGLE:
                raise InvalidApiPayloadError("Payload mode SINGLE requires a single mapping.")

            records = []

            for index, item in enumerate(payload):
                if not isinstance(item, Mapping):
                    raise InvalidApiPayloadError(
                        f"Payload item at index {index} must be a mapping."
                    )

                records.append(item)

        else:
            raise InvalidApiPayloadError("Payload must be a mapping or sequence of mappings.")

        if self.config.reject_empty_payload and not records:
            raise InvalidApiPayloadError("Payload cannot be empty.")

        return records

    def _validate_limits(self, records: Sequence[Mapping[str, Any]]) -> None:
        if len(records) > self.config.max_records_per_request:
            raise PayloadTooLargeError(
                f"Payload contains {len(records)} records. "
                f"Limit is {self.config.max_records_per_request}."
            )

        payload_size = len(
            json.dumps(records, default=str, ensure_ascii=False).encode("utf-8")
        )

        if payload_size > self.config.max_payload_size_bytes:
            raise PayloadTooLargeError(
                f"Payload size {payload_size} bytes exceeds limit "
                f"{self.config.max_payload_size_bytes} bytes."
            )

    def _build_metadata(
        self,
        *,
        request: ApiIngestionRequest,
        record_count: int,
        payload_hash: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        metadata = dict(request.metadata)

        if not self.config.enrich_with_request_metadata:
            return metadata

        metadata.update(
            {
                "entrypoint": "api",
                "record_count": record_count,
                "payload_hash": payload_hash,
                "idempotency_key": idempotency_key,
                "priority": request.priority.value,
                "payload_mode": request.payload_mode.value,
                "received_at": datetime.now(timezone.utc).isoformat(),
                "headers": self._safe_headers(request.headers),
            }
        )

        return metadata

    @staticmethod
    def _payload_hash(records: Sequence[Mapping[str, Any]]) -> str:
        canonical = json.dumps(
            records,
            sort_keys=True,
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _extract_correlation_id(headers: Mapping[str, str]) -> str | None:
        for key in (
            "x-correlation-id",
            "x-request-id",
            "correlation-id",
            "request-id",
        ):
            value = headers.get(key) or headers.get(key.title())
            if value:
                return value
        return None

    @staticmethod
    def _extract_idempotency_key(headers: Mapping[str, str]) -> str | None:
        for key in ("idempotency-key", "x-idempotency-key"):
            value = headers.get(key) or headers.get(key.title())
            if value:
                return value
        return None

    @staticmethod
    def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
        sensitive = {
            "authorization",
            "cookie",
            "set-cookie",
            "x-api-key",
            "api-key",
            "proxy-authorization",
        }

        safe: dict[str, str] = {}

        for key, value in headers.items():
            if key.lower() in sensitive:
                safe[key] = "***"
            else:
                safe[key] = value

        return safe
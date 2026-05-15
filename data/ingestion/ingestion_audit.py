# data/ingestion/ingestion_audit.py

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    STARTED = "started"
    FINISHED = "finished"
    FAILED = "failed"
    VALIDATION_FAILED = "validation_failed"
    RECORD_REJECTED = "record_rejected"
    FILE_DISCOVERED = "file_discovered"
    FILE_PROCESSED = "file_processed"
    BATCH_FLUSHED = "batch_flushed"
    RETRY = "retry"
    SKIPPED = "skipped"


@dataclass(slots=True)
class AuditEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: AuditEventType | str = AuditEventType.STARTED
    source: str | None = None
    tenant_id: str | None = None
    correlation_id: str | None = None
    requested_by: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AuditWriter(Protocol):
    async def write(self, event: AuditEvent) -> None:
        ...


class LoggingAuditWriter:
    async def write(self, event: AuditEvent) -> None:
        logger.info(
            "ingestion.audit",
            extra={
                "event_id": event.event_id,
                "event_type": str(event.event_type),
                "source": event.source,
                "tenant_id": event.tenant_id,
                "correlation_id": event.correlation_id,
                "requested_by": event.requested_by,
                "payload": event.payload,
                "created_at": event.created_at,
            },
        )


class JsonFileAuditWriter:
    def __init__(self, file_path: str) -> None:
        self.file_path = file_path

    async def write(self, event: AuditEvent) -> None:
        line = json.dumps(asdict(event), default=str, ensure_ascii=False)

        with open(self.file_path, "a", encoding="utf-8") as file:
            file.write(line + "\n")


class IngestionAudit:
    """
    Serviço de auditoria da ingestão.

    Pode auditar:
    - Início e fim de execução
    - Falhas
    - Arquivos processados
    - Registros rejeitados
    - Validações inválidas
    - Flush de batches
    - Retentativas
    """

    def __init__(self, writer: AuditWriter | None = None) -> None:
        self.writer = writer or LoggingAuditWriter()

    async def event(
        self,
        event_type: AuditEventType | str,
        context: Any | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_type=event_type,
            source=getattr(context, "source", None),
            tenant_id=getattr(context, "tenant_id", None),
            correlation_id=getattr(context, "correlation_id", None),
            requested_by=getattr(context, "requested_by", None),
            payload=self._sanitize_payload(payload or {}),
        )

        await self.writer.write(event)
        return event

    async def started(self, context: Any, payload: dict[str, Any] | None = None) -> AuditEvent:
        return await self.event(AuditEventType.STARTED, context, payload)

    async def finished(self, context: Any, result: Any | None = None) -> AuditEvent:
        return await self.event(
            AuditEventType.FINISHED,
            context,
            self._object_to_dict(result) if result is not None else {},
        )

    async def failed(self, context: Any, exc: Exception) -> AuditEvent:
        return await self.event(
            AuditEventType.FAILED,
            context,
            {
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            },
        )

    async def validation_failed(
        self,
        context: Any,
        errors: list[str],
        warnings: list[str] | None = None,
    ) -> AuditEvent:
        return await self.event(
            AuditEventType.VALIDATION_FAILED,
            context,
            {
                "errors": errors,
                "warnings": warnings or [],
            },
        )

    async def record_rejected(
        self,
        context: Any,
        record: dict[str, Any],
        reason: str,
    ) -> AuditEvent:
        return await self.event(
            AuditEventType.RECORD_REJECTED,
            context,
            {
                "reason": reason,
                "record": self._sanitize_payload(record),
            },
        )

    async def file_discovered(
        self,
        context: Any,
        file_path: str,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        return await self.event(
            AuditEventType.FILE_DISCOVERED,
            context,
            {
                "file_path": file_path,
                "metadata": metadata or {},
            },
        )

    async def file_processed(
        self,
        context: Any,
        file_path: str,
        result: Any,
    ) -> AuditEvent:
        return await self.event(
            AuditEventType.FILE_PROCESSED,
            context,
            {
                "file_path": file_path,
                "result": self._object_to_dict(result),
            },
        )

    async def batch_flushed(
        self,
        context: Any,
        batch_size: int,
        result: Any | None = None,
    ) -> AuditEvent:
        return await self.event(
            AuditEventType.BATCH_FLUSHED,
            context,
            {
                "batch_size": batch_size,
                "result": self._object_to_dict(result) if result is not None else None,
            },
        )

    async def retry(
        self,
        context: Any,
        attempt: int,
        reason: str,
    ) -> AuditEvent:
        return await self.event(
            AuditEventType.RETRY,
            context,
            {
                "attempt": attempt,
                "reason": reason,
            },
        )

    async def skipped(
        self,
        context: Any,
        reason: str,
    ) -> AuditEvent:
        return await self.event(
            AuditEventType.SKIPPED,
            context,
            {
                "reason": reason,
            },
        )

    @staticmethod
    def _object_to_dict(value: Any) -> dict[str, Any]:
        if value is None:
            return {}

        if is_dataclass(value):
            return asdict(value)

        if isinstance(value, dict):
            return dict(value)

        if hasattr(value, "__dict__"):
            return dict(value.__dict__)

        return {"value": str(value)}

    @staticmethod
    def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
        sensitive_keys = {
            "password",
            "senha",
            "secret",
            "token",
            "access_token",
            "refresh_token",
            "authorization",
            "api_key",
            "apikey",
            "x-api-key",
            "cookie",
        }

        def sanitize(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: "***" if key.lower() in sensitive_keys else sanitize(inner_value)
                    for key, inner_value in value.items()
                }

            if isinstance(value, list):
                return [sanitize(item) for item in value]

            return value

        return sanitize(payload)
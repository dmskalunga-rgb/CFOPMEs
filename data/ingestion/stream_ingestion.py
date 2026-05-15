# data/ingestion/stream_ingestion.py

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from data.ingestion.ingestion_pipeline import (
    IngestionContext,
    IngestionPipeline,
    IngestionResult,
    SinkFn,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StreamIngestionConfig:
    flush_size: int = 500
    flush_interval_seconds: float = 5.0
    max_buffer_size: int = 50_000
    max_batches: int | None = None
    stop_on_error: bool = False
    idle_timeout_seconds: float | None = None
    enable_backpressure: bool = True


@dataclass(slots=True)
class StreamIngestionRequest:
    source: str = "stream"
    tenant_id: str | None = None
    requested_by: str | None = None
    correlation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StreamIngestionSummary:
    source: str
    correlation_id: str
    total_batches: int
    total_records: int
    accepted_records: int
    rejected_records: int
    duration_ms: float
    results: list[IngestionResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class StreamIngestionError(Exception):
    pass


class StreamBufferOverflowError(StreamIngestionError):
    pass


class StreamIngestionService:
    """
    Serviço enterprise para ingestão contínua.

    Recursos:
    - Async stream
    - Flush por tamanho
    - Flush por tempo
    - Backpressure
    - Controle de buffer máximo
    - Timeout de inatividade
    - Resumo consolidado
    - Integração com IngestionPipeline
    """

    def __init__(
        self,
        *,
        pipeline: IngestionPipeline | None = None,
        config: StreamIngestionConfig | None = None,
    ) -> None:
        self.pipeline = pipeline or IngestionPipeline()
        self.config = config or StreamIngestionConfig()

        if self.config.flush_size <= 0:
            raise ValueError("flush_size must be greater than zero.")

        if self.config.flush_interval_seconds <= 0:
            raise ValueError("flush_interval_seconds must be greater than zero.")

    async def ingest_stream(
        self,
        *,
        stream: AsyncIterator[Mapping[str, Any]],
        sink: SinkFn,
        request: StreamIngestionRequest | None = None,
        context: IngestionContext | None = None,
    ) -> StreamIngestionSummary:
        started = time.perf_counter()

        ingestion_context = context or self._build_context(request or StreamIngestionRequest())
        buffer: list[Mapping[str, Any]] = []
        results: list[IngestionResult] = []
        errors: list[str] = []

        total_batches = 0
        last_flush = asyncio.get_running_loop().time()
        last_record_at = last_flush

        logger.info(
            "Stream ingestion started.",
            extra={
                "source": ingestion_context.source,
                "tenant_id": ingestion_context.tenant_id,
                "correlation_id": ingestion_context.correlation_id,
                "flush_size": self.config.flush_size,
                "flush_interval_seconds": self.config.flush_interval_seconds,
            },
        )

        try:
            while True:
                try:
                    item = await self._next_with_timeout(stream)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    if buffer:
                        result = await self._flush(buffer, sink, ingestion_context)
                        results.append(result)
                        total_batches += 1
                        buffer = []
                    break

                last_record_at = asyncio.get_running_loop().time()
                buffer.append(item)

                if len(buffer) > self.config.max_buffer_size:
                    raise StreamBufferOverflowError(
                        f"Stream buffer exceeded max size: {self.config.max_buffer_size}"
                    )

                should_flush_by_size = len(buffer) >= self.config.flush_size
                should_flush_by_time = (
                    last_record_at - last_flush >= self.config.flush_interval_seconds
                )

                if should_flush_by_size or should_flush_by_time:
                    result = await self._safe_flush(buffer, sink, ingestion_context, errors)
                    results.append(result)
                    total_batches += 1
                    buffer = []
                    last_flush = asyncio.get_running_loop().time()

                    if self.config.max_batches and total_batches >= self.config.max_batches:
                        break

                    if self.config.enable_backpressure:
                        await asyncio.sleep(0)

            if buffer:
                result = await self._safe_flush(buffer, sink, ingestion_context, errors)
                results.append(result)
                total_batches += 1

            summary = self._summary(
                context=ingestion_context,
                started=started,
                results=results,
                errors=errors,
                total_batches=total_batches,
            )

            logger.info(
                "Stream ingestion finished.",
                extra={
                    "source": summary.source,
                    "correlation_id": summary.correlation_id,
                    "total_batches": summary.total_batches,
                    "total_records": summary.total_records,
                    "accepted_records": summary.accepted_records,
                    "rejected_records": summary.rejected_records,
                    "duration_ms": summary.duration_ms,
                },
            )

            return summary

        except Exception as exc:
            logger.exception(
                "Stream ingestion failed.",
                extra={
                    "source": ingestion_context.source,
                    "correlation_id": ingestion_context.correlation_id,
                    "error": str(exc),
                },
            )

            if self.config.stop_on_error:
                raise

            errors.append(str(exc))

            return self._summary(
                context=ingestion_context,
                started=started,
                results=results,
                errors=errors,
                total_batches=total_batches,
            )

    async def _safe_flush(
        self,
        buffer: list[Mapping[str, Any]],
        sink: SinkFn,
        context: IngestionContext,
        errors: list[str],
    ) -> IngestionResult:
        try:
            return await self._flush(buffer, sink, context)
        except Exception as exc:
            logger.exception(
                "Stream batch flush failed.",
                extra={
                    "source": context.source,
                    "correlation_id": context.correlation_id,
                    "batch_size": len(buffer),
                },
            )

            errors.append(str(exc))

            if self.config.stop_on_error:
                raise

            return IngestionResult(
                status="failed",
                source=context.source,
                total_records=len(buffer),
                accepted_records=0,
                rejected_records=len(buffer),
                correlation_id=context.correlation_id,
                errors=[str(exc)],
                metadata=context.metadata,
            )

    async def _flush(
        self,
        buffer: list[Mapping[str, Any]],
        sink: SinkFn,
        context: IngestionContext,
    ) -> IngestionResult:
        return await self.pipeline.run(
            records=list(buffer),
            context=context,
            sink=sink,
        )

    async def _next_with_timeout(
        self,
        stream: AsyncIterator[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        if self.config.idle_timeout_seconds is None:
            return await anext(stream)

        return await asyncio.wait_for(
            anext(stream),
            timeout=self.config.idle_timeout_seconds,
        )

    @staticmethod
    def _build_context(request: StreamIngestionRequest) -> IngestionContext:
        return IngestionContext(
            source=request.source,
            tenant_id=request.tenant_id,
            requested_by=request.requested_by,
            correlation_id=request.correlation_id or str(uuid.uuid4()),
            metadata={
                **request.metadata,
                "entrypoint": "stream",
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    @staticmethod
    def _summary(
        *,
        context: IngestionContext,
        started: float,
        results: list[IngestionResult],
        errors: list[str],
        total_batches: int,
    ) -> StreamIngestionSummary:
        return StreamIngestionSummary(
            source=context.source,
            correlation_id=context.correlation_id,
            total_batches=total_batches,
            total_records=sum(r.total_records for r in results),
            accepted_records=sum(r.accepted_records for r in results),
            rejected_records=sum(r.rejected_records for r in results),
            duration_ms=(time.perf_counter() - started) * 1000,
            results=results,
            errors=errors,
        )
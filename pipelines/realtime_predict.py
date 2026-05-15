"""
kwanza-ai-core/pipelines/realtime_predict.py

Enterprise-grade realtime prediction pipeline.

Purpose
-------
Consume events in near real time, execute predictions through PredictionService,
and publish enriched prediction events to a sink with operational resilience.

Capabilities
------------
- Async event loop with backpressure.
- Pluggable source and sink adapters.
- Local JSONL source/sink for development and tests.
- PredictionService integration.
- Per-event validation and feature extraction.
- Retries with exponential backoff.
- Dead-letter queue for poison messages.
- Circuit breaker protection.
- Graceful shutdown.
- Metrics, audit and structured logs.

Production adapters can wrap Kafka, Redpanda, RabbitMQ, Redis Streams, NATS,
Supabase Realtime, Postgres LISTEN/NOTIFY, webhooks or cloud pub/sub services.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Deque, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple

try:
    from kwanza_ai_core.services.prediction_service import (
        PredictionRequest,
        PredictionResult,
        PredictionService,
        PredictionServiceConfig,
        PredictionTask,
        PredictionOutputType,
        AggregationStrategy,
        build_prediction_service,
    )
except Exception:  # pragma: no cover - local script fallback
    try:
        from services.prediction_service import (  # type: ignore
            PredictionRequest,
            PredictionResult,
            PredictionService,
            PredictionServiceConfig,
            PredictionTask,
            PredictionOutputType,
            AggregationStrategy,
            build_prediction_service,
        )
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Unable to import PredictionService. Ensure this file runs inside the kwanza-ai-core project."
        ) from exc

logger = logging.getLogger("kwanza_ai_core.pipelines.realtime_predict")

JsonDict = Dict[str, Any]


# =============================================================================
# Exceptions
# =============================================================================


class RealtimePredictPipelineError(RuntimeError):
    """Base exception for realtime prediction pipeline failures."""


class RealtimePredictValidationError(RealtimePredictPipelineError):
    """Raised when event/config validation fails."""


class RealtimeCircuitOpenError(RealtimePredictPipelineError):
    """Raised when circuit breaker is open."""


# =============================================================================
# Enums and data models
# =============================================================================


class PipelineStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class SourceType(str, Enum):
    JSONL = "jsonl"
    STDIN = "stdin"
    MEMORY = "memory"


class SinkType(str, Enum):
    JSONL = "jsonl"
    STDOUT = "stdout"
    MEMORY = "memory"


@dataclass(frozen=True)
class RealtimePredictConfig:
    tenant_id: Optional[str] = None
    task: PredictionTask = PredictionTask.GENERIC
    model_name: Optional[str] = None
    model_version: Optional[str] = None
    source_type: SourceType = SourceType.JSONL
    sink_type: SinkType = SinkType.JSONL
    source_path: Optional[Path] = None
    sink_path: Optional[Path] = None
    dead_letter_path: Optional[Path] = None
    poll_interval_ms: int = 250
    max_events: Optional[int] = None
    queue_max_size: int = 10_000
    workers: int = 4
    max_retries: int = 3
    retry_base_delay_ms: int = 100
    retry_jitter_ms: int = 50
    circuit_failure_threshold: int = 10
    circuit_recovery_seconds: int = 30
    id_field: Optional[str] = None
    features_field: Optional[str] = None
    include_input: bool = False
    explain: bool = False
    use_cache: bool = True
    confidence_level: float = 0.95
    aggregation_strategy: AggregationStrategy = AggregationStrategy.SINGLE
    output_type: PredictionOutputType = PredictionOutputType.STRUCTURED
    fail_fast: bool = False
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def validate(self) -> None:
        if self.poll_interval_ms < 0:
            raise RealtimePredictValidationError("poll_interval_ms cannot be negative.")
        if self.queue_max_size <= 0:
            raise RealtimePredictValidationError("queue_max_size must be positive.")
        if self.workers <= 0:
            raise RealtimePredictValidationError("workers must be positive.")
        if self.max_retries < 0:
            raise RealtimePredictValidationError("max_retries cannot be negative.")
        if self.circuit_failure_threshold <= 0:
            raise RealtimePredictValidationError("circuit_failure_threshold must be positive.")
        if not 0 < self.confidence_level < 1:
            raise RealtimePredictValidationError("confidence_level must be between 0 and 1.")
        if self.source_type == SourceType.JSONL and not self.source_path:
            raise RealtimePredictValidationError("source_path is required for JSONL source.")
        if self.sink_type == SinkType.JSONL and not self.sink_path:
            raise RealtimePredictValidationError("sink_path is required for JSONL sink.")


@dataclass(frozen=True)
class RealtimeEvent:
    event_id: str
    payload: Mapping[str, Any]
    received_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PredictionEnvelope:
    run_id: str
    event_id: str
    status: str
    prediction: Any = None
    confidence: Optional[float] = None
    interval: Optional[Mapping[str, Any]] = None
    model: Optional[Mapping[str, Any]] = None
    explanation: Optional[Mapping[str, Any]] = None
    cached: Optional[bool] = None
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    input: Optional[Mapping[str, Any]] = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class PipelineSummary:
    run_id: str
    status: PipelineStatus
    consumed: int
    produced: int
    failed: int
    dead_lettered: int
    started_at: str
    completed_at: str
    processing_ms: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


# =============================================================================
# Protocols
# =============================================================================


class EventSource(Protocol):
    async def events(self) -> AsyncIterator[RealtimeEvent]: ...

    async def close(self) -> None: ...


class EventSink(Protocol):
    async def publish(self, envelope: PredictionEnvelope) -> None: ...

    async def close(self) -> None: ...


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
# Circuit breaker
# =============================================================================


@dataclass
class CircuitState:
    failures: int = 0
    opened_at: Optional[float] = None


class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_seconds: int) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._state = CircuitState()
        self._lock = asyncio.Lock()

    async def before_call(self) -> None:
        async with self._lock:
            if self._state.opened_at is None:
                return
            if time.monotonic() - self._state.opened_at >= self.recovery_seconds:
                self._state = CircuitState()
                return
            raise RealtimeCircuitOpenError("Realtime prediction circuit breaker is open.")

    async def success(self) -> None:
        async with self._lock:
            self._state = CircuitState()

    async def failure(self) -> None:
        async with self._lock:
            self._state.failures += 1
            if self._state.failures >= self.failure_threshold:
                self._state.opened_at = time.monotonic()


# =============================================================================
# Utility functions
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def interval_to_dict(interval: Any) -> Optional[Mapping[str, Any]]:
    if interval is None:
        return None
    if hasattr(interval, "__dataclass_fields__"):
        return asdict(interval)
    if isinstance(interval, Mapping):
        return dict(interval)
    return {"value": str(interval)}


def dataclass_or_mapping_to_dict(value: Any) -> Optional[Mapping[str, Any]]:
    if value is None:
        return None
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    return {"value": str(value)}


# =============================================================================
# Sources and sinks
# =============================================================================


class JsonlEventSource:
    """Reads a JSONL file as an event stream. Useful for local replay and tests."""

    def __init__(self, path: Path, poll_interval_ms: int = 250, follow: bool = False) -> None:
        self.path = path
        self.poll_interval_ms = poll_interval_ms
        self.follow = follow
        self._closed = False

    async def events(self) -> AsyncIterator[RealtimeEvent]:
        if not self.path.exists():
            raise RealtimePredictValidationError(f"Source file not found: {self.path}")
        with self.path.open("r", encoding="utf-8") as fh:
            line_number = 0
            while not self._closed:
                line = fh.readline()
                if not line:
                    if not self.follow:
                        break
                    await asyncio.sleep(self.poll_interval_ms / 1000)
                    continue
                line_number += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RealtimePredictValidationError(f"Invalid JSONL at line {line_number}: {exc}") from exc
                if not isinstance(payload, Mapping):
                    raise RealtimePredictValidationError(f"JSONL line {line_number} must be an object.")
                event_id = str(payload.get("event_id") or payload.get("id") or stable_hash({"line": line_number, "payload": payload})[:24])
                yield RealtimeEvent(event_id=event_id, payload=dict(payload), metadata={"line_number": line_number})

    async def close(self) -> None:
        self._closed = True


class StdinEventSource:
    def __init__(self) -> None:
        self._closed = False

    async def events(self) -> AsyncIterator[RealtimeEvent]:
        line_number = 0
        loop = asyncio.get_running_loop()
        while not self._closed:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            line_number += 1
            payload = json.loads(line)
            if not isinstance(payload, Mapping):
                raise RealtimePredictValidationError(f"stdin line {line_number} must be a JSON object.")
            event_id = str(payload.get("event_id") or payload.get("id") or stable_hash({"line": line_number, "payload": payload})[:24])
            yield RealtimeEvent(event_id=event_id, payload=dict(payload), metadata={"line_number": line_number})

    async def close(self) -> None:
        self._closed = True


class MemoryEventSource:
    def __init__(self, events: Sequence[Mapping[str, Any]]) -> None:
        self._events = deque(events)
        self._closed = False

    async def events(self) -> AsyncIterator[RealtimeEvent]:
        idx = 0
        while self._events and not self._closed:
            payload = self._events.popleft()
            idx += 1
            event_id = str(payload.get("event_id") or payload.get("id") or stable_hash({"idx": idx, "payload": payload})[:24])
            yield RealtimeEvent(event_id=event_id, payload=dict(payload), metadata={"index": idx})

    async def close(self) -> None:
        self._closed = True


class JsonlEventSink:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def publish(self, envelope: PredictionEnvelope) -> None:
        async with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(envelope.to_dict(), ensure_ascii=False, default=str) + "\n")

    async def close(self) -> None:
        return None


class StdoutEventSink:
    async def publish(self, envelope: PredictionEnvelope) -> None:
        print(json.dumps(envelope.to_dict(), ensure_ascii=False, default=str), flush=True)

    async def close(self) -> None:
        return None


class MemoryEventSink:
    def __init__(self) -> None:
        self.records: List[PredictionEnvelope] = []
        self._lock = asyncio.Lock()

    async def publish(self, envelope: PredictionEnvelope) -> None:
        async with self._lock:
            self.records.append(envelope)

    async def close(self) -> None:
        return None


# =============================================================================
# Pipeline
# =============================================================================


class RealtimePredictPipeline:
    def __init__(
        self,
        config: RealtimePredictConfig,
        prediction_service: Optional[PredictionService] = None,
        source: Optional[EventSource] = None,
        sink: Optional[EventSink] = None,
        dead_letter_sink: Optional[EventSink] = None,
        metrics: Optional[MetricsClient] = None,
        audit_sink: Optional[AuditSink] = None,
    ) -> None:
        config.validate()
        self.config = config
        self.prediction_service = prediction_service or build_prediction_service(
            config=PredictionServiceConfig(default_timeout_ms=2500)
        )
        self.source = source or self._build_source(config)
        self.sink = sink or self._build_sink(config)
        self.dead_letter_sink = dead_letter_sink or (JsonlEventSink(config.dead_letter_path) if config.dead_letter_path else None)
        self.metrics = metrics or NoopMetricsClient()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.circuit_breaker = CircuitBreaker(config.circuit_failure_threshold, config.circuit_recovery_seconds)
        self.queue: asyncio.Queue[RealtimeEvent] = asyncio.Queue(maxsize=config.queue_max_size)
        self.stop_event = asyncio.Event()

    async def run(self) -> PipelineSummary:
        started = time.perf_counter()
        started_at = utc_now_iso()
        consumed = 0
        produced = 0
        failed = 0
        dead_lettered = 0
        status = PipelineStatus.RUNNING
        self._install_signal_handlers()
        self.metrics.increment("realtime_predict.started", tags=self._tags())
        await self._audit("realtime_predict.started", {"config": self._safe_config()})

        counters = {"produced": 0, "failed": 0, "dead_lettered": 0}

        async def producer() -> None:
            nonlocal consumed
            async for event in self.source.events():
                if self.stop_event.is_set():
                    break
                await self.queue.put(event)
                consumed += 1
                self.metrics.increment("realtime_predict.event.consumed", tags=self._tags())
                if self.config.max_events and consumed >= self.config.max_events:
                    break
            self.stop_event.set()

        async def worker(worker_id: int) -> None:
            while not self.stop_event.is_set() or not self.queue.empty():
                try:
                    event = await asyncio.wait_for(self.queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                try:
                    envelope = await self._process_event(event, worker_id)
                    await self.sink.publish(envelope)
                    counters["produced"] += 1
                    self.metrics.increment("realtime_predict.event.produced", tags=self._tags())
                except Exception as exc:
                    counters["failed"] += 1
                    self.metrics.increment("realtime_predict.event.failed", tags={**self._tags(), "error": exc.__class__.__name__})
                    logger.exception("Realtime event processing failed", extra={"event_id": event.event_id})
                    if self.config.fail_fast:
                        self.stop_event.set()
                        raise
                    dead = self._error_envelope(event, exc)
                    if self.dead_letter_sink:
                        await self.dead_letter_sink.publish(dead)
                        counters["dead_lettered"] += 1
                finally:
                    self.queue.task_done()

        producer_task = asyncio.create_task(producer())
        worker_tasks = [asyncio.create_task(worker(i)) for i in range(self.config.workers)]

        try:
            await producer_task
            await self.queue.join()
            self.stop_event.set()
            await asyncio.gather(*worker_tasks)
            status = PipelineStatus.STOPPED
        except Exception:
            status = PipelineStatus.FAILED
            self.stop_event.set()
            for task in worker_tasks:
                task.cancel()
            raise
        finally:
            await self.source.close()
            await self.sink.close()
            if self.dead_letter_sink:
                await self.dead_letter_sink.close()

        produced = counters["produced"]
        failed = counters["failed"]
        dead_lettered = counters["dead_lettered"]
        summary = PipelineSummary(
            run_id=self.config.run_id,
            status=status,
            consumed=consumed,
            produced=produced,
            failed=failed,
            dead_lettered=dead_lettered,
            started_at=started_at,
            completed_at=utc_now_iso(),
            processing_ms=round((time.perf_counter() - started) * 1000, 4),
            metadata={"queue_max_size": self.config.queue_max_size, "workers": self.config.workers},
        )
        self.metrics.increment("realtime_predict.completed", tags={**self._tags(), "status": status.value})
        self.metrics.timing("realtime_predict.processing_ms", summary.processing_ms, tags=self._tags())
        await self._audit("realtime_predict.completed", summary.to_dict())
        return summary

    async def _process_event(self, event: RealtimeEvent, worker_id: int) -> PredictionEnvelope:
        started = time.perf_counter()
        features = self._extract_features(event)
        await self.circuit_breaker.before_call()
        last_exc: Optional[Exception] = None
        for attempt in range(self.config.max_retries + 1):
            try:
                request = PredictionRequest(
                    features=features,
                    task=self.config.task,
                    model_name=self.config.model_name,
                    model_version=self.config.model_version,
                    tenant_id=self.config.tenant_id or str(event.payload.get("tenant_id") or "") or None,
                    request_id=f"{self.config.run_id}:{event.event_id}",
                    output_type=self.config.output_type,
                    aggregation_strategy=self.config.aggregation_strategy,
                    explain=self.config.explain,
                    use_cache=self.config.use_cache,
                    confidence_level=self.config.confidence_level,
                    metadata={
                        "pipeline": "realtime_predict",
                        "run_id": self.config.run_id,
                        "event_id": event.event_id,
                        "worker_id": worker_id,
                    },
                )
                result = await self.prediction_service.predict(request)
                await self.circuit_breaker.success()
                envelope = self._result_to_envelope(event, result)
                envelope.metadata.update if False else None  # keeps static analyzers from treating metadata as unused
                return PredictionEnvelope(
                    run_id=envelope.run_id,
                    event_id=envelope.event_id,
                    status=envelope.status,
                    prediction=envelope.prediction,
                    confidence=envelope.confidence,
                    interval=envelope.interval,
                    model=envelope.model,
                    explanation=envelope.explanation,
                    cached=envelope.cached,
                    latency_ms=round((time.perf_counter() - started) * 1000, 4),
                    input=envelope.input,
                    metadata={**dict(envelope.metadata), "attempt": attempt, "worker_id": worker_id},
                )
            except Exception as exc:
                last_exc = exc
                await self.circuit_breaker.failure()
                if attempt < self.config.max_retries:
                    delay_ms = self.config.retry_base_delay_ms * (2**attempt) + self.config.retry_jitter_ms
                    await asyncio.sleep(delay_ms / 1000)
                    continue
                raise last_exc
        assert last_exc is not None
        raise last_exc

    def _extract_features(self, event: RealtimeEvent) -> Mapping[str, Any]:
        payload = event.payload
        if self.config.features_field:
            features = payload.get(self.config.features_field)
            if not isinstance(features, Mapping):
                raise RealtimePredictValidationError(
                    f"Event {event.event_id}: features_field '{self.config.features_field}' must be an object."
                )
            return dict(features)
        excluded = {"event_id", "id", "tenant_id", "timestamp", "metadata"}
        return {key: value for key, value in payload.items() if key not in excluded}

    def _result_to_envelope(self, event: RealtimeEvent, result: PredictionResult) -> PredictionEnvelope:
        return PredictionEnvelope(
            run_id=self.config.run_id,
            event_id=event.event_id,
            status=result.status.value,
            prediction=result.prediction,
            confidence=result.confidence,
            interval=interval_to_dict(result.interval),
            model=dataclass_or_mapping_to_dict(result.model),
            explanation=dataclass_or_mapping_to_dict(result.explanation),
            cached=result.cached,
            latency_ms=result.latency_ms,
            input=event.payload if self.config.include_input else None,
            metadata={"source_metadata": dict(event.metadata), "received_at": event.received_at},
        )

    def _error_envelope(self, event: RealtimeEvent, exc: Exception) -> PredictionEnvelope:
        return PredictionEnvelope(
            run_id=self.config.run_id,
            event_id=event.event_id,
            status="failed",
            error=f"{exc.__class__.__name__}: {exc}",
            input=event.payload if self.config.include_input else None,
            metadata={"source_metadata": dict(event.metadata), "received_at": event.received_at},
        )

    def _build_source(self, config: RealtimePredictConfig) -> EventSource:
        if config.source_type == SourceType.JSONL:
            assert config.source_path is not None
            return JsonlEventSource(config.source_path, config.poll_interval_ms, follow=False)
        if config.source_type == SourceType.STDIN:
            return StdinEventSource()
        if config.source_type == SourceType.MEMORY:
            return MemoryEventSource([])
        raise RealtimePredictValidationError(f"Unsupported source type: {config.source_type}")

    def _build_sink(self, config: RealtimePredictConfig) -> EventSink:
        if config.sink_type == SinkType.JSONL:
            assert config.sink_path is not None
            return JsonlEventSink(config.sink_path)
        if config.sink_type == SinkType.STDOUT:
            return StdoutEventSink()
        if config.sink_type == SinkType.MEMORY:
            return MemoryEventSink()
        raise RealtimePredictValidationError(f"Unsupported sink type: {config.sink_type}")

    def _install_signal_handlers(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self.stop_event.set)
        except (NotImplementedError, RuntimeError):
            return

    def _tags(self) -> Dict[str, str]:
        return {
            "tenant_id": self.config.tenant_id or "event",
            "task": self.config.task.value,
            "run_id": self.config.run_id,
        }

    def _safe_config(self) -> Mapping[str, Any]:
        payload = asdict(self.config)
        payload["task"] = self.config.task.value
        payload["source_type"] = self.config.source_type.value
        payload["sink_type"] = self.config.sink_type.value
        payload["source_path"] = str(self.config.source_path) if self.config.source_path else None
        payload["sink_path"] = str(self.config.sink_path) if self.config.sink_path else None
        payload["dead_letter_path"] = str(self.config.dead_letter_path) if self.config.dead_letter_path else None
        payload["aggregation_strategy"] = self.config.aggregation_strategy.value
        payload["output_type"] = self.config.output_type.value
        return payload

    async def _audit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        try:
            await self.audit_sink.write(event_name, payload)
        except Exception:
            logger.exception("Failed to write realtime prediction audit event", extra={"event_name": event_name})


# =============================================================================
# CLI
# =============================================================================


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enterprise realtime prediction pipeline")
    parser.add_argument("--source-type", default=SourceType.JSONL.value, choices=[x.value for x in SourceType])
    parser.add_argument("--sink-type", default=SinkType.JSONL.value, choices=[x.value for x in SinkType])
    parser.add_argument("--source", dest="source_path", default=None, help="Source JSONL path")
    parser.add_argument("--sink", dest="sink_path", default=None, help="Sink JSONL path")
    parser.add_argument("--dead-letter", dest="dead_letter_path", default=None, help="Dead-letter JSONL path")
    parser.add_argument("--tenant-id", default=None)
    parser.add_argument("--task", default=PredictionTask.GENERIC.value, choices=[x.value for x in PredictionTask])
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--model-version", default=None)
    parser.add_argument("--poll-interval-ms", type=int, default=250)
    parser.add_argument("--max-events", type=int, default=None)
    parser.add_argument("--queue-max-size", type=int, default=10000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--id-field", default=None)
    parser.add_argument("--features-field", default=None)
    parser.add_argument("--include-input", action="store_true")
    parser.add_argument("--explain", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--confidence-level", type=float, default=0.95)
    parser.add_argument("--aggregation-strategy", default=AggregationStrategy.SINGLE.value, choices=[x.value for x in AggregationStrategy])
    parser.add_argument("--output-type", default=PredictionOutputType.STRUCTURED.value, choices=[x.value for x in PredictionOutputType])
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> RealtimePredictConfig:
    return RealtimePredictConfig(
        tenant_id=args.tenant_id,
        task=PredictionTask(args.task),
        model_name=args.model_name,
        model_version=args.model_version,
        source_type=SourceType(args.source_type),
        sink_type=SinkType(args.sink_type),
        source_path=Path(args.source_path) if args.source_path else None,
        sink_path=Path(args.sink_path) if args.sink_path else None,
        dead_letter_path=Path(args.dead_letter_path) if args.dead_letter_path else None,
        poll_interval_ms=args.poll_interval_ms,
        max_events=args.max_events,
        queue_max_size=args.queue_max_size,
        workers=args.workers,
        max_retries=args.max_retries,
        id_field=args.id_field,
        features_field=args.features_field,
        include_input=args.include_input,
        explain=args.explain,
        use_cache=not args.no_cache,
        confidence_level=args.confidence_level,
        aggregation_strategy=AggregationStrategy(args.aggregation_strategy),
        output_type=PredictionOutputType(args.output_type),
        fail_fast=args.fail_fast,
        run_id=args.run_id or str(uuid.uuid4()),
    )


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def async_main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level)
    config = config_from_args(args)
    pipeline = RealtimePredictPipeline(config)
    try:
        summary = await pipeline.run()
        print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False, default=str))
        return 0 if summary.status in {PipelineStatus.STOPPED, PipelineStatus.STOPPING} else 1
    except Exception as exc:
        logger.exception("Realtime prediction pipeline failed")
        print(json.dumps({"status": "failed", "error": f"{exc.__class__.__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 1


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()

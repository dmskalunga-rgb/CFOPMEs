"""
stream_processor.py
===================

Enterprise-grade stream processing module for data pipelines.

Core capabilities
-----------------
- Async stream consumption and processing with bounded concurrency.
- Backpressure using asyncio.Queue and configurable worker pool.
- Micro-batching by size and/or time window.
- Retry policy with exponential backoff and jitter.
- Dead-letter queue abstraction for poison messages.
- Checkpoint management with in-memory and pluggable stores.
- Idempotency support using configurable message keys.
- Graceful shutdown and drain semantics.
- Metrics, audit events and structured processing reports.
- Source/sink protocols for Kafka, Redis Streams, files, APIs, etc.
- Dependency-light design. No vendor lock-in.

This module intentionally defines generic protocols and in-memory defaults so it can
be integrated with Kafka, RabbitMQ, Pub/Sub, Kinesis, Redis Streams, WebSockets or
custom event sources without forcing a specific vendor dependency.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime as dt
import enum
import hashlib
import inspect
import json
import logging
import random
import signal
import time
import traceback
import uuid
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Deque,
    Dict,
    Generic,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    TypeVar,
    Union,
    runtime_checkable,
)

logger = logging.getLogger(__name__)

TIn = TypeVar("TIn")
TOut = TypeVar("TOut")
JsonDict = Dict[str, Any]
MaybeAwaitable = Union[Any, Awaitable[Any]]


class StreamProcessorError(Exception):
    """Base exception for stream processor failures."""


class RetryableProcessingError(StreamProcessorError):
    """Raise this for transient failures that should be retried."""


class NonRetryableProcessingError(StreamProcessorError):
    """Raise this for permanent failures that should go directly to DLQ."""


class StreamState(str, enum.Enum):
    INITIALIZED = "initialized"
    RUNNING = "running"
    DRAINING = "draining"
    STOPPED = "stopped"
    FAILED = "failed"


class AckMode(str, enum.Enum):
    AUTO = "auto"
    MANUAL = "manual"
    NONE = "none"


class ProcessingGuarantee(str, enum.Enum):
    AT_MOST_ONCE = "at_most_once"
    AT_LEAST_ONCE = "at_least_once"
    BEST_EFFORT = "best_effort"


class BatchFailurePolicy(str, enum.Enum):
    FAIL_WHOLE_BATCH = "fail_whole_batch"
    SPLIT_AND_RETRY = "split_and_retry"
    CONTINUE_FAILED_TO_DLQ = "continue_failed_to_dlq"


@dataclass(frozen=True)
class StreamMessage(Generic[TIn]):
    """A generic stream message envelope."""

    payload: TIn
    key: Optional[str] = None
    offset: Optional[Union[str, int]] = None
    partition: Optional[Union[str, int]] = None
    topic: Optional[str] = None
    timestamp: Optional[dt.datetime] = None
    headers: JsonDict = field(default_factory=dict)
    attributes: JsonDict = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def identity(self) -> str:
        if self.key:
            return str(self.key)
        if self.topic is not None and self.partition is not None and self.offset is not None:
            return f"{self.topic}:{self.partition}:{self.offset}"
        return self.message_id

    def to_dict(self) -> JsonDict:
        return {
            "message_id": self.message_id,
            "key": self.key,
            "offset": self.offset,
            "partition": self.partition,
            "topic": self.topic,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "headers": dict(self.headers),
            "attributes": dict(self.attributes),
            "payload": self.payload,
        }


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_delay_seconds: float = 0.25
    max_delay_seconds: float = 10.0
    multiplier: float = 2.0
    jitter_seconds: float = 0.2
    retry_exceptions: Tuple[type, ...] = (RetryableProcessingError, TimeoutError, ConnectionError)

    def delay_for_attempt(self, attempt: int) -> float:
        base = self.initial_delay_seconds * (self.multiplier ** max(attempt - 1, 0))
        delay = min(base, self.max_delay_seconds)
        if self.jitter_seconds > 0:
            delay += random.uniform(0, self.jitter_seconds)
        return delay

    def should_retry(self, exc: BaseException, attempt: int) -> bool:
        if isinstance(exc, NonRetryableProcessingError):
            return False
        return attempt < self.max_attempts and isinstance(exc, self.retry_exceptions)


@dataclass(frozen=True)
class BatchConfig:
    enabled: bool = True
    max_size: int = 100
    max_wait_seconds: float = 1.0
    failure_policy: BatchFailurePolicy = BatchFailurePolicy.SPLIT_AND_RETRY

    def __post_init__(self) -> None:
        if self.max_size <= 0:
            raise ValueError("BatchConfig.max_size must be > 0")
        if self.max_wait_seconds <= 0:
            raise ValueError("BatchConfig.max_wait_seconds must be > 0")


@dataclass(frozen=True)
class StreamProcessorConfig:
    name: str = "stream_processor"
    worker_count: int = 4
    queue_maxsize: int = 10_000
    ack_mode: AckMode = AckMode.AUTO
    guarantee: ProcessingGuarantee = ProcessingGuarantee.AT_LEAST_ONCE
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    batch: BatchConfig = field(default_factory=BatchConfig)
    graceful_shutdown_timeout_seconds: float = 30.0
    stop_on_source_exhausted: bool = True
    enable_idempotency: bool = True
    idempotency_ttl_seconds: int = 86_400
    log_every_n_messages: int = 1_000
    metadata: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.worker_count <= 0:
            raise ValueError("worker_count must be > 0")
        if self.queue_maxsize <= 0:
            raise ValueError("queue_maxsize must be > 0")


@dataclass
class ProcessingResult(Generic[TOut]):
    output: Optional[TOut] = None
    ack: bool = True
    skip_sink: bool = False
    metadata: JsonDict = field(default_factory=dict)


@dataclass
class ProcessingError:
    message_id: str
    error_type: str
    error_message: str
    attempt: int
    traceback: str
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    context: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "message_id": self.message_id,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "attempt": self.attempt,
            "traceback": self.traceback,
            "created_at": self.created_at.isoformat(),
            "context": dict(self.context),
        }


@dataclass
class StreamMetrics:
    received: int = 0
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    retried: int = 0
    sent_to_dlq: int = 0
    skipped_duplicate: int = 0
    emitted: int = 0
    acked: int = 0
    nacked: int = 0
    batches_processed: int = 0
    source_errors: int = 0
    sink_errors: int = 0
    processing_latency_ms_total: float = 0.0
    max_processing_latency_ms: float = 0.0
    counters: Counter = field(default_factory=Counter)
    started_at: float = field(default_factory=time.time)
    stopped_at: Optional[float] = None

    @property
    def uptime_seconds(self) -> float:
        end = self.stopped_at or time.time()
        return round(end - self.started_at, 3)

    @property
    def avg_processing_latency_ms(self) -> float:
        if self.processed == 0:
            return 0.0
        return round(self.processing_latency_ms_total / self.processed, 3)

    @property
    def throughput_per_second(self) -> float:
        uptime = max(self.uptime_seconds, 0.001)
        return round(self.processed / uptime, 3)

    def observe_latency(self, latency_ms: float) -> None:
        self.processing_latency_ms_total += latency_ms
        self.max_processing_latency_ms = max(self.max_processing_latency_ms, latency_ms)

    def stop(self) -> None:
        self.stopped_at = time.time()

    def to_dict(self) -> JsonDict:
        return {
            "received": self.received,
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "retried": self.retried,
            "sent_to_dlq": self.sent_to_dlq,
            "skipped_duplicate": self.skipped_duplicate,
            "emitted": self.emitted,
            "acked": self.acked,
            "nacked": self.nacked,
            "batches_processed": self.batches_processed,
            "source_errors": self.source_errors,
            "sink_errors": self.sink_errors,
            "avg_processing_latency_ms": self.avg_processing_latency_ms,
            "max_processing_latency_ms": round(self.max_processing_latency_ms, 3),
            "throughput_per_second": self.throughput_per_second,
            "uptime_seconds": self.uptime_seconds,
            "counters": dict(self.counters),
        }


@runtime_checkable
class StreamSource(Protocol[TIn]):
    """Protocol for async stream sources."""

    async def __aiter__(self) -> AsyncIterator[StreamMessage[TIn]]:
        ...

    async def ack(self, message: StreamMessage[TIn]) -> None:
        ...

    async def nack(self, message: StreamMessage[TIn], reason: Optional[str] = None) -> None:
        ...

    async def close(self) -> None:
        ...


@runtime_checkable
class StreamSink(Protocol[TOut]):
    """Protocol for async stream sinks."""

    async def emit(self, item: TOut, source_message: Optional[StreamMessage[Any]] = None) -> None:
        ...

    async def emit_batch(self, items: Sequence[TOut], source_messages: Sequence[StreamMessage[Any]]) -> None:
        ...

    async def close(self) -> None:
        ...


@runtime_checkable
class DeadLetterQueue(Protocol[TIn]):
    async def publish(self, message: StreamMessage[TIn], error: ProcessingError) -> None:
        ...

    async def close(self) -> None:
        ...


@runtime_checkable
class CheckpointStore(Protocol):
    async def get(self, key: str) -> Optional[Any]:
        ...

    async def set(self, key: str, value: Any) -> None:
        ...

    async def close(self) -> None:
        ...


class InMemorySource(StreamSource[TIn]):
    """Simple source for tests, local pipelines and examples."""

    def __init__(self, items: Iterable[Union[TIn, StreamMessage[TIn]]]) -> None:
        self._items: Deque[Union[TIn, StreamMessage[TIn]]] = deque(items)
        self.acked: List[str] = []
        self.nacked: List[Tuple[str, Optional[str]]] = []
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[StreamMessage[TIn]]:
        while self._items:
            item = self._items.popleft()
            if isinstance(item, StreamMessage):
                yield item
            else:
                yield StreamMessage(payload=item)

    async def ack(self, message: StreamMessage[TIn]) -> None:
        self.acked.append(message.identity())

    async def nack(self, message: StreamMessage[TIn], reason: Optional[str] = None) -> None:
        self.nacked.append((message.identity(), reason))

    async def close(self) -> None:
        self.closed = True


class InMemorySink(StreamSink[TOut]):
    """Simple sink for tests and local runs."""

    def __init__(self) -> None:
        self.items: List[TOut] = []
        self.closed = False

    async def emit(self, item: TOut, source_message: Optional[StreamMessage[Any]] = None) -> None:
        self.items.append(item)

    async def emit_batch(self, items: Sequence[TOut], source_messages: Sequence[StreamMessage[Any]]) -> None:
        self.items.extend(items)

    async def close(self) -> None:
        self.closed = True


class InMemoryDeadLetterQueue(DeadLetterQueue[TIn]):
    def __init__(self) -> None:
        self.messages: List[Tuple[StreamMessage[TIn], ProcessingError]] = []
        self.closed = False

    async def publish(self, message: StreamMessage[TIn], error: ProcessingError) -> None:
        self.messages.append((message, error))

    async def close(self) -> None:
        self.closed = True


class InMemoryCheckpointStore(CheckpointStore):
    def __init__(self) -> None:
        self.values: JsonDict = {}
        self.closed = False

    async def get(self, key: str) -> Optional[Any]:
        return self.values.get(key)

    async def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    async def close(self) -> None:
        self.closed = True


class TTLIdempotencyStore:
    """In-memory TTL store for duplicate suppression."""

    def __init__(self, ttl_seconds: int = 86_400) -> None:
        self.ttl_seconds = ttl_seconds
        self._seen: Dict[str, float] = {}

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [key for key, ts in self._seen.items() if now - ts > self.ttl_seconds]
        for key in expired:
            self._seen.pop(key, None)

    async def exists(self, key: str) -> bool:
        self._purge_expired()
        return key in self._seen

    async def mark(self, key: str) -> None:
        self._purge_expired()
        self._seen[key] = time.time()

    async def close(self) -> None:
        self._seen.clear()


class AuditLogger:
    """Structured audit logger. Replace or wrap this for OpenTelemetry/SIEM integrations."""

    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger

    async def event(self, event_type: str, **payload: Any) -> None:
        self.log.info("stream_audit_event", extra={"event_type": event_type, "payload": payload})


async def maybe_await(value: MaybeAwaitable) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


ProcessorFn = Callable[[StreamMessage[TIn]], Union[TOut, ProcessingResult[TOut], Awaitable[Union[TOut, ProcessingResult[TOut]]]]]
BatchProcessorFn = Callable[
    [Sequence[StreamMessage[TIn]]],
    Union[Sequence[TOut], Sequence[ProcessingResult[TOut]], Awaitable[Union[Sequence[TOut], Sequence[ProcessingResult[TOut]]]]],
]


class StreamProcessor(Generic[TIn, TOut]):
    """Async enterprise stream processor with retries, batching, DLQ and checkpoints."""

    _SENTINEL = object()

    def __init__(
        self,
        *,
        source: StreamSource[TIn],
        processor: Optional[ProcessorFn[TIn, TOut]] = None,
        batch_processor: Optional[BatchProcessorFn[TIn, TOut]] = None,
        sink: Optional[StreamSink[TOut]] = None,
        dlq: Optional[DeadLetterQueue[TIn]] = None,
        checkpoint_store: Optional[CheckpointStore] = None,
        idempotency_store: Optional[TTLIdempotencyStore] = None,
        config: Optional[StreamProcessorConfig] = None,
        audit_logger: Optional[AuditLogger] = None,
    ) -> None:
        self.config = config or StreamProcessorConfig()
        self.source = source
        self.processor = processor
        self.batch_processor = batch_processor
        self.sink = sink
        self.dlq = dlq or InMemoryDeadLetterQueue()
        self.checkpoints = checkpoint_store or InMemoryCheckpointStore()
        self.idempotency = idempotency_store or TTLIdempotencyStore(self.config.idempotency_ttl_seconds)
        self.audit = audit_logger or AuditLogger(logger)

        if self.processor is None and self.batch_processor is None:
            raise ValueError("Either processor or batch_processor must be provided")
        if self.config.batch.enabled and self.batch_processor is None and self.processor is None:
            raise ValueError("Batch mode requires a processor or batch_processor")

        self.state = StreamState.INITIALIZED
        self.metrics = StreamMetrics()
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=self.config.queue_maxsize)
        self._stop_event = asyncio.Event()
        self._consumer_task: Optional[asyncio.Task[Any]] = None
        self._worker_tasks: List[asyncio.Task[Any]] = []
        self._errors: List[ProcessingError] = []

    async def run(self) -> StreamMetrics:
        """Run until source is exhausted or stop() is called."""
        if self.state == StreamState.RUNNING:
            raise RuntimeError("StreamProcessor is already running")

        self.state = StreamState.RUNNING
        self.metrics.started_at = time.time()
        await self.audit.event("processor_started", name=self.config.name, config=dataclasses.asdict(self.config))

        self._install_signal_handlers()
        self._consumer_task = asyncio.create_task(self._consume_source(), name=f"{self.config.name}:consumer")
        self._worker_tasks = [
            asyncio.create_task(self._worker(worker_id), name=f"{self.config.name}:worker:{worker_id}")
            for worker_id in range(self.config.worker_count)
        ]

        try:
            await self._consumer_task
            await self._queue.join()
            await self._stop_workers()
            self.state = StreamState.STOPPED
        except asyncio.CancelledError:
            self.state = StreamState.DRAINING
            await self.stop(drain=True)
            raise
        except Exception:
            self.state = StreamState.FAILED
            self.metrics.source_errors += 1
            logger.exception("Stream processor failed")
            await self.stop(drain=False)
            raise
        finally:
            self.metrics.stop()
            await self._close_resources()
            await self.audit.event("processor_stopped", name=self.config.name, metrics=self.metrics.to_dict(), state=self.state.value)

        return self.metrics

    async def stop(self, *, drain: bool = True) -> None:
        """Request graceful shutdown."""
        if self.state in {StreamState.STOPPED, StreamState.FAILED}:
            return
        self.state = StreamState.DRAINING if drain else StreamState.STOPPED
        self._stop_event.set()

        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._consumer_task

        if drain:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=self.config.graceful_shutdown_timeout_seconds)
            except asyncio.TimeoutError:
                logger.warning("Graceful drain timed out; cancelling workers")
        await self._stop_workers()
        self.state = StreamState.STOPPED

    async def _consume_source(self) -> None:
        try:
            async for message in self.source:
                if self._stop_event.is_set():
                    break
                self.metrics.received += 1
                await self._queue.put(message)
                if self.config.log_every_n_messages and self.metrics.received % self.config.log_every_n_messages == 0:
                    logger.info("Received %s messages", self.metrics.received)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.metrics.source_errors += 1
            await self.audit.event("source_error", error=str(exc), traceback=traceback.format_exc())
            raise
        finally:
            for _ in range(self.config.worker_count):
                await self._queue.put(self._SENTINEL)

    async def _worker(self, worker_id: int) -> None:
        batch: List[StreamMessage[TIn]] = []
        last_flush = time.monotonic()

        while True:
            timeout = self.config.batch.max_wait_seconds if self.config.batch.enabled else None
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                if batch:
                    await self._process_batch_safe(batch, worker_id)
                    batch = []
                    last_flush = time.monotonic()
                continue

            try:
                if item is self._SENTINEL:
                    if batch:
                        await self._process_batch_safe(batch, worker_id)
                    return

                message: StreamMessage[TIn] = item
                if self.config.batch.enabled:
                    batch.append(message)
                    should_flush = (
                        len(batch) >= self.config.batch.max_size
                        or time.monotonic() - last_flush >= self.config.batch.max_wait_seconds
                    )
                    if should_flush:
                        await self._process_batch_safe(batch, worker_id)
                        batch = []
                        last_flush = time.monotonic()
                else:
                    await self._process_one_safe(message, worker_id)
            finally:
                self._queue.task_done()

    async def _process_batch_safe(self, messages: Sequence[StreamMessage[TIn]], worker_id: int) -> None:
        if not messages:
            return
        started = time.perf_counter()
        self.metrics.batches_processed += 1

        if self.batch_processor is None:
            for message in messages:
                await self._process_one_safe(message, worker_id)
            return

        try:
            outputs = await self._with_retry_batch(messages)
            normalized = self._normalize_batch_results(outputs, len(messages))
            await self._emit_batch_results(normalized, messages)
            for message, result in zip(messages, normalized):
                await self._finalize_success(message, result)
        except Exception as exc:
            if self.config.batch.failure_policy == BatchFailurePolicy.SPLIT_AND_RETRY and len(messages) > 1:
                midpoint = len(messages) // 2
                await self._process_batch_safe(messages[:midpoint], worker_id)
                await self._process_batch_safe(messages[midpoint:], worker_id)
            elif self.config.batch.failure_policy == BatchFailurePolicy.CONTINUE_FAILED_TO_DLQ:
                for message in messages:
                    await self._handle_failure(message, exc, self.config.retry_policy.max_attempts)
            else:
                for message in messages:
                    await self._handle_failure(message, exc, self.config.retry_policy.max_attempts)
        finally:
            latency_ms = (time.perf_counter() - started) * 1000
            self.metrics.observe_latency(latency_ms)

    async def _process_one_safe(self, message: StreamMessage[TIn], worker_id: int) -> None:
        started = time.perf_counter()
        try:
            if await self._is_duplicate(message):
                self.metrics.skipped_duplicate += 1
                await self._ack(message)
                return

            result = await self._with_retry_one(message)
            normalized = self._normalize_result(result)
            if not normalized.skip_sink and normalized.output is not None and self.sink is not None:
                await self.sink.emit(normalized.output, message)
                self.metrics.emitted += 1
            await self._finalize_success(message, normalized)
        except Exception as exc:
            await self._handle_failure(message, exc, self.config.retry_policy.max_attempts)
        finally:
            latency_ms = (time.perf_counter() - started) * 1000
            self.metrics.observe_latency(latency_ms)
            self.metrics.processed += 1

    async def _with_retry_one(self, message: StreamMessage[TIn]) -> Union[TOut, ProcessingResult[TOut]]:
        if self.processor is None:
            raise RuntimeError("processor function is not configured")
        attempt = 1
        while True:
            try:
                return await maybe_await(self.processor(message))
            except Exception as exc:
                if not self.config.retry_policy.should_retry(exc, attempt):
                    raise
                self.metrics.retried += 1
                await self.audit.event("message_retry", message_id=message.identity(), attempt=attempt, error=str(exc))
                await asyncio.sleep(self.config.retry_policy.delay_for_attempt(attempt))
                attempt += 1

    async def _with_retry_batch(
        self, messages: Sequence[StreamMessage[TIn]]
    ) -> Union[Sequence[TOut], Sequence[ProcessingResult[TOut]]]:
        if self.batch_processor is None:
            raise RuntimeError("batch_processor function is not configured")
        attempt = 1
        while True:
            try:
                return await maybe_await(self.batch_processor(messages))
            except Exception as exc:
                if not self.config.retry_policy.should_retry(exc, attempt):
                    raise
                self.metrics.retried += len(messages)
                await self.audit.event("batch_retry", batch_size=len(messages), attempt=attempt, error=str(exc))
                await asyncio.sleep(self.config.retry_policy.delay_for_attempt(attempt))
                attempt += 1

    def _normalize_result(self, value: Union[TOut, ProcessingResult[TOut]]) -> ProcessingResult[TOut]:
        if isinstance(value, ProcessingResult):
            return value
        return ProcessingResult(output=value)

    def _normalize_batch_results(
        self,
        values: Union[Sequence[TOut], Sequence[ProcessingResult[TOut]]],
        expected_len: int,
    ) -> List[ProcessingResult[TOut]]:
        if len(values) != expected_len:
            raise NonRetryableProcessingError(f"Batch output length mismatch: expected {expected_len}, got {len(values)}")
        return [self._normalize_result(v) for v in values]

    async def _emit_batch_results(self, results: Sequence[ProcessingResult[TOut]], messages: Sequence[StreamMessage[TIn]]) -> None:
        if self.sink is None:
            return
        emit_items = [r.output for r in results if not r.skip_sink and r.output is not None]
        if not emit_items:
            return
        try:
            await self.sink.emit_batch(emit_items, messages)
            self.metrics.emitted += len(emit_items)
        except AttributeError:
            for result, message in zip(results, messages):
                if not result.skip_sink and result.output is not None:
                    await self.sink.emit(result.output, message)
                    self.metrics.emitted += 1
        except Exception:
            self.metrics.sink_errors += 1
            raise

    async def _finalize_success(self, message: StreamMessage[TIn], result: ProcessingResult[TOut]) -> None:
        self.metrics.succeeded += 1
        await self._mark_duplicate(message)
        await self._checkpoint(message)
        if result.ack:
            await self._ack(message)

    async def _handle_failure(self, message: StreamMessage[TIn], exc: BaseException, attempt: int) -> None:
        self.metrics.failed += 1
        error = ProcessingError(
            message_id=message.identity(),
            error_type=type(exc).__name__,
            error_message=str(exc),
            attempt=attempt,
            traceback=traceback.format_exc(),
            context={
                "topic": message.topic,
                "partition": message.partition,
                "offset": message.offset,
                "key": message.key,
                "processor": self.config.name,
            },
        )
        self._errors.append(error)
        await self.audit.event("message_failed", error=error.to_dict())

        try:
            await self.dlq.publish(message, error)
            self.metrics.sent_to_dlq += 1
        except Exception as dlq_exc:
            self.metrics.counters["dlq_publish_errors"] += 1
            logger.exception("Failed to publish message to DLQ: %s", dlq_exc)

        await self._nack(message, str(exc))

    async def _ack(self, message: StreamMessage[TIn]) -> None:
        if self.config.ack_mode == AckMode.NONE:
            return
        try:
            await self.source.ack(message)
            self.metrics.acked += 1
        except Exception as exc:
            self.metrics.counters["ack_errors"] += 1
            logger.warning("ACK failed for message %s: %s", message.identity(), exc)

    async def _nack(self, message: StreamMessage[TIn], reason: Optional[str]) -> None:
        if self.config.ack_mode == AckMode.NONE:
            return
        try:
            await self.source.nack(message, reason)
            self.metrics.nacked += 1
        except Exception as exc:
            self.metrics.counters["nack_errors"] += 1
            logger.warning("NACK failed for message %s: %s", message.identity(), exc)

    async def _checkpoint(self, message: StreamMessage[TIn]) -> None:
        if message.offset is None:
            return
        key = f"checkpoint:{self.config.name}:{message.topic or 'default'}:{message.partition or 0}"
        await self.checkpoints.set(
            key,
            {
                "offset": message.offset,
                "message_id": message.message_id,
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
        )

    async def _is_duplicate(self, message: StreamMessage[TIn]) -> bool:
        if not self.config.enable_idempotency:
            return False
        return await self.idempotency.exists(self._idempotency_key(message))

    async def _mark_duplicate(self, message: StreamMessage[TIn]) -> None:
        if not self.config.enable_idempotency:
            return
        await self.idempotency.mark(self._idempotency_key(message))

    def _idempotency_key(self, message: StreamMessage[TIn]) -> str:
        raw = message.identity()
        if raw:
            return raw
        payload = json.dumps(message.payload, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def _stop_workers(self) -> None:
        for task in self._worker_tasks:
            if not task.done():
                task.cancel()
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks = []

    async def _close_resources(self) -> None:
        closeables = [self.source, self.sink, self.dlq, self.checkpoints, self.idempotency]
        for resource in closeables:
            if resource is None:
                continue
            close = getattr(resource, "close", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    await maybe_await(close())

    def _install_signal_handlers(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError, RuntimeError):
                    loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self.stop(drain=True)))
        except Exception:
            # Signal handlers are optional and may not work on all platforms/event loops.
            pass

    def error_report(self, limit: int = 100) -> List[JsonDict]:
        return [error.to_dict() for error in self._errors[-limit:]]

    def status(self) -> JsonDict:
        return {
            "name": self.config.name,
            "state": self.state.value,
            "queue_size": self._queue.qsize(),
            "metrics": self.metrics.to_dict(),
            "recent_errors": self.error_report(limit=10),
        }


# -----------------------------------------------------------------------------
# Utility processor builders
# -----------------------------------------------------------------------------


def json_decoder_processor(
    *,
    encoding: str = "utf-8",
    strict: bool = True,
) -> ProcessorFn[Union[str, bytes, bytearray], JsonDict]:
    async def _processor(message: StreamMessage[Union[str, bytes, bytearray]]) -> JsonDict:
        payload = message.payload
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode(encoding)
        try:
            return json.loads(str(payload))
        except json.JSONDecodeError as exc:
            if strict:
                raise NonRetryableProcessingError(f"Invalid JSON payload: {exc}") from exc
            return {"raw": payload, "_json_error": str(exc)}

    return _processor


def mapping_processor(mapper: Callable[[Any], Any]) -> ProcessorFn[Any, Any]:
    async def _processor(message: StreamMessage[Any]) -> Any:
        return await maybe_await(mapper(message.payload))

    return _processor


def filter_processor(predicate: Callable[[Any], bool]) -> ProcessorFn[Any, Any]:
    async def _processor(message: StreamMessage[Any]) -> ProcessingResult[Any]:
        keep = await maybe_await(predicate(message.payload))
        return ProcessingResult(output=message.payload, skip_sink=not bool(keep))

    return _processor


def chain_processors(*processors: ProcessorFn[Any, Any]) -> ProcessorFn[Any, Any]:
    async def _processor(message: StreamMessage[Any]) -> Any:
        current_message = message
        current_payload: Any = message.payload
        for processor in processors:
            current_message = dataclasses.replace(current_message, payload=current_payload)
            result = await maybe_await(processor(current_message))
            normalized = result if isinstance(result, ProcessingResult) else ProcessingResult(output=result)
            if normalized.skip_sink:
                return normalized
            current_payload = normalized.output
        return current_payload

    return _processor


# -----------------------------------------------------------------------------
# Example usage
# -----------------------------------------------------------------------------


async def _example() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    source = InMemorySource(
        [
            StreamMessage(payload='{"id": 1, "name": "Ana"}', key="1", topic="customers", partition=0, offset=1),
            StreamMessage(payload='{"id": 2, "name": "Bruno"}', key="2", topic="customers", partition=0, offset=2),
            StreamMessage(payload="invalid-json", key="3", topic="customers", partition=0, offset=3),
        ]
    )
    sink: InMemorySink[JsonDict] = InMemorySink()
    dlq: InMemoryDeadLetterQueue[Union[str, bytes, bytearray]] = InMemoryDeadLetterQueue()

    processor = StreamProcessor(
        source=source,
        processor=json_decoder_processor(strict=True),
        sink=sink,
        dlq=dlq,
        config=StreamProcessorConfig(
            name="customer_json_stream",
            worker_count=2,
            batch=BatchConfig(enabled=False),
            retry_policy=RetryPolicy(max_attempts=2),
            log_every_n_messages=1,
        ),
    )

    metrics = await processor.run()
    print("METRICS")
    print(json.dumps(metrics.to_dict(), indent=2, ensure_ascii=False, default=str))
    print("SINK ITEMS")
    print(json.dumps(sink.items, indent=2, ensure_ascii=False, default=str))
    print("DLQ")
    print(json.dumps([error.to_dict() for _, error in dlq.messages], indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(_example())

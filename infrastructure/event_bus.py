# kwanza-ai-core/infrastructure/event_bus.py
from __future__ import annotations

import abc
import asyncio
import contextlib
import inspect
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Protocol, Sequence


class EventPriority(int, Enum):
    LOW = 10
    NORMAL = 50
    HIGH = 80
    CRITICAL = 100


class EventStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"
    IGNORED = "ignored"


@dataclass(frozen=True)
class EventMetadata:
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    causation_id: Optional[str] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    source: str = "kwanza-ai-core"
    trace_id: Optional[str] = None
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Event:
    name: str
    payload: Dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    version: str = "1.0"
    priority: EventPriority = EventPriority.NORMAL
    metadata: EventMetadata = field(default_factory=EventMetadata)

    def topic(self) -> str:
        return self.name.strip().lower()


@dataclass
class EventEnvelope:
    event: Event
    status: EventStatus = EventStatus.PENDING
    attempts: int = 0
    max_attempts: int = 3
    locked_at: Optional[float] = None
    last_error: Optional[str] = None


@dataclass(frozen=True)
class EventBusConfig:
    max_queue_size: int = 100_000
    worker_count: int = 4
    retry_attempts: int = 3
    retry_base_delay_seconds: float = 0.25
    retry_max_delay_seconds: float = 10.0
    handler_timeout_seconds: float = 30.0
    enable_dead_letter: bool = True
    enable_idempotency: bool = True
    idempotency_ttl_seconds: int = 86_400
    shutdown_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class EventPublishResult:
    event_id: str
    topic: str
    accepted: bool
    message: str = ""


@dataclass(frozen=True)
class EventProcessingResult:
    event_id: str
    topic: str
    status: EventStatus
    attempts: int
    elapsed_ms: float
    error: Optional[str] = None


class MetricsSink(Protocol):
    def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None: ...

    def timing(
        self,
        name: str,
        value_ms: float,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None: ...


class NoopMetricsSink:
    def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        return None

    def timing(
        self,
        name: str,
        value_ms: float,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        return None


EventHandler = Callable[[Event], Awaitable[None] | None]
EventMiddleware = Callable[[Event, EventHandler], Awaitable[None]]


class EventBusError(RuntimeError):
    pass


class EventPublishError(EventBusError):
    pass


class EventHandlerError(EventBusError):
    pass


class EventStore(abc.ABC):
    @abc.abstractmethod
    async def enqueue(self, envelope: EventEnvelope) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def dequeue(self) -> Optional[EventEnvelope]:
        raise NotImplementedError

    @abc.abstractmethod
    async def mark_processed(self, envelope: EventEnvelope) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def mark_failed(self, envelope: EventEnvelope, error: str) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def dead_letter(self, envelope: EventEnvelope, error: str) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


class InMemoryEventStore(EventStore):
    def __init__(self, max_queue_size: int = 100_000) -> None:
        self._queue: asyncio.PriorityQueue[tuple[int, float, EventEnvelope]] = asyncio.PriorityQueue(
            maxsize=max_queue_size
        )
        self.processed: Dict[str, EventEnvelope] = {}
        self.failed: Dict[str, EventEnvelope] = {}
        self.dead_letters: Dict[str, EventEnvelope] = {}

    async def enqueue(self, envelope: EventEnvelope) -> None:
        priority = -int(envelope.event.priority)
        await self._queue.put((priority, time.time(), envelope))

    async def dequeue(self) -> Optional[EventEnvelope]:
        _priority, _created_at, envelope = await self._queue.get()
        envelope.status = EventStatus.PROCESSING
        envelope.locked_at = time.time()
        return envelope

    async def mark_processed(self, envelope: EventEnvelope) -> None:
        envelope.status = EventStatus.PROCESSED
        self.processed[envelope.event.event_id] = envelope
        self._queue.task_done()

    async def mark_failed(self, envelope: EventEnvelope, error: str) -> None:
        envelope.status = EventStatus.FAILED
        envelope.last_error = error
        self.failed[envelope.event.event_id] = envelope
        self._queue.task_done()

    async def dead_letter(self, envelope: EventEnvelope, error: str) -> None:
        envelope.status = EventStatus.DEAD_LETTERED
        envelope.last_error = error
        self.dead_letters[envelope.event.event_id] = envelope
        self._queue.task_done()

    async def close(self) -> None:
        return None


class IdempotencyStore:
    def __init__(self, ttl_seconds: int = 86_400) -> None:
        self.ttl_seconds = ttl_seconds
        self._seen: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def exists(self, key: str) -> bool:
        async with self._lock:
            self._cleanup()
            return key in self._seen

    async def mark(self, key: str) -> None:
        async with self._lock:
            self._cleanup()
            self._seen[key] = time.time() + self.ttl_seconds

    def _cleanup(self) -> None:
        now = time.time()
        expired = [key for key, expires_at in self._seen.items() if expires_at <= now]
        for key in expired:
            self._seen.pop(key, None)


class EventRouter:
    def __init__(self) -> None:
        self._handlers: Dict[str, List[EventHandler]] = {}
        self._wildcard_handlers: List[EventHandler] = []

    def subscribe(self, topic: str, handler: EventHandler) -> None:
        normalized = topic.strip().lower()

        if normalized == "*":
            self._wildcard_handlers.append(handler)
            return

        self._handlers.setdefault(normalized, []).append(handler)

    def unsubscribe(self, topic: str, handler: EventHandler) -> None:
        normalized = topic.strip().lower()

        if normalized == "*":
            with contextlib.suppress(ValueError):
                self._wildcard_handlers.remove(handler)
            return

        handlers = self._handlers.get(normalized, [])
        with contextlib.suppress(ValueError):
            handlers.remove(handler)

    def resolve(self, topic: str) -> List[EventHandler]:
        normalized = topic.strip().lower()
        return [
            *self._handlers.get(normalized, []),
            *self._wildcard_handlers,
        ]


class EventBus:
    def __init__(
        self,
        config: Optional[EventBusConfig] = None,
        store: Optional[EventStore] = None,
        metrics: Optional[MetricsSink] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or EventBusConfig()
        self.store = store or InMemoryEventStore(self.config.max_queue_size)
        self.metrics = metrics or NoopMetricsSink()
        self.logger = logger or logging.getLogger("kwanza.infrastructure.event_bus")

        self.router = EventRouter()
        self.idempotency = IdempotencyStore(self.config.idempotency_ttl_seconds)
        self.middlewares: List[EventMiddleware] = []

        self._running = False
        self._workers: set[asyncio.Task[Any]] = set()
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return

        self._running = True
        self._shutdown_event.clear()

        for index in range(self.config.worker_count):
            task = asyncio.create_task(
                self._worker_loop(index),
                name=f"event-bus-worker-{index}",
            )
            self._workers.add(task)
            task.add_done_callback(self._workers.discard)

        self.metrics.increment("event_bus.started")

    async def stop(self) -> None:
        if not self._running:
            return

        self._running = False
        self._shutdown_event.set()

        for task in list(self._workers):
            task.cancel()

        await asyncio.wait(
            self._workers,
            timeout=self.config.shutdown_timeout_seconds,
        )

        await self.store.close()
        self.metrics.increment("event_bus.stopped")

    async def publish(
        self,
        event: Event,
        *,
        max_attempts: Optional[int] = None,
    ) -> EventPublishResult:
        try:
            envelope = EventEnvelope(
                event=event,
                max_attempts=max_attempts or self.config.retry_attempts,
            )
            await self.store.enqueue(envelope)

            self.metrics.increment(
                "event_bus.published",
                tags=self._tags(event),
            )

            return EventPublishResult(
                event_id=event.event_id,
                topic=event.topic(),
                accepted=True,
                message="Event accepted",
            )

        except Exception as exc:
            self.metrics.increment(
                "event_bus.publish_error",
                tags={"topic": event.topic()},
            )
            raise EventPublishError(str(exc)) from exc

    async def publish_many(self, events: Sequence[Event]) -> List[EventPublishResult]:
        return [await self.publish(event) for event in events]

    def subscribe(self, topic: str, handler: EventHandler) -> None:
        self.router.subscribe(topic, handler)

    def unsubscribe(self, topic: str, handler: EventHandler) -> None:
        self.router.unsubscribe(topic, handler)

    def use(self, middleware: EventMiddleware) -> None:
        self.middlewares.append(middleware)

    async def process_once(self) -> Optional[EventProcessingResult]:
        envelope = await self.store.dequeue()
        if envelope is None:
            return None

        return await self._process_envelope(envelope)

    async def _worker_loop(self, worker_index: int) -> None:
        while self._running:
            try:
                envelope = await self.store.dequeue()
                if envelope is None:
                    await asyncio.sleep(0.05)
                    continue

                await self._process_envelope(envelope)

            except asyncio.CancelledError:
                break
            except Exception:
                self.logger.exception("Event worker failed", extra={"worker": worker_index})
                await asyncio.sleep(0.5)

    async def _process_envelope(self, envelope: EventEnvelope) -> EventProcessingResult:
        event = envelope.event
        started = time.monotonic()
        envelope.attempts += 1

        try:
            idempotency_key = self._idempotency_key(event)

            if self.config.enable_idempotency and await self.idempotency.exists(idempotency_key):
                await self.store.mark_processed(envelope)
                return EventProcessingResult(
                    event_id=event.event_id,
                    topic=event.topic(),
                    status=EventStatus.IGNORED,
                    attempts=envelope.attempts,
                    elapsed_ms=(time.monotonic() - started) * 1000,
                )

            handlers = self.router.resolve(event.topic())

            if not handlers:
                await self.store.mark_processed(envelope)
                self.metrics.increment("event_bus.no_handler", tags=self._tags(event))
                return EventProcessingResult(
                    event_id=event.event_id,
                    topic=event.topic(),
                    status=EventStatus.PROCESSED,
                    attempts=envelope.attempts,
                    elapsed_ms=(time.monotonic() - started) * 1000,
                )

            for handler in handlers:
                await self._run_handler_with_middlewares(event, handler)

            if self.config.enable_idempotency:
                await self.idempotency.mark(idempotency_key)

            await self.store.mark_processed(envelope)

            elapsed_ms = (time.monotonic() - started) * 1000
            self.metrics.increment("event_bus.processed", tags=self._tags(event))
            self.metrics.timing("event_bus.processing_latency_ms", elapsed_ms, tags=self._tags(event))

            return EventProcessingResult(
                event_id=event.event_id,
                topic=event.topic(),
                status=EventStatus.PROCESSED,
                attempts=envelope.attempts,
                elapsed_ms=elapsed_ms,
            )

        except Exception as exc:
            elapsed_ms = (time.monotonic() - started) * 1000
            error = repr(exc)

            if envelope.attempts < envelope.max_attempts:
                await self._retry_later(envelope)
                status = EventStatus.FAILED
            elif self.config.enable_dead_letter:
                await self.store.dead_letter(envelope, error)
                status = EventStatus.DEAD_LETTERED
            else:
                await self.store.mark_failed(envelope, error)
                status = EventStatus.FAILED

            self.metrics.increment("event_bus.processing_error", tags=self._tags(event))
            self.logger.exception("Event processing failed: %s", event.topic())

            return EventProcessingResult(
                event_id=event.event_id,
                topic=event.topic(),
                status=status,
                attempts=envelope.attempts,
                elapsed_ms=elapsed_ms,
                error=error,
            )

    async def _retry_later(self, envelope: EventEnvelope) -> None:
        delay = min(
            self.config.retry_base_delay_seconds * (2 ** (envelope.attempts - 1)),
            self.config.retry_max_delay_seconds,
        )

        await asyncio.sleep(delay)
        envelope.status = EventStatus.PENDING
        await self.store.enqueue(envelope)

    async def _run_handler_with_middlewares(
        self,
        event: Event,
        handler: EventHandler,
    ) -> None:
        async def call_handler(inner_event: Event) -> None:
            result = handler(inner_event)
            if inspect.isawaitable(result):
                await result

        chain = call_handler

        for middleware in reversed(self.middlewares):
            next_handler = chain

            async def wrapped(
                inner_event: Event,
                mw: EventMiddleware = middleware,
                nxt: EventHandler = next_handler,
            ) -> None:
                await mw(inner_event, nxt)

            chain = wrapped

        await asyncio.wait_for(
            chain(event),
            timeout=self.config.handler_timeout_seconds,
        )

    def _idempotency_key(self, event: Event) -> str:
        return f"{event.topic()}:{event.event_id}"

    def _tags(self, event: Event) -> Dict[str, str]:
        return {
            "topic": event.topic(),
            "source": event.metadata.source,
            "priority": event.priority.name.lower(),
        }


class LoggingMiddleware:
    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger("kwanza.infrastructure.event_bus.middleware")

    async def __call__(self, event: Event, next_handler: EventHandler) -> None:
        started = time.monotonic()
        self.logger.info(
            "Event handling started",
            extra={
                "event_id": event.event_id,
                "topic": event.topic(),
                "correlation_id": event.metadata.correlation_id,
            },
        )

        try:
            result = next_handler(event)
            if inspect.isawaitable(result):
                await result

            self.logger.info(
                "Event handling completed",
                extra={
                    "event_id": event.event_id,
                    "topic": event.topic(),
                    "elapsed_ms": (time.monotonic() - started) * 1000,
                },
            )

        except Exception:
            self.logger.exception(
                "Event handling failed",
                extra={
                    "event_id": event.event_id,
                    "topic": event.topic(),
                },
            )
            raise


class TenantGuardMiddleware:
    def __init__(self, require_tenant: bool = False) -> None:
        self.require_tenant = require_tenant

    async def __call__(self, event: Event, next_handler: EventHandler) -> None:
        if self.require_tenant and not event.metadata.tenant_id:
            raise EventHandlerError("tenant_id is required for this event bus")

        result = next_handler(event)
        if inspect.isawaitable(result):
            await result


def event_handler(
    bus: EventBus,
    topic: str,
) -> Callable[[EventHandler], EventHandler]:
    def decorator(func: EventHandler) -> EventHandler:
        bus.subscribe(topic, func)
        return func

    return decorator


def create_event(
    name: str,
    payload: Optional[Mapping[str, Any]] = None,
    *,
    source: str = "kwanza-ai-core",
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    priority: EventPriority = EventPriority.NORMAL,
    version: str = "1.0",
) -> Event:
    return Event(
        name=name,
        payload=dict(payload or {}),
        version=version,
        priority=priority,
        metadata=EventMetadata(
            correlation_id=correlation_id or str(uuid.uuid4()),
            tenant_id=tenant_id,
            user_id=user_id,
            source=source,
        ),
    )


def build_event_bus_from_env() -> EventBus:
    import os

    config = EventBusConfig(
        max_queue_size=int(os.getenv("EVENT_BUS_MAX_QUEUE_SIZE", "100000")),
        worker_count=int(os.getenv("EVENT_BUS_WORKER_COUNT", "4")),
        retry_attempts=int(os.getenv("EVENT_BUS_RETRY_ATTEMPTS", "3")),
        retry_base_delay_seconds=float(os.getenv("EVENT_BUS_RETRY_BASE_DELAY_SECONDS", "0.25")),
        retry_max_delay_seconds=float(os.getenv("EVENT_BUS_RETRY_MAX_DELAY_SECONDS", "10")),
        handler_timeout_seconds=float(os.getenv("EVENT_BUS_HANDLER_TIMEOUT_SECONDS", "30")),
        enable_dead_letter=os.getenv("EVENT_BUS_ENABLE_DEAD_LETTER", "true").lower() == "true",
        enable_idempotency=os.getenv("EVENT_BUS_ENABLE_IDEMPOTENCY", "true").lower() == "true",
        idempotency_ttl_seconds=int(os.getenv("EVENT_BUS_IDEMPOTENCY_TTL_SECONDS", "86400")),
        shutdown_timeout_seconds=float(os.getenv("EVENT_BUS_SHUTDOWN_TIMEOUT_SECONDS", "30")),
    )

    bus = EventBus(config=config)
    bus.use(LoggingMiddleware())
    return bus
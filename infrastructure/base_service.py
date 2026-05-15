# kwanza-ai-core/infrastructure/base_service.py
from __future__ import annotations

import abc
import asyncio
import contextlib
import logging
import os
import signal
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Generic,
    Iterable,
    Mapping,
    Optional,
    Protocol,
    TypeVar,
)

T = TypeVar("T")


class ServiceState(str, Enum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class HealthStatus(str, Enum):
    PASSING = "passing"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ServiceIdentity:
    name: str
    version: str = "1.0.0"
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    instance_id: str = field(default_factory=lambda: os.getenv("INSTANCE_ID", str(uuid.uuid4())))
    region: str = field(default_factory=lambda: os.getenv("APP_REGION", "local"))


@dataclass
class ServiceConfig:
    startup_timeout_seconds: float = 30.0
    shutdown_timeout_seconds: float = 30.0
    health_timeout_seconds: float = 5.0
    enable_signal_handlers: bool = True
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    graceful_shutdown_signals: tuple[int, ...] = (signal.SIGINT, signal.SIGTERM)


@dataclass
class HealthCheckResult:
    name: str
    status: HealthStatus
    message: str = ""
    latency_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ServiceHealth:
    service: str
    state: ServiceState
    status: HealthStatus
    checked_at: str
    uptime_seconds: float
    checks: list[HealthCheckResult]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ServiceEvent:
    event_id: str
    service: str
    event_type: str
    occurred_at: str
    payload: Dict[str, Any] = field(default_factory=dict)


class MetricsSink(Protocol):
    def increment(self, name: str, value: float = 1.0, tags: Optional[Mapping[str, str]] = None) -> None: ...

    def timing(self, name: str, value_ms: float, tags: Optional[Mapping[str, str]] = None) -> None: ...

    def gauge(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None: ...


class EventPublisher(Protocol):
    async def publish(self, event: ServiceEvent) -> None: ...


class NoopMetricsSink:
    def increment(self, name: str, value: float = 1.0, tags: Optional[Mapping[str, str]] = None) -> None:
        return None

    def timing(self, name: str, value_ms: float, tags: Optional[Mapping[str, str]] = None) -> None:
        return None

    def gauge(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        return None


class NoopEventPublisher:
    async def publish(self, event: ServiceEvent) -> None:
        return None


class ServiceError(RuntimeError):
    pass


class ServiceStartupError(ServiceError):
    pass


class ServiceShutdownError(ServiceError):
    pass


class ServiceHealthError(ServiceError):
    pass


class AsyncRetryPolicy:
    def __init__(
        self,
        attempts: int = 3,
        base_delay: float = 0.25,
        max_delay: float = 5.0,
        retry_exceptions: tuple[type[BaseException], ...] = (Exception,),
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be >= 1")

        self.attempts = attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.retry_exceptions = retry_exceptions

    async def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        last_error: Optional[BaseException] = None

        for attempt in range(1, self.attempts + 1):
            try:
                return await operation()
            except self.retry_exceptions as exc:
                last_error = exc
                if attempt >= self.attempts:
                    break

                delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
                await asyncio.sleep(delay)

        raise last_error or ServiceError("Retry operation failed")


class BaseService(abc.ABC):
    """
    Classe base enterprise para serviços do kwanza-ai-core.

    Recursos:
    - lifecycle padronizado: start, stop, restart
    - health checks async
    - readiness/liveness
    - métricas plugáveis
    - auditoria/eventos
    - signal handling
    - graceful shutdown
    - contexto operacional
    - retry policy
    """

    def __init__(
        self,
        identity: ServiceIdentity,
        config: Optional[ServiceConfig] = None,
        metrics: Optional[MetricsSink] = None,
        event_publisher: Optional[EventPublisher] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.identity = identity
        self.config = config or ServiceConfig()
        self.metrics = metrics or NoopMetricsSink()
        self.event_publisher = event_publisher or NoopEventPublisher()
        self.logger = logger or logging.getLogger(identity.name)

        self._state: ServiceState = ServiceState.CREATED
        self._started_at: Optional[float] = None
        self._stopped_at: Optional[float] = None
        self._shutdown_event = asyncio.Event()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._health_checks: Dict[str, Callable[[], Awaitable[HealthCheckResult]]] = {}
        self._lock = asyncio.Lock()

        self._configure_logging()
        self.register_health_check("service_state", self._state_health_check)

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state in {ServiceState.RUNNING, ServiceState.DEGRADED}

    @property
    def uptime_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self._started_at)

    async def start(self) -> None:
        async with self._lock:
            if self.is_running:
                return

            self._transition(ServiceState.STARTING)
            await self._emit("service.starting")

            try:
                await asyncio.wait_for(
                    self.on_start(),
                    timeout=self.config.startup_timeout_seconds,
                )

                self._started_at = time.monotonic()
                self._stopped_at = None
                self._shutdown_event.clear()

                if self.config.enable_signal_handlers:
                    self._install_signal_handlers()

                self._transition(ServiceState.RUNNING)
                self.metrics.increment("service.started", tags=self._metric_tags())
                await self._emit("service.started")

            except Exception as exc:
                self._transition(ServiceState.FAILED)
                self.metrics.increment("service.start_failed", tags=self._metric_tags())
                await self._emit("service.start_failed", {"error": repr(exc)})
                self.logger.exception("Service startup failed")
                raise ServiceStartupError(str(exc)) from exc

    async def stop(self) -> None:
        async with self._lock:
            if self._state in {ServiceState.STOPPING, ServiceState.STOPPED}:
                return

            self._transition(ServiceState.STOPPING)
            self._shutdown_event.set()
            await self._emit("service.stopping")

            try:
                await self._cancel_background_tasks()

                await asyncio.wait_for(
                    self.on_stop(),
                    timeout=self.config.shutdown_timeout_seconds,
                )

                self._stopped_at = time.monotonic()
                self._transition(ServiceState.STOPPED)
                self.metrics.increment("service.stopped", tags=self._metric_tags())
                await self._emit("service.stopped")

            except Exception as exc:
                self._transition(ServiceState.FAILED)
                self.metrics.increment("service.stop_failed", tags=self._metric_tags())
                await self._emit("service.stop_failed", {"error": repr(exc)})
                self.logger.exception("Service shutdown failed")
                raise ServiceShutdownError(str(exc)) from exc

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def run_until_stopped(self) -> None:
        await self.start()
        await self._shutdown_event.wait()
        await self.stop()

    async def health(self) -> ServiceHealth:
        started = time.monotonic()
        results: list[HealthCheckResult] = []

        for name, check in self._health_checks.items():
            check_started = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    check(),
                    timeout=self.config.health_timeout_seconds,
                )
                result.latency_ms = (time.monotonic() - check_started) * 1000
                results.append(result)
            except Exception as exc:
                results.append(
                    HealthCheckResult(
                        name=name,
                        status=HealthStatus.CRITICAL,
                        message=f"Health check failed: {exc}",
                        latency_ms=(time.monotonic() - check_started) * 1000,
                    )
                )

        final_status = self._aggregate_health(results)

        self.metrics.timing(
            "service.health.latency_ms",
            (time.monotonic() - started) * 1000,
            tags=self._metric_tags(),
        )

        self.metrics.gauge(
            "service.health.status",
            1 if final_status == HealthStatus.PASSING else 0,
            tags=self._metric_tags(),
        )

        return ServiceHealth(
            service=self.identity.name,
            state=self._state,
            status=final_status,
            checked_at=self._now_iso(),
            uptime_seconds=self.uptime_seconds,
            checks=results,
            metadata={
                "version": self.identity.version,
                "environment": self.identity.environment,
                "instance_id": self.identity.instance_id,
                "region": self.identity.region,
            },
        )

    async def readiness(self) -> bool:
        health = await self.health()
        return self.is_running and health.status in {HealthStatus.PASSING, HealthStatus.WARNING}

    async def liveness(self) -> bool:
        return self._state not in {ServiceState.FAILED, ServiceState.STOPPED}

    def register_health_check(
        self,
        name: str,
        check: Callable[[], Awaitable[HealthCheckResult]],
    ) -> None:
        if not name:
            raise ValueError("Health check name cannot be empty")
        self._health_checks[name] = check

    def create_background_task(
        self,
        coro: Awaitable[Any],
        *,
        name: Optional[str] = None,
    ) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._log_task_failure)
        return task

    async def wait_for_shutdown(self) -> None:
        await self._shutdown_event.wait()

    def mark_degraded(self, reason: str = "") -> None:
        if self._state == ServiceState.RUNNING:
            self._transition(ServiceState.DEGRADED)
            self.logger.warning("Service degraded: %s", reason)

    def mark_recovered(self) -> None:
        if self._state == ServiceState.DEGRADED:
            self._transition(ServiceState.RUNNING)
            self.logger.info("Service recovered")

    @contextlib.asynccontextmanager
    async def operation_context(
        self,
        operation_name: str,
        metadata: Optional[Mapping[str, Any]] = None,
    ):
        operation_id = str(uuid.uuid4())
        started = time.monotonic()

        self.logger.info(
            "Operation started",
            extra={
                "operation": operation_name,
                "operation_id": operation_id,
                "metadata": dict(metadata or {}),
            },
        )

        try:
            yield operation_id

            elapsed_ms = (time.monotonic() - started) * 1000
            self.metrics.timing(
                f"service.operation.{operation_name}.latency_ms",
                elapsed_ms,
                tags=self._metric_tags(),
            )
            self.metrics.increment(
                f"service.operation.{operation_name}.success",
                tags=self._metric_tags(),
            )

        except Exception:
            elapsed_ms = (time.monotonic() - started) * 1000
            self.metrics.timing(
                f"service.operation.{operation_name}.latency_ms",
                elapsed_ms,
                tags=self._metric_tags(),
            )
            self.metrics.increment(
                f"service.operation.{operation_name}.failure",
                tags=self._metric_tags(),
            )
            self.logger.exception(
                "Operation failed",
                extra={
                    "operation": operation_name,
                    "operation_id": operation_id,
                },
            )
            raise

    async def retry(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        attempts: int = 3,
        base_delay: float = 0.25,
        max_delay: float = 5.0,
    ) -> T:
        policy = AsyncRetryPolicy(
            attempts=attempts,
            base_delay=base_delay,
            max_delay=max_delay,
        )
        return await policy.run(operation)

    @abc.abstractmethod
    async def on_start(self) -> None:
        """
        Implementar inicialização real do serviço.
        Exemplo: abrir conexões, carregar modelos, iniciar consumidores.
        """

    @abc.abstractmethod
    async def on_stop(self) -> None:
        """
        Implementar finalização real do serviço.
        Exemplo: fechar conexões, descarregar modelos, flush de métricas.
        """

    async def _state_health_check(self) -> HealthCheckResult:
        if self._state == ServiceState.RUNNING:
            status = HealthStatus.PASSING
        elif self._state == ServiceState.DEGRADED:
            status = HealthStatus.WARNING
        else:
            status = HealthStatus.CRITICAL

        return HealthCheckResult(
            name="service_state",
            status=status,
            message=f"Service state is {self._state.value}",
            metadata={"state": self._state.value},
        )

    def _aggregate_health(self, checks: Iterable[HealthCheckResult]) -> HealthStatus:
        statuses = [check.status for check in checks]

        if not statuses:
            return HealthStatus.WARNING

        if HealthStatus.CRITICAL in statuses:
            return HealthStatus.CRITICAL

        if HealthStatus.WARNING in statuses:
            return HealthStatus.WARNING

        return HealthStatus.PASSING

    async def _cancel_background_tasks(self) -> None:
        if not self._tasks:
            return

        for task in list(self._tasks):
            task.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def _transition(self, new_state: ServiceState) -> None:
        previous = self._state
        self._state = new_state
        self.logger.info(
            "Service state changed",
            extra={
                "previous_state": previous.value,
                "new_state": new_state.value,
                "service": self.identity.name,
            },
        )

    async def _emit(self, event_type: str, payload: Optional[Mapping[str, Any]] = None) -> None:
        event = ServiceEvent(
            event_id=str(uuid.uuid4()),
            service=self.identity.name,
            event_type=event_type,
            occurred_at=self._now_iso(),
            payload=dict(payload or {}),
        )

        try:
            await self.event_publisher.publish(event)
        except Exception:
            self.logger.exception("Failed to publish service event: %s", event_type)

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()

        for sig in self.config.graceful_shutdown_signals:
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, self._shutdown_event.set)

    def _log_task_failure(self, task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return

        exc = task.exception()
        if exc is not None:
            self.logger.exception(
                "Background task failed",
                exc_info=exc,
                extra={
                    "task_name": task.get_name(),
                    "service": self.identity.name,
                },
            )
            self.mark_degraded(f"Background task failed: {task.get_name()}")

    def _metric_tags(self) -> Dict[str, str]:
        return {
            "service": self.identity.name,
            "version": self.identity.version,
            "environment": self.identity.environment,
            "region": self.identity.region,
            "instance_id": self.identity.instance_id,
        }

    def _configure_logging(self) -> None:
        level = getattr(logging, self.config.log_level.upper(), logging.INFO)
        self.logger.setLevel(level)

        if not logging.getLogger().handlers:
            logging.basicConfig(
                level=level,
                format=(
                    "%(asctime)s | %(levelname)s | %(name)s | "
                    "%(message)s"
                ),
            )

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


class ManagedService(BaseService):
    """
    Serviço base opcional para componentes simples que precisam registrar callbacks
    em vez de herdar diretamente e implementar tudo manualmente.
    """

    def __init__(
        self,
        identity: ServiceIdentity,
        *,
        startup_hooks: Optional[list[Callable[[], Awaitable[None]]]] = None,
        shutdown_hooks: Optional[list[Callable[[], Awaitable[None]]]] = None,
        config: Optional[ServiceConfig] = None,
        metrics: Optional[MetricsSink] = None,
        event_publisher: Optional[EventPublisher] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        super().__init__(
            identity=identity,
            config=config,
            metrics=metrics,
            event_publisher=event_publisher,
            logger=logger,
        )
        self.startup_hooks = startup_hooks or []
        self.shutdown_hooks = shutdown_hooks or []

    async def on_start(self) -> None:
        for hook in self.startup_hooks:
            await hook()

    async def on_stop(self) -> None:
        for hook in reversed(self.shutdown_hooks):
            await hook()


class ServiceRegistry:
    """
    Registry simples para orquestrar múltiplos serviços internos.
    """

    def __init__(self) -> None:
        self._services: Dict[str, BaseService] = {}

    def register(self, service: BaseService) -> None:
        name = service.identity.name
        if name in self._services:
            raise ValueError(f"Service already registered: {name}")
        self._services[name] = service

    def get(self, name: str) -> BaseService:
        try:
            return self._services[name]
        except KeyError as exc:
            raise KeyError(f"Service not found: {name}") from exc

    def all(self) -> list[BaseService]:
        return list(self._services.values())

    async def start_all(self) -> None:
        for service in self._services.values():
            await service.start()

    async def stop_all(self) -> None:
        for service in reversed(list(self._services.values())):
            await service.stop()

    async def health_all(self) -> Dict[str, ServiceHealth]:
        return {
            service.identity.name: await service.health()
            for service in self._services.values()
        }


class BaseRepository(Generic[T], abc.ABC):
    """
    Base opcional para repositórios usados pelos services.
    """

    @abc.abstractmethod
    async def get_by_id(self, entity_id: str) -> Optional[T]:
        raise NotImplementedError

    @abc.abstractmethod
    async def save(self, entity: T) -> T:
        raise NotImplementedError

    @abc.abstractmethod
    async def delete(self, entity_id: str) -> None:
        raise NotImplementedError
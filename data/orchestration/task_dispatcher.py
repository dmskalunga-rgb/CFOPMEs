"""
data/orchestration/task_dispatcher.py

Enterprise Task Dispatcher.

Recursos:
- Dispatcher central para tarefas de orchestration
- Roteamento por task_type, tenant, domínio, prioridade e fila
- Estratégias: round-robin, least-loaded, direct, broadcast
- Policies de dispatch
- Dead-letter
- Requeue
- Delayed dispatch
- Idempotência
- Auditoria
- Métricas
- Multi-tenant
- Exportação JSON
- Sem dependências externas obrigatórias
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Protocol, Set, Tuple


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class DispatchStatus(str, Enum):
    PENDING = "pending"
    ROUTED = "routed"
    DISPATCHED = "dispatched"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class DispatchPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class DispatchStrategy(str, Enum):
    DIRECT = "direct"
    ROUND_ROBIN = "round_robin"
    LEAST_LOADED = "least_loaded"
    BROADCAST = "broadcast"
    HASH_PARTITION = "hash_partition"


class DispatchTargetStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class DispatchOutcome(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"
    TIMEOUT = "timeout"


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# =============================================================================
# Exceptions
# =============================================================================

class TaskDispatcherError(Exception):
    """Erro base do Task Dispatcher."""


class DispatchValidationError(TaskDispatcherError):
    """Erro de validação."""


class DispatchTargetNotFound(TaskDispatcherError):
    """Target de dispatch não encontrado."""


class DispatchRouteNotFound(TaskDispatcherError):
    """Rota de dispatch não encontrada."""


class DispatchExecutionError(TaskDispatcherError):
    """Erro durante dispatch."""


# =============================================================================
# Protocols
# =============================================================================

class DispatchHandler(Protocol):
    def dispatch(
        self,
        task: "DispatchTask",
        target: "DispatchTarget",
        context: "DispatchContext",
    ) -> "DispatchResult":
        ...


class AuditBackend(Protocol):
    def write_event(self, event: Dict[str, Any]) -> None:
        ...


class MetricsBackend(Protocol):
    def increment(
        self,
        metric_name: str,
        value: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        ...

    def gauge(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        ...

    def timing(
        self,
        metric_name: str,
        value_ms: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        ...


# =============================================================================
# Default Backends
# =============================================================================

class LoggingAuditBackend:
    def write_event(self, event: Dict[str, Any]) -> None:
        logger.info("task_dispatcher_audit=%s", json.dumps(event, ensure_ascii=False, default=str))


class LoggingMetricsBackend:
    def increment(
        self,
        metric_name: str,
        value: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("metric=%s value=%s tags=%s", metric_name, value, tags or {})

    def gauge(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("gauge=%s value=%s tags=%s", metric_name, value, tags or {})

    def timing(
        self,
        metric_name: str,
        value_ms: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("timing=%s value_ms=%s tags=%s", metric_name, value_ms, tags or {})


# =============================================================================
# Models
# =============================================================================

@dataclass(frozen=True)
class DispatchContext:
    dispatcher_id: str
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    correlation_id: Optional[str] = None
    run_id: Optional[str] = None
    workflow_id: Optional[str] = None
    dag_id: Optional[str] = None
    scheduler_id: Optional[str] = None
    user_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DispatchTask:
    task_id: str
    task_type: str
    payload: Dict[str, Any]
    priority: DispatchPriority = DispatchPriority.NORMAL
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    queue_name: Optional[str] = None
    route_key: Optional[str] = None
    idempotency_key: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    max_attempts: int = 3
    attempt: int = 0
    status: DispatchStatus = DispatchStatus.PENDING
    target_id: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = None
    dispatched_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    error: Optional[str] = None
    result: Any = None
    tags: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.task_id:
            raise DispatchValidationError("task_id é obrigatório")

        if not self.task_type:
            raise DispatchValidationError("task_type é obrigatório")

        if self.payload is None:
            raise DispatchValidationError("payload não pode ser None")

        if self.max_attempts < 1:
            raise DispatchValidationError("max_attempts precisa ser >= 1")


@dataclass
class DispatchTarget:
    target_id: str
    name: str
    handler_name: str
    status: DispatchTargetStatus = DispatchTargetStatus.ACTIVE
    capacity: int = 1
    active_tasks: Set[str] = field(default_factory=set)
    supported_task_types: Set[str] = field(default_factory=set)
    queue_names: Set[str] = field(default_factory=set)
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    weight: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_dispatch_at: Optional[datetime] = None

    def validate(self) -> None:
        if not self.target_id:
            raise DispatchValidationError("target_id é obrigatório")

        if not self.name:
            raise DispatchValidationError("name é obrigatório")

        if not self.handler_name:
            raise DispatchValidationError("handler_name é obrigatório")

        if self.capacity < 1:
            raise DispatchValidationError("capacity precisa ser >= 1")

    def available_capacity(self) -> int:
        return max(0, self.capacity - len(self.active_tasks))

    def accepts(self, task: DispatchTask) -> bool:
        if self.status != DispatchTargetStatus.ACTIVE:
            return False

        if self.available_capacity() <= 0:
            return False

        if self.supported_task_types and task.task_type not in self.supported_task_types:
            return False

        if self.queue_names and task.queue_name and task.queue_name not in self.queue_names:
            return False

        if self.tenant_id and task.tenant_id and self.tenant_id != task.tenant_id:
            return False

        if self.domain and task.domain and self.domain != task.domain:
            return False

        return True


@dataclass(frozen=True)
class DispatchRoute:
    route_id: str
    name: str
    task_types: Set[str]
    target_ids: List[str]
    strategy: DispatchStrategy = DispatchStrategy.LEAST_LOADED
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    queue_name: Optional[str] = None
    enabled: bool = True
    priority_boost: int = 0
    tags: Dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.route_id:
            raise DispatchValidationError("route_id é obrigatório")

        if not self.name:
            raise DispatchValidationError("name é obrigatório")

        if not self.task_types:
            raise DispatchValidationError("task_types é obrigatório")

        if not self.target_ids:
            raise DispatchValidationError("target_ids é obrigatório")


@dataclass
class DispatchResult:
    task_id: str
    target_id: str
    outcome: DispatchOutcome
    accepted: bool
    message: str = ""
    result: Any = None
    error: Optional[str] = None
    dispatched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DispatchSnapshot:
    dispatcher_id: str
    generated_at: datetime
    tasks_total: int
    tasks_by_status: Dict[str, int]
    targets_total: int
    active_targets: int
    queue_depth: int
    dead_letter_count: int
    routes_total: int


# =============================================================================
# Handlers
# =============================================================================

class FunctionDispatchHandler:
    def __init__(self) -> None:
        self._handlers: Dict[str, Callable[[DispatchTask, DispatchTarget, DispatchContext], DispatchResult]] = {}

    def register(
        self,
        handler_name: str,
        fn: Callable[[DispatchTask, DispatchTarget, DispatchContext], DispatchResult],
    ) -> None:
        if not handler_name:
            raise DispatchValidationError("handler_name é obrigatório")

        self._handlers[handler_name] = fn

    def dispatch(
        self,
        task: DispatchTask,
        target: DispatchTarget,
        context: DispatchContext,
    ) -> DispatchResult:
        fn = self._handlers.get(target.handler_name)

        if not fn:
            raise DispatchExecutionError(f"Handler não registrado: {target.handler_name}")

        return fn(task, target, context)


# =============================================================================
# Stores
# =============================================================================

class DispatchTaskStore:
    def __init__(self) -> None:
        self._tasks: Dict[str, DispatchTask] = {}
        self._idempotency_index: Dict[str, str] = {}
        self._dead_letter: Dict[str, DispatchTask] = {}
        self._lock = threading.RLock()

    def save(self, task: DispatchTask) -> None:
        task.validate()

        with self._lock:
            task.updated_at = datetime.now(timezone.utc)
            self._tasks[task.task_id] = task

            if task.idempotency_key:
                self._idempotency_index[task.idempotency_key] = task.task_id

    def get(self, task_id: str) -> DispatchTask:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise DispatchValidationError(f"Tarefa não encontrada: {task_id}")
            return task

    def find_by_idempotency_key(self, key: str) -> Optional[DispatchTask]:
        with self._lock:
            task_id = self._idempotency_index.get(key)
            return self._tasks.get(task_id) if task_id else None

    def list_all(
        self,
        status: Optional[DispatchStatus] = None,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> List[DispatchTask]:
        with self._lock:
            tasks = list(self._tasks.values())

        if status:
            tasks = [task for task in tasks if task.status == status]

        if tenant_id:
            tasks = [task for task in tasks if task.tenant_id == tenant_id]

        if domain:
            tasks = [task for task in tasks if task.domain == domain]

        return sorted(tasks, key=lambda task: task.created_at)

    def move_to_dead_letter(self, task: DispatchTask) -> None:
        with self._lock:
            task.status = DispatchStatus.DEAD_LETTER
            task.updated_at = datetime.now(timezone.utc)
            self._dead_letter[task.task_id] = task
            self._tasks[task.task_id] = task

    def dead_letter_count(self) -> int:
        with self._lock:
            return len(self._dead_letter)


class DispatchTargetRegistry:
    def __init__(self) -> None:
        self._targets: Dict[str, DispatchTarget] = {}
        self._lock = threading.RLock()

    def register(self, target: DispatchTarget) -> None:
        target.validate()
        with self._lock:
            self._targets[target.target_id] = target

    def get(self, target_id: str) -> DispatchTarget:
        with self._lock:
            target = self._targets.get(target_id)
            if not target:
                raise DispatchTargetNotFound(target_id)
            return target

    def list_all(
        self,
        status: Optional[DispatchTargetStatus] = None,
    ) -> List[DispatchTarget]:
        with self._lock:
            targets = list(self._targets.values())

        if status:
            targets = [target for target in targets if target.status == status]

        return targets

    def update_status(self, target_id: str, status: DispatchTargetStatus) -> None:
        target = self.get(target_id)
        target.status = status


class DispatchRouteRegistry:
    def __init__(self) -> None:
        self._routes: Dict[str, DispatchRoute] = {}
        self._lock = threading.RLock()

    def register(self, route: DispatchRoute) -> None:
        route.validate()
        with self._lock:
            self._routes[route.route_id] = route

    def get(self, route_id: str) -> DispatchRoute:
        with self._lock:
            route = self._routes.get(route_id)
            if not route:
                raise DispatchRouteNotFound(route_id)
            return route

    def list_all(self, enabled_only: bool = True) -> List[DispatchRoute]:
        with self._lock:
            routes = list(self._routes.values())

        if enabled_only:
            routes = [route for route in routes if route.enabled]

        return routes

    def match(self, task: DispatchTask) -> List[DispatchRoute]:
        routes: List[DispatchRoute] = []

        for route in self.list_all(enabled_only=True):
            if task.task_type not in route.task_types:
                continue

            if route.tenant_id and task.tenant_id and route.tenant_id != task.tenant_id:
                continue

            if route.domain and task.domain and route.domain != task.domain:
                continue

            if route.queue_name and task.queue_name and route.queue_name != task.queue_name:
                continue

            routes.append(route)

        return routes


class DispatchPriorityQueue:
    WEIGHTS = {
        DispatchPriority.CRITICAL: 0,
        DispatchPriority.HIGH: 1,
        DispatchPriority.NORMAL: 2,
        DispatchPriority.LOW: 3,
    }

    def __init__(self) -> None:
        self._queue: queue.PriorityQueue[Tuple[int, float, str]] = queue.PriorityQueue()
        self._queued: Set[str] = set()
        self._lock = threading.RLock()

    def enqueue(self, task: DispatchTask) -> None:
        with self._lock:
            if task.task_id in self._queued:
                return

            scheduled = task.scheduled_at or task.created_at
            self._queue.put((self.WEIGHTS[task.priority], scheduled.timestamp(), task.task_id))
            self._queued.add(task.task_id)

    def dequeue(self, timeout: Optional[float] = None) -> Optional[str]:
        try:
            _, _, task_id = self._queue.get(timeout=timeout)

            with self._lock:
                self._queued.discard(task_id)

            return task_id
        except queue.Empty:
            return None

    def depth(self) -> int:
        return self._queue.qsize()


# =============================================================================
# Dispatcher
# =============================================================================

class TaskDispatcher:
    def __init__(
        self,
        dispatcher_id: Optional[str] = None,
        task_store: Optional[DispatchTaskStore] = None,
        target_registry: Optional[DispatchTargetRegistry] = None,
        route_registry: Optional[DispatchRouteRegistry] = None,
        dispatch_queue: Optional[DispatchPriorityQueue] = None,
        handler: Optional[FunctionDispatchHandler] = None,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
    ) -> None:
        self.dispatcher_id = dispatcher_id or str(uuid.uuid4())
        self.task_store = task_store or DispatchTaskStore()
        self.target_registry = target_registry or DispatchTargetRegistry()
        self.route_registry = route_registry or DispatchRouteRegistry()
        self.dispatch_queue = dispatch_queue or DispatchPriorityQueue()
        self.handler = handler or FunctionDispatchHandler()
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()
        self._round_robin_indexes: Dict[str, int] = defaultdict(int)
        self._lock = threading.RLock()

    def register_handler(
        self,
        handler_name: str,
        fn: Callable[[DispatchTask, DispatchTarget, DispatchContext], DispatchResult],
    ) -> None:
        self.handler.register(handler_name, fn)

        self._audit(
            "dispatcher.handler.registered",
            AuditSeverity.INFO,
            details={"handler_name": handler_name},
        )

    def register_target(self, target: DispatchTarget) -> None:
        self.target_registry.register(target)

        self._audit(
            "dispatcher.target.registered",
            AuditSeverity.INFO,
            target_id=target.target_id,
            details={
                "handler_name": target.handler_name,
                "capacity": target.capacity,
                "supported_task_types": sorted(target.supported_task_types),
            },
        )

    def register_route(self, route: DispatchRoute) -> None:
        for target_id in route.target_ids:
            self.target_registry.get(target_id)

        self.route_registry.register(route)

        self._audit(
            "dispatcher.route.registered",
            AuditSeverity.INFO,
            route_id=route.route_id,
            details={
                "strategy": route.strategy.value,
                "task_types": sorted(route.task_types),
                "target_ids": route.target_ids,
            },
        )

    def submit(self, task: DispatchTask) -> DispatchTask:
        task.validate()

        with self._lock:
            if task.idempotency_key:
                existing = self.task_store.find_by_idempotency_key(task.idempotency_key)
                if existing:
                    self._audit(
                        "dispatcher.task.idempotent_duplicate",
                        AuditSeverity.INFO,
                        task_id=existing.task_id,
                        details={"idempotency_key": task.idempotency_key},
                    )
                    return existing

            self.task_store.save(task)
            self.dispatch_queue.enqueue(task)

        self.metrics_backend.increment(
            "dispatcher.task.submitted.total",
            tags={
                "task_type": task.task_type,
                "priority": task.priority.value,
                "tenant_id": task.tenant_id or "-",
            },
        )

        self._audit(
            "dispatcher.task.submitted",
            AuditSeverity.INFO,
            task_id=task.task_id,
            details={
                "task_type": task.task_type,
                "priority": task.priority.value,
                "queue_name": task.queue_name,
            },
        )

        return task

    def submit_many(self, tasks: Iterable[DispatchTask]) -> List[DispatchTask]:
        return [self.submit(task) for task in tasks]

    def dispatch_once(
        self,
        context: Optional[DispatchContext] = None,
        dequeue_timeout: Optional[float] = 0.1,
    ) -> Optional[DispatchResult]:
        task_id = self.dispatch_queue.dequeue(timeout=dequeue_timeout)

        if not task_id:
            return None

        task = self.task_store.get(task_id)

        if self._is_expired(task):
            task.status = DispatchStatus.EXPIRED
            task.error = "Task expired before dispatch"
            self.task_store.save(task)
            return DispatchResult(
                task_id=task.task_id,
                target_id="-",
                outcome=DispatchOutcome.REJECTED,
                accepted=False,
                error=task.error,
            )

        if task.scheduled_at and datetime.now(timezone.utc) < task.scheduled_at:
            self.dispatch_queue.enqueue(task)
            return None

        context = context or DispatchContext(
            dispatcher_id=self.dispatcher_id,
            tenant_id=task.tenant_id,
            domain=task.domain,
            correlation_id=str(uuid.uuid4()),
        )

        try:
            route = self._select_route(task)
            targets = self._select_targets(task, route)

            if not targets:
                raise DispatchExecutionError("Nenhum target disponível para dispatch")

            if route.strategy == DispatchStrategy.BROADCAST:
                results = [
                    self._dispatch_to_target(task, target, context)
                    for target in targets
                ]

                accepted = [result for result in results if result.accepted]
                return accepted[0] if accepted else results[0]

            return self._dispatch_to_target(task, targets[0], context)

        except Exception as exc:
            return self._handle_dispatch_failure(task, str(exc))

    def dispatch_loop(
        self,
        stop_event: threading.Event,
        idle_sleep_seconds: float = 1.0,
    ) -> None:
        self._audit("dispatcher.loop.started", AuditSeverity.INFO)

        while not stop_event.is_set():
            result = self.dispatch_once()

            if result is None:
                time.sleep(idle_sleep_seconds)

        self._audit("dispatcher.loop.stopped", AuditSeverity.WARNING)

    def acknowledge(
        self,
        task_id: str,
        result: Any = None,
    ) -> DispatchTask:
        task = self.task_store.get(task_id)
        task.status = DispatchStatus.ACKNOWLEDGED
        task.acknowledged_at = datetime.now(timezone.utc)
        task.result = result if result is not None else task.result

        if task.target_id:
            target = self.target_registry.get(task.target_id)
            target.active_tasks.discard(task.task_id)

        self.task_store.save(task)

        self._audit(
            "dispatcher.task.acknowledged",
            AuditSeverity.INFO,
            task_id=task_id,
            target_id=task.target_id,
        )

        return task

    def requeue(
        self,
        task_id: str,
        delay_seconds: float = 0.0,
        reason: str = "",
    ) -> DispatchTask:
        task = self.task_store.get(task_id)

        task.status = DispatchStatus.RETRYING
        task.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        task.error = reason
        task.target_id = None

        self.task_store.save(task)
        self.dispatch_queue.enqueue(task)

        self._audit(
            "dispatcher.task.requeued",
            AuditSeverity.WARNING,
            task_id=task_id,
            details={"delay_seconds": delay_seconds, "reason": reason},
        )

        return task

    def cancel(self, task_id: str, reason: str = "") -> DispatchTask:
        task = self.task_store.get(task_id)
        task.status = DispatchStatus.CANCELLED
        task.error = reason
        self.task_store.save(task)

        self._audit(
            "dispatcher.task.cancelled",
            AuditSeverity.WARNING,
            task_id=task_id,
            details={"reason": reason},
        )

        return task

    def _dispatch_to_target(
        self,
        task: DispatchTask,
        target: DispatchTarget,
        context: DispatchContext,
    ) -> DispatchResult:
        started = time.perf_counter()

        task.status = DispatchStatus.ROUTED
        task.target_id = target.target_id
        task.attempt += 1
        task.dispatched_at = datetime.now(timezone.utc)

        target.active_tasks.add(task.task_id)
        target.last_dispatch_at = datetime.now(timezone.utc)

        self.task_store.save(task)

        self._audit(
            "dispatcher.task.routed",
            AuditSeverity.INFO,
            task_id=task.task_id,
            target_id=target.target_id,
            details={"attempt": task.attempt},
        )

        try:
            result = self.handler.dispatch(task, target, context)
            result.duration_ms = (time.perf_counter() - started) * 1000

            if result.accepted:
                task.status = DispatchStatus.DISPATCHED
                task.result = result.result
                task.error = None
            else:
                task.status = DispatchStatus.FAILED
                task.error = result.error or result.message
                target.active_tasks.discard(task.task_id)

            self.task_store.save(task)

            self.metrics_backend.increment(
                "dispatcher.task.dispatched.total",
                tags={
                    "task_type": task.task_type,
                    "target_id": target.target_id,
                    "outcome": result.outcome.value,
                },
            )

            self.metrics_backend.timing(
                "dispatcher.task.dispatch.duration_ms",
                result.duration_ms,
                tags={"task_type": task.task_type, "target_id": target.target_id},
            )

            self._audit(
                "dispatcher.task.dispatched",
                AuditSeverity.INFO if result.accepted else AuditSeverity.ERROR,
                task_id=task.task_id,
                target_id=target.target_id,
                details={
                    "accepted": result.accepted,
                    "outcome": result.outcome.value,
                    "message": result.message,
                    "duration_ms": result.duration_ms,
                },
            )

            return result

        except Exception as exc:
            target.active_tasks.discard(task.task_id)
            return self._handle_dispatch_failure(task, str(exc))

    def _handle_dispatch_failure(
        self,
        task: DispatchTask,
        error: str,
    ) -> DispatchResult:
        task.error = error

        if task.attempt < task.max_attempts:
            task.status = DispatchStatus.RETRYING
            delay = min(60, 2 ** max(0, task.attempt))
            task.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            self.task_store.save(task)
            self.dispatch_queue.enqueue(task)
        else:
            self.task_store.move_to_dead_letter(task)

        self.metrics_backend.increment(
            "dispatcher.task.failed.total",
            tags={"task_type": task.task_type, "status": task.status.value},
        )

        self._audit(
            "dispatcher.task.failed",
            AuditSeverity.ERROR,
            task_id=task.task_id,
            details={
                "error": error,
                "attempt": task.attempt,
                "status": task.status.value,
            },
        )

        return DispatchResult(
            task_id=task.task_id,
            target_id=task.target_id or "-",
            outcome=DispatchOutcome.FAILED,
            accepted=False,
            error=error,
        )

    def _select_route(self, task: DispatchTask) -> DispatchRoute:
        routes = self.route_registry.match(task)

        if not routes:
            raise DispatchRouteNotFound(f"Nenhuma rota encontrada para task_type={task.task_type}")

        return sorted(routes, key=lambda route: route.priority_boost, reverse=True)[0]

    def _select_targets(
        self,
        task: DispatchTask,
        route: DispatchRoute,
    ) -> List[DispatchTarget]:
        candidates = [
            self.target_registry.get(target_id)
            for target_id in route.target_ids
        ]

        candidates = [target for target in candidates if target.accepts(task)]

        if not candidates:
            return []

        if route.strategy == DispatchStrategy.DIRECT:
            return [candidates[0]]

        if route.strategy == DispatchStrategy.BROADCAST:
            return candidates

        if route.strategy == DispatchStrategy.LEAST_LOADED:
            return sorted(
                candidates,
                key=lambda target: (-target.available_capacity(), len(target.active_tasks), target.target_id),
            )[:1]

        if route.strategy == DispatchStrategy.ROUND_ROBIN:
            index = self._round_robin_indexes[route.route_id] % len(candidates)
            self._round_robin_indexes[route.route_id] += 1
            return [candidates[index]]

        if route.strategy == DispatchStrategy.HASH_PARTITION:
            raw = task.route_key or task.idempotency_key or task.task_id
            digest = int(hashlib.sha256(raw.encode("utf-8")).hexdigest(), 16)
            return [candidates[digest % len(candidates)]]

        return [candidates[0]]

    @staticmethod
    def _is_expired(task: DispatchTask) -> bool:
        return bool(task.expires_at and datetime.now(timezone.utc) > task.expires_at)

    def snapshot(self) -> DispatchSnapshot:
        tasks = self.task_store.list_all()
        targets = self.target_registry.list_all()
        routes = self.route_registry.list_all(enabled_only=False)

        tasks_by_status = {
            status.value: sum(1 for task in tasks if task.status == status)
            for status in DispatchStatus
        }

        return DispatchSnapshot(
            dispatcher_id=self.dispatcher_id,
            generated_at=datetime.now(timezone.utc),
            tasks_total=len(tasks),
            tasks_by_status=tasks_by_status,
            targets_total=len(targets),
            active_targets=sum(1 for target in targets if target.status == DispatchTargetStatus.ACTIVE),
            queue_depth=self.dispatch_queue.depth(),
            dead_letter_count=self.task_store.dead_letter_count(),
            routes_total=len(routes),
        )

    def list_tasks(
        self,
        status: Optional[DispatchStatus] = None,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> List[DispatchTask]:
        return self.task_store.list_all(status=status, tenant_id=tenant_id, domain=domain)

    def list_targets(self) -> List[DispatchTarget]:
        return self.target_registry.list_all()

    def export_snapshot_json(self) -> str:
        snapshot = self.snapshot()
        data = asdict(snapshot)
        data["generated_at"] = snapshot.generated_at.isoformat()
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)

    def export_tasks_json(self) -> str:
        return json.dumps(
            [self._task_to_dict(task) for task in self.task_store.list_all()],
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def export_targets_json(self) -> str:
        return json.dumps(
            [self._target_to_dict(target) for target in self.target_registry.list_all()],
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def _audit(
        self,
        event_type: str,
        severity: AuditSeverity,
        task_id: Optional[str] = None,
        target_id: Optional[str] = None,
        route_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "severity": severity.value,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "dispatcher_id": self.dispatcher_id,
                "task_id": task_id,
                "target_id": target_id,
                "route_id": route_id,
                "details": details or {},
            }
        )

    @staticmethod
    def _task_to_dict(task: DispatchTask) -> Dict[str, Any]:
        data = asdict(task)
        data["priority"] = task.priority.value
        data["status"] = task.status.value
        data["created_at"] = task.created_at.isoformat()
        data["updated_at"] = task.updated_at.isoformat() if task.updated_at else None
        data["scheduled_at"] = task.scheduled_at.isoformat() if task.scheduled_at else None
        data["expires_at"] = task.expires_at.isoformat() if task.expires_at else None
        data["dispatched_at"] = task.dispatched_at.isoformat() if task.dispatched_at else None
        data["acknowledged_at"] = task.acknowledged_at.isoformat() if task.acknowledged_at else None
        return data

    @staticmethod
    def _target_to_dict(target: DispatchTarget) -> Dict[str, Any]:
        data = asdict(target)
        data["status"] = target.status.value
        data["active_tasks"] = sorted(target.active_tasks)
        data["supported_task_types"] = sorted(target.supported_task_types)
        data["queue_names"] = sorted(target.queue_names)
        data["registered_at"] = target.registered_at.isoformat()
        data["last_dispatch_at"] = target.last_dispatch_at.isoformat() if target.last_dispatch_at else None
        data["available_capacity"] = target.available_capacity()
        return data


# =============================================================================
# Factory
# =============================================================================

def create_default_task_dispatcher() -> TaskDispatcher:
    dispatcher = TaskDispatcher(dispatcher_id="default-task-dispatcher")

    def local_handler(
        task: DispatchTask,
        target: DispatchTarget,
        context: DispatchContext,
    ) -> DispatchResult:
        return DispatchResult(
            task_id=task.task_id,
            target_id=target.target_id,
            outcome=DispatchOutcome.ACCEPTED,
            accepted=True,
            message="Task accepted by local handler",
            result={
                "task_type": task.task_type,
                "payload": task.payload,
                "target": target.target_id,
                "dispatcher": context.dispatcher_id,
            },
        )

    dispatcher.register_handler("local", local_handler)

    dispatcher.register_target(
        DispatchTarget(
            target_id="local-worker-001",
            name="Local Worker 001",
            handler_name="local",
            capacity=4,
            supported_task_types={"extract", "transform", "load", "notify"},
            queue_names={"default", "etl"},
            tenant_id="tenant-default",
        )
    )

    dispatcher.register_target(
        DispatchTarget(
            target_id="local-worker-002",
            name="Local Worker 002",
            handler_name="local",
            capacity=2,
            supported_task_types={"extract", "transform", "load"},
            queue_names={"default", "etl"},
            tenant_id="tenant-default",
        )
    )

    dispatcher.register_route(
        DispatchRoute(
            route_id="etl-route",
            name="ETL Route",
            task_types={"extract", "transform", "load"},
            target_ids=["local-worker-001", "local-worker-002"],
            strategy=DispatchStrategy.LEAST_LOADED,
            tenant_id="tenant-default",
            queue_name="etl",
        )
    )

    dispatcher.register_route(
        DispatchRoute(
            route_id="notify-route",
            name="Notify Route",
            task_types={"notify"},
            target_ids=["local-worker-001"],
            strategy=DispatchStrategy.DIRECT,
            tenant_id="tenant-default",
            queue_name="default",
        )
    )

    return dispatcher


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    dispatcher = create_default_task_dispatcher()

    dispatcher.submit_many(
        [
            DispatchTask(
                task_id=str(uuid.uuid4()),
                task_type="extract",
                tenant_id="tenant-default",
                queue_name="etl",
                priority=DispatchPriority.HIGH,
                payload={"source": "sales_orders"},
                idempotency_key="extract-sales-orders-001",
            ),
            DispatchTask(
                task_id=str(uuid.uuid4()),
                task_type="transform",
                tenant_id="tenant-default",
                queue_name="etl",
                priority=DispatchPriority.NORMAL,
                payload={"dataset": "sales_orders"},
            ),
            DispatchTask(
                task_id=str(uuid.uuid4()),
                task_type="notify",
                tenant_id="tenant-default",
                queue_name="default",
                priority=DispatchPriority.LOW,
                payload={"message": "pipeline finished"},
            ),
        ]
    )

    for _ in range(5):
        result = dispatcher.dispatch_once()
        if not result:
            break

        if result.accepted:
            dispatcher.acknowledge(result.task_id, result=result.result)

    print(dispatcher.export_snapshot_json())
    print(dispatcher.export_tasks_json())
    print(dispatcher.export_targets_json())


if __name__ == "__main__":
    example_usage()
"""
data/orchestration/distributed_executor.py

Enterprise Distributed Executor.

Recursos:
- Execução distribuída de tarefas
- Worker registry
- Heartbeat de workers
- Task queue em memória extensível
- Lease/claim de tarefas
- Retry com backoff
- Timeout
- Idempotência por task_id/idempotency_key
- Balanceamento simples por capacidade
- Auditoria
- Métricas
- Multi-tenant
- Exportação JSON
- Sem dependências externas obrigatórias
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Set


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class DistributedTaskStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    DEAD_LETTER = "dead_letter"


class WorkerStatus(str, Enum):
    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    DRAINING = "draining"


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class RetryStrategy(str, Enum):
    NONE = "none"
    FIXED = "fixed"
    EXPONENTIAL = "exponential"


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# =============================================================================
# Exceptions
# =============================================================================

class DistributedExecutorError(Exception):
    """Erro base do executor distribuído."""


class TaskNotFoundError(DistributedExecutorError):
    """Tarefa não encontrada."""


class WorkerNotFoundError(DistributedExecutorError):
    """Worker não encontrado."""


class TaskExecutionError(DistributedExecutorError):
    """Erro durante execução da tarefa."""


class TaskValidationError(DistributedExecutorError):
    """Tarefa inválida."""


class WorkerCapacityError(DistributedExecutorError):
    """Worker sem capacidade disponível."""


# =============================================================================
# Protocols
# =============================================================================

class TaskHandler(Protocol):
    def __call__(
        self,
        task: "DistributedTask",
        context: "ExecutionContext",
    ) -> Any:
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
        logger.info(
            "distributed_executor_audit=%s",
            json.dumps(event, ensure_ascii=False, default=str),
        )


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
class RetryPolicy:
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    max_attempts: int = 3
    delay_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 60.0

    def validate(self) -> None:
        if self.max_attempts < 1:
            raise TaskValidationError("max_attempts precisa ser >= 1")

        if self.delay_seconds < 0:
            raise TaskValidationError("delay_seconds não pode ser negativo")

        if self.backoff_multiplier < 1:
            raise TaskValidationError("backoff_multiplier precisa ser >= 1")

    def delay_for_attempt(self, attempt: int) -> float:
        if self.strategy == RetryStrategy.NONE:
            return 0.0

        if self.strategy == RetryStrategy.FIXED:
            return self.delay_seconds

        if self.strategy == RetryStrategy.EXPONENTIAL:
            return min(
                self.delay_seconds * (self.backoff_multiplier ** max(0, attempt - 1)),
                self.max_delay_seconds,
            )

        return 0.0


@dataclass(frozen=True)
class ExecutionContext:
    executor_id: str
    worker_id: Optional[str] = None
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    correlation_id: Optional[str] = None
    triggered_by: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DistributedTask:
    task_id: str
    task_type: str
    payload: Dict[str, Any]
    priority: TaskPriority = TaskPriority.NORMAL
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    idempotency_key: Optional[str] = None
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_seconds: Optional[float] = None
    lease_seconds: float = 300.0
    max_runtime_seconds: Optional[float] = None
    tags: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    scheduled_at: Optional[datetime] = None
    status: DistributedTaskStatus = DistributedTaskStatus.PENDING
    attempts: int = 0
    claimed_by: Optional[str] = None
    claimed_at: Optional[datetime] = None
    lease_until: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    result: Any = None
    error: Optional[str] = None
    traceback_text: Optional[str] = None

    def validate(self) -> None:
        if not self.task_id:
            raise TaskValidationError("task_id é obrigatório")

        if not self.task_type:
            raise TaskValidationError("task_type é obrigatório")

        if self.payload is None:
            raise TaskValidationError("payload não pode ser None")

        if self.lease_seconds <= 0:
            raise TaskValidationError("lease_seconds precisa ser maior que zero")

        self.retry_policy.validate()


@dataclass
class WorkerInfo:
    worker_id: str
    hostname: str
    status: WorkerStatus = WorkerStatus.STARTING
    capacity: int = 1
    active_tasks: Set[str] = field(default_factory=set)
    supported_task_types: Set[str] = field(default_factory=set)
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def available_capacity(self) -> int:
        return max(0, self.capacity - len(self.active_tasks))

    def supports(self, task: DistributedTask) -> bool:
        if self.supported_task_types and task.task_type not in self.supported_task_types:
            return False

        if self.tenant_id and task.tenant_id and self.tenant_id != task.tenant_id:
            return False

        if self.domain and task.domain and self.domain != task.domain:
            return False

        return self.status in {WorkerStatus.IDLE, WorkerStatus.BUSY}


@dataclass
class TaskExecutionResult:
    task_id: str
    worker_id: str
    status: DistributedTaskStatus
    result: Any = None
    error: Optional[str] = None
    traceback_text: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[float] = None


@dataclass
class ExecutorSnapshot:
    executor_id: str
    generated_at: datetime
    workers_total: int
    workers_online: int
    tasks_total: int
    tasks_by_status: Dict[str, int]
    queue_depth: int
    active_tasks: int
    dead_letter_count: int


# =============================================================================
# Stores
# =============================================================================

class InMemoryTaskStore:
    def __init__(self) -> None:
        self._tasks: Dict[str, DistributedTask] = {}
        self._idempotency_index: Dict[str, str] = {}
        self._dead_letter: Dict[str, DistributedTask] = {}
        self._lock = threading.RLock()

    def save(self, task: DistributedTask) -> None:
        task.validate()

        with self._lock:
            self._tasks[task.task_id] = task

            if task.idempotency_key:
                self._idempotency_index[task.idempotency_key] = task.task_id

    def get(self, task_id: str) -> DistributedTask:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise TaskNotFoundError(task_id)
            return task

    def find_by_idempotency_key(self, key: str) -> Optional[DistributedTask]:
        with self._lock:
            task_id = self._idempotency_index.get(key)
            return self._tasks.get(task_id) if task_id else None

    def list_all(
        self,
        status: Optional[DistributedTaskStatus] = None,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> List[DistributedTask]:
        with self._lock:
            tasks = list(self._tasks.values())

        if status is not None:
            tasks = [task for task in tasks if task.status == status]

        if tenant_id is not None:
            tasks = [task for task in tasks if task.tenant_id == tenant_id]

        if domain is not None:
            tasks = [task for task in tasks if task.domain == domain]

        return sorted(tasks, key=lambda item: item.created_at)

    def move_to_dead_letter(self, task: DistributedTask) -> None:
        with self._lock:
            task.status = DistributedTaskStatus.DEAD_LETTER
            self._dead_letter[task.task_id] = task
            self._tasks[task.task_id] = task

    def dead_letter_count(self) -> int:
        with self._lock:
            return len(self._dead_letter)


class InMemoryWorkerRegistry:
    def __init__(self) -> None:
        self._workers: Dict[str, WorkerInfo] = {}
        self._lock = threading.RLock()

    def register(self, worker: WorkerInfo) -> None:
        with self._lock:
            self._workers[worker.worker_id] = worker

    def get(self, worker_id: str) -> WorkerInfo:
        with self._lock:
            worker = self._workers.get(worker_id)
            if not worker:
                raise WorkerNotFoundError(worker_id)
            return worker

    def list_all(self) -> List[WorkerInfo]:
        with self._lock:
            return list(self._workers.values())

    def heartbeat(self, worker_id: str, status: Optional[WorkerStatus] = None) -> None:
        with self._lock:
            worker = self._workers.get(worker_id)
            if not worker:
                raise WorkerNotFoundError(worker_id)

            worker.last_heartbeat_at = datetime.now(timezone.utc)

            if status:
                worker.status = status

    def mark_offline_stale_workers(self, stale_after_seconds: float) -> List[str]:
        now = datetime.now(timezone.utc)
        offline: List[str] = []

        with self._lock:
            for worker in self._workers.values():
                age = (now - worker.last_heartbeat_at).total_seconds()

                if age > stale_after_seconds and worker.status != WorkerStatus.OFFLINE:
                    worker.status = WorkerStatus.OFFLINE
                    offline.append(worker.worker_id)

        return offline


# =============================================================================
# Priority Queue
# =============================================================================

class DistributedTaskQueue:
    PRIORITY_WEIGHT = {
        TaskPriority.CRITICAL: 0,
        TaskPriority.HIGH: 1,
        TaskPriority.NORMAL: 2,
        TaskPriority.LOW: 3,
    }

    def __init__(self) -> None:
        self._queue: queue.PriorityQueue[Tuple[int, float, str]] = queue.PriorityQueue()
        self._queued: Set[str] = set()
        self._lock = threading.RLock()

    def enqueue(self, task: DistributedTask) -> None:
        with self._lock:
            if task.task_id in self._queued:
                return

            priority = self.PRIORITY_WEIGHT[task.priority]
            timestamp = task.scheduled_at.timestamp() if task.scheduled_at else task.created_at.timestamp()

            self._queue.put((priority, timestamp, task.task_id))
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
# Executor
# =============================================================================

class DistributedExecutor:
    def __init__(
        self,
        executor_id: Optional[str] = None,
        task_store: Optional[InMemoryTaskStore] = None,
        worker_registry: Optional[InMemoryWorkerRegistry] = None,
        task_queue: Optional[DistributedTaskQueue] = None,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
        stale_worker_seconds: float = 120.0,
    ) -> None:
        self.executor_id = executor_id or str(uuid.uuid4())
        self.task_store = task_store or InMemoryTaskStore()
        self.worker_registry = worker_registry or InMemoryWorkerRegistry()
        self.task_queue = task_queue or DistributedTaskQueue()
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()
        self.stale_worker_seconds = stale_worker_seconds
        self._handlers: Dict[str, TaskHandler] = {}
        self._processed_idempotency_keys: Set[str] = set()
        self._lock = threading.RLock()

    def register_handler(self, task_type: str, handler: TaskHandler) -> None:
        if not task_type:
            raise TaskValidationError("task_type é obrigatório")

        self._handlers[task_type] = handler

        self._audit(
            "distributed.handler.registered",
            AuditSeverity.INFO,
            details={"task_type": task_type},
        )

    def register_worker(self, worker: WorkerInfo) -> None:
        self.worker_registry.register(worker)

        self._audit(
            "distributed.worker.registered",
            AuditSeverity.INFO,
            worker_id=worker.worker_id,
            details={
                "hostname": worker.hostname,
                "capacity": worker.capacity,
                "supported_task_types": sorted(worker.supported_task_types),
                "tenant_id": worker.tenant_id,
                "domain": worker.domain,
            },
        )

        self.metrics_backend.increment("distributed.worker.registered.total")

    def heartbeat(self, worker_id: str, status: Optional[WorkerStatus] = None) -> None:
        self.worker_registry.heartbeat(worker_id, status=status)

        self.metrics_backend.increment(
            "distributed.worker.heartbeat.total",
            tags={"worker_id": worker_id},
        )

    def submit_task(self, task: DistributedTask) -> DistributedTask:
        task.validate()

        with self._lock:
            if task.idempotency_key:
                existing = self.task_store.find_by_idempotency_key(task.idempotency_key)
                if existing:
                    self._audit(
                        "distributed.task.idempotent_duplicate",
                        AuditSeverity.INFO,
                        task_id=existing.task_id,
                        details={
                            "idempotency_key": task.idempotency_key,
                            "original_status": existing.status.value,
                        },
                    )
                    return existing

            self.task_store.save(task)
            self.task_queue.enqueue(task)

        self._audit(
            "distributed.task.submitted",
            AuditSeverity.INFO,
            task_id=task.task_id,
            details={
                "task_type": task.task_type,
                "priority": task.priority.value,
                "tenant_id": task.tenant_id,
                "domain": task.domain,
            },
        )

        self.metrics_backend.increment(
            "distributed.task.submitted.total",
            tags={
                "task_type": task.task_type,
                "priority": task.priority.value,
                "tenant_id": task.tenant_id or "-",
            },
        )

        return task

    def submit_many(self, tasks: Iterable[DistributedTask]) -> List[DistributedTask]:
        return [self.submit_task(task) for task in tasks]

    def claim_task(
        self,
        worker_id: str,
        dequeue_timeout: Optional[float] = 0.1,
    ) -> Optional[DistributedTask]:
        worker = self.worker_registry.get(worker_id)

        if worker.available_capacity() <= 0:
            raise WorkerCapacityError(f"Worker sem capacidade: {worker_id}")

        for _ in range(max(1, self.task_queue.depth() + 1)):
            task_id = self.task_queue.dequeue(timeout=dequeue_timeout)
            if not task_id:
                return None

            task = self.task_store.get(task_id)

            if task.status not in {
                DistributedTaskStatus.PENDING,
                DistributedTaskStatus.RETRYING,
            }:
                continue

            if task.scheduled_at and datetime.now(timezone.utc) < task.scheduled_at:
                self.task_queue.enqueue(task)
                continue

            if not worker.supports(task):
                self.task_queue.enqueue(task)
                continue

            now = datetime.now(timezone.utc)

            task.status = DistributedTaskStatus.CLAIMED
            task.claimed_by = worker_id
            task.claimed_at = now
            task.lease_until = now + timedelta(seconds=task.lease_seconds)

            worker.active_tasks.add(task.task_id)
            worker.status = WorkerStatus.BUSY if worker.available_capacity() == 0 else WorkerStatus.IDLE

            self.task_store.save(task)

            self._audit(
                "distributed.task.claimed",
                AuditSeverity.INFO,
                task_id=task.task_id,
                worker_id=worker_id,
                details={
                    "lease_until": task.lease_until.isoformat(),
                    "attempts": task.attempts,
                },
            )

            return task

        return None

    def execute_claimed_task(
        self,
        worker_id: str,
        task: DistributedTask,
        context: Optional[ExecutionContext] = None,
    ) -> TaskExecutionResult:
        worker = self.worker_registry.get(worker_id)

        if task.claimed_by != worker_id:
            raise TaskExecutionError(
                f"Tarefa {task.task_id} não está atribuída ao worker {worker_id}"
            )

        context = context or ExecutionContext(
            executor_id=self.executor_id,
            worker_id=worker_id,
            tenant_id=task.tenant_id,
            domain=task.domain,
        )

        handler = self._handlers.get(task.task_type)

        if not handler:
            raise TaskExecutionError(f"Handler não registrado para task_type={task.task_type}")

        task.status = DistributedTaskStatus.RUNNING
        task.started_at = datetime.now(timezone.utc)
        task.attempts += 1
        self.task_store.save(task)

        self._audit(
            "distributed.task.started",
            AuditSeverity.INFO,
            task_id=task.task_id,
            worker_id=worker_id,
            details={
                "task_type": task.task_type,
                "attempt": task.attempts,
            },
        )

        started = time.perf_counter()

        try:
            if task.idempotency_key and task.idempotency_key in self._processed_idempotency_keys:
                task.status = DistributedTaskStatus.SUCCESS
                task.result = {
                    "idempotent": True,
                    "message": "Tarefa já processada anteriormente.",
                }
            else:
                result = self._execute_with_optional_timeout(handler, task, context)
                task.status = DistributedTaskStatus.SUCCESS
                task.result = result
                task.error = None
                task.traceback_text = None

                if task.idempotency_key:
                    self._processed_idempotency_keys.add(task.idempotency_key)

            severity = AuditSeverity.INFO

        except Exception as exc:
            task.error = str(exc)
            task.traceback_text = traceback.format_exc()

            if task.timeout_seconds and "timed out" in str(exc).lower():
                task.status = DistributedTaskStatus.TIMED_OUT
            else:
                task.status = DistributedTaskStatus.FAILED

            severity = AuditSeverity.ERROR

            if task.attempts < task.retry_policy.max_attempts:
                task.status = DistributedTaskStatus.RETRYING
                delay = task.retry_policy.delay_for_attempt(task.attempts)
                task.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                self.task_queue.enqueue(task)
            else:
                self.task_store.move_to_dead_letter(task)

        finally:
            task.finished_at = datetime.now(timezone.utc)
            duration_ms = (time.perf_counter() - started) * 1000

            worker.active_tasks.discard(task.task_id)
            worker.status = WorkerStatus.IDLE if worker.available_capacity() > 0 else WorkerStatus.BUSY
            worker.last_heartbeat_at = datetime.now(timezone.utc)

            self.task_store.save(task)

        result = TaskExecutionResult(
            task_id=task.task_id,
            worker_id=worker_id,
            status=task.status,
            result=task.result,
            error=task.error,
            traceback_text=task.traceback_text,
            started_at=task.started_at,
            finished_at=task.finished_at,
            duration_ms=duration_ms,
        )

        self._audit(
            "distributed.task.finished",
            severity,
            task_id=task.task_id,
            worker_id=worker_id,
            details={
                "status": task.status.value,
                "attempts": task.attempts,
                "duration_ms": duration_ms,
                "error": task.error,
            },
        )

        self.metrics_backend.increment(
            "distributed.task.finished.total",
            tags={
                "task_type": task.task_type,
                "status": task.status.value,
                "worker_id": worker_id,
            },
        )

        self.metrics_backend.timing(
            "distributed.task.duration_ms",
            duration_ms,
            tags={
                "task_type": task.task_type,
                "status": task.status.value,
            },
        )

        return result

    def run_once(self, worker_id: str) -> Optional[TaskExecutionResult]:
        task = self.claim_task(worker_id)

        if not task:
            return None

        return self.execute_claimed_task(worker_id, task)

    def run_worker_loop(
        self,
        worker_id: str,
        stop_event: threading.Event,
        idle_sleep_seconds: float = 1.0,
    ) -> None:
        worker = self.worker_registry.get(worker_id)
        worker.status = WorkerStatus.IDLE

        self._audit(
            "distributed.worker.loop_started",
            AuditSeverity.INFO,
            worker_id=worker_id,
            details={},
        )

        while not stop_event.is_set():
            try:
                self.heartbeat(worker_id)

                result = self.run_once(worker_id)

                if result is None:
                    time.sleep(idle_sleep_seconds)

            except WorkerCapacityError:
                time.sleep(idle_sleep_seconds)

            except Exception as exc:
                logger.exception("Erro no worker loop")
                worker.status = WorkerStatus.DEGRADED

                self._audit(
                    "distributed.worker.loop_error",
                    AuditSeverity.ERROR,
                    worker_id=worker_id,
                    details={
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )

                time.sleep(idle_sleep_seconds)

        worker.status = WorkerStatus.OFFLINE

        self._audit(
            "distributed.worker.loop_stopped",
            AuditSeverity.WARNING,
            worker_id=worker_id,
            details={},
        )

    def recover_expired_leases(self) -> List[str]:
        now = datetime.now(timezone.utc)
        recovered: List[str] = []

        for task in self.task_store.list_all():
            if (
                task.status in {
                    DistributedTaskStatus.CLAIMED,
                    DistributedTaskStatus.RUNNING,
                }
                and task.lease_until
                and now > task.lease_until
            ):
                task.status = DistributedTaskStatus.RETRYING
                task.claimed_by = None
                task.claimed_at = None
                task.lease_until = None
                task.started_at = None

                if task.attempts >= task.retry_policy.max_attempts:
                    self.task_store.move_to_dead_letter(task)
                else:
                    self.task_store.save(task)
                    self.task_queue.enqueue(task)

                recovered.append(task.task_id)

                self._audit(
                    "distributed.task.lease_expired",
                    AuditSeverity.WARNING,
                    task_id=task.task_id,
                    details={"attempts": task.attempts},
                )

        return recovered

    def mark_stale_workers_offline(self) -> List[str]:
        offline = self.worker_registry.mark_offline_stale_workers(
            self.stale_worker_seconds
        )

        for worker_id in offline:
            self._audit(
                "distributed.worker.marked_offline",
                AuditSeverity.WARNING,
                worker_id=worker_id,
                details={"stale_after_seconds": self.stale_worker_seconds},
            )

        return offline

    def cancel_task(self, task_id: str, reason: str = "") -> DistributedTask:
        task = self.task_store.get(task_id)

        if task.status in {
            DistributedTaskStatus.SUCCESS,
            DistributedTaskStatus.DEAD_LETTER,
        }:
            return task

        task.status = DistributedTaskStatus.CANCELLED
        task.error = reason or "cancelled"
        task.finished_at = datetime.now(timezone.utc)
        self.task_store.save(task)

        self._audit(
            "distributed.task.cancelled",
            AuditSeverity.WARNING,
            task_id=task_id,
            details={"reason": reason},
        )

        return task

    def get_task(self, task_id: str) -> DistributedTask:
        return self.task_store.get(task_id)

    def list_tasks(
        self,
        status: Optional[DistributedTaskStatus] = None,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> List[DistributedTask]:
        return self.task_store.list_all(
            status=status,
            tenant_id=tenant_id,
            domain=domain,
        )

    def list_workers(self) -> List[WorkerInfo]:
        return self.worker_registry.list_all()

    def snapshot(self) -> ExecutorSnapshot:
        tasks = self.task_store.list_all()
        workers = self.worker_registry.list_all()

        tasks_by_status: Dict[str, int] = {}

        for status in DistributedTaskStatus:
            tasks_by_status[status.value] = sum(
                1 for task in tasks if task.status == status
            )

        online_statuses = {
            WorkerStatus.IDLE,
            WorkerStatus.BUSY,
            WorkerStatus.DEGRADED,
            WorkerStatus.DRAINING,
        }

        active_tasks = sum(len(worker.active_tasks) for worker in workers)

        return ExecutorSnapshot(
            executor_id=self.executor_id,
            generated_at=datetime.now(timezone.utc),
            workers_total=len(workers),
            workers_online=sum(1 for worker in workers if worker.status in online_statuses),
            tasks_total=len(tasks),
            tasks_by_status=tasks_by_status,
            queue_depth=self.task_queue.depth(),
            active_tasks=active_tasks,
            dead_letter_count=self.task_store.dead_letter_count(),
        )

    def export_snapshot_json(self) -> str:
        snapshot = self.snapshot()
        data = asdict(snapshot)
        data["generated_at"] = snapshot.generated_at.isoformat()
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)

    def export_task_json(self, task_id: str) -> str:
        task = self.task_store.get(task_id)
        return json.dumps(
            self._task_to_dict(task),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def export_workers_json(self) -> str:
        return json.dumps(
            [self._worker_to_dict(worker) for worker in self.worker_registry.list_all()],
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def _execute_with_optional_timeout(
        self,
        handler: TaskHandler,
        task: DistributedTask,
        context: ExecutionContext,
    ) -> Any:
        if not task.timeout_seconds:
            return handler(task, context)

        result_holder: Dict[str, Any] = {}
        error_holder: Dict[str, BaseException] = {}

        def target() -> None:
            try:
                result_holder["result"] = handler(task, context)
            except BaseException as exc:
                error_holder["error"] = exc

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(timeout=task.timeout_seconds)

        if thread.is_alive():
            raise TimeoutError(f"Task {task.task_id} timed out after {task.timeout_seconds}s")

        if "error" in error_holder:
            raise error_holder["error"]

        return result_holder.get("result")

    def _audit(
        self,
        event_type: str,
        severity: AuditSeverity,
        task_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "severity": severity.value,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "executor_id": self.executor_id,
                "task_id": task_id,
                "worker_id": worker_id,
                "details": details or {},
            }
        )

    @staticmethod
    def _task_to_dict(task: DistributedTask) -> Dict[str, Any]:
        data = asdict(task)
        data["priority"] = task.priority.value
        data["status"] = task.status.value
        data["retry_policy"]["strategy"] = task.retry_policy.strategy.value
        data["created_at"] = task.created_at.isoformat()
        data["scheduled_at"] = task.scheduled_at.isoformat() if task.scheduled_at else None
        data["claimed_at"] = task.claimed_at.isoformat() if task.claimed_at else None
        data["lease_until"] = task.lease_until.isoformat() if task.lease_until else None
        data["started_at"] = task.started_at.isoformat() if task.started_at else None
        data["finished_at"] = task.finished_at.isoformat() if task.finished_at else None
        return data

    @staticmethod
    def _worker_to_dict(worker: WorkerInfo) -> Dict[str, Any]:
        data = asdict(worker)
        data["status"] = worker.status.value
        data["active_tasks"] = sorted(worker.active_tasks)
        data["supported_task_types"] = sorted(worker.supported_task_types)
        data["registered_at"] = worker.registered_at.isoformat()
        data["last_heartbeat_at"] = worker.last_heartbeat_at.isoformat()
        data["available_capacity"] = worker.available_capacity()
        return data


# =============================================================================
# Factory
# =============================================================================

def create_default_distributed_executor() -> DistributedExecutor:
    executor = DistributedExecutor(executor_id="default-distributed-executor")

    def echo_handler(task: DistributedTask, context: ExecutionContext) -> Dict[str, Any]:
        return {
            "task_id": task.task_id,
            "task_type": task.task_type,
            "payload": task.payload,
            "worker_id": context.worker_id,
            "executor_id": context.executor_id,
        }

    def failing_once_handler(task: DistributedTask, context: ExecutionContext) -> Dict[str, Any]:
        if task.attempts < 2:
            raise TaskExecutionError("Falha simulada na primeira tentativa")

        return {
            "task_id": task.task_id,
            "status": "recovered",
            "attempts": task.attempts,
        }

    executor.register_handler("echo", echo_handler)
    executor.register_handler("failing_once", failing_once_handler)

    executor.register_worker(
        WorkerInfo(
            worker_id="worker-001",
            hostname="localhost",
            status=WorkerStatus.IDLE,
            capacity=2,
            supported_task_types={"echo", "failing_once"},
            tenant_id="tenant-default",
        )
    )

    return executor


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    executor = create_default_distributed_executor()

    tasks = [
        DistributedTask(
            task_id=str(uuid.uuid4()),
            task_type="echo",
            tenant_id="tenant-default",
            payload={"message": "hello enterprise executor"},
            priority=TaskPriority.HIGH,
            idempotency_key="echo-001",
        ),
        DistributedTask(
            task_id=str(uuid.uuid4()),
            task_type="failing_once",
            tenant_id="tenant-default",
            payload={"message": "retry test"},
            priority=TaskPriority.NORMAL,
            retry_policy=RetryPolicy(
                strategy=RetryStrategy.FIXED,
                max_attempts=3,
                delay_seconds=0.1,
            ),
        ),
    ]

    executor.submit_many(tasks)

    for _ in range(5):
        result = executor.run_once("worker-001")
        if result is None:
            break

        if result.status == DistributedTaskStatus.RETRYING:
            time.sleep(0.2)

    print(executor.export_snapshot_json())
    print(executor.export_workers_json())

    for task in executor.list_tasks():
        print(executor.export_task_json(task.task_id))


if __name__ == "__main__":
    example_usage()
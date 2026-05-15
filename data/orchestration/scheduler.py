"""
data/orchestration/scheduler.py

Enterprise Scheduler Engine.

Recursos:
- Agendamentos one-shot, interval e cron simplificado
- Misfire policy
- Trigger manual
- Execução plugável
- Estado de schedules e execuções
- Pausar, retomar, cancelar e reprocessar
- Multi-tenant
- Auditoria e métricas
- Exportação JSON
- Sem dependências externas obrigatórias
"""

from __future__ import annotations

import json
import logging
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

class ScheduleType(str, Enum):
    ONE_SHOT = "one_shot"
    INTERVAL = "interval"
    CRON = "cron"
    MANUAL = "manual"


class ScheduleStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"
    COMPLETED = "completed"
    ERROR = "error"


class ScheduleExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    MISFIRED = "misfired"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class MisfirePolicy(str, Enum):
    SKIP = "skip"
    FIRE_ONCE = "fire_once"
    FIRE_ALL = "fire_all"


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# =============================================================================
# Exceptions
# =============================================================================

class SchedulerError(Exception):
    """Erro base do scheduler."""


class ScheduleValidationError(SchedulerError):
    """Erro de validação do schedule."""


class ScheduleNotFoundError(SchedulerError):
    """Schedule não encontrado."""


class ScheduleExecutionError(SchedulerError):
    """Erro na execução do schedule."""


# =============================================================================
# Protocols
# =============================================================================

class ScheduleExecutor(Protocol):
    def execute(
        self,
        schedule: "ScheduleDefinition",
        execution: "ScheduleExecution",
        context: "ScheduleContext",
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

    def timing(
        self,
        metric_name: str,
        value_ms: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        ...


# =============================================================================
# Backends
# =============================================================================

class LoggingAuditBackend:
    def write_event(self, event: Dict[str, Any]) -> None:
        logger.info("scheduler_audit=%s", json.dumps(event, ensure_ascii=False, default=str))


class LoggingMetricsBackend:
    def increment(
        self,
        metric_name: str,
        value: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("metric=%s value=%s tags=%s", metric_name, value, tags or {})

    def timing(
        self,
        metric_name: str,
        value_ms: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("timing=%s value_ms=%s tags=%s", metric_name, value_ms, tags or {})


class FunctionScheduleExecutor:
    def __init__(self) -> None:
        self._handlers: Dict[str, Callable[..., Any]] = {}

    def register(self, target_name: str, handler: Callable[..., Any]) -> None:
        if not target_name:
            raise ScheduleValidationError("target_name é obrigatório")
        self._handlers[target_name] = handler

    def execute(
        self,
        schedule: "ScheduleDefinition",
        execution: "ScheduleExecution",
        context: "ScheduleContext",
    ) -> Any:
        handler = self._handlers.get(schedule.target_name)

        if not handler:
            raise ScheduleExecutionError(
                f"Handler não registrado para target_name={schedule.target_name}"
            )

        return handler(
            schedule=schedule,
            execution=execution,
            context=context,
            **schedule.payload,
        )


# =============================================================================
# Models
# =============================================================================

@dataclass(frozen=True)
class ScheduleContext:
    scheduler_id: str
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    user_id: Optional[str] = None
    correlation_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IntervalSchedule:
    every_seconds: int

    def validate(self) -> None:
        if self.every_seconds <= 0:
            raise ScheduleValidationError("every_seconds precisa ser maior que zero")


@dataclass(frozen=True)
class CronSchedule:
    """
    Cron simplificado: minute hour day_of_month month day_of_week

    Suporta:
    - "*" qualquer valor
    - número exato: "5"
    - lista: "1,2,3"
    - passo: "*/5"
    """

    expression: str

    def validate(self) -> None:
        parts = self.expression.split()
        if len(parts) != 5:
            raise ScheduleValidationError(
                "Cron precisa ter 5 campos: minute hour day_of_month month day_of_week"
            )


@dataclass
class ScheduleDefinition:
    schedule_id: str
    name: str
    schedule_type: ScheduleType
    target_name: str
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    status: ScheduleStatus = ScheduleStatus.ACTIVE
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    run_at: Optional[datetime] = None
    interval: Optional[IntervalSchedule] = None
    cron: Optional[CronSchedule] = None
    timezone_name: str = "UTC"
    misfire_policy: MisfirePolicy = MisfirePolicy.FIRE_ONCE
    misfire_grace_seconds: int = 300
    max_runs: Optional[int] = None
    allow_concurrent_runs: bool = False
    payload: Dict[str, Any] = field(default_factory=dict)
    tags: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    run_count: int = 0
    error_count: int = 0

    def validate(self) -> None:
        if not self.schedule_id:
            raise ScheduleValidationError("schedule_id é obrigatório")

        if not self.name:
            raise ScheduleValidationError("name é obrigatório")

        if not self.target_name:
            raise ScheduleValidationError("target_name é obrigatório")

        if self.end_at and self.start_at and self.end_at <= self.start_at:
            raise ScheduleValidationError("end_at deve ser maior que start_at")

        if self.max_runs is not None and self.max_runs <= 0:
            raise ScheduleValidationError("max_runs precisa ser maior que zero")

        if self.schedule_type == ScheduleType.ONE_SHOT and not self.run_at:
            raise ScheduleValidationError("ONE_SHOT exige run_at")

        if self.schedule_type == ScheduleType.INTERVAL:
            if not self.interval:
                raise ScheduleValidationError("INTERVAL exige interval")
            self.interval.validate()

        if self.schedule_type == ScheduleType.CRON:
            if not self.cron:
                raise ScheduleValidationError("CRON exige cron")
            self.cron.validate()


@dataclass
class ScheduleExecution:
    execution_id: str
    schedule_id: str
    scheduled_for: datetime
    status: ScheduleExecutionStatus = ScheduleExecutionStatus.PENDING
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    traceback_text: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Repository
# =============================================================================

class SchedulerRepository:
    def __init__(self, schedules: Optional[List[ScheduleDefinition]] = None) -> None:
        self._schedules: Dict[str, ScheduleDefinition] = {}
        self._executions: Dict[str, ScheduleExecution] = {}
        self._lock = threading.RLock()

        for schedule in schedules or []:
            self.save_schedule(schedule)

    def save_schedule(self, schedule: ScheduleDefinition) -> None:
        schedule.validate()
        with self._lock:
            self._schedules[schedule.schedule_id] = schedule

    def get_schedule(self, schedule_id: str) -> ScheduleDefinition:
        with self._lock:
            schedule = self._schedules.get(schedule_id)
            if not schedule:
                raise ScheduleNotFoundError(schedule_id)
            return schedule

    def list_schedules(
        self,
        status: Optional[ScheduleStatus] = None,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> List[ScheduleDefinition]:
        with self._lock:
            schedules = list(self._schedules.values())

        if status is not None:
            schedules = [item for item in schedules if item.status == status]

        if tenant_id is not None:
            schedules = [item for item in schedules if item.tenant_id == tenant_id]

        if domain is not None:
            schedules = [item for item in schedules if item.domain == domain]

        return sorted(schedules, key=lambda item: item.next_run_at or item.created_at)

    def save_execution(self, execution: ScheduleExecution) -> None:
        with self._lock:
            self._executions[execution.execution_id] = execution

    def get_execution(self, execution_id: str) -> ScheduleExecution:
        with self._lock:
            execution = self._executions.get(execution_id)
            if not execution:
                raise ScheduleExecutionError(f"Execução não encontrada: {execution_id}")
            return execution

    def list_executions(
        self,
        schedule_id: Optional[str] = None,
        status: Optional[ScheduleExecutionStatus] = None,
    ) -> List[ScheduleExecution]:
        with self._lock:
            executions = list(self._executions.values())

        if schedule_id is not None:
            executions = [item for item in executions if item.schedule_id == schedule_id]

        if status is not None:
            executions = [item for item in executions if item.status == status]

        return sorted(executions, key=lambda item: item.scheduled_for, reverse=True)

    def has_running_execution(self, schedule_id: str) -> bool:
        return any(
            execution.status == ScheduleExecutionStatus.RUNNING
            for execution in self.list_executions(schedule_id=schedule_id)
        )


# =============================================================================
# Time Calculation
# =============================================================================

class ScheduleCalculator:
    @staticmethod
    def initial_next_run(schedule: ScheduleDefinition, now: Optional[datetime] = None) -> Optional[datetime]:
        now = now or datetime.now(timezone.utc)

        if schedule.status != ScheduleStatus.ACTIVE:
            return None

        if schedule.max_runs is not None and schedule.run_count >= schedule.max_runs:
            return None

        if schedule.end_at and now > schedule.end_at:
            return None

        base = max(now, schedule.start_at or now)

        if schedule.schedule_type == ScheduleType.ONE_SHOT:
            if schedule.run_at and schedule.run_at >= now:
                return schedule.run_at
            return None

        if schedule.schedule_type == ScheduleType.INTERVAL:
            if schedule.last_run_at:
                return schedule.last_run_at + timedelta(seconds=schedule.interval.every_seconds)
            return base

        if schedule.schedule_type == ScheduleType.CRON:
            return ScheduleCalculator.next_cron_time(schedule.cron, base)

        if schedule.schedule_type == ScheduleType.MANUAL:
            return None

        return None

    @staticmethod
    def next_after(schedule: ScheduleDefinition, previous_run: datetime) -> Optional[datetime]:
        if schedule.status != ScheduleStatus.ACTIVE:
            return None

        if schedule.max_runs is not None and schedule.run_count >= schedule.max_runs:
            return None

        if schedule.schedule_type == ScheduleType.ONE_SHOT:
            return None

        if schedule.schedule_type == ScheduleType.INTERVAL:
            next_time = previous_run + timedelta(seconds=schedule.interval.every_seconds)

        elif schedule.schedule_type == ScheduleType.CRON:
            next_time = ScheduleCalculator.next_cron_time(
                schedule.cron,
                previous_run + timedelta(minutes=1),
            )

        else:
            return None

        if schedule.end_at and next_time and next_time > schedule.end_at:
            return None

        return next_time

    @staticmethod
    def next_cron_time(cron: CronSchedule, start: datetime) -> Optional[datetime]:
        cron.validate()
        minute_expr, hour_expr, dom_expr, month_expr, dow_expr = cron.expression.split()

        candidate = start.replace(second=0, microsecond=0)

        for _ in range(366 * 24 * 60):
            if (
                ScheduleCalculator._matches(candidate.minute, minute_expr)
                and ScheduleCalculator._matches(candidate.hour, hour_expr)
                and ScheduleCalculator._matches(candidate.day, dom_expr)
                and ScheduleCalculator._matches(candidate.month, month_expr)
                and ScheduleCalculator._matches(candidate.weekday(), dow_expr)
            ):
                return candidate

            candidate += timedelta(minutes=1)

        return None

    @staticmethod
    def _matches(value: int, expr: str) -> bool:
        if expr == "*":
            return True

        if expr.startswith("*/"):
            step = int(expr[2:])
            return value % step == 0

        if "," in expr:
            return value in {int(part) for part in expr.split(",")}

        return value == int(expr)


# =============================================================================
# Scheduler Engine
# =============================================================================

class SchedulerEngine:
    def __init__(
        self,
        scheduler_id: Optional[str] = None,
        repository: Optional[SchedulerRepository] = None,
        executor: Optional[ScheduleExecutor] = None,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
    ) -> None:
        self.scheduler_id = scheduler_id or str(uuid.uuid4())
        self.repository = repository or SchedulerRepository()
        self.executor = executor or FunctionScheduleExecutor()
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()
        self._lock = threading.RLock()

    def register_schedule(self, schedule: ScheduleDefinition) -> None:
        schedule.next_run_at = ScheduleCalculator.initial_next_run(schedule)
        self.repository.save_schedule(schedule)

        self._audit(
            "scheduler.schedule.registered",
            AuditSeverity.INFO,
            schedule_id=schedule.schedule_id,
            details={
                "name": schedule.name,
                "schedule_type": schedule.schedule_type.value,
                "target_name": schedule.target_name,
                "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
            },
        )

    def pause_schedule(self, schedule_id: str) -> ScheduleDefinition:
        schedule = self.repository.get_schedule(schedule_id)
        schedule.status = ScheduleStatus.PAUSED
        schedule.updated_at = datetime.now(timezone.utc)
        self.repository.save_schedule(schedule)

        self._audit("scheduler.schedule.paused", AuditSeverity.WARNING, schedule_id=schedule_id)
        return schedule

    def resume_schedule(self, schedule_id: str) -> ScheduleDefinition:
        schedule = self.repository.get_schedule(schedule_id)
        schedule.status = ScheduleStatus.ACTIVE
        schedule.updated_at = datetime.now(timezone.utc)
        schedule.next_run_at = ScheduleCalculator.initial_next_run(schedule)
        self.repository.save_schedule(schedule)

        self._audit("scheduler.schedule.resumed", AuditSeverity.INFO, schedule_id=schedule_id)
        return schedule

    def disable_schedule(self, schedule_id: str) -> ScheduleDefinition:
        schedule = self.repository.get_schedule(schedule_id)
        schedule.status = ScheduleStatus.DISABLED
        schedule.updated_at = datetime.now(timezone.utc)
        schedule.next_run_at = None
        self.repository.save_schedule(schedule)

        self._audit("scheduler.schedule.disabled", AuditSeverity.WARNING, schedule_id=schedule_id)
        return schedule

    def due_schedules(self, now: Optional[datetime] = None) -> List[ScheduleDefinition]:
        now = now or datetime.now(timezone.utc)
        due: List[ScheduleDefinition] = []

        for schedule in self.repository.list_schedules(status=ScheduleStatus.ACTIVE):
            if not schedule.next_run_at:
                schedule.next_run_at = ScheduleCalculator.initial_next_run(schedule, now)
                self.repository.save_schedule(schedule)

            if schedule.next_run_at and schedule.next_run_at <= now:
                due.append(schedule)

        return due

    def tick(self, now: Optional[datetime] = None) -> List[ScheduleExecution]:
        now = now or datetime.now(timezone.utc)
        executions: List[ScheduleExecution] = []

        for schedule in self.due_schedules(now):
            executions.extend(self._process_due_schedule(schedule, now))

        return executions

    def trigger_now(
        self,
        schedule_id: str,
        context: Optional[ScheduleContext] = None,
    ) -> ScheduleExecution:
        schedule = self.repository.get_schedule(schedule_id)
        return self._execute_schedule(
            schedule=schedule,
            scheduled_for=datetime.now(timezone.utc),
            context=context,
            manual=True,
        )

    def run_loop(
        self,
        stop_event: threading.Event,
        tick_seconds: float = 1.0,
    ) -> None:
        self._audit(
            "scheduler.loop.started",
            AuditSeverity.INFO,
            details={"tick_seconds": tick_seconds},
        )

        while not stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                logger.exception("Erro no scheduler loop")
                self._audit(
                    "scheduler.loop.error",
                    AuditSeverity.ERROR,
                    details={
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )

            time.sleep(tick_seconds)

        self._audit("scheduler.loop.stopped", AuditSeverity.WARNING)

    def _process_due_schedule(
        self,
        schedule: ScheduleDefinition,
        now: datetime,
    ) -> List[ScheduleExecution]:
        if (
            not schedule.allow_concurrent_runs
            and self.repository.has_running_execution(schedule.schedule_id)
        ):
            return [
                self._mark_skipped(
                    schedule,
                    schedule.next_run_at or now,
                    "Execução concorrente não permitida",
                )
            ]

        scheduled_times = self._resolve_due_times(schedule, now)
        executions: List[ScheduleExecution] = []

        for scheduled_for in scheduled_times:
            executions.append(
                self._execute_schedule(
                    schedule=schedule,
                    scheduled_for=scheduled_for,
                    context=None,
                    manual=False,
                )
            )

        return executions

    def _resolve_due_times(
        self,
        schedule: ScheduleDefinition,
        now: datetime,
    ) -> List[datetime]:
        if not schedule.next_run_at:
            return []

        lateness = (now - schedule.next_run_at).total_seconds()

        if lateness > schedule.misfire_grace_seconds:
            if schedule.misfire_policy == MisfirePolicy.SKIP:
                self._mark_misfire(schedule, schedule.next_run_at)
                schedule.next_run_at = ScheduleCalculator.next_after(schedule, now)
                self.repository.save_schedule(schedule)
                return []

            if schedule.misfire_policy == MisfirePolicy.FIRE_ONCE:
                return [now]

            if schedule.misfire_policy == MisfirePolicy.FIRE_ALL:
                times: List[datetime] = []
                cursor = schedule.next_run_at

                while cursor and cursor <= now:
                    times.append(cursor)
                    cursor = ScheduleCalculator.next_after(schedule, cursor)

                return times

        return [schedule.next_run_at]

    def _execute_schedule(
        self,
        schedule: ScheduleDefinition,
        scheduled_for: datetime,
        context: Optional[ScheduleContext],
        manual: bool = False,
    ) -> ScheduleExecution:
        execution = ScheduleExecution(
            execution_id=str(uuid.uuid4()),
            schedule_id=schedule.schedule_id,
            scheduled_for=scheduled_for,
            status=ScheduleExecutionStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
            metadata={"manual": manual},
        )

        self.repository.save_execution(execution)

        context = context or ScheduleContext(
            scheduler_id=self.scheduler_id,
            tenant_id=schedule.tenant_id,
            domain=schedule.domain,
            correlation_id=str(uuid.uuid4()),
        )

        self._audit(
            "scheduler.execution.started",
            AuditSeverity.INFO,
            schedule_id=schedule.schedule_id,
            execution_id=execution.execution_id,
            details={
                "target_name": schedule.target_name,
                "scheduled_for": scheduled_for.isoformat(),
                "manual": manual,
            },
        )

        started = time.perf_counter()

        try:
            execution.result = self.executor.execute(schedule, execution, context)
            execution.status = ScheduleExecutionStatus.SUCCESS

        except Exception as exc:
            execution.status = ScheduleExecutionStatus.FAILED
            execution.error = str(exc)
            execution.traceback_text = traceback.format_exc()
            schedule.error_count += 1

        finally:
            execution.finished_at = datetime.now(timezone.utc)
            execution.duration_ms = (time.perf_counter() - started) * 1000

            schedule.last_run_at = execution.started_at
            schedule.run_count += 1

            if not manual:
                schedule.next_run_at = ScheduleCalculator.next_after(schedule, scheduled_for)

            if schedule.max_runs is not None and schedule.run_count >= schedule.max_runs:
                schedule.status = ScheduleStatus.COMPLETED
                schedule.next_run_at = None

            self.repository.save_execution(execution)
            self.repository.save_schedule(schedule)

            self.metrics_backend.increment(
                "scheduler.execution.total",
                tags={
                    "schedule_id": schedule.schedule_id,
                    "target_name": schedule.target_name,
                    "status": execution.status.value,
                },
            )

            self.metrics_backend.timing(
                "scheduler.execution.duration_ms",
                execution.duration_ms,
                tags={
                    "schedule_id": schedule.schedule_id,
                    "status": execution.status.value,
                },
            )

            self._audit(
                (
                    "scheduler.execution.succeeded"
                    if execution.status == ScheduleExecutionStatus.SUCCESS
                    else "scheduler.execution.failed"
                ),
                (
                    AuditSeverity.INFO
                    if execution.status == ScheduleExecutionStatus.SUCCESS
                    else AuditSeverity.ERROR
                ),
                schedule_id=schedule.schedule_id,
                execution_id=execution.execution_id,
                details={
                    "status": execution.status.value,
                    "duration_ms": execution.duration_ms,
                    "error": execution.error,
                    "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
                },
            )

        return execution

    def _mark_misfire(
        self,
        schedule: ScheduleDefinition,
        scheduled_for: datetime,
    ) -> ScheduleExecution:
        execution = ScheduleExecution(
            execution_id=str(uuid.uuid4()),
            schedule_id=schedule.schedule_id,
            scheduled_for=scheduled_for,
            status=ScheduleExecutionStatus.MISFIRED,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            duration_ms=0,
            error="Misfire grace exceeded",
        )

        self.repository.save_execution(execution)

        self._audit(
            "scheduler.execution.misfired",
            AuditSeverity.WARNING,
            schedule_id=schedule.schedule_id,
            execution_id=execution.execution_id,
            details={"scheduled_for": scheduled_for.isoformat()},
        )

        return execution

    def _mark_skipped(
        self,
        schedule: ScheduleDefinition,
        scheduled_for: datetime,
        reason: str,
    ) -> ScheduleExecution:
        execution = ScheduleExecution(
            execution_id=str(uuid.uuid4()),
            schedule_id=schedule.schedule_id,
            scheduled_for=scheduled_for,
            status=ScheduleExecutionStatus.SKIPPED,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            duration_ms=0,
            error=reason,
        )

        self.repository.save_execution(execution)

        self._audit(
            "scheduler.execution.skipped",
            AuditSeverity.WARNING,
            schedule_id=schedule.schedule_id,
            execution_id=execution.execution_id,
            details={"reason": reason},
        )

        return execution

    def list_schedules(
        self,
        status: Optional[ScheduleStatus] = None,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> List[ScheduleDefinition]:
        return self.repository.list_schedules(status=status, tenant_id=tenant_id, domain=domain)

    def list_executions(
        self,
        schedule_id: Optional[str] = None,
        status: Optional[ScheduleExecutionStatus] = None,
    ) -> List[ScheduleExecution]:
        return self.repository.list_executions(schedule_id=schedule_id, status=status)

    def export_schedule_json(self, schedule_id: str) -> str:
        schedule = self.repository.get_schedule(schedule_id)
        return json.dumps(
            self._schedule_to_dict(schedule),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def export_execution_json(self, execution_id: str) -> str:
        execution = self.repository.get_execution(execution_id)
        return json.dumps(
            self._execution_to_dict(execution),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def export_all_json(self) -> str:
        return json.dumps(
            {
                "scheduler_id": self.scheduler_id,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "schedules": [
                    self._schedule_to_dict(item)
                    for item in self.repository.list_schedules()
                ],
                "executions": [
                    self._execution_to_dict(item)
                    for item in self.repository.list_executions()
                ],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def _audit(
        self,
        event_type: str,
        severity: AuditSeverity,
        schedule_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "severity": severity.value,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "scheduler_id": self.scheduler_id,
                "schedule_id": schedule_id,
                "execution_id": execution_id,
                "details": details or {},
            }
        )

    @staticmethod
    def _schedule_to_dict(schedule: ScheduleDefinition) -> Dict[str, Any]:
        data = asdict(schedule)
        data["schedule_type"] = schedule.schedule_type.value
        data["status"] = schedule.status.value
        data["misfire_policy"] = schedule.misfire_policy.value
        data["created_at"] = schedule.created_at.isoformat()
        data["updated_at"] = schedule.updated_at.isoformat() if schedule.updated_at else None
        data["start_at"] = schedule.start_at.isoformat() if schedule.start_at else None
        data["end_at"] = schedule.end_at.isoformat() if schedule.end_at else None
        data["run_at"] = schedule.run_at.isoformat() if schedule.run_at else None
        data["last_run_at"] = schedule.last_run_at.isoformat() if schedule.last_run_at else None
        data["next_run_at"] = schedule.next_run_at.isoformat() if schedule.next_run_at else None
        return data

    @staticmethod
    def _execution_to_dict(execution: ScheduleExecution) -> Dict[str, Any]:
        data = asdict(execution)
        data["status"] = execution.status.value
        data["scheduled_for"] = execution.scheduled_for.isoformat()
        data["started_at"] = execution.started_at.isoformat() if execution.started_at else None
        data["finished_at"] = execution.finished_at.isoformat() if execution.finished_at else None
        return data


# =============================================================================
# Factory
# =============================================================================

def create_default_scheduler_engine() -> SchedulerEngine:
    executor = FunctionScheduleExecutor()

    def echo_job(
        schedule: ScheduleDefinition,
        execution: ScheduleExecution,
        context: ScheduleContext,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return {
            "schedule_id": schedule.schedule_id,
            "execution_id": execution.execution_id,
            "target_name": schedule.target_name,
            "payload": kwargs,
            "scheduler_id": context.scheduler_id,
            "tenant_id": context.tenant_id,
        }

    executor.register("echo_job", echo_job)

    return SchedulerEngine(
        scheduler_id="default-scheduler",
        executor=executor,
    )


# =============================================================================
# Compatibility Alias
# =============================================================================

ScheduleDefinitionAlias = ScheduleDefinition


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    scheduler = create_default_scheduler_engine()

    schedule = ScheduleDefinition(
        schedule_id="sales-refresh-every-5s",
        name="Sales Refresh Every 5 Seconds",
        schedule_type=ScheduleType.INTERVAL,
        target_name="echo_job",
        tenant_id="tenant-default",
        domain="sales",
        interval=IntervalSchedule(every_seconds=5),
        payload={"job": "refresh_sales_mart"},
        max_runs=2,
    )

    scheduler.register_schedule(schedule)

    executions = scheduler.tick(datetime.now(timezone.utc) + timedelta(seconds=1))

    for execution in executions:
        print(scheduler.export_execution_json(execution.execution_id))

    print(scheduler.export_schedule_json(schedule.schedule_id))


if __name__ == "__main__":
    example_usage()
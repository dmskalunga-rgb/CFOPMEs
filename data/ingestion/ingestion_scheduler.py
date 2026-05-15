#!/usr/bin/env python3
"""
data/ingestion/ingestion_scheduler.py

Enterprise-grade ingestion scheduler.

Objetivo:
- Agendar e executar jobs de ingestão com intervalos, cron simples e execução one-shot.
- Controlar retries, timeout, concorrência, histórico, estado, auditoria leve e métricas.
- Funcionar sem dependências externas obrigatórias.
- Integrar com handlers/pipelines de data.ingestion.

Uso:
    from data.ingestion.ingestion_scheduler import IngestionScheduler, ScheduledJob, IntervalSchedule

    scheduler = IngestionScheduler()

    def job_fn(ctx):
        return handler.ingest()

    scheduler.add_job(ScheduledJob(
        name="api_orders_ingestion",
        task=job_fn,
        schedule=IntervalSchedule(seconds=300),
    ))

    scheduler.start()

Notas:
- Para produção distribuída, usar lock externo: Redis/Postgres advisory lock/Kubernetes CronJob.
- Este módulo fornece scheduler local thread-safe e idempotente para um processo.
"""

from __future__ import annotations

import heapq
import logging
import threading
import time
import traceback
import uuid
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Deque, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple, Union

try:
    from data.ingestion import IngestionResult, IngestionStatus
except Exception:  # pragma: no cover
    class IngestionStatus(str, Enum):
        PROCESSED = "processed"
        FAILED = "failed"
        SKIPPED = "skipped"
        PARTIAL = "partial"

    @dataclass(frozen=True)
    class IngestionResult:  # type: ignore
        status: IngestionStatus
        accepted: int = 0
        processed: int = 0
        skipped: int = 0
        failed: int = 0
        errors: List[str] = field(default_factory=list)
        warnings: List[str] = field(default_factory=list)
        metadata: Dict[str, Any] = field(default_factory=dict)
        started_at: Optional[str] = None
        finished_at: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())

try:
    from data.ingestion.ingestion_metrics import get_ingestion_metrics
except Exception:  # pragma: no cover
    get_ingestion_metrics = None  # type: ignore


LOGGER = logging.getLogger(__name__)
SCHEDULER_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DISABLED = "disabled"
    TIMEOUT = "timeout"
    RETRYING = "retrying"


class MisfirePolicy(str, Enum):
    RUN_ONCE = "run_once"
    SKIP = "skip"
    RUN_ALL = "run_all"


class Schedule(Protocol):
    def next_run_after(self, after: datetime) -> Optional[datetime]:
        ...


@dataclass(frozen=True)
class IntervalSchedule:
    seconds: int
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None

    def next_run_after(self, after: datetime) -> Optional[datetime]:
        if self.seconds <= 0:
            raise SchedulerConfigError("IntervalSchedule.seconds precisa ser maior que zero")
        base = self.start_at or after
        if after < base:
            next_run = base
        else:
            elapsed = (after - base).total_seconds()
            increments = int(elapsed // self.seconds) + 1
            next_run = base + timedelta(seconds=increments * self.seconds)
        if self.end_at and next_run > self.end_at:
            return None
        return ensure_tz(next_run)


@dataclass(frozen=True)
class OneShotSchedule:
    run_at: datetime
    consumed: bool = False

    def next_run_after(self, after: datetime) -> Optional[datetime]:
        run_at = ensure_tz(self.run_at)
        if after < run_at:
            return run_at
        return None


@dataclass(frozen=True)
class DailyTimeSchedule:
    hour: int
    minute: int = 0
    second: int = 0
    timezone_name: str = "UTC"

    def next_run_after(self, after: datetime) -> Optional[datetime]:
        if not 0 <= self.hour <= 23:
            raise SchedulerConfigError("hour inválido")
        if not 0 <= self.minute <= 59:
            raise SchedulerConfigError("minute inválido")
        if not 0 <= self.second <= 59:
            raise SchedulerConfigError("second inválido")
        after = ensure_tz(after)
        candidate = after.replace(hour=self.hour, minute=self.minute, second=self.second, microsecond=0)
        if candidate <= after:
            candidate += timedelta(days=1)
        return candidate


@dataclass(frozen=True)
class CronSchedule:
    """Cron simples: minute hour day month weekday. Suporta *, número e listas com vírgula."""

    expression: str

    def next_run_after(self, after: datetime) -> Optional[datetime]:
        fields = self.expression.split()
        if len(fields) != 5:
            raise SchedulerConfigError("Cron deve ter 5 campos: minute hour day month weekday")
        minute_set = parse_cron_field(fields[0], 0, 59)
        hour_set = parse_cron_field(fields[1], 0, 23)
        day_set = parse_cron_field(fields[2], 1, 31)
        month_set = parse_cron_field(fields[3], 1, 12)
        weekday_set = parse_cron_field(fields[4], 0, 6)
        cursor = ensure_tz(after).replace(second=0, microsecond=0) + timedelta(minutes=1)
        max_cursor = cursor + timedelta(days=366)
        while cursor <= max_cursor:
            if (
                cursor.minute in minute_set
                and cursor.hour in hour_set
                and cursor.day in day_set
                and cursor.month in month_set
                and cursor.weekday() in weekday_set
            ):
                return cursor
            cursor += timedelta(minutes=1)
        return None


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: float = 2.0
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 60.0

    def delay_for_attempt(self, attempt: int) -> float:
        if attempt <= 1:
            return 0.0
        return min(self.backoff_seconds * (self.backoff_multiplier ** (attempt - 2)), self.max_backoff_seconds)


@dataclass(frozen=True)
class JobContext:
    job_id: str
    run_id: str
    name: str
    scheduled_at: str
    attempt: int
    metadata: Dict[str, Any] = field(default_factory=dict)


TaskCallable = Callable[[JobContext], Any]


@dataclass
class ScheduledJob:
    name: str
    task: TaskCallable
    schedule: Schedule
    job_id: str = field(default_factory=lambda: f"job_{uuid.uuid4().hex[:16]}")
    enabled: bool = True
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_seconds: Optional[float] = None
    max_concurrent_runs: int = 1
    misfire_policy: MisfirePolicy = MisfirePolicy.RUN_ONCE
    source: str = "scheduler"
    tenant_id: Optional[str] = None
    pipeline: str = "default"
    mode: str = "scheduled"
    metadata: Dict[str, Any] = field(default_factory=dict)
    next_run_at: Optional[datetime] = None
    running_count: int = 0
    total_runs: int = 0
    success_runs: int = 0
    failed_runs: int = 0
    last_status: JobStatus = JobStatus.PENDING
    last_run_at: Optional[str] = None
    last_error: Optional[str] = None

    def compute_next(self, after: Optional[datetime] = None) -> Optional[datetime]:
        after = ensure_tz(after or datetime.now(tz=DEFAULT_TIMEZONE))
        self.next_run_at = self.schedule.next_run_after(after)
        return self.next_run_at


@dataclass(frozen=True)
class JobRunRecord:
    run_id: str
    job_id: str
    name: str
    status: JobStatus
    scheduled_at: str
    started_at: str
    finished_at: str
    attempt: int
    latency_ms: float
    result_summary: Dict[str, Any]
    error: Optional[str] = None
    traceback_text: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "job_id": self.job_id,
            "name": self.name,
            "status": self.status.value,
            "scheduled_at": self.scheduled_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "attempt": self.attempt,
            "latency_ms": self.latency_ms,
            "result_summary": self.result_summary,
            "error": self.error,
            "traceback_text": self.traceback_text,
        }


@dataclass(frozen=True)
class SchedulerSnapshot:
    scheduler_id: str
    version: str
    running: bool
    created_at: str
    snapshot_at: str
    job_count: int
    enabled_jobs: int
    running_jobs: int
    totals: Dict[str, Any]
    jobs: List[Dict[str, Any]]
    recent_runs: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scheduler_id": self.scheduler_id,
            "version": self.version,
            "running": self.running,
            "created_at": self.created_at,
            "snapshot_at": self.snapshot_at,
            "job_count": self.job_count,
            "enabled_jobs": self.enabled_jobs,
            "running_jobs": self.running_jobs,
            "totals": self.totals,
            "jobs": self.jobs,
            "recent_runs": self.recent_runs,
        }


class SchedulerError(Exception):
    """Base scheduler error."""


class SchedulerConfigError(SchedulerError):
    """Invalid scheduler configuration."""


class JobNotFoundError(SchedulerError):
    """Job not found."""


class JobTimeoutError(SchedulerError):
    """Job execution timed out."""


class IngestionScheduler:
    def __init__(self, poll_interval_seconds: float = 1.0, max_history: int = 1000) -> None:
        self.scheduler_id = f"sch_{uuid.uuid4().hex[:16]}"
        self.poll_interval_seconds = poll_interval_seconds
        self.created_at = utc_now_iso()
        self._jobs: Dict[str, ScheduledJob] = {}
        self._heap: List[Tuple[float, str]] = []
        self._history: Deque[JobRunRecord] = deque(maxlen=max_history)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def add_job(self, job: ScheduledJob, replace: bool = False) -> ScheduledJob:
        validate_job(job)
        with self._lock:
            if job.job_id in self._jobs and not replace:
                raise SchedulerConfigError(f"Job já existe: {job.job_id}")
            job.compute_next(datetime.now(tz=DEFAULT_TIMEZONE) - timedelta(microseconds=1))
            self._jobs[job.job_id] = job
            self._push_job(job)
        return job

    def remove_job(self, job_id: str) -> None:
        with self._lock:
            if job_id not in self._jobs:
                raise JobNotFoundError(job_id)
            self._jobs.pop(job_id)
            self._rebuild_heap()

    def enable_job(self, job_id: str) -> None:
        with self._lock:
            job = self._get_job(job_id)
            job.enabled = True
            job.last_status = JobStatus.PENDING
            job.compute_next()
            self._push_job(job)

    def disable_job(self, job_id: str) -> None:
        with self._lock:
            job = self._get_job(job_id)
            job.enabled = False
            job.last_status = JobStatus.DISABLED
            self._rebuild_heap()

    def run_job_now(self, job_id: str) -> JobRunRecord:
        with self._lock:
            job = self._get_job(job_id)
        return self._execute_job(job, scheduled_at=datetime.now(tz=DEFAULT_TIMEZONE))

    def start(self, daemon: bool = True) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, name=f"IngestionScheduler-{self.scheduler_id}", daemon=daemon)
            self._thread.start()

    def stop(self, timeout_seconds: Optional[float] = 10.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout_seconds)
        with self._lock:
            self._running = False

    def list_jobs(self) -> List[ScheduledJob]:
        with self._lock:
            return list(self._jobs.values())

    def history(self, limit: int = 100) -> List[JobRunRecord]:
        with self._lock:
            return list(self._history)[-limit:]

    def snapshot(self) -> SchedulerSnapshot:
        with self._lock:
            jobs = [job_to_dict(job) for job in self._jobs.values()]
            history = list(self._history)
            status_counts = Counter(item.status.value for item in history)
            return SchedulerSnapshot(
                scheduler_id=self.scheduler_id,
                version=SCHEDULER_VERSION,
                running=self._running,
                created_at=self.created_at,
                snapshot_at=utc_now_iso(),
                job_count=len(self._jobs),
                enabled_jobs=sum(1 for job in self._jobs.values() if job.enabled),
                running_jobs=sum(job.running_count for job in self._jobs.values()),
                totals={
                    "run_count": len(history),
                    "status_counts": dict(status_counts),
                    "success_runs": status_counts.get(JobStatus.SUCCESS.value, 0),
                    "failed_runs": status_counts.get(JobStatus.FAILED.value, 0),
                    "timeout_runs": status_counts.get(JobStatus.TIMEOUT.value, 0),
                },
                jobs=jobs,
                recent_runs=[item.to_dict() for item in history[-50:]],
            )

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            due_jobs: List[ScheduledJob] = []
            now = datetime.now(tz=DEFAULT_TIMEZONE)
            with self._lock:
                while self._heap and self._heap[0][0] <= now.timestamp():
                    _, job_id = heapq.heappop(self._heap)
                    job = self._jobs.get(job_id)
                    if not job or not job.enabled or not job.next_run_at:
                        continue
                    if job.running_count >= job.max_concurrent_runs:
                        job.last_status = JobStatus.SKIPPED
                        job.compute_next(now)
                        self._push_job(job)
                        continue
                    due_jobs.append(job)
            for job in due_jobs:
                threading.Thread(target=self._execute_and_reschedule, args=(job,), daemon=True, name=f"IngestionJob-{job.name}").start()
            self._stop_event.wait(self.poll_interval_seconds)

    def _execute_and_reschedule(self, job: ScheduledJob) -> None:
        scheduled_at = job.next_run_at or datetime.now(tz=DEFAULT_TIMEZONE)
        self._execute_job(job, scheduled_at)
        with self._lock:
            if job.job_id in self._jobs and job.enabled:
                job.compute_next(datetime.now(tz=DEFAULT_TIMEZONE))
                self._push_job(job)

    def _execute_job(self, job: ScheduledJob, scheduled_at: datetime) -> JobRunRecord:
        with self._lock:
            if job.running_count >= job.max_concurrent_runs:
                record = self._record_skipped(job, scheduled_at, "max_concurrent_runs_reached")
                self._history.append(record)
                return record
            job.running_count += 1
            job.total_runs += 1
            job.last_status = JobStatus.RUNNING
            job.last_run_at = utc_now_iso()

        last_record: Optional[JobRunRecord] = None
        try:
            for attempt in range(1, job.retry_policy.max_attempts + 1):
                delay = job.retry_policy.delay_for_attempt(attempt)
                if delay > 0:
                    time.sleep(delay)
                record = self._run_attempt(job, scheduled_at, attempt)
                last_record = record
                if record.status == JobStatus.SUCCESS:
                    break
                if attempt < job.retry_policy.max_attempts:
                    with self._lock:
                        job.last_status = JobStatus.RETRYING
            assert last_record is not None
            return last_record
        finally:
            with self._lock:
                job.running_count = max(job.running_count - 1, 0)

    def _run_attempt(self, job: ScheduledJob, scheduled_at: datetime, attempt: int) -> JobRunRecord:
        run_id = f"run_{uuid.uuid4().hex[:20]}"
        started_perf = time.perf_counter()
        started_at = utc_now_iso()
        ctx = JobContext(job_id=job.job_id, run_id=run_id, name=job.name, scheduled_at=scheduled_at.isoformat(), attempt=attempt, metadata=sanitize_metadata(job.metadata))
        error: Optional[str] = None
        tb_text: Optional[str] = None
        result_summary: Dict[str, Any] = {}
        status = JobStatus.FAILED

        try:
            result = run_with_optional_timeout(job.task, ctx, job.timeout_seconds)
            result_summary = summarize_result(result)
            status = result_to_status(result)
        except JobTimeoutError as exc:
            status = JobStatus.TIMEOUT
            error = str(exc)
            tb_text = traceback.format_exc(limit=20)
        except Exception as exc:  # noqa: BLE001
            status = JobStatus.FAILED
            error = str(exc)
            tb_text = traceback.format_exc(limit=20)

        latency_ms = elapsed_ms(started_perf)
        record = JobRunRecord(
            run_id=run_id,
            job_id=job.job_id,
            name=job.name,
            status=status,
            scheduled_at=scheduled_at.isoformat(),
            started_at=started_at,
            finished_at=utc_now_iso(),
            attempt=attempt,
            latency_ms=latency_ms,
            result_summary=result_summary,
            error=error,
            traceback_text=tb_text,
        )

        with self._lock:
            job.last_status = status
            job.last_error = error
            if status == JobStatus.SUCCESS:
                job.success_runs += 1
            elif status in {JobStatus.FAILED, JobStatus.TIMEOUT}:
                job.failed_runs += 1
            self._history.append(record)

        record_metrics(job, record, result_summary)
        return record

    def _record_skipped(self, job: ScheduledJob, scheduled_at: datetime, reason: str) -> JobRunRecord:
        return JobRunRecord(
            run_id=f"run_{uuid.uuid4().hex[:20]}",
            job_id=job.job_id,
            name=job.name,
            status=JobStatus.SKIPPED,
            scheduled_at=scheduled_at.isoformat(),
            started_at=utc_now_iso(),
            finished_at=utc_now_iso(),
            attempt=0,
            latency_ms=0.0,
            result_summary={"reason": reason},
            error=reason,
        )

    def _push_job(self, job: ScheduledJob) -> None:
        if job.enabled and job.next_run_at:
            heapq.heappush(self._heap, (job.next_run_at.timestamp(), job.job_id))

    def _rebuild_heap(self) -> None:
        self._heap.clear()
        for job in self._jobs.values():
            self._push_job(job)
        heapq.heapify(self._heap)

    def _get_job(self, job_id: str) -> ScheduledJob:
        job = self._jobs.get(job_id)
        if not job:
            raise JobNotFoundError(job_id)
        return job


def run_with_optional_timeout(task: TaskCallable, ctx: JobContext, timeout_seconds: Optional[float]) -> Any:
    if timeout_seconds is None or timeout_seconds <= 0:
        return task(ctx)

    result_box: Dict[str, Any] = {}
    error_box: Dict[str, BaseException] = {}

    def target() -> None:
        try:
            result_box["result"] = task(ctx)
        except BaseException as exc:  # noqa: BLE001
            error_box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)
    if thread.is_alive():
        raise JobTimeoutError(f"Job excedeu timeout de {timeout_seconds}s")
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("result")


def result_to_status(result: Any) -> JobStatus:
    status = getattr(result, "status", None)
    status_value = getattr(status, "value", status)
    failed = int(getattr(result, "failed", 0) or 0)
    if failed > 0:
        return JobStatus.FAILED if status_value == "failed" else JobStatus.FAILED
    if status_value in {"processed", "accepted", "success"}:
        return JobStatus.SUCCESS
    if status_value in {"skipped"}:
        return JobStatus.SKIPPED
    if status_value in {"partial"}:
        return JobStatus.FAILED
    return JobStatus.SUCCESS if result is not None else JobStatus.SUCCESS


def summarize_result(result: Any) -> Dict[str, Any]:
    if result is None:
        return {"result": None}
    if hasattr(result, "to_dict"):
        try:
            return result.to_dict()
        except Exception:
            pass
    return {
        "status": getattr(getattr(result, "status", None), "value", getattr(result, "status", None)),
        "accepted": getattr(result, "accepted", None),
        "processed": getattr(result, "processed", None),
        "skipped": getattr(result, "skipped", None),
        "failed": getattr(result, "failed", None),
        "errors": getattr(result, "errors", None),
        "warnings": getattr(result, "warnings", None),
        "value": str(result)[:500] if not isinstance(result, IngestionResult) else None,
    }


def record_metrics(job: ScheduledJob, record: JobRunRecord, result_summary: Mapping[str, Any]) -> None:
    if get_ingestion_metrics is None:
        return
    try:
        metrics = get_ingestion_metrics()
        metrics.record_batch(
            source=job.source,
            tenant_id=job.tenant_id,
            pipeline=job.pipeline,
            mode=job.mode,
            accepted=int(result_summary.get("accepted") or 0),
            processed=int(result_summary.get("processed") or 0),
            skipped=int(result_summary.get("skipped") or 0),
            failed=int(result_summary.get("failed") or (1 if record.status in {JobStatus.FAILED, JobStatus.TIMEOUT} else 0)),
            latency_ms=record.latency_ms,
            status=record.status.value,
            error=record.error,
            metadata={"job_id": job.job_id, "run_id": record.run_id, "job_name": job.name},
        )
    except Exception:  # pragma: no cover
        LOGGER.debug("scheduler_metrics_record_failed", exc_info=True)


def validate_job(job: ScheduledJob) -> None:
    if not job.name or not job.name.strip():
        raise SchedulerConfigError("job.name é obrigatório")
    if job.max_concurrent_runs < 1:
        raise SchedulerConfigError("max_concurrent_runs precisa ser >= 1")
    if job.retry_policy.max_attempts < 1:
        raise SchedulerConfigError("retry_policy.max_attempts precisa ser >= 1")
    if job.timeout_seconds is not None and job.timeout_seconds <= 0:
        raise SchedulerConfigError("timeout_seconds precisa ser positivo")


def parse_cron_field(field: str, minimum: int, maximum: int) -> Set[int]:
    field = field.strip()
    if field == "*":
        return set(range(minimum, maximum + 1))
    values: Set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            base, step_text = part.split("/", 1)
            step = int(step_text)
            base_values = parse_cron_field(base or "*", minimum, maximum)
            values.update(value for value in base_values if (value - minimum) % step == 0)
        elif "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start < minimum or end > maximum or start > end:
                raise SchedulerConfigError(f"Campo cron fora do range: {field}")
            values.update(range(start, end + 1))
        else:
            value = int(part)
            if value < minimum or value > maximum:
                raise SchedulerConfigError(f"Campo cron fora do range: {field}")
            values.add(value)
    return values


def ensure_tz(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=DEFAULT_TIMEZONE)
    return value.astimezone(DEFAULT_TIMEZONE)


def job_to_dict(job: ScheduledJob) -> Dict[str, Any]:
    return {
        "job_id": job.job_id,
        "name": job.name,
        "enabled": job.enabled,
        "next_run_at": None if job.next_run_at is None else job.next_run_at.isoformat(),
        "running_count": job.running_count,
        "total_runs": job.total_runs,
        "success_runs": job.success_runs,
        "failed_runs": job.failed_runs,
        "last_status": job.last_status.value,
        "last_run_at": job.last_run_at,
        "last_error": job.last_error,
        "source": job.source,
        "tenant_id": job.tenant_id,
        "pipeline": job.pipeline,
        "mode": job.mode,
        "metadata": sanitize_metadata(job.metadata),
    }


def sanitize_metadata(metadata: Mapping[str, Any]) -> Dict[str, Any]:
    sensitive = {"password", "secret", "token", "api_key", "apikey", "authorization", "cookie"}
    result: Dict[str, Any] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if any(item in key_text.lower() for item in sensitive):
            result[key_text] = "[REDACTED]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key_text] = value
        else:
            result[key_text] = str(value)[:500]
    return result


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 4)


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()


_default_scheduler: Optional[IngestionScheduler] = None
_default_lock = threading.Lock()


def get_ingestion_scheduler() -> IngestionScheduler:
    global _default_scheduler
    with _default_lock:
        if _default_scheduler is None:
            _default_scheduler = IngestionScheduler()
        return _default_scheduler


def reset_ingestion_scheduler() -> None:
    global _default_scheduler
    with _default_lock:
        if _default_scheduler:
            _default_scheduler.stop(timeout_seconds=2)
        _default_scheduler = IngestionScheduler()


def scheduler_health() -> Dict[str, Any]:
    scheduler = get_ingestion_scheduler()
    snapshot = scheduler.snapshot()
    return {
        "status": "ok",
        "version": SCHEDULER_VERSION,
        "running": snapshot.running,
        "job_count": snapshot.job_count,
        "enabled_jobs": snapshot.enabled_jobs,
        "running_jobs": snapshot.running_jobs,
        "totals": snapshot.totals,
        "checked_at": utc_now_iso(),
    }


__all__ = [
    "SCHEDULER_VERSION",
    "JobStatus",
    "MisfirePolicy",
    "IntervalSchedule",
    "OneShotSchedule",
    "DailyTimeSchedule",
    "CronSchedule",
    "RetryPolicy",
    "JobContext",
    "ScheduledJob",
    "JobRunRecord",
    "SchedulerSnapshot",
    "SchedulerError",
    "SchedulerConfigError",
    "JobNotFoundError",
    "JobTimeoutError",
    "IngestionScheduler",
    "get_ingestion_scheduler",
    "reset_ingestion_scheduler",
    "scheduler_health",
]

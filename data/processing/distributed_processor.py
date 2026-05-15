"""
data/processing/distributed_processor.py

Enterprise-grade distributed processing orchestrator for data platforms.

Purpose
-------
Provides a dependency-light orchestration layer for distributed or parallel data
processing workloads. It supports local threaded execution out of the box and
provides clean backend contracts for plugging in remote executors such as Spark,
Ray, Dask, Celery, Kubernetes jobs, serverless functions or internal worker
fleets.

Core capabilities
-----------------
- Partition planning for records, ranges, hashes and custom partitioners.
- Local thread backend for immediate execution without third-party dependencies.
- Backend protocol for external distributed runtimes.
- Task lifecycle, retries, backoff, timeout and cancellation.
- Checkpointing and resumable job execution.
- Dead-letter records for failed partitions/tasks.
- Result aggregation and JSON reports.
- Worker heartbeat model and health summary.
- Optional telemetry integration.
- Safe metadata sanitization.
- Standard library only.

Example
-------
processor = DistributedProcessor()

result = processor.run(
    records,
    process_fn=lambda partition: [item * 2 for item in partition],
    partitioning=PartitioningSpec(strategy=PartitionStrategy.CHUNK, partition_size=1000),
)
print(result.to_json())
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import dataclasses
import hashlib
import json
import logging
import os
import queue
import random
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Generic, Iterable, Iterator, List, Mapping, Optional, Protocol, Sequence, Tuple, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|session|jwt|bearer)",
    re.IGNORECASE,
)

MAX_TEXT_LENGTH = 16_384
DEFAULT_PARTITION_SIZE = 10_000
DEFAULT_MAX_WORKERS = 4
DEFAULT_MAX_RETRIES = 3


class PartitionStrategy(str, Enum):
    CHUNK = "chunk"
    HASH = "hash"
    RANGE = "range"
    ROUND_ROBIN = "round_robin"
    CUSTOM = "custom"


class DistributedBackendType(str, Enum):
    LOCAL_THREADS = "local_threads"
    CUSTOM = "custom"
    SPARK = "spark"
    RAY = "ray"
    DASK = "dask"
    CELERY = "celery"
    KUBERNETES = "kubernetes"
    SERVERLESS = "serverless"


class TaskStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"


class JobStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FailurePolicy(str, Enum):
    CONTINUE = "continue"
    FAIL_FAST = "fail_fast"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = DEFAULT_MAX_RETRIES
    initial_delay_seconds: float = 0.5
    max_delay_seconds: float = 60.0
    backoff_multiplier: float = 2.0
    jitter_seconds: float = 0.25

    def delay_for_attempt(self, attempt: int) -> float:
        base = min(self.max_delay_seconds, self.initial_delay_seconds * (self.backoff_multiplier ** max(0, attempt - 1)))
        return max(0.0, base + random.uniform(0, self.jitter_seconds))


@dataclass(frozen=True)
class PartitioningSpec:
    strategy: PartitionStrategy = PartitionStrategy.CHUNK
    partition_size: int = DEFAULT_PARTITION_SIZE
    partition_count: Optional[int] = None
    key_field: Optional[str] = None
    range_field: Optional[str] = None
    custom_partitioner: Optional[Callable[[Any], int]] = None
    preserve_order: bool = True

    def validate(self) -> None:
        if self.partition_size <= 0:
            raise DistributedConfigError("partition_size must be positive")
        if self.partition_count is not None and self.partition_count <= 0:
            raise DistributedConfigError("partition_count must be positive")
        if self.strategy == PartitionStrategy.HASH and not self.key_field:
            raise DistributedConfigError("HASH partitioning requires key_field")
        if self.strategy == PartitionStrategy.RANGE and not self.range_field:
            raise DistributedConfigError("RANGE partitioning requires range_field")
        if self.strategy == PartitionStrategy.CUSTOM and not self.custom_partitioner:
            raise DistributedConfigError("CUSTOM partitioning requires custom_partitioner")


@dataclass(frozen=True)
class DistributedProcessorConfig:
    backend_type: DistributedBackendType = DistributedBackendType.LOCAL_THREADS
    max_workers: int = DEFAULT_MAX_WORKERS
    task_timeout_seconds: Optional[float] = None
    failure_policy: FailurePolicy = FailurePolicy.DEAD_LETTER
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    checkpoint_path: Optional[str] = None
    dead_letter_path: Optional[str] = None
    report_path: Optional[str] = None
    telemetry_enabled: bool = True
    include_outputs: bool = True
    max_output_items: int = 1_000_000
    job_name: str = "distributed_processor"
    heartbeat_interval_seconds: int = 30

    @classmethod
    def from_env(cls) -> "DistributedProcessorConfig":
        return cls(
            backend_type=DistributedBackendType(os.getenv("DISTRIBUTED_BACKEND_TYPE", DistributedBackendType.LOCAL_THREADS.value)),
            max_workers=int_env("DISTRIBUTED_MAX_WORKERS", DEFAULT_MAX_WORKERS),
            task_timeout_seconds=float_env_optional("DISTRIBUTED_TASK_TIMEOUT_SECONDS"),
            failure_policy=FailurePolicy(os.getenv("DISTRIBUTED_FAILURE_POLICY", FailurePolicy.DEAD_LETTER.value)),
            retry_policy=RetryPolicy(
                max_retries=int_env("DISTRIBUTED_MAX_RETRIES", DEFAULT_MAX_RETRIES),
                initial_delay_seconds=float_env("DISTRIBUTED_RETRY_INITIAL_DELAY", 0.5),
                max_delay_seconds=float_env("DISTRIBUTED_RETRY_MAX_DELAY", 60.0),
                backoff_multiplier=float_env("DISTRIBUTED_RETRY_BACKOFF", 2.0),
                jitter_seconds=float_env("DISTRIBUTED_RETRY_JITTER", 0.25),
            ),
            checkpoint_path=os.getenv("DISTRIBUTED_CHECKPOINT_PATH"),
            dead_letter_path=os.getenv("DISTRIBUTED_DEAD_LETTER_PATH"),
            report_path=os.getenv("DISTRIBUTED_REPORT_PATH"),
            telemetry_enabled=bool_env("DISTRIBUTED_TELEMETRY_ENABLED", True),
            include_outputs=bool_env("DISTRIBUTED_INCLUDE_OUTPUTS", True),
            max_output_items=int_env("DISTRIBUTED_MAX_OUTPUT_ITEMS", 1_000_000),
            job_name=os.getenv("DISTRIBUTED_JOB_NAME", "distributed_processor"),
            heartbeat_interval_seconds=int_env("DISTRIBUTED_HEARTBEAT_INTERVAL_SECONDS", 30),
        )


@dataclass(frozen=True)
class Partition(Generic[T]):
    id: str
    index: int
    records: List[T]
    key: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.records)

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping({
            "id": self.id,
            "index": self.index,
            "size": self.size,
            "key": self.key,
            "metadata": self.metadata,
        })


@dataclass(frozen=True)
class DistributedTask(Generic[T]):
    id: str
    job_id: str
    partition: Partition[T]
    attempt: int = 0
    status: TaskStatus = TaskStatus.PENDING
    submitted_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    worker_id: Optional[str] = None
    error: Optional[str] = None

    def with_status(self, status: TaskStatus, **updates: Any) -> "DistributedTask[T]":
        data = asdict(self)
        data["status"] = status
        data.update(updates)
        return DistributedTask(**data)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["partition"] = self.partition.to_dict()
        return sanitize_mapping(data)


@dataclass(frozen=True)
class TaskResult(Generic[R]):
    task_id: str
    partition_id: str
    partition_index: int
    status: TaskStatus
    output: Optional[R] = None
    output_count: int = 0
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    attempts: int = 1
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return sanitize_mapping(data)


@dataclass(frozen=True)
class WorkerHeartbeat:
    worker_id: str
    backend_type: DistributedBackendType
    status: str
    active_tasks: int
    completed_tasks: int
    failed_tasks: int
    last_seen_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["backend_type"] = self.backend_type.value
        return sanitize_mapping(data)


@dataclass(frozen=True)
class DistributedCheckpoint:
    job_id: str
    job_name: str
    updated_at: str
    completed_partition_ids: List[str] = field(default_factory=list)
    failed_partition_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))

    @classmethod
    def empty(cls, job_id: str, job_name: str) -> "DistributedCheckpoint":
        return cls(job_id=job_id, job_name=job_name, updated_at=utc_now_iso())


@dataclass(frozen=True)
class DistributedResult(Generic[R]):
    job_id: str
    job_name: str
    status: JobStatus
    started_at: str
    finished_at: str
    duration_ms: float
    input_count: int
    partition_count: int
    success_count: int
    failure_count: int
    skipped_count: int
    output_count: int
    backend_type: DistributedBackendType
    outputs: List[R] = field(default_factory=list)
    task_results: List[TaskResult[Any]] = field(default_factory=list)
    heartbeats: List[WorkerHeartbeat] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["backend_type"] = self.backend_type.value
        data["task_results"] = [item.to_dict() for item in self.task_results]
        data["heartbeats"] = [item.to_dict() for item in self.heartbeats]
        return sanitize_mapping(data)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True, default=safe_json_default)


class DistributedBackend(Protocol):
    backend_type: DistributedBackendType

    def submit(
        self,
        tasks: Sequence[DistributedTask[Any]],
        process_fn: Callable[[List[Any]], Any],
        config: DistributedProcessorConfig,
    ) -> Sequence[TaskResult[Any]]:
        ...

    def cancel(self, job_id: str) -> None:
        ...

    def heartbeats(self) -> Sequence[WorkerHeartbeat]:
        ...


class DistributedError(Exception):
    """Base distributed processor error."""


class DistributedConfigError(DistributedError):
    """Invalid distributed processor configuration."""


class DistributedExecutionError(DistributedError):
    """Distributed task execution failed."""


class DistributedCancelledError(DistributedError):
    """Distributed job was cancelled."""


class DistributedTimeoutError(DistributedError):
    """Distributed task timed out."""


class DeadLetterWriter:
    def __init__(self, path: Optional[str]) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.RLock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, result: TaskResult[Any], task: Optional[DistributedTask[Any]] = None) -> None:
        if not self.path:
            return
        payload = {
            "timestamp": utc_now_iso(),
            "task_result": result.to_dict(),
            "task": task.to_dict() if task else None,
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=safe_json_default) + "\n")


class CheckpointStore:
    def __init__(self, path: Optional[str]) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.RLock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self, job_id: str, job_name: str) -> DistributedCheckpoint:
        if not self.path or not self.path.exists():
            return DistributedCheckpoint.empty(job_id, job_name)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("job_id") != job_id:
                return DistributedCheckpoint.empty(job_id, job_name)
            return DistributedCheckpoint(
                job_id=str(payload.get("job_id", job_id)),
                job_name=str(payload.get("job_name", job_name)),
                updated_at=str(payload.get("updated_at", utc_now_iso())),
                completed_partition_ids=[str(x) for x in payload.get("completed_partition_ids", [])],
                failed_partition_ids=[str(x) for x in payload.get("failed_partition_ids", [])],
                metadata=dict(payload.get("metadata", {})),
            )
        except Exception:
            logger.warning("Could not load distributed checkpoint from %s", self.path, exc_info=True)
            return DistributedCheckpoint.empty(job_id, job_name)

    def save(self, checkpoint: DistributedCheckpoint) -> None:
        if not self.path:
            return
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with self._lock:
            tmp.write_text(json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2, sort_keys=True, default=safe_json_default), encoding="utf-8")
            tmp.replace(self.path)


class LocalThreadBackend:
    """Default backend using ThreadPoolExecutor."""

    backend_type = DistributedBackendType.LOCAL_THREADS

    def __init__(self) -> None:
        self._cancelled_jobs: set[str] = set()
        self._lock = threading.RLock()
        self._completed = 0
        self._failed = 0
        self._active = 0
        self.worker_id = f"local-thread-{uuid.uuid4()}"

    def submit(
        self,
        tasks: Sequence[DistributedTask[Any]],
        process_fn: Callable[[List[Any]], Any],
        config: DistributedProcessorConfig,
    ) -> Sequence[TaskResult[Any]]:
        results: List[TaskResult[Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=config.max_workers, thread_name_prefix="distributed-worker") as executor:
            future_to_task = {
                executor.submit(self._execute_with_retry, task, process_fn, config): task
                for task in tasks
            }
            for future in concurrent.futures.as_completed(future_to_task, timeout=None):
                task = future_to_task[future]
                if self._is_cancelled(task.job_id):
                    results.append(task_result_from_error(task, DistributedCancelledError("Job cancelled"), TaskStatus.CANCELLED, attempts=task.attempt + 1))
                    continue
                try:
                    result = future.result(timeout=config.task_timeout_seconds)
                except concurrent.futures.TimeoutError as exc:
                    result = task_result_from_error(task, DistributedTimeoutError(str(exc)), TaskStatus.TIMED_OUT, attempts=task.attempt + 1)
                except Exception as exc:
                    result = task_result_from_error(task, exc, TaskStatus.FAILED, attempts=task.attempt + 1)
                results.append(result)
        return results

    def cancel(self, job_id: str) -> None:
        with self._lock:
            self._cancelled_jobs.add(job_id)

    def heartbeats(self) -> Sequence[WorkerHeartbeat]:
        with self._lock:
            return [
                WorkerHeartbeat(
                    worker_id=self.worker_id,
                    backend_type=self.backend_type,
                    status="running",
                    active_tasks=self._active,
                    completed_tasks=self._completed,
                    failed_tasks=self._failed,
                    last_seen_at=utc_now_iso(),
                )
            ]

    def _execute_with_retry(self, task: DistributedTask[Any], process_fn: Callable[[List[Any]], Any], config: DistributedProcessorConfig) -> TaskResult[Any]:
        attempts = 0
        last_exc: Optional[BaseException] = None
        while attempts <= config.retry_policy.max_retries:
            attempts += 1
            started_perf = time.perf_counter()
            started_iso = utc_now_iso()
            with self._lock:
                self._active += 1
            try:
                if self._is_cancelled(task.job_id):
                    raise DistributedCancelledError("Job cancelled")
                output = process_fn(task.partition.records)
                output_count = len(output) if isinstance(output, list) else 1 if output is not None else 0
                finished_iso = utc_now_iso()
                duration_ms = (time.perf_counter() - started_perf) * 1000.0
                with self._lock:
                    self._completed += 1
                return TaskResult(
                    task_id=task.id,
                    partition_id=task.partition.id,
                    partition_index=task.partition.index,
                    status=TaskStatus.SUCCEEDED,
                    output=output,
                    output_count=output_count,
                    attempts=attempts,
                    started_at=started_iso,
                    finished_at=finished_iso,
                    duration_ms=round(duration_ms, 3),
                    metadata={"worker_id": self.worker_id},
                )
            except Exception as exc:
                last_exc = exc
                with self._lock:
                    self._failed += 1
                if attempts > config.retry_policy.max_retries:
                    break
                time.sleep(config.retry_policy.delay_for_attempt(attempts))
            finally:
                with self._lock:
                    self._active = max(0, self._active - 1)
        assert last_exc is not None
        return task_result_from_error(task, last_exc, TaskStatus.FAILED, attempts=attempts)

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelled_jobs


class DistributedProcessor(Generic[T, R]):
    """Enterprise distributed processing orchestrator."""

    def __init__(self, config: Optional[DistributedProcessorConfig] = None, backend: Optional[DistributedBackend] = None) -> None:
        self.config = config or DistributedProcessorConfig.from_env()
        self.backend = backend or self._build_backend(self.config)
        self.dead_letter = DeadLetterWriter(self.config.dead_letter_path)
        self.checkpoints = CheckpointStore(self.config.checkpoint_path)
        self._cancel_requested = threading.Event()

    @staticmethod
    def _build_backend(config: DistributedProcessorConfig) -> DistributedBackend:
        if config.backend_type == DistributedBackendType.LOCAL_THREADS:
            return LocalThreadBackend()
        raise DistributedConfigError(
            f"Backend {config.backend_type.value} requires a custom DistributedBackend implementation"
        )

    def cancel(self) -> None:
        self._cancel_requested.set()

    def run(
        self,
        records: Iterable[T],
        *,
        process_fn: Callable[[List[T]], Any],
        partitioning: Optional[PartitioningSpec] = None,
        job_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> DistributedResult[R]:
        partitioning = partitioning or PartitioningSpec()
        partitioning.validate()
        job_id = job_id or stable_job_id(self.config.job_name, metadata or {})
        started_perf = time.perf_counter()
        started_iso = utc_now_iso()
        self._cancel_requested.clear()

        with telemetry_operation("distributed_processor.run", self.config.telemetry_enabled, attributes={"job_id": job_id, "job_name": self.config.job_name}):
            checkpoint = self.checkpoints.load(job_id, self.config.job_name)
            completed_partition_ids = set(checkpoint.completed_partition_ids)
            failed_partition_ids = set(checkpoint.failed_partition_ids)
            partitions = plan_partitions(list(records), partitioning)
            tasks = [
                DistributedTask(id=str(uuid.uuid4()), job_id=job_id, partition=partition)
                for partition in partitions
                if partition.id not in completed_partition_ids
            ]
            skipped_count = len(partitions) - len(tasks)

            if self._cancel_requested.is_set():
                self.backend.cancel(job_id)
                return self._build_cancelled_result(job_id, started_iso, started_perf, partitions, skipped_count, metadata)

            try:
                task_results = list(self.backend.submit(tasks, process_fn, self.config))
            except Exception:
                logger.exception("Distributed backend failed job_id=%s", job_id)
                if self.config.failure_policy == FailurePolicy.FAIL_FAST:
                    raise
                task_results = [task_result_from_error(task, DistributedExecutionError("backend submit failed"), TaskStatus.FAILED) for task in tasks]

            outputs: List[Any] = []
            success_count = 0
            failure_count = 0
            for task, result in zip(tasks, task_results):
                if result.status == TaskStatus.SUCCEEDED:
                    success_count += 1
                    completed_partition_ids.add(result.partition_id)
                    append_output(outputs, result.output)
                else:
                    failure_count += 1
                    failed_partition_ids.add(result.partition_id)
                    self.dead_letter.write(result, task)
                    if self.config.failure_policy == FailurePolicy.FAIL_FAST:
                        raise DistributedExecutionError(result.error_message or "task failed")

            self.checkpoints.save(
                DistributedCheckpoint(
                    job_id=job_id,
                    job_name=self.config.job_name,
                    updated_at=utc_now_iso(),
                    completed_partition_ids=sorted(completed_partition_ids),
                    failed_partition_ids=sorted(failed_partition_ids),
                    metadata=sanitize_mapping(dict(metadata or {})),
                )
            )

            if not self.config.include_outputs:
                outputs = []
            elif len(outputs) > self.config.max_output_items:
                outputs = outputs[: self.config.max_output_items]

            status = determine_job_status(success_count, failure_count, len(tasks), skipped_count)
            result = DistributedResult[R](
                job_id=job_id,
                job_name=self.config.job_name,
                status=status,
                started_at=started_iso,
                finished_at=utc_now_iso(),
                duration_ms=round((time.perf_counter() - started_perf) * 1000.0, 3),
                input_count=sum(partition.size for partition in partitions),
                partition_count=len(partitions),
                success_count=success_count,
                failure_count=failure_count,
                skipped_count=skipped_count,
                output_count=len(outputs),
                backend_type=self.backend.backend_type,
                outputs=outputs,
                task_results=task_results,
                heartbeats=list(self.backend.heartbeats()),
                metadata=sanitize_mapping(dict(metadata or {})),
            )
            self._save_report(result)
            telemetry_metric("distributed_processor.input_count", result.input_count, self.config.telemetry_enabled)
            telemetry_metric("distributed_processor.partition_count", result.partition_count, self.config.telemetry_enabled)
            telemetry_metric("distributed_processor.failure_count", result.failure_count, self.config.telemetry_enabled)
            telemetry_metric("distributed_processor.duration_ms", result.duration_ms, self.config.telemetry_enabled)
            return result

    def _build_cancelled_result(
        self,
        job_id: str,
        started_iso: str,
        started_perf: float,
        partitions: Sequence[Partition[Any]],
        skipped_count: int,
        metadata: Optional[Mapping[str, Any]],
    ) -> DistributedResult[R]:
        return DistributedResult(
            job_id=job_id,
            job_name=self.config.job_name,
            status=JobStatus.CANCELLED,
            started_at=started_iso,
            finished_at=utc_now_iso(),
            duration_ms=round((time.perf_counter() - started_perf) * 1000.0, 3),
            input_count=sum(p.size for p in partitions),
            partition_count=len(partitions),
            success_count=0,
            failure_count=0,
            skipped_count=skipped_count,
            output_count=0,
            backend_type=self.backend.backend_type,
            outputs=[],
            task_results=[],
            heartbeats=list(self.backend.heartbeats()),
            metadata=sanitize_mapping(dict(metadata or {})),
        )

    def _save_report(self, result: DistributedResult[Any]) -> None:
        if not self.config.report_path:
            return
        target = Path(self.config.report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(result.to_json(indent=2), encoding="utf-8")
        tmp.replace(target)


def plan_partitions(records: Sequence[T], spec: PartitioningSpec) -> List[Partition[T]]:
    if not records:
        return []
    if spec.strategy == PartitionStrategy.CHUNK:
        return [
            Partition(id=partition_id(index, chunk), index=index, records=chunk, key=f"chunk-{index}")
            for index, chunk in enumerate(chunked(records, spec.partition_size))
        ]
    if spec.strategy == PartitionStrategy.ROUND_ROBIN:
        count = spec.partition_count or max(1, min(len(records), os.cpu_count() or DEFAULT_MAX_WORKERS))
        buckets: List[List[T]] = [[] for _ in range(count)]
        for idx, record in enumerate(records):
            buckets[idx % count].append(record)
        return [Partition(id=partition_id(i, bucket), index=i, records=bucket, key=f"rr-{i}") for i, bucket in enumerate(buckets) if bucket]
    if spec.strategy == PartitionStrategy.HASH:
        count = spec.partition_count or max(1, math_ceil(len(records) / spec.partition_size))
        buckets = [[] for _ in range(count)]
        for record in records:
            value = get_field(to_mapping(record), spec.key_field or "")
            bucket_index = int(hashlib.sha256(str(value).encode("utf-8")).hexdigest(), 16) % count
            buckets[bucket_index].append(record)
        return [Partition(id=partition_id(i, bucket), index=i, records=bucket, key=f"hash-{i}") for i, bucket in enumerate(buckets) if bucket]
    if spec.strategy == PartitionStrategy.RANGE:
        sorted_records = sorted(records, key=lambda item: get_field(to_mapping(item), spec.range_field or ""))
        return [
            Partition(id=partition_id(index, chunk), index=index, records=chunk, key=f"range-{index}")
            for index, chunk in enumerate(chunked(sorted_records, spec.partition_size))
        ]
    if spec.strategy == PartitionStrategy.CUSTOM and spec.custom_partitioner:
        buckets_map: Dict[int, List[T]] = {}
        for record in records:
            bucket_index = int(spec.custom_partitioner(record))
            buckets_map.setdefault(bucket_index, []).append(record)
        return [
            Partition(id=partition_id(index, bucket), index=index, records=bucket, key=f"custom-{index}")
            for index, bucket in sorted(buckets_map.items())
        ]
    return []


def chunked(records: Sequence[T], size: int) -> Iterator[List[T]]:
    for index in range(0, len(records), size):
        yield list(records[index:index + size])


def partition_id(index: int, records: Sequence[Any]) -> str:
    raw = json.dumps({"index": index, "size": len(records), "sample": sanitize_value(records[0]) if records else None}, ensure_ascii=False, sort_keys=True, default=safe_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def task_result_from_error(task: DistributedTask[Any], exc: BaseException, status: TaskStatus, attempts: int = 1) -> TaskResult[Any]:
    return TaskResult(
        task_id=task.id,
        partition_id=task.partition.id,
        partition_index=task.partition.index,
        status=status,
        error_type=exc.__class__.__name__,
        error_message=str(exc),
        attempts=attempts,
        finished_at=utc_now_iso(),
    )


def append_output(outputs: List[Any], output: Any) -> None:
    if output is None:
        return
    if isinstance(output, list):
        outputs.extend(output)
    else:
        outputs.append(output)


def determine_job_status(success_count: int, failure_count: int, task_count: int, skipped_count: int) -> JobStatus:
    if failure_count and success_count == 0:
        return JobStatus.FAILED
    if failure_count or skipped_count:
        return JobStatus.PARTIALLY_SUCCEEDED
    return JobStatus.SUCCEEDED if task_count >= 0 else JobStatus.FAILED


def stable_job_id(job_name: str, metadata: Mapping[str, Any]) -> str:
    raw = json.dumps({"job_name": job_name, "metadata": sanitize_mapping(metadata)}, ensure_ascii=False, sort_keys=True, default=safe_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def math_ceil(value: float) -> int:
    import math
    return int(math.ceil(value))


def to_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if dataclasses.is_dataclass(value):
        return asdict(value)
    if hasattr(value, "_asdict"):
        return value._asdict()
    if hasattr(value, "__dict__"):
        return vars(value)
    return {"value": value}


def get_field(row: Mapping[str, Any], field_path: str) -> Any:
    current: Any = row
    for part in field_path.split(".") if field_path else []:
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def sanitize_mapping(values: Mapping[str, Any], *, depth: int = 0) -> Dict[str, Any]:
    if depth > 6:
        return {"_truncated": "max_depth_exceeded"}
    result: Dict[str, Any] = {}
    for key, value in values.items():
        key_str = str(key)
        if SENSITIVE_KEY_PATTERN.search(key_str):
            result[key_str] = "[REDACTED]"
        elif isinstance(value, Mapping):
            result[key_str] = sanitize_mapping(value, depth=depth + 1)
        elif isinstance(value, (list, tuple, set)):
            result[key_str] = [sanitize_value(item, depth=depth + 1) for item in list(value)[:10_000]]
        else:
            result[key_str] = sanitize_value(value, depth=depth)
    return result


def sanitize_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return sanitize_mapping(asdict(value), depth=depth + 1)
    if isinstance(value, Mapping):
        return sanitize_mapping(value, depth=depth + 1)
    if isinstance(value, (list, tuple, set)):
        return [sanitize_value(item, depth=depth + 1) for item in list(value)[:10_000]]
    text = str(value)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)=([^\s&]+)", r"\1=[REDACTED]", text)
    if len(text) > MAX_TEXT_LENGTH:
        text = text[: MAX_TEXT_LENGTH - 15] + "...[truncated]"
    return text


@contextlib.contextmanager
def telemetry_operation(name: str, enabled: bool, attributes: Optional[Mapping[str, Any]] = None) -> Iterator[None]:
    if not enabled:
        yield
        return
    try:
        from data.observability.telemetry import get_telemetry
        telemetry = get_telemetry()
        with telemetry.operation(name, attributes=attributes):
            yield
    except Exception:
        yield


def telemetry_metric(name: str, value: float, enabled: bool) -> None:
    if not enabled:
        return
    try:
        from data.observability.telemetry import get_telemetry
        get_telemetry().gauge(name, value)
    except Exception:
        logger.debug("Distributed processor telemetry metric failed", exc_info=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return asdict(value)
    if isinstance(value, (set, tuple)):
        return list(value)
    return str(value)


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def float_env_optional(name: str) -> Optional[float]:
    raw = os.getenv(name)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "CheckpointStore",
    "DeadLetterWriter",
    "DistributedBackend",
    "DistributedBackendType",
    "DistributedCancelledError",
    "DistributedCheckpoint",
    "DistributedConfigError",
    "DistributedError",
    "DistributedExecutionError",
    "DistributedProcessor",
    "DistributedProcessorConfig",
    "DistributedResult",
    "DistributedTask",
    "DistributedTimeoutError",
    "FailurePolicy",
    "JobStatus",
    "LocalThreadBackend",
    "Partition",
    "PartitionStrategy",
    "PartitioningSpec",
    "RetryPolicy",
    "TaskResult",
    "TaskStatus",
    "WorkerHeartbeat",
    "plan_partitions",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    processor: DistributedProcessor[int, int] = DistributedProcessor(
        DistributedProcessorConfig(max_workers=4, telemetry_enabled=False, include_outputs=True)
    )
    result = processor.run(
        list(range(100)),
        process_fn=lambda part: [x * 2 for x in part],
        partitioning=PartitioningSpec(strategy=PartitionStrategy.CHUNK, partition_size=10),
        metadata={"example": True},
    )
    print(result.to_json())

"""
data/processing/batch_processor.py

Enterprise-grade batch processor for data platforms.

Purpose
-------
Provides a robust batch execution engine for ETL/ELT jobs, file processing,
API backfills, database extracts, data quality routines, transformations,
aggregations and AI/data enrichment workloads.

Core capabilities
-----------------
- Chunked batch processing with configurable batch size.
- Sync processor functions with optional parallel execution.
- Retry with exponential backoff and jitter.
- Checkpointing for resumable execution.
- Dead-letter handling for failed records/chunks.
- Hooks for lifecycle events.
- Validation callbacks for input/output.
- Per-record or per-chunk processing modes.
- Result summaries and JSON reports.
- Optional telemetry integration.
- Safe metadata sanitization.
- Standard library only.

Example
-------
processor = BatchProcessor(BatchProcessorConfig(batch_size=500))

result = processor.run(
    records,
    process_fn=lambda row: {**row, "processed": True},
    record_id_fn=lambda row: row["id"],
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
DEFAULT_BATCH_SIZE = 10_000
DEFAULT_MAX_RETRIES = 3


class BatchProcessingMode(str, Enum):
    RECORD = "record"
    CHUNK = "chunk"


class BatchStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FailurePolicy(str, Enum):
    CONTINUE = "continue"
    FAIL_FAST = "fail_fast"
    DEAD_LETTER = "dead_letter"


class CheckpointMode(str, Enum):
    DISABLED = "disabled"
    CHUNK = "chunk"
    RECORD = "record"


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = DEFAULT_MAX_RETRIES
    initial_delay_seconds: float = 0.5
    max_delay_seconds: float = 30.0
    backoff_multiplier: float = 2.0
    jitter_seconds: float = 0.2
    retry_exceptions: Tuple[type[BaseException], ...] = (Exception,)

    def delay_for_attempt(self, attempt: int) -> float:
        base = min(self.max_delay_seconds, self.initial_delay_seconds * (self.backoff_multiplier ** max(0, attempt - 1)))
        return max(0.0, base + random.uniform(0, self.jitter_seconds))


@dataclass(frozen=True)
class BatchProcessorConfig:
    batch_size: int = DEFAULT_BATCH_SIZE
    mode: BatchProcessingMode = BatchProcessingMode.RECORD
    failure_policy: FailurePolicy = FailurePolicy.DEAD_LETTER
    checkpoint_mode: CheckpointMode = CheckpointMode.CHUNK
    checkpoint_path: Optional[str] = None
    dead_letter_path: Optional[str] = None
    report_path: Optional[str] = None
    max_workers: int = 1
    preserve_order: bool = True
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    telemetry_enabled: bool = True
    validate_input: bool = True
    validate_output: bool = False
    include_outputs: bool = True
    max_output_items: int = 100_000
    job_name: str = "batch_processor"

    @classmethod
    def from_env(cls) -> "BatchProcessorConfig":
        return cls(
            batch_size=int_env("BATCH_PROCESSOR_BATCH_SIZE", DEFAULT_BATCH_SIZE),
            mode=BatchProcessingMode(os.getenv("BATCH_PROCESSOR_MODE", BatchProcessingMode.RECORD.value)),
            failure_policy=FailurePolicy(os.getenv("BATCH_PROCESSOR_FAILURE_POLICY", FailurePolicy.DEAD_LETTER.value)),
            checkpoint_mode=CheckpointMode(os.getenv("BATCH_PROCESSOR_CHECKPOINT_MODE", CheckpointMode.CHUNK.value)),
            checkpoint_path=os.getenv("BATCH_PROCESSOR_CHECKPOINT_PATH"),
            dead_letter_path=os.getenv("BATCH_PROCESSOR_DEAD_LETTER_PATH"),
            report_path=os.getenv("BATCH_PROCESSOR_REPORT_PATH"),
            max_workers=int_env("BATCH_PROCESSOR_MAX_WORKERS", 1),
            preserve_order=bool_env("BATCH_PROCESSOR_PRESERVE_ORDER", True),
            retry_policy=RetryPolicy(
                max_retries=int_env("BATCH_PROCESSOR_MAX_RETRIES", DEFAULT_MAX_RETRIES),
                initial_delay_seconds=float_env("BATCH_PROCESSOR_RETRY_INITIAL_DELAY", 0.5),
                max_delay_seconds=float_env("BATCH_PROCESSOR_RETRY_MAX_DELAY", 30.0),
                backoff_multiplier=float_env("BATCH_PROCESSOR_RETRY_BACKOFF", 2.0),
                jitter_seconds=float_env("BATCH_PROCESSOR_RETRY_JITTER", 0.2),
            ),
            telemetry_enabled=bool_env("BATCH_PROCESSOR_TELEMETRY_ENABLED", True),
            validate_input=bool_env("BATCH_PROCESSOR_VALIDATE_INPUT", True),
            validate_output=bool_env("BATCH_PROCESSOR_VALIDATE_OUTPUT", False),
            include_outputs=bool_env("BATCH_PROCESSOR_INCLUDE_OUTPUTS", True),
            max_output_items=int_env("BATCH_PROCESSOR_MAX_OUTPUT_ITEMS", 100_000),
            job_name=os.getenv("BATCH_PROCESSOR_JOB_NAME", "batch_processor"),
        )


@dataclass(frozen=True)
class ProcessingErrorRecord:
    id: str
    timestamp: str
    job_id: str
    chunk_index: int
    record_index: Optional[int]
    record_id: Optional[str]
    error_type: str
    error_message: str
    attempts: int
    record: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))


@dataclass(frozen=True)
class BatchCheckpoint:
    job_id: str
    job_name: str
    updated_at: str
    completed_chunks: List[int] = field(default_factory=list)
    completed_record_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))

    @classmethod
    def empty(cls, job_id: str, job_name: str) -> "BatchCheckpoint":
        return cls(job_id=job_id, job_name=job_name, updated_at=utc_now_iso())


@dataclass(frozen=True)
class BatchResult(Generic[R]):
    job_id: str
    job_name: str
    status: BatchStatus
    started_at: str
    finished_at: str
    duration_ms: float
    input_count: int
    output_count: int
    success_count: int
    failure_count: int
    skipped_count: int
    chunk_count: int
    failed_chunks: List[int]
    outputs: List[R] = field(default_factory=list)
    errors: List[ProcessingErrorRecord] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return sanitize_mapping(data)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True, default=safe_json_default)


class BatchHook(Protocol):
    def on_start(self, job_id: str, metadata: Mapping[str, Any]) -> None: ...
    def on_chunk_start(self, job_id: str, chunk_index: int, chunk_size: int) -> None: ...
    def on_chunk_success(self, job_id: str, chunk_index: int, output_count: int) -> None: ...
    def on_chunk_failure(self, job_id: str, chunk_index: int, error: BaseException) -> None: ...
    def on_finish(self, result: BatchResult[Any]) -> None: ...


class NoopBatchHook:
    def on_start(self, job_id: str, metadata: Mapping[str, Any]) -> None: return None
    def on_chunk_start(self, job_id: str, chunk_index: int, chunk_size: int) -> None: return None
    def on_chunk_success(self, job_id: str, chunk_index: int, output_count: int) -> None: return None
    def on_chunk_failure(self, job_id: str, chunk_index: int, error: BaseException) -> None: return None
    def on_finish(self, result: BatchResult[Any]) -> None: return None


class BatchProcessorError(Exception):
    """Base batch processor error."""


class BatchValidationError(BatchProcessorError):
    """Input or output validation failed."""


class BatchExecutionError(BatchProcessorError):
    """Batch execution failed."""


class BatchCancelledError(BatchProcessorError):
    """Batch execution was cancelled."""


class DeadLetterWriter:
    """JSONL dead-letter writer."""

    def __init__(self, path: Optional[str]) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.RLock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, error: ProcessingErrorRecord) -> None:
        if not self.path:
            return
        line = json.dumps(error.to_dict(), ensure_ascii=False, sort_keys=True, default=safe_json_default)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")


class CheckpointStore:
    """JSON checkpoint store."""

    def __init__(self, path: Optional[str]) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.RLock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self, job_id: str, job_name: str) -> BatchCheckpoint:
        if not self.path or not self.path.exists():
            return BatchCheckpoint.empty(job_id, job_name)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("job_id") != job_id:
                return BatchCheckpoint.empty(job_id, job_name)
            return BatchCheckpoint(
                job_id=str(payload.get("job_id", job_id)),
                job_name=str(payload.get("job_name", job_name)),
                updated_at=str(payload.get("updated_at", utc_now_iso())),
                completed_chunks=[int(x) for x in payload.get("completed_chunks", [])],
                completed_record_ids=[str(x) for x in payload.get("completed_record_ids", [])],
                metadata=dict(payload.get("metadata", {})),
            )
        except Exception:
            logger.warning("Could not load checkpoint from %s", self.path, exc_info=True)
            return BatchCheckpoint.empty(job_id, job_name)

    def save(self, checkpoint: BatchCheckpoint) -> None:
        if not self.path:
            return
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with self._lock:
            tmp.write_text(json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2, sort_keys=True, default=safe_json_default), encoding="utf-8")
            tmp.replace(self.path)


class BatchProcessor(Generic[T, R]):
    """Enterprise batch processor."""

    def __init__(self, config: Optional[BatchProcessorConfig] = None, hooks: Optional[Sequence[BatchHook]] = None) -> None:
        self.config = config or BatchProcessorConfig.from_env()
        self.hooks = list(hooks or [NoopBatchHook()])
        self.dead_letter = DeadLetterWriter(self.config.dead_letter_path)
        self.checkpoints = CheckpointStore(self.config.checkpoint_path)
        self._cancel_requested = threading.Event()

    def cancel(self) -> None:
        self._cancel_requested.set()

    def run(
        self,
        records: Iterable[T],
        *,
        process_fn: Callable[[Any], Any],
        record_id_fn: Optional[Callable[[T], str]] = None,
        input_validator: Optional[Callable[[T], bool]] = None,
        output_validator: Optional[Callable[[Any], bool]] = None,
        job_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> BatchResult[R]:
        job_id = job_id or stable_job_id(self.config.job_name, metadata or {})
        started = time.perf_counter()
        started_iso = utc_now_iso()
        checkpoint = self.checkpoints.load(job_id, self.config.job_name)
        completed_chunks = set(checkpoint.completed_chunks)
        completed_record_ids = set(checkpoint.completed_record_ids)

        outputs: List[R] = []
        errors: List[ProcessingErrorRecord] = []
        failed_chunks: List[int] = []
        input_count = 0
        success_count = 0
        skipped_count = 0
        chunk_count = 0

        self._cancel_requested.clear()
        self._notify_start(job_id, metadata or {})

        try:
            with telemetry_operation("batch_processor.run", self.config.telemetry_enabled, attributes={"job_id": job_id, "job_name": self.config.job_name}):
                chunks = enumerate(chunked(records, self.config.batch_size))
                if self.config.max_workers > 1:
                    # Parallel mode processes chunks concurrently. Checkpoints are saved after each completed chunk.
                    with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
                        futures: Dict[concurrent.futures.Future[ChunkOutcome[R]], int] = {}
                        for chunk_index, chunk in chunks:
                            chunk_count += 1
                            input_count += len(chunk)
                            if self._should_skip_chunk(chunk_index, chunk, record_id_fn, completed_chunks, completed_record_ids):
                                skipped_count += len(chunk)
                                continue
                            self._ensure_not_cancelled()
                            self._notify_chunk_start(job_id, chunk_index, len(chunk))
                            futures[executor.submit(self._process_chunk, job_id, chunk_index, chunk, process_fn, record_id_fn, input_validator, output_validator)] = chunk_index
                        for future in concurrent.futures.as_completed(futures):
                            outcome = future.result()
                            outputs.extend(outcome.outputs)
                            errors.extend(outcome.errors)
                            success_count += outcome.success_count
                            if outcome.errors:
                                failed_chunks.append(outcome.chunk_index)
                            self._after_chunk(job_id, outcome, completed_chunks, completed_record_ids)
                else:
                    for chunk_index, chunk in chunks:
                        chunk_count += 1
                        input_count += len(chunk)
                        if self._should_skip_chunk(chunk_index, chunk, record_id_fn, completed_chunks, completed_record_ids):
                            skipped_count += len(chunk)
                            continue
                        self._ensure_not_cancelled()
                        self._notify_chunk_start(job_id, chunk_index, len(chunk))
                        outcome = self._process_chunk(job_id, chunk_index, chunk, process_fn, record_id_fn, input_validator, output_validator)
                        outputs.extend(outcome.outputs)
                        errors.extend(outcome.errors)
                        success_count += outcome.success_count
                        if outcome.errors:
                            failed_chunks.append(chunk_index)
                        self._after_chunk(job_id, outcome, completed_chunks, completed_record_ids)
                        if outcome.errors and self.config.failure_policy == FailurePolicy.FAIL_FAST:
                            raise BatchExecutionError(f"Chunk {chunk_index} failed")
        except BatchCancelledError:
            status = BatchStatus.CANCELLED
        except Exception:
            status = BatchStatus.FAILED
            logger.exception("Batch processing failed job_id=%s", job_id)
            if self.config.failure_policy == FailurePolicy.FAIL_FAST:
                raise
        else:
            status = determine_status(errors, success_count, input_count, skipped_count)

        if not self.config.include_outputs:
            outputs = []
        elif len(outputs) > self.config.max_output_items:
            outputs = outputs[: self.config.max_output_items]

        finished_iso = utc_now_iso()
        duration_ms = (time.perf_counter() - started) * 1000.0
        result: BatchResult[R] = BatchResult(
            job_id=job_id,
            job_name=self.config.job_name,
            status=status,
            started_at=started_iso,
            finished_at=finished_iso,
            duration_ms=round(duration_ms, 3),
            input_count=input_count,
            output_count=len(outputs),
            success_count=success_count,
            failure_count=len(errors),
            skipped_count=skipped_count,
            chunk_count=chunk_count,
            failed_chunks=failed_chunks,
            outputs=outputs,
            errors=errors,
            metadata=sanitize_mapping(dict(metadata or {})),
        )
        self._notify_finish(result)
        self._save_report(result)
        telemetry_metric("batch_processor.input_count", input_count, self.config.telemetry_enabled)
        telemetry_metric("batch_processor.success_count", success_count, self.config.telemetry_enabled)
        telemetry_metric("batch_processor.failure_count", len(errors), self.config.telemetry_enabled)
        telemetry_metric("batch_processor.duration_ms", duration_ms, self.config.telemetry_enabled)
        return result

    def _process_chunk(
        self,
        job_id: str,
        chunk_index: int,
        chunk: List[T],
        process_fn: Callable[[Any], Any],
        record_id_fn: Optional[Callable[[T], str]],
        input_validator: Optional[Callable[[T], bool]],
        output_validator: Optional[Callable[[Any], bool]],
    ) -> "ChunkOutcome[R]":
        errors: List[ProcessingErrorRecord] = []
        outputs: List[R] = []
        success_count = 0
        try:
            if self.config.mode == BatchProcessingMode.CHUNK:
                output = self._with_retry(lambda: process_fn(chunk))
                if output is None:
                    output_items: List[Any] = []
                elif isinstance(output, list):
                    output_items = output
                else:
                    output_items = [output]
                for item in output_items:
                    self._validate_output(item, output_validator)
                    outputs.append(item)
                success_count = len(chunk)
            else:
                for record_index, record in enumerate(chunk):
                    record_id = safe_record_id(record, record_id_fn)
                    try:
                        self._validate_input(record, input_validator)
                        output = self._with_retry(lambda r=record: process_fn(r))
                        self._validate_output(output, output_validator)
                        if output is not None:
                            outputs.append(output)
                        success_count += 1
                    except Exception as exc:
                        error = self._build_error(job_id, chunk_index, record_index, record_id, exc, record)
                        errors.append(error)
                        self.dead_letter.write(error)
                        if self.config.failure_policy == FailurePolicy.FAIL_FAST:
                            raise
        except Exception as exc:
            self._notify_chunk_failure(job_id, chunk_index, exc)
            if not errors:
                error = self._build_error(job_id, chunk_index, None, None, exc, chunk)
                errors.append(error)
                self.dead_letter.write(error)
        return ChunkOutcome(chunk_index=chunk_index, outputs=outputs, errors=errors, success_count=success_count, input_records=chunk)

    def _with_retry(self, fn: Callable[[], Any]) -> Any:
        attempts = 0
        last_exc: Optional[BaseException] = None
        while attempts <= self.config.retry_policy.max_retries:
            try:
                return fn()
            except self.config.retry_policy.retry_exceptions as exc:
                attempts += 1
                last_exc = exc
                if attempts > self.config.retry_policy.max_retries:
                    break
                time.sleep(self.config.retry_policy.delay_for_attempt(attempts))
        if last_exc:
            raise last_exc
        return None

    def _validate_input(self, record: T, validator: Optional[Callable[[T], bool]]) -> None:
        if not self.config.validate_input or validator is None:
            return
        if not validator(record):
            raise BatchValidationError("Input validation failed")

    def _validate_output(self, output: Any, validator: Optional[Callable[[Any], bool]]) -> None:
        if not self.config.validate_output or validator is None:
            return
        if not validator(output):
            raise BatchValidationError("Output validation failed")

    def _should_skip_chunk(
        self,
        chunk_index: int,
        chunk: List[T],
        record_id_fn: Optional[Callable[[T], str]],
        completed_chunks: set[int],
        completed_record_ids: set[str],
    ) -> bool:
        if self.config.checkpoint_mode == CheckpointMode.DISABLED:
            return False
        if self.config.checkpoint_mode == CheckpointMode.CHUNK:
            return chunk_index in completed_chunks
        if self.config.checkpoint_mode == CheckpointMode.RECORD and record_id_fn:
            return all(safe_record_id(record, record_id_fn) in completed_record_ids for record in chunk)
        return False

    def _after_chunk(self, job_id: str, outcome: "ChunkOutcome[R]", completed_chunks: set[int], completed_record_ids: set[str]) -> None:
        if outcome.errors:
            self._notify_chunk_failure(job_id, outcome.chunk_index, BatchExecutionError(f"Chunk had {len(outcome.errors)} errors"))
        else:
            self._notify_chunk_success(job_id, outcome.chunk_index, len(outcome.outputs))
        if self.config.checkpoint_mode == CheckpointMode.CHUNK and not outcome.errors:
            completed_chunks.add(outcome.chunk_index)
        elif self.config.checkpoint_mode == CheckpointMode.RECORD:
            for record in outcome.input_records:
                completed_record_ids.add(stable_hash(record))
        checkpoint = BatchCheckpoint(
            job_id=job_id,
            job_name=self.config.job_name,
            updated_at=utc_now_iso(),
            completed_chunks=sorted(completed_chunks),
            completed_record_ids=sorted(completed_record_ids),
        )
        self.checkpoints.save(checkpoint)

    def _build_error(self, job_id: str, chunk_index: int, record_index: Optional[int], record_id: Optional[str], exc: BaseException, record: Any) -> ProcessingErrorRecord:
        return ProcessingErrorRecord(
            id=str(uuid.uuid4()),
            timestamp=utc_now_iso(),
            job_id=job_id,
            chunk_index=chunk_index,
            record_index=record_index,
            record_id=record_id,
            error_type=exc.__class__.__name__,
            error_message=str(exc),
            attempts=self.config.retry_policy.max_retries + 1,
            record=sanitize_value(record),
        )

    def _ensure_not_cancelled(self) -> None:
        if self._cancel_requested.is_set():
            raise BatchCancelledError("Batch processing cancelled")

    def _save_report(self, result: BatchResult[Any]) -> None:
        if not self.config.report_path:
            return
        target = Path(self.config.report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(result.to_json(indent=2), encoding="utf-8")
        tmp.replace(target)

    def _notify_start(self, job_id: str, metadata: Mapping[str, Any]) -> None:
        for hook in self.hooks:
            safe_hook(lambda h=hook: h.on_start(job_id, metadata))

    def _notify_chunk_start(self, job_id: str, chunk_index: int, chunk_size: int) -> None:
        for hook in self.hooks:
            safe_hook(lambda h=hook: h.on_chunk_start(job_id, chunk_index, chunk_size))

    def _notify_chunk_success(self, job_id: str, chunk_index: int, output_count: int) -> None:
        for hook in self.hooks:
            safe_hook(lambda h=hook: h.on_chunk_success(job_id, chunk_index, output_count))

    def _notify_chunk_failure(self, job_id: str, chunk_index: int, error: BaseException) -> None:
        for hook in self.hooks:
            safe_hook(lambda h=hook: h.on_chunk_failure(job_id, chunk_index, error))

    def _notify_finish(self, result: BatchResult[Any]) -> None:
        for hook in self.hooks:
            safe_hook(lambda h=hook: h.on_finish(result))


@dataclass(frozen=True)
class ChunkOutcome(Generic[R]):
    chunk_index: int
    outputs: List[R]
    errors: List[ProcessingErrorRecord]
    success_count: int
    input_records: List[Any]


def chunked(records: Iterable[T], batch_size: int) -> Iterator[List[T]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    chunk: List[T] = []
    for record in records:
        chunk.append(record)
        if len(chunk) >= batch_size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def determine_status(errors: Sequence[ProcessingErrorRecord], success_count: int, input_count: int, skipped_count: int) -> BatchStatus:
    if errors and success_count == 0:
        return BatchStatus.FAILED
    if errors or skipped_count:
        return BatchStatus.PARTIALLY_SUCCEEDED
    return BatchStatus.SUCCEEDED if input_count > 0 else BatchStatus.SUCCEEDED


def safe_record_id(record: Any, record_id_fn: Optional[Callable[[Any], str]]) -> str:
    if record_id_fn:
        try:
            return str(record_id_fn(record))
        except Exception:
            pass
    return stable_hash(record)


def stable_job_id(job_name: str, metadata: Mapping[str, Any]) -> str:
    raw = json.dumps({"job_name": job_name, "metadata": sanitize_mapping(metadata)}, ensure_ascii=False, sort_keys=True, default=safe_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def stable_hash(value: Any) -> str:
    raw = json.dumps(sanitize_value(value), ensure_ascii=False, sort_keys=True, default=safe_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
    if isinstance(value, Mapping):
        return sanitize_mapping(value, depth=depth + 1)
    if dataclasses.is_dataclass(value):
        return sanitize_mapping(asdict(value), depth=depth + 1)
    if isinstance(value, (list, tuple, set)):
        return [sanitize_value(item, depth=depth + 1) for item in list(value)[:10_000]]
    text = str(value)
    if len(text) > MAX_TEXT_LENGTH:
        return text[: MAX_TEXT_LENGTH - 15] + "...[truncated]"
    return text


def safe_hook(fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception:
        logger.debug("Batch hook failed", exc_info=True)


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
        logger.debug("Batch telemetry metric failed", exc_info=True)


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


def bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "BatchCancelledError",
    "BatchCheckpoint",
    "BatchExecutionError",
    "BatchHook",
    "BatchProcessingMode",
    "BatchProcessor",
    "BatchProcessorConfig",
    "BatchProcessorError",
    "BatchResult",
    "BatchStatus",
    "BatchValidationError",
    "CheckpointMode",
    "CheckpointStore",
    "ChunkOutcome",
    "DeadLetterWriter",
    "FailurePolicy",
    "NoopBatchHook",
    "ProcessingErrorRecord",
    "RetryPolicy",
    "chunked",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    records = [{"id": i, "value": i * 2} for i in range(25)]
    processor: BatchProcessor[Dict[str, Any], Dict[str, Any]] = BatchProcessor(
        BatchProcessorConfig(batch_size=10, max_workers=1, validate_output=True, telemetry_enabled=False)
    )
    result = processor.run(
        records,
        process_fn=lambda row: {**row, "processed": True},
        record_id_fn=lambda row: str(row["id"]),
        output_validator=lambda row: row.get("processed") is True,
        metadata={"example": True},
    )
    print(result.to_json())

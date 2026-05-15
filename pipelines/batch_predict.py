"""
kwanza-ai-core/pipelines/batch_predict.py

Enterprise-grade batch prediction pipeline.

Purpose
-------
Run large-scale batch predictions using the Kwanza AI Core PredictionService.
It supports local JSON/JSONL/CSV datasets, chunked processing, async concurrency,
checkpointing, retries, resumability, structured outputs and operational metrics.

Typical usage
-------------
python -m kwanza_ai_core.pipelines.batch_predict \
  --input data/input.jsonl \
  --output data/predictions.jsonl \
  --task generic \
  --tenant-id tenant-ao \
  --batch-size 256 \
  --concurrency 4

Notes
-----
- This module is framework-agnostic and can be used from CLI, Airflow, Prefect,
  Celery, Kubernetes CronJobs or internal orchestration services.
- It assumes `services.prediction_service` exists in the project. If imported as
  part of a package, adjust the fallback import section according to your package
  name.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import logging
import os
import signal
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple

try:
    from kwanza_ai_core.services.prediction_service import (
        BatchPredictionRequest,
        BatchPredictionResult,
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
            BatchPredictionRequest,
            BatchPredictionResult,
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

logger = logging.getLogger("kwanza_ai_core.pipelines.batch_predict")

JsonDict = Dict[str, Any]


# =============================================================================
# Exceptions
# =============================================================================


class BatchPredictPipelineError(RuntimeError):
    """Base exception for batch prediction pipeline failures."""


class BatchPredictValidationError(BatchPredictPipelineError):
    """Raised when input configuration or data is invalid."""


# =============================================================================
# Enums / models
# =============================================================================


class InputFormat(str, Enum):
    AUTO = "auto"
    JSON = "json"
    JSONL = "jsonl"
    CSV = "csv"


class OutputFormat(str, Enum):
    AUTO = "auto"
    JSONL = "jsonl"
    JSON = "json"
    CSV = "csv"


class PipelineStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class BatchPredictConfig:
    input_path: Path
    output_path: Path
    tenant_id: Optional[str] = None
    task: PredictionTask = PredictionTask.GENERIC
    model_name: Optional[str] = None
    model_version: Optional[str] = None
    input_format: InputFormat = InputFormat.AUTO
    output_format: OutputFormat = OutputFormat.AUTO
    batch_size: int = 256
    concurrency: int = 4
    max_retries: int = 3
    retry_base_delay_ms: int = 150
    checkpoint_path: Optional[Path] = None
    dead_letter_path: Optional[Path] = None
    id_field: Optional[str] = None
    features_field: Optional[str] = None
    include_input: bool = False
    explain: bool = False
    use_cache: bool = True
    confidence_level: float = 0.95
    aggregation_strategy: AggregationStrategy = AggregationStrategy.SINGLE
    output_type: PredictionOutputType = PredictionOutputType.STRUCTURED
    fail_fast: bool = False
    resume: bool = True
    dry_run: bool = False
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def validate(self) -> None:
        if not self.input_path.exists():
            raise BatchPredictValidationError(f"Input file not found: {self.input_path}")
        if self.batch_size <= 0:
            raise BatchPredictValidationError("batch_size must be positive.")
        if self.concurrency <= 0:
            raise BatchPredictValidationError("concurrency must be positive.")
        if self.max_retries < 0:
            raise BatchPredictValidationError("max_retries cannot be negative.")
        if not 0 < self.confidence_level < 1:
            raise BatchPredictValidationError("confidence_level must be between 0 and 1.")


@dataclass(frozen=True)
class InputRecord:
    row_number: int
    record_id: str
    features: Mapping[str, Any]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class OutputRecord:
    run_id: str
    row_number: int
    record_id: str
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

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass
class PipelineCheckpoint:
    run_id: str
    input_hash: str
    completed_rows: set[int] = field(default_factory=set)
    failed_rows: set[int] = field(default_factory=set)
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> JsonDict:
        return {
            "run_id": self.run_id,
            "input_hash": self.input_hash,
            "completed_rows": sorted(self.completed_rows),
            "failed_rows": sorted(self.failed_rows),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PipelineCheckpoint":
        return cls(
            run_id=str(payload["run_id"]),
            input_hash=str(payload["input_hash"]),
            completed_rows=set(int(x) for x in payload.get("completed_rows", [])),
            failed_rows=set(int(x) for x in payload.get("failed_rows", [])),
            updated_at=str(payload.get("updated_at") or datetime.now(UTC).isoformat()),
        )


@dataclass(frozen=True)
class PipelineSummary:
    run_id: str
    status: PipelineStatus
    input_path: str
    output_path: str
    total_rows: int
    processed_rows: int
    succeeded_rows: int
    failed_rows: int
    skipped_rows: int
    batches: int
    started_at: str
    completed_at: str
    processing_ms: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


# =============================================================================
# Metrics / audit light contracts
# =============================================================================


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
# Utility functions
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def infer_input_format(path: Path, configured: InputFormat) -> InputFormat:
    if configured != InputFormat.AUTO:
        return configured
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return InputFormat.JSONL
    if suffix == ".json":
        return InputFormat.JSON
    if suffix == ".csv":
        return InputFormat.CSV
    raise BatchPredictValidationError(f"Cannot infer input format from extension: {path.suffix}")


def infer_output_format(path: Path, configured: OutputFormat) -> OutputFormat:
    if configured != OutputFormat.AUTO:
        return configured
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return OutputFormat.JSONL
    if suffix == ".json":
        return OutputFormat.JSON
    if suffix == ".csv":
        return OutputFormat.CSV
    return OutputFormat.JSONL


def chunks(items: Sequence[InputRecord], size: int) -> Iterable[Sequence[InputRecord]]:
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


def flatten_record(record: Mapping[str, Any], prefix: str = "") -> Dict[str, Any]:
    output: Dict[str, Any] = {}
    for key, value in record.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            output.update(flatten_record(value, name))
        elif isinstance(value, list):
            output[name] = json.dumps(value, ensure_ascii=False, default=str)
        else:
            output[name] = value
    return output


# =============================================================================
# Readers / Writers
# =============================================================================


class BatchInputReader:
    def __init__(self, config: BatchPredictConfig) -> None:
        self.config = config
        self.input_format = infer_input_format(config.input_path, config.input_format)

    def read_all(self) -> List[InputRecord]:
        if self.input_format == InputFormat.JSONL:
            rows = self._read_jsonl()
        elif self.input_format == InputFormat.JSON:
            rows = self._read_json()
        elif self.input_format == InputFormat.CSV:
            rows = self._read_csv()
        else:
            raise BatchPredictValidationError(f"Unsupported input format: {self.input_format}")
        return [self._to_input_record(idx + 1, row) for idx, row in enumerate(rows)]

    def _read_jsonl(self) -> List[Mapping[str, Any]]:
        rows: List[Mapping[str, Any]] = []
        with self.config.input_path.open("r", encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise BatchPredictValidationError(f"Invalid JSONL at line {line_number}: {exc}") from exc
                if not isinstance(row, Mapping):
                    raise BatchPredictValidationError(f"JSONL line {line_number} must be an object.")
                rows.append(row)
        return rows

    def _read_json(self) -> List[Mapping[str, Any]]:
        payload = json.loads(self.config.input_path.read_text(encoding="utf-8"))
        if isinstance(payload, Mapping):
            if "rows" in payload and isinstance(payload["rows"], list):
                payload = payload["rows"]
            else:
                payload = [payload]
        if not isinstance(payload, list):
            raise BatchPredictValidationError("JSON input must be an object, list, or object with a 'rows' list.")
        if not all(isinstance(row, Mapping) for row in payload):
            raise BatchPredictValidationError("All JSON rows must be objects.")
        return payload

    def _read_csv(self) -> List[Mapping[str, Any]]:
        with self.config.input_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            return [dict(row) for row in reader]

    def _to_input_record(self, row_number: int, row: Mapping[str, Any]) -> InputRecord:
        if self.config.features_field:
            features = row.get(self.config.features_field)
            if not isinstance(features, Mapping):
                raise BatchPredictValidationError(
                    f"Row {row_number}: features_field '{self.config.features_field}' must contain an object."
                )
        else:
            features = dict(row)

        record_id = None
        if self.config.id_field:
            value = row.get(self.config.id_field)
            record_id = str(value) if value is not None else None
        record_id = record_id or stable_hash({"row_number": row_number, "row": row})[:24]
        return InputRecord(row_number=row_number, record_id=record_id, features=dict(features), raw=dict(row))


class BatchOutputWriter:
    def __init__(self, config: BatchPredictConfig) -> None:
        self.config = config
        self.output_format = infer_output_format(config.output_path, config.output_format)
        self.config.output_path.parent.mkdir(parents=True, exist_ok=True)
        if config.dead_letter_path:
            config.dead_letter_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._json_records: List[Mapping[str, Any]] = []
        self._csv_header_written = config.output_path.exists() and config.output_path.stat().st_size > 0
        self._dead_letter_header_written = bool(
            config.dead_letter_path and config.dead_letter_path.exists() and config.dead_letter_path.stat().st_size > 0
        )

    async def write_records(self, records: Sequence[OutputRecord]) -> None:
        async with self._lock:
            if self.output_format == OutputFormat.JSONL:
                with self.config.output_path.open("a", encoding="utf-8") as fh:
                    for record in records:
                        fh.write(json.dumps(record.to_dict(), ensure_ascii=False, default=str) + "\n")
            elif self.output_format == OutputFormat.JSON:
                self._json_records.extend(record.to_dict() for record in records)
                self.config.output_path.write_text(
                    json.dumps(self._json_records, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
            elif self.output_format == OutputFormat.CSV:
                await self._write_csv(records, self.config.output_path, dead_letter=False)
            else:
                raise BatchPredictValidationError(f"Unsupported output format: {self.output_format}")

    async def write_dead_letters(self, records: Sequence[OutputRecord]) -> None:
        if not self.config.dead_letter_path or not records:
            return
        async with self._lock:
            if self.config.dead_letter_path.suffix.lower() == ".csv":
                await self._write_csv(records, self.config.dead_letter_path, dead_letter=True)
            else:
                with self.config.dead_letter_path.open("a", encoding="utf-8") as fh:
                    for record in records:
                        fh.write(json.dumps(record.to_dict(), ensure_ascii=False, default=str) + "\n")

    async def _write_csv(self, records: Sequence[OutputRecord], path: Path, dead_letter: bool) -> None:
        flattened = [flatten_record(record.to_dict()) for record in records]
        if not flattened:
            return
        header_written = self._dead_letter_header_written if dead_letter else self._csv_header_written
        fieldnames = sorted({key for row in flattened for key in row.keys()})
        with path.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            if not header_written:
                writer.writeheader()
            writer.writerows(flattened)
        if dead_letter:
            self._dead_letter_header_written = True
        else:
            self._csv_header_written = True


class CheckpointStore:
    def __init__(self, path: Optional[Path]) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    async def load(self) -> Optional[PipelineCheckpoint]:
        if not self.path or not self.path.exists():
            return None
        async with self._lock:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return PipelineCheckpoint.from_dict(payload)

    async def save(self, checkpoint: PipelineCheckpoint) -> None:
        if not self.path:
            return
        checkpoint.updated_at = utc_now_iso()
        async with self._lock:
            tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp_path.write_text(json.dumps(checkpoint.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
            tmp_path.replace(self.path)


# =============================================================================
# Pipeline
# =============================================================================


class BatchPredictPipeline:
    def __init__(
        self,
        config: BatchPredictConfig,
        prediction_service: Optional[PredictionService] = None,
        metrics: Optional[MetricsClient] = None,
        audit_sink: Optional[AuditSink] = None,
    ) -> None:
        config.validate()
        self.config = config
        self.prediction_service = prediction_service or build_prediction_service(
            config=PredictionServiceConfig(max_batch_size=max(config.batch_size, 1))
        )
        self.metrics = metrics or NoopMetricsClient()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.reader = BatchInputReader(config)
        self.writer = BatchOutputWriter(config)
        self.checkpoints = CheckpointStore(config.checkpoint_path or config.output_path.with_suffix(config.output_path.suffix + ".checkpoint.json"))
        self._stop_event = asyncio.Event()

    async def run(self) -> PipelineSummary:
        started = time.perf_counter()
        started_at = utc_now_iso()
        status = PipelineStatus.RUNNING
        self._install_signal_handlers()
        self.metrics.increment("batch_predict.started", tags=self._tags())
        await self._audit("batch_predict.started", {"config": self._safe_config()})

        records = self.reader.read_all()
        input_hash = file_sha256(self.config.input_path)
        checkpoint = await self._load_or_create_checkpoint(input_hash)

        pending = [record for record in records if not self.config.resume or record.row_number not in checkpoint.completed_rows]
        skipped = len(records) - len(pending)
        batches = list(chunks(pending, self.config.batch_size))

        if self.config.dry_run:
            summary = PipelineSummary(
                run_id=self.config.run_id,
                status=PipelineStatus.COMPLETED,
                input_path=str(self.config.input_path),
                output_path=str(self.config.output_path),
                total_rows=len(records),
                processed_rows=0,
                succeeded_rows=0,
                failed_rows=0,
                skipped_rows=skipped,
                batches=len(batches),
                started_at=started_at,
                completed_at=utc_now_iso(),
                processing_ms=round((time.perf_counter() - started) * 1000, 4),
                metadata={"dry_run": True, "input_hash": input_hash},
            )
            await self._audit("batch_predict.dry_run.completed", summary.to_dict())
            return summary

        succeeded = 0
        failed = 0
        processed = 0
        batch_queue: asyncio.Queue[Sequence[InputRecord]] = asyncio.Queue()
        for batch in batches:
            batch_queue.put_nowait(batch)

        async def worker(worker_id: int) -> None:
            nonlocal succeeded, failed, processed
            while not batch_queue.empty() and not self._stop_event.is_set():
                batch = await batch_queue.get()
                try:
                    ok, bad = await self._process_batch(batch, checkpoint, worker_id)
                    succeeded += ok
                    failed += bad
                    processed += len(batch)
                finally:
                    batch_queue.task_done()

        workers = [asyncio.create_task(worker(i)) for i in range(self.config.concurrency)]
        try:
            await asyncio.gather(*workers)
            status = PipelineStatus.CANCELLED if self._stop_event.is_set() else PipelineStatus.COMPLETED
        except Exception:
            status = PipelineStatus.FAILED
            for task in workers:
                task.cancel()
            raise
        finally:
            await self.checkpoints.save(checkpoint)

        summary = PipelineSummary(
            run_id=self.config.run_id,
            status=status,
            input_path=str(self.config.input_path),
            output_path=str(self.config.output_path),
            total_rows=len(records),
            processed_rows=processed,
            succeeded_rows=succeeded,
            failed_rows=failed,
            skipped_rows=skipped,
            batches=len(batches),
            started_at=started_at,
            completed_at=utc_now_iso(),
            processing_ms=round((time.perf_counter() - started) * 1000, 4),
            metadata={"input_hash": input_hash, "checkpoint_path": str(self.checkpoints.path) if self.checkpoints.path else None},
        )
        self.metrics.increment("batch_predict.completed", tags={**self._tags(), "status": status.value})
        self.metrics.timing("batch_predict.processing_ms", summary.processing_ms, tags=self._tags())
        self.metrics.gauge("batch_predict.succeeded_rows", succeeded, tags=self._tags())
        self.metrics.gauge("batch_predict.failed_rows", failed, tags=self._tags())
        await self._audit("batch_predict.completed", summary.to_dict())
        return summary

    async def _process_batch(
        self,
        batch: Sequence[InputRecord],
        checkpoint: PipelineCheckpoint,
        worker_id: int,
    ) -> Tuple[int, int]:
        for attempt in range(self.config.max_retries + 1):
            try:
                request = BatchPredictionRequest(
                    rows=[record.features for record in batch],
                    task=self.config.task,
                    model_name=self.config.model_name,
                    model_version=self.config.model_version,
                    tenant_id=self.config.tenant_id,
                    request_id=f"{self.config.run_id}:worker-{worker_id}:batch-{batch[0].row_number}-{batch[-1].row_number}",
                    output_type=self.config.output_type,
                    aggregation_strategy=self.config.aggregation_strategy,
                    explain=self.config.explain,
                    use_cache=self.config.use_cache,
                    confidence_level=self.config.confidence_level,
                    metadata={"pipeline": "batch_predict", "run_id": self.config.run_id, "worker_id": worker_id},
                )
                result = await self.prediction_service.predict_batch(request)
                return await self._handle_batch_result(batch, result, checkpoint)
            except Exception as exc:
                if attempt < self.config.max_retries:
                    delay = (self.config.retry_base_delay_ms * (2**attempt)) / 1000
                    logger.warning("Batch prediction attempt failed; retrying", extra={"attempt": attempt + 1, "error": str(exc)})
                    await asyncio.sleep(delay)
                    continue
                if self.config.fail_fast:
                    raise
                return await self._handle_batch_failure(batch, checkpoint, exc)
        return 0, len(batch)

    async def _handle_batch_result(
        self,
        batch: Sequence[InputRecord],
        result: BatchPredictionResult,
        checkpoint: PipelineCheckpoint,
    ) -> Tuple[int, int]:
        output_records: List[OutputRecord] = []
        dead_letters: List[OutputRecord] = []
        success_count = 0
        failure_count = 0

        by_index = {idx: item for idx, item in enumerate(result.results)}
        for idx, record in enumerate(batch):
            prediction = by_index.get(idx)
            if prediction is None or prediction.status.value == "failed":
                out = OutputRecord(
                    run_id=self.config.run_id,
                    row_number=record.row_number,
                    record_id=record.record_id,
                    status="failed",
                    error="Missing or failed prediction result.",
                    input=record.raw if self.config.include_input else None,
                )
                dead_letters.append(out)
                checkpoint.failed_rows.add(record.row_number)
                failure_count += 1
                continue
            out = self._prediction_to_output(record, prediction)
            output_records.append(out)
            checkpoint.completed_rows.add(record.row_number)
            success_count += 1

        await self.writer.write_records(output_records)
        await self.writer.write_dead_letters(dead_letters)
        await self.checkpoints.save(checkpoint)
        return success_count, failure_count

    async def _handle_batch_failure(
        self,
        batch: Sequence[InputRecord],
        checkpoint: PipelineCheckpoint,
        exc: Exception,
    ) -> Tuple[int, int]:
        records = []
        for record in batch:
            checkpoint.failed_rows.add(record.row_number)
            records.append(
                OutputRecord(
                    run_id=self.config.run_id,
                    row_number=record.row_number,
                    record_id=record.record_id,
                    status="failed",
                    error=f"{exc.__class__.__name__}: {exc}",
                    input=record.raw if self.config.include_input else None,
                )
            )
        await self.writer.write_dead_letters(records)
        await self.checkpoints.save(checkpoint)
        return 0, len(batch)

    def _prediction_to_output(self, record: InputRecord, prediction: PredictionResult) -> OutputRecord:
        interval = asdict(prediction.interval) if prediction.interval else None
        model = asdict(prediction.model) if prediction.model else None
        explanation = asdict(prediction.explanation) if prediction.explanation else None
        return OutputRecord(
            run_id=self.config.run_id,
            row_number=record.row_number,
            record_id=record.record_id,
            status=prediction.status.value,
            prediction=prediction.prediction,
            confidence=prediction.confidence,
            interval=interval,
            model=model,
            explanation=explanation,
            cached=prediction.cached,
            latency_ms=prediction.latency_ms,
            input=record.raw if self.config.include_input else None,
        )

    async def _load_or_create_checkpoint(self, input_hash: str) -> PipelineCheckpoint:
        existing = await self.checkpoints.load()
        if existing and self.config.resume:
            if existing.input_hash != input_hash:
                raise BatchPredictValidationError("Checkpoint input_hash does not match current input file.")
            return existing
        checkpoint = PipelineCheckpoint(run_id=self.config.run_id, input_hash=input_hash)
        await self.checkpoints.save(checkpoint)
        return checkpoint

    def _install_signal_handlers(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._stop_event.set)
        except (NotImplementedError, RuntimeError):
            return

    def _tags(self) -> Dict[str, str]:
        return {
            "tenant_id": self.config.tenant_id or "global",
            "task": self.config.task.value,
            "run_id": self.config.run_id,
        }

    def _safe_config(self) -> Mapping[str, Any]:
        payload = asdict(self.config)
        payload["input_path"] = str(self.config.input_path)
        payload["output_path"] = str(self.config.output_path)
        payload["checkpoint_path"] = str(self.config.checkpoint_path) if self.config.checkpoint_path else None
        payload["dead_letter_path"] = str(self.config.dead_letter_path) if self.config.dead_letter_path else None
        payload["task"] = self.config.task.value
        payload["input_format"] = self.config.input_format.value
        payload["output_format"] = self.config.output_format.value
        payload["aggregation_strategy"] = self.config.aggregation_strategy.value
        payload["output_type"] = self.config.output_type.value
        return payload

    async def _audit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        try:
            await self.audit_sink.write(event_name, payload)
        except Exception:
            logger.exception("Failed to write batch prediction audit event", extra={"event_name": event_name})


# =============================================================================
# CLI
# =============================================================================


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enterprise batch prediction pipeline")
    parser.add_argument("--input", required=True, dest="input_path", help="Input file path: .json, .jsonl or .csv")
    parser.add_argument("--output", required=True, dest="output_path", help="Output file path: .jsonl, .json or .csv")
    parser.add_argument("--tenant-id", default=None)
    parser.add_argument("--task", default=PredictionTask.GENERIC.value, choices=[x.value for x in PredictionTask])
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--model-version", default=None)
    parser.add_argument("--input-format", default=InputFormat.AUTO.value, choices=[x.value for x in InputFormat])
    parser.add_argument("--output-format", default=OutputFormat.AUTO.value, choices=[x.value for x in OutputFormat])
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--checkpoint", default=None, dest="checkpoint_path")
    parser.add_argument("--dead-letter", default=None, dest="dead_letter_path")
    parser.add_argument("--id-field", default=None)
    parser.add_argument("--features-field", default=None)
    parser.add_argument("--include-input", action="store_true")
    parser.add_argument("--explain", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--confidence-level", type=float, default=0.95)
    parser.add_argument("--aggregation-strategy", default=AggregationStrategy.SINGLE.value, choices=[x.value for x in AggregationStrategy])
    parser.add_argument("--output-type", default=PredictionOutputType.STRUCTURED.value, choices=[x.value for x in PredictionOutputType])
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> BatchPredictConfig:
    return BatchPredictConfig(
        input_path=Path(args.input_path),
        output_path=Path(args.output_path),
        tenant_id=args.tenant_id,
        task=PredictionTask(args.task),
        model_name=args.model_name,
        model_version=args.model_version,
        input_format=InputFormat(args.input_format),
        output_format=OutputFormat(args.output_format),
        batch_size=args.batch_size,
        concurrency=args.concurrency,
        max_retries=args.max_retries,
        checkpoint_path=Path(args.checkpoint_path) if args.checkpoint_path else None,
        dead_letter_path=Path(args.dead_letter_path) if args.dead_letter_path else None,
        id_field=args.id_field,
        features_field=args.features_field,
        include_input=args.include_input,
        explain=args.explain,
        use_cache=not args.no_cache,
        confidence_level=args.confidence_level,
        aggregation_strategy=AggregationStrategy(args.aggregation_strategy),
        output_type=PredictionOutputType(args.output_type),
        fail_fast=args.fail_fast,
        resume=not args.no_resume,
        dry_run=args.dry_run,
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
    pipeline = BatchPredictPipeline(config)
    try:
        summary = await pipeline.run()
        print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False, default=str))
        return 0 if summary.status in {PipelineStatus.COMPLETED, PipelineStatus.CANCELLED} else 1
    except Exception as exc:
        logger.exception("Batch prediction pipeline failed")
        print(json.dumps({"status": "failed", "error": f"{exc.__class__.__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 1


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()

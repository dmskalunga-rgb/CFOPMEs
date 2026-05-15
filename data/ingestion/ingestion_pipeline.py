"""
data/ingestion/ingestion_pipeline.py

Pipeline enterprise de ingestão de dados.

Objetivo:
- Orquestrar fluxos completos de ingestão com etapas plugáveis.
- Integrar loaders, validators, transformers, enrichers e sinks.
- Oferecer resiliência com retry, DLQ, auditoria, métricas e logs estruturados.
- Permitir execução síncrona, batch-oriented e extensível para produção.

Recursos principais:
- Pipeline baseado em stages.
- Contexto de execução com correlation_id e run_id.
- Configuração tipada via dataclass e variáveis de ambiente.
- Retry com backoff exponencial e jitter.
- DLQ JSONL para registros com falha.
- Auditoria JSONL opcional.
- Métricas de execução por stage e por pipeline.
- Hooks before/after/error.
- Suporte a execução dry-run.
- Sinks plugáveis.
- Validação e transformação desacopladas.
- Checkpoint básico em arquivo JSON.
- Arquitetura preparada para integração com JSON/XML/API/Kafka/File loaders.

Dependências:
    pip install pydantic
"""

from __future__ import annotations

import json
import logging
import os
import random
import socket
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Generator, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple, Union

try:
    from pydantic import BaseModel, Field, ValidationError
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Dependência ausente: instale com `pip install pydantic`.") from exc


# =============================================================================
# Logging
# =============================================================================

LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(name)s | "
    "%(message)s | service=%(service)s host=%(host)s"
)


class ContextFilter(logging.Filter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name
        self.host = socket.gethostname()

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self.service_name
        record.host = self.host
        return True


def build_logger(name: str = "data.ingestion.ingestion_pipeline") -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ContextFilter(service_name=os.getenv("SERVICE_NAME", "ingestion-pipeline")))

    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = build_logger()


# =============================================================================
# Enums e modelos
# =============================================================================


class PipelineStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RecordStatus(str, Enum):
    RECEIVED = "received"
    VALIDATED = "validated"
    TRANSFORMED = "transformed"
    ENRICHED = "enriched"
    WRITTEN = "written"
    SKIPPED = "skipped"
    FAILED = "failed"
    SENT_TO_DLQ = "sent_to_dlq"


class InvalidRecordStrategy(str, Enum):
    RAISE = "raise"
    SKIP = "skip"
    SEND_TO_DLQ = "send_to_dlq"


class StageType(str, Enum):
    SOURCE = "source"
    VALIDATOR = "validator"
    TRANSFORMER = "transformer"
    ENRICHER = "enricher"
    SINK = "sink"
    CUSTOM = "custom"


class IngestionRecord(BaseModel):
    record_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    status: RecordStatus = RecordStatus.RECEIVED
    created_at: str = Field(default_factory=lambda: utc_now_iso())
    updated_at: str = Field(default_factory=lambda: utc_now_iso())

    def touch(self, status: Optional[RecordStatus] = None) -> "IngestionRecord":
        self.updated_at = utc_now_iso()
        if status:
            self.status = status
        return self


class PipelineExecutionContext(BaseModel):
    pipeline_name: str
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str = Field(default_factory=lambda: utc_now_iso())
    finished_at: Optional[str] = None
    status: PipelineStatus = PipelineStatus.CREATED
    dry_run: bool = False
    attributes: Dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_seconds: float = 0.5
    max_seconds: float = 20.0
    jitter: bool = True

    def sleep_seconds(self, attempt: int) -> float:
        exponential = self.base_seconds * (2 ** max(0, attempt - 1))
        jitter_value = random.uniform(0, self.base_seconds) if self.jitter else 0.0
        return min(exponential + jitter_value, self.max_seconds)


@dataclass(frozen=True)
class PipelineConfig:
    pipeline_name: str = "enterprise-ingestion-pipeline"
    batch_size: int = 500
    dry_run: bool = False
    invalid_record_strategy: InvalidRecordStrategy = InvalidRecordStrategy.SEND_TO_DLQ
    dlq_path: Optional[Path] = Path("data/dlq/ingestion_pipeline_dlq.jsonl")
    audit_path: Optional[Path] = Path("data/audit/ingestion_pipeline_audit.jsonl")
    checkpoint_path: Optional[Path] = Path("data/checkpoints/ingestion_pipeline_checkpoint.json")
    enable_audit: bool = True
    enable_checkpoint: bool = True
    fail_fast: bool = False
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    max_records: Optional[int] = None

    @staticmethod
    def from_env() -> "PipelineConfig":
        return PipelineConfig(
            pipeline_name=os.getenv("INGESTION_PIPELINE_NAME", "enterprise-ingestion-pipeline"),
            batch_size=int(os.getenv("INGESTION_BATCH_SIZE", "500")),
            dry_run=env_bool("INGESTION_DRY_RUN", False),
            invalid_record_strategy=InvalidRecordStrategy(
                os.getenv("INGESTION_INVALID_RECORD_STRATEGY", InvalidRecordStrategy.SEND_TO_DLQ.value)
            ),
            dlq_path=Path(os.getenv("INGESTION_DLQ_PATH", "data/dlq/ingestion_pipeline_dlq.jsonl"))
            if os.getenv("INGESTION_DLQ_PATH", "data/dlq/ingestion_pipeline_dlq.jsonl")
            else None,
            audit_path=Path(os.getenv("INGESTION_AUDIT_PATH", "data/audit/ingestion_pipeline_audit.jsonl"))
            if os.getenv("INGESTION_AUDIT_PATH", "data/audit/ingestion_pipeline_audit.jsonl")
            else None,
            checkpoint_path=Path(os.getenv("INGESTION_CHECKPOINT_PATH", "data/checkpoints/ingestion_pipeline_checkpoint.json"))
            if os.getenv("INGESTION_CHECKPOINT_PATH", "data/checkpoints/ingestion_pipeline_checkpoint.json")
            else None,
            enable_audit=env_bool("INGESTION_ENABLE_AUDIT", True),
            enable_checkpoint=env_bool("INGESTION_ENABLE_CHECKPOINT", True),
            fail_fast=env_bool("INGESTION_FAIL_FAST", False),
            retry_policy=RetryPolicy(
                max_attempts=int(os.getenv("INGESTION_RETRY_MAX_ATTEMPTS", "3")),
                base_seconds=float(os.getenv("INGESTION_RETRY_BASE_SECONDS", "0.5")),
                max_seconds=float(os.getenv("INGESTION_RETRY_MAX_SECONDS", "20.0")),
                jitter=env_bool("INGESTION_RETRY_JITTER", True),
            ),
            max_records=int(os.getenv("INGESTION_MAX_RECORDS"))
            if os.getenv("INGESTION_MAX_RECORDS")
            else None,
        )


@dataclass
class StageMetrics:
    name: str
    stage_type: StageType
    processed: int = 0
    failed: int = 0
    skipped: int = 0
    total_seconds: float = 0.0
    last_error: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "stage_type": self.stage_type.value,
            "processed": self.processed,
            "failed": self.failed,
            "skipped": self.skipped,
            "total_seconds": round(self.total_seconds, 6),
            "last_error": self.last_error,
        }


@dataclass
class PipelineMetrics:
    records_received: int = 0
    records_processed: int = 0
    records_failed: int = 0
    records_skipped: int = 0
    records_sent_to_dlq: int = 0
    batches_processed: int = 0
    retries: int = 0
    total_seconds: float = 0.0
    stage_metrics: Dict[str, StageMetrics] = field(default_factory=dict)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "records_received": self.records_received,
            "records_processed": self.records_processed,
            "records_failed": self.records_failed,
            "records_skipped": self.records_skipped,
            "records_sent_to_dlq": self.records_sent_to_dlq,
            "batches_processed": self.batches_processed,
            "retries": self.retries,
            "total_seconds": round(self.total_seconds, 6),
            "stages": {name: metrics.snapshot() for name, metrics in self.stage_metrics.items()},
        }


# =============================================================================
# Protocols
# =============================================================================


class SourceStage(Protocol):
    def read(self, context: PipelineExecutionContext) -> Iterable[IngestionRecord]:
        """Produz registros para o pipeline."""


class RecordStage(Protocol):
    def process(self, record: IngestionRecord, context: PipelineExecutionContext) -> IngestionRecord:
        """Processa um único registro."""


class BatchSinkStage(Protocol):
    def write_batch(self, records: List[IngestionRecord], context: PipelineExecutionContext) -> None:
        """Persiste um batch de registros."""


class PipelineHook(Protocol):
    def __call__(self, context: PipelineExecutionContext, payload: Optional[Any] = None) -> None:
        """Hook de lifecycle."""


# =============================================================================
# Stages base
# =============================================================================


@dataclass
class RegisteredStage:
    name: str
    stage_type: StageType
    component: Any
    enabled: bool = True
    retryable: bool = True


class IterableSource:
    def __init__(self, records: Iterable[Union[IngestionRecord, Mapping[str, Any]]], source_name: str = "iterable") -> None:
        self.records = records
        self.source_name = source_name

    def read(self, context: PipelineExecutionContext) -> Iterable[IngestionRecord]:
        for item in self.records:
            if isinstance(item, IngestionRecord):
                yield item
            else:
                yield IngestionRecord(source=self.source_name, data=dict(item))


class RequiredFieldsStage:
    def __init__(self, required_fields: Sequence[str]) -> None:
        self.required_fields = list(required_fields)

    def process(self, record: IngestionRecord, context: PipelineExecutionContext) -> IngestionRecord:
        missing = [field for field in self.required_fields if get_nested_value(record.data, field) in (None, "")]
        if missing:
            raise ValueError(f"Campos obrigatórios ausentes: {missing}")
        return record.touch(RecordStatus.VALIDATED)


class MappingTransformerStage:
    """
    Renomeia campos usando um mapa origem -> destino.

    Exemplo:
        {"customer.name": "cliente.nome", "amount": "valor"}
    """

    def __init__(self, mapping: Mapping[str, str], keep_unmapped: bool = True) -> None:
        self.mapping = dict(mapping)
        self.keep_unmapped = keep_unmapped

    def process(self, record: IngestionRecord, context: PipelineExecutionContext) -> IngestionRecord:
        output: Dict[str, Any] = dict(record.data) if self.keep_unmapped else {}

        for source_path, target_path in self.mapping.items():
            value = get_nested_value(record.data, source_path)
            if value is not None:
                set_nested_value(output, target_path, value)

        record.data = output
        return record.touch(RecordStatus.TRANSFORMED)


class FunctionStage:
    def __init__(self, fn: Callable[[IngestionRecord, PipelineExecutionContext], IngestionRecord]) -> None:
        self.fn = fn

    def process(self, record: IngestionRecord, context: PipelineExecutionContext) -> IngestionRecord:
        return self.fn(record, context)


class MetadataEnrichmentStage:
    def __init__(self, metadata: Mapping[str, Any]) -> None:
        self.metadata = dict(metadata)

    def process(self, record: IngestionRecord, context: PipelineExecutionContext) -> IngestionRecord:
        record.metadata.update(self.metadata)
        record.metadata["pipeline_name"] = context.pipeline_name
        record.metadata["run_id"] = context.run_id
        record.metadata["correlation_id"] = context.correlation_id
        return record.touch(RecordStatus.ENRICHED)


class JsonlSink:
    def __init__(self, output_path: Union[str, Path]) -> None:
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def write_batch(self, records: List[IngestionRecord], context: PipelineExecutionContext) -> None:
        with self.output_path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(model_to_dict(record), ensure_ascii=False, default=str) + "\n")

        logger.info("Batch gravado em JSONL. output=%s size=%s", self.output_path, len(records))


class MemorySink:
    def __init__(self) -> None:
        self.records: List[IngestionRecord] = []

    def write_batch(self, records: List[IngestionRecord], context: PipelineExecutionContext) -> None:
        self.records.extend(records)


class LoggingSink:
    def write_batch(self, records: List[IngestionRecord], context: PipelineExecutionContext) -> None:
        logger.info("Batch recebido no sink. size=%s run_id=%s", len(records), context.run_id)


# =============================================================================
# Auditoria e Checkpoint
# =============================================================================


class JsonlAuditWriter:
    def __init__(self, path: Optional[Path]) -> None:
        self.path = path
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event_type: str, context: PipelineExecutionContext, payload: Optional[Mapping[str, Any]] = None) -> None:
        if not self.path:
            return

        event = {
            "event_type": event_type,
            "pipeline_name": context.pipeline_name,
            "run_id": context.run_id,
            "correlation_id": context.correlation_id,
            "status": context.status.value,
            "timestamp": utc_now_iso(),
            "payload": payload or {},
        }

        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


class FileCheckpointStore:
    def __init__(self, path: Optional[Path]) -> None:
        self.path = path
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, context: PipelineExecutionContext, metrics: PipelineMetrics) -> None:
        if not self.path:
            return

        payload = {
            "context": model_to_dict(context),
            "metrics": metrics.snapshot(),
            "saved_at": utc_now_iso(),
        }

        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)

    def load(self) -> Optional[Dict[str, Any]]:
        if not self.path or not self.path.exists():
            return None

        with self.path.open("r", encoding="utf-8") as handle:
            return json.load(handle)


class JsonlDLQWriter:
    def __init__(self, path: Optional[Path]) -> None:
        self.path = path
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        record: Optional[IngestionRecord],
        exc: Exception,
        context: PipelineExecutionContext,
        stage_name: Optional[str] = None,
    ) -> None:
        if not self.path:
            logger.warning("DLQ desabilitada. error=%s", exc)
            return

        payload = {
            "status": RecordStatus.SENT_TO_DLQ.value,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
            "traceback": traceback.format_exc(),
            "stage_name": stage_name,
            "pipeline_name": context.pipeline_name,
            "run_id": context.run_id,
            "correlation_id": context.correlation_id,
            "failed_at": utc_now_iso(),
            "record": model_to_dict(record) if record else None,
        }

        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


# =============================================================================
# Pipeline principal
# =============================================================================


class EnterpriseIngestionPipeline:
    def __init__(self, config: Optional[PipelineConfig] = None) -> None:
        self.config = config or PipelineConfig.from_env()
        self.context = PipelineExecutionContext(
            pipeline_name=self.config.pipeline_name,
            dry_run=self.config.dry_run,
        )
        self.metrics = PipelineMetrics()

        self.source_stage: Optional[RegisteredStage] = None
        self.record_stages: List[RegisteredStage] = []
        self.sink_stages: List[RegisteredStage] = []

        self.before_run_hooks: List[PipelineHook] = []
        self.after_run_hooks: List[PipelineHook] = []
        self.on_error_hooks: List[PipelineHook] = []

        self.audit_writer = JsonlAuditWriter(self.config.audit_path if self.config.enable_audit else None)
        self.checkpoint_store = FileCheckpointStore(
            self.config.checkpoint_path if self.config.enable_checkpoint else None
        )
        self.dlq_writer = JsonlDLQWriter(self.config.dlq_path)

    def set_source(self, name: str, source: SourceStage, retryable: bool = True) -> "EnterpriseIngestionPipeline":
        self.source_stage = RegisteredStage(
            name=name,
            stage_type=StageType.SOURCE,
            component=source,
            retryable=retryable,
        )
        self._ensure_stage_metrics(name, StageType.SOURCE)
        return self

    def add_validator(self, name: str, validator: RecordStage, retryable: bool = False) -> "EnterpriseIngestionPipeline":
        return self._add_record_stage(name, StageType.VALIDATOR, validator, retryable)

    def add_transformer(self, name: str, transformer: RecordStage, retryable: bool = True) -> "EnterpriseIngestionPipeline":
        return self._add_record_stage(name, StageType.TRANSFORMER, transformer, retryable)

    def add_enricher(self, name: str, enricher: RecordStage, retryable: bool = True) -> "EnterpriseIngestionPipeline":
        return self._add_record_stage(name, StageType.ENRICHER, enricher, retryable)

    def add_custom_stage(self, name: str, stage: RecordStage, retryable: bool = True) -> "EnterpriseIngestionPipeline":
        return self._add_record_stage(name, StageType.CUSTOM, stage, retryable)

    def add_sink(self, name: str, sink: BatchSinkStage, retryable: bool = True) -> "EnterpriseIngestionPipeline":
        self.sink_stages.append(
            RegisteredStage(
                name=name,
                stage_type=StageType.SINK,
                component=sink,
                retryable=retryable,
            )
        )
        self._ensure_stage_metrics(name, StageType.SINK)
        return self

    def add_before_run_hook(self, hook: PipelineHook) -> "EnterpriseIngestionPipeline":
        self.before_run_hooks.append(hook)
        return self

    def add_after_run_hook(self, hook: PipelineHook) -> "EnterpriseIngestionPipeline":
        self.after_run_hooks.append(hook)
        return self

    def add_on_error_hook(self, hook: PipelineHook) -> "EnterpriseIngestionPipeline":
        self.on_error_hooks.append(hook)
        return self

    def run(self) -> PipelineMetrics:
        if not self.source_stage:
            raise ValueError("Pipeline sem source configurado.")
        if not self.sink_stages:
            logger.warning("Pipeline sem sink configurado. Será usado LoggingSink automaticamente.")
            self.add_sink("logging_sink", LoggingSink())

        started = time.perf_counter()
        self.context.status = PipelineStatus.RUNNING
        self.context.started_at = utc_now_iso()

        logger.info(
            "Iniciando pipeline. name=%s run_id=%s correlation_id=%s dry_run=%s",
            self.context.pipeline_name,
            self.context.run_id,
            self.context.correlation_id,
            self.context.dry_run,
        )

        self.audit_writer.write("pipeline_started", self.context)
        self._run_hooks(self.before_run_hooks)

        try:
            self._execute()
            self.context.status = self._final_status()
            return self.metrics
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.context.status = PipelineStatus.FAILED
            logger.exception("Pipeline falhou. error=%s", exc)
            self.audit_writer.write(
                "pipeline_failed",
                self.context,
                {"error": str(exc), "error_type": exc.__class__.__name__},
            )
            self._run_hooks(self.on_error_hooks, exc)
            raise
        finally:
            self.context.finished_at = utc_now_iso()
            self.metrics.total_seconds += time.perf_counter() - started
            self.checkpoint_store.save(self.context, self.metrics)
            self.audit_writer.write("pipeline_finished", self.context, self.metrics.snapshot())
            self._run_hooks(self.after_run_hooks, self.metrics.snapshot())
            logger.info(
                "Pipeline finalizado. status=%s metrics=%s",
                self.context.status.value,
                json.dumps(self.metrics.snapshot(), ensure_ascii=False, default=str),
            )

    def _execute(self) -> None:
        batch: List[IngestionRecord] = []
        count = 0

        source_records = self._read_source_with_retry()

        for record in source_records:
            if self.config.max_records is not None and count >= self.config.max_records:
                logger.info("Limite INGESTION_MAX_RECORDS atingido. max=%s", self.config.max_records)
                break

            count += 1
            self.metrics.records_received += 1

            processed = self._process_record_safely(record)
            if processed is None:
                continue

            batch.append(processed)

            if len(batch) >= self.config.batch_size:
                self._write_batch_safely(batch)
                batch = []

        if batch:
            self._write_batch_safely(batch)

    def _read_source_with_retry(self) -> Iterable[IngestionRecord]:
        assert self.source_stage is not None
        stage = self.source_stage

        def _read() -> Iterable[IngestionRecord]:
            return stage.component.read(self.context)

        return self._execute_with_retry(
            stage=stage,
            operation=_read,
            record=None,
        )

    def _process_record_safely(self, record: IngestionRecord) -> Optional[IngestionRecord]:
        current = record

        for stage in self.record_stages:
            if not stage.enabled:
                continue

            try:
                current = self._execute_with_retry(
                    stage=stage,
                    operation=lambda stage=stage, current=current: stage.component.process(current, self.context),
                    record=current,
                )
                self._stage_metrics(stage.name).processed += 1

            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._stage_metrics(stage.name).failed += 1
                self._stage_metrics(stage.name).last_error = str(exc)
                self.metrics.records_failed += 1
                logger.exception(
                    "Falha no stage. stage=%s record_id=%s error=%s",
                    stage.name,
                    current.record_id,
                    exc,
                )

                handled = self._handle_invalid_record(current, exc, stage.name)
                if self.config.fail_fast:
                    raise
                return handled

        return current

    def _write_batch_safely(self, batch: List[IngestionRecord]) -> None:
        if not batch:
            return

        if self.context.dry_run:
            logger.info("Dry-run ativo. Batch não será persistido. size=%s", len(batch))
            self.metrics.batches_processed += 1
            self.metrics.records_processed += len(batch)
            return

        for sink_stage in self.sink_stages:
            if not sink_stage.enabled:
                continue

            started = time.perf_counter()
            try:
                self._execute_with_retry(
                    stage=sink_stage,
                    operation=lambda sink_stage=sink_stage: sink_stage.component.write_batch(batch, self.context),
                    record=None,
                )
                stage_metrics = self._stage_metrics(sink_stage.name)
                stage_metrics.processed += len(batch)
                stage_metrics.total_seconds += time.perf_counter() - started
            except Exception as exc:  # pylint: disable=broad-exception-caught
                stage_metrics = self._stage_metrics(sink_stage.name)
                stage_metrics.failed += len(batch)
                stage_metrics.last_error = str(exc)
                self.metrics.records_failed += len(batch)
                logger.exception("Falha ao escrever batch. sink=%s error=%s", sink_stage.name, exc)

                for record in batch:
                    self._handle_invalid_record(record, exc, sink_stage.name)

                if self.config.fail_fast:
                    raise
                return

        for record in batch:
            record.touch(RecordStatus.WRITTEN)

        self.metrics.batches_processed += 1
        self.metrics.records_processed += len(batch)
        self.checkpoint_store.save(self.context, self.metrics)

    def _execute_with_retry(self, stage: RegisteredStage, operation: Callable[[], Any], record: Optional[IngestionRecord]) -> Any:
        attempts = self.config.retry_policy.max_attempts if stage.retryable else 1
        last_error: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                result = operation()
                self._stage_metrics(stage.name).total_seconds += time.perf_counter() - started
                return result
            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_error = exc
                self._stage_metrics(stage.name).total_seconds += time.perf_counter() - started

                if attempt >= attempts:
                    break

                self.metrics.retries += 1
                sleep_seconds = self.config.retry_policy.sleep_seconds(attempt)
                logger.warning(
                    "Erro em stage com retry. stage=%s attempt=%s/%s sleep=%.2fs error=%s",
                    stage.name,
                    attempt,
                    attempts,
                    sleep_seconds,
                    exc,
                )
                time.sleep(sleep_seconds)

        raise RuntimeError(f"Stage falhou após {attempts} tentativa(s): {stage.name}") from last_error

    def _handle_invalid_record(
        self,
        record: IngestionRecord,
        exc: Exception,
        stage_name: Optional[str],
    ) -> Optional[IngestionRecord]:
        record.touch(RecordStatus.FAILED)

        if self.config.invalid_record_strategy == InvalidRecordStrategy.RAISE:
            raise exc

        if self.config.invalid_record_strategy == InvalidRecordStrategy.SKIP:
            self.metrics.records_skipped += 1
            if stage_name:
                self._stage_metrics(stage_name).skipped += 1
            logger.warning("Registro ignorado. record_id=%s stage=%s error=%s", record.record_id, stage_name, exc)
            return None

        self.dlq_writer.write(record, exc, self.context, stage_name)
        self.metrics.records_sent_to_dlq += 1
        record.touch(RecordStatus.SENT_TO_DLQ)
        return None

    def _add_record_stage(
        self,
        name: str,
        stage_type: StageType,
        component: RecordStage,
        retryable: bool,
    ) -> "EnterpriseIngestionPipeline":
        self.record_stages.append(
            RegisteredStage(
                name=name,
                stage_type=stage_type,
                component=component,
                retryable=retryable,
            )
        )
        self._ensure_stage_metrics(name, stage_type)
        return self

    def _ensure_stage_metrics(self, name: str, stage_type: StageType) -> None:
        if name not in self.metrics.stage_metrics:
            self.metrics.stage_metrics[name] = StageMetrics(name=name, stage_type=stage_type)

    def _stage_metrics(self, name: str) -> StageMetrics:
        return self.metrics.stage_metrics[name]

    def _run_hooks(self, hooks: Sequence[PipelineHook], payload: Optional[Any] = None) -> None:
        for hook in hooks:
            try:
                hook(self.context, payload)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Hook falhou. error=%s", exc)

    def _final_status(self) -> PipelineStatus:
        if self.metrics.records_failed > 0 or self.metrics.records_sent_to_dlq > 0:
            if self.metrics.records_processed > 0:
                return PipelineStatus.PARTIALLY_SUCCEEDED
            return PipelineStatus.FAILED
        return PipelineStatus.SUCCEEDED


# =============================================================================
# Utilitários
# =============================================================================


def get_nested_value(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def set_nested_value(data: MutableMapping[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current: MutableMapping[str, Any] = data
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, MutableMapping):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[parts[-1]] = value


def model_to_dict(model: Optional[BaseModel]) -> Optional[Dict[str, Any]]:
    if model is None:
        return None
    if hasattr(model, "model_dump"):
        return model.model_dump()  # type: ignore[no-any-return]
    return model.dict()  # type: ignore[no-any-return]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "sim", "s"}


# =============================================================================
# Exemplo de uso
# =============================================================================


def example_pipeline() -> EnterpriseIngestionPipeline:
    records = [
        {"id": 1, "customer": {"name": "Ana"}, "amount": 100.5},
        {"id": 2, "customer": {"name": "Bruno"}, "amount": 230.0},
    ]

    pipeline = EnterpriseIngestionPipeline(
        PipelineConfig(
            pipeline_name="example-enterprise-pipeline",
            batch_size=100,
            dry_run=False,
            audit_path=Path("data/audit/example_pipeline_audit.jsonl"),
            dlq_path=Path("data/dlq/example_pipeline_dlq.jsonl"),
            checkpoint_path=Path("data/checkpoints/example_pipeline_checkpoint.json"),
        )
    )

    pipeline.set_source("memory_source", IterableSource(records, source_name="example"))
    pipeline.add_validator("required_fields", RequiredFieldsStage(["id", "customer.name", "amount"]))
    pipeline.add_transformer(
        "field_mapping",
        MappingTransformerStage(
            {
                "id": "external_id",
                "customer.name": "customer_name",
                "amount": "transaction_amount",
            },
            keep_unmapped=False,
        ),
    )
    pipeline.add_enricher("metadata_enrichment", MetadataEnrichmentStage({"domain": "sales"}))
    pipeline.add_sink("jsonl_sink", JsonlSink("data/output/example_pipeline_output.jsonl"))

    return pipeline


def main() -> None:
    # Para produção, monte o pipeline no composition root da aplicação.
    # Este main usa um exemplo local para validar o funcionamento do módulo.
    pipeline = example_pipeline()
    pipeline.run()


if __name__ == "__main__":
    main()

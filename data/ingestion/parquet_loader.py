"""
data/ingestion/parquet_loader.py

Parquet Loader enterprise para pipelines de ingestão analítica.

Recursos principais:
- Leitura de arquivos Parquet locais em modo batch/streaming.
- Suporte a diretórios particionados e descoberta recursiva.
- Leitura por row groups e/ou batches para grandes volumes.
- Filtros de colunas e predicate pushdown quando suportado.
- Validação de schema esperado.
- Normalização de nomes de colunas.
- Transformação e validação plugáveis por batch ou registro.
- Conversão para records/dicts ou DataFrame Arrow/Pandas.
- DLQ JSONL para registros inválidos.
- Métricas de arquivos, row groups, linhas, batches, erros e latência.
- Logs estruturados.
- Arquivamento opcional de arquivos processados.
- Sinks plugáveis: logging, JSONL, callback e memória.

Dependências recomendadas:
    pip install pyarrow pandas pydantic

Observação:
- Para produção em grande escala, prefira processar batches Arrow ao invés de converter tudo para pandas.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Generator, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple, Union

try:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Dependência ausente: instale com `pip install pyarrow`.") from exc

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None  # type: ignore[assignment]

try:
    from pydantic import BaseModel, Field
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


def build_logger(name: str = "data.ingestion.parquet_loader") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ContextFilter(service_name=os.getenv("SERVICE_NAME", "parquet-loader")))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = build_logger()


# =============================================================================
# Enums e modelos
# =============================================================================


class InvalidRecordStrategy(str, Enum):
    RAISE = "raise"
    SKIP = "skip"
    SEND_TO_DLQ = "send_to_dlq"


class ColumnNormalizeMode(str, Enum):
    NONE = "none"
    LOWER = "lower"
    SNAKE_CASE = "snake_case"


class OutputMode(str, Enum):
    RECORDS = "records"
    ARROW_BATCH = "arrow_batch"
    PANDAS = "pandas"


class LoadedRecordStatus(str, Enum):
    LOADED = "loaded"
    VALIDATED = "validated"
    TRANSFORMED = "transformed"
    WRITTEN = "written"
    FAILED = "failed"
    SKIPPED = "skipped"
    SENT_TO_DLQ = "sent_to_dlq"


class ParquetRecord(BaseModel):
    source_file: str
    record_index: int
    row_group: Optional[int] = None
    batch_index: Optional[int] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    loaded_at: str = Field(default_factory=lambda: utc_now_iso())
    status: LoadedRecordStatus = LoadedRecordStatus.LOADED
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def touch(self, status: Optional[LoadedRecordStatus] = None) -> "ParquetRecord":
        if status:
            self.status = status
        self.metadata["updated_at"] = utc_now_iso()
        return self


@dataclass(frozen=True)
class ExpectedColumn:
    name: str
    arrow_type: Optional[str] = None
    required: bool = True


@dataclass(frozen=True)
class ParquetLoaderConfig:
    input_path: Path
    recursive: bool = True
    file_patterns: Tuple[str, ...] = ("*.parquet", "*.pq")

    columns: Optional[Tuple[str, ...]] = None
    filters: Optional[Any] = None
    batch_size: int = 10_000
    output_mode: OutputMode = OutputMode.RECORDS

    column_normalize_mode: ColumnNormalizeMode = ColumnNormalizeMode.SNAKE_CASE
    expected_columns: Tuple[ExpectedColumn, ...] = tuple()
    validate_schema: bool = True
    allow_extra_columns: bool = True

    invalid_record_strategy: InvalidRecordStrategy = InvalidRecordStrategy.SEND_TO_DLQ
    dlq_path: Optional[Path] = Path("data/dlq/parquet_loader_dlq.jsonl")

    archive_processed: bool = False
    archive_dir: Optional[Path] = Path("data/archive/parquet")
    max_file_size_mb: Optional[int] = None
    fail_on_empty_file: bool = False
    dry_run: bool = False

    read_dictionary: Optional[Sequence[str]] = None
    use_threads: bool = True
    coerce_int96_timestamp_unit: Optional[str] = None

    @staticmethod
    def from_env() -> "ParquetLoaderConfig":
        input_path_raw = os.getenv("PARQUET_INPUT_PATH")
        if not input_path_raw:
            raise ValueError("PARQUET_INPUT_PATH é obrigatório.")

        columns_raw = os.getenv("PARQUET_COLUMNS")
        columns = tuple(x.strip() for x in columns_raw.split(",") if x.strip()) if columns_raw else None

        expected_columns = parse_expected_columns_env("PARQUET_EXPECTED_COLUMNS_JSON")
        filters = parse_json_env("PARQUET_FILTERS_JSON", default=None)

        dlq_raw = os.getenv("PARQUET_DLQ_PATH", "data/dlq/parquet_loader_dlq.jsonl")
        archive_raw = os.getenv("PARQUET_ARCHIVE_DIR", "data/archive/parquet")

        return ParquetLoaderConfig(
            input_path=Path(input_path_raw),
            recursive=env_bool("PARQUET_RECURSIVE", True),
            file_patterns=tuple(
                x.strip()
                for x in os.getenv("PARQUET_FILE_PATTERNS", "*.parquet,*.pq").split(",")
                if x.strip()
            ),
            columns=columns,
            filters=filters,
            batch_size=int(os.getenv("PARQUET_BATCH_SIZE", "10000")),
            output_mode=OutputMode(os.getenv("PARQUET_OUTPUT_MODE", OutputMode.RECORDS.value)),
            column_normalize_mode=ColumnNormalizeMode(
                os.getenv("PARQUET_COLUMN_NORMALIZE_MODE", ColumnNormalizeMode.SNAKE_CASE.value)
            ),
            expected_columns=expected_columns,
            validate_schema=env_bool("PARQUET_VALIDATE_SCHEMA", True),
            allow_extra_columns=env_bool("PARQUET_ALLOW_EXTRA_COLUMNS", True),
            invalid_record_strategy=InvalidRecordStrategy(
                os.getenv("PARQUET_INVALID_RECORD_STRATEGY", InvalidRecordStrategy.SEND_TO_DLQ.value)
            ),
            dlq_path=Path(dlq_raw) if dlq_raw else None,
            archive_processed=env_bool("PARQUET_ARCHIVE_PROCESSED", False),
            archive_dir=Path(archive_raw) if archive_raw else None,
            max_file_size_mb=int(os.getenv("PARQUET_MAX_FILE_SIZE_MB"))
            if os.getenv("PARQUET_MAX_FILE_SIZE_MB")
            else None,
            fail_on_empty_file=env_bool("PARQUET_FAIL_ON_EMPTY_FILE", False),
            dry_run=env_bool("PARQUET_DRY_RUN", False),
            read_dictionary=tuple(
                x.strip()
                for x in os.getenv("PARQUET_READ_DICTIONARY", "").split(",")
                if x.strip()
            )
            or None,
            use_threads=env_bool("PARQUET_USE_THREADS", True),
            coerce_int96_timestamp_unit=os.getenv("PARQUET_COERCE_INT96_TIMESTAMP_UNIT") or None,
        )


@dataclass
class ParquetLoaderMetrics:
    files_seen: int = 0
    files_loaded: int = 0
    files_failed: int = 0
    row_groups_seen: int = 0
    rows_seen: int = 0
    rows_validated: int = 0
    rows_transformed: int = 0
    rows_written: int = 0
    rows_failed: int = 0
    rows_skipped: int = 0
    rows_sent_to_dlq: int = 0
    batches_emitted: int = 0
    total_seconds: float = 0.0
    last_loaded_at: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "files_seen": self.files_seen,
            "files_loaded": self.files_loaded,
            "files_failed": self.files_failed,
            "row_groups_seen": self.row_groups_seen,
            "rows_seen": self.rows_seen,
            "rows_validated": self.rows_validated,
            "rows_transformed": self.rows_transformed,
            "rows_written": self.rows_written,
            "rows_failed": self.rows_failed,
            "rows_skipped": self.rows_skipped,
            "rows_sent_to_dlq": self.rows_sent_to_dlq,
            "batches_emitted": self.batches_emitted,
            "total_seconds": round(self.total_seconds, 6),
            "last_loaded_at": self.last_loaded_at,
        }


# =============================================================================
# Protocols
# =============================================================================


class ParquetRecordValidator(Protocol):
    def validate(self, record: ParquetRecord) -> ParquetRecord:
        """Valida um registro Parquet convertido para dict."""


class ParquetRecordTransformer(Protocol):
    def transform(self, record: ParquetRecord) -> ParquetRecord:
        """Transforma um registro Parquet convertido para dict."""


class ParquetRecordSink(Protocol):
    def write_batch(self, records: List[ParquetRecord]) -> None:
        """Persiste batch de registros Parquet."""


class ArrowBatchSink(Protocol):
    def write_arrow_batch(self, batch: pa.RecordBatch, metadata: Mapping[str, Any]) -> None:
        """Persiste batch Arrow sem conversão para dict."""


# =============================================================================
# Implementações base
# =============================================================================


class NoOpParquetRecordValidator:
    def validate(self, record: ParquetRecord) -> ParquetRecord:
        return record.touch(LoadedRecordStatus.VALIDATED)


class NoOpParquetRecordTransformer:
    def transform(self, record: ParquetRecord) -> ParquetRecord:
        return record.touch(LoadedRecordStatus.TRANSFORMED)


class RequiredFieldsValidator:
    def __init__(self, required_fields: Sequence[str]) -> None:
        self.required_fields = list(required_fields)

    def validate(self, record: ParquetRecord) -> ParquetRecord:
        missing = [field for field in self.required_fields if get_nested_value(record.data, field) in (None, "")]
        if missing:
            raise ValueError(f"Campos obrigatórios ausentes/vazios: {missing}")
        return record.touch(LoadedRecordStatus.VALIDATED)


class FunctionParquetTransformer:
    def __init__(self, fn: Callable[[ParquetRecord], ParquetRecord]) -> None:
        self.fn = fn

    def transform(self, record: ParquetRecord) -> ParquetRecord:
        return self.fn(record).touch(LoadedRecordStatus.TRANSFORMED)


class LoggingParquetRecordSink:
    def write_batch(self, records: List[ParquetRecord]) -> None:
        logger.info("Batch Parquet recebido pelo sink. size=%s", len(records))


class JsonlParquetRecordSink:
    def __init__(self, output_path: Union[str, Path]) -> None:
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def write_batch(self, records: List[ParquetRecord]) -> None:
        with self.output_path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(model_to_dict(record), ensure_ascii=False, default=json_default) + "\n")
        logger.info("Batch Parquet gravado em JSONL. output=%s size=%s", self.output_path, len(records))


class MemoryParquetRecordSink:
    def __init__(self) -> None:
        self.records: List[ParquetRecord] = []

    def write_batch(self, records: List[ParquetRecord]) -> None:
        self.records.extend(records)


class CallbackParquetRecordSink:
    def __init__(self, callback: Callable[[List[ParquetRecord]], None]) -> None:
        self.callback = callback

    def write_batch(self, records: List[ParquetRecord]) -> None:
        self.callback(records)


# =============================================================================
# Loader principal
# =============================================================================


class EnterpriseParquetLoader:
    def __init__(
        self,
        config: ParquetLoaderConfig,
        sink: Optional[ParquetRecordSink] = None,
        validator: Optional[ParquetRecordValidator] = None,
        transformer: Optional[ParquetRecordTransformer] = None,
        arrow_sink: Optional[ArrowBatchSink] = None,
    ) -> None:
        self.config = config
        self.sink = sink or LoggingParquetRecordSink()
        self.validator = validator or NoOpParquetRecordValidator()
        self.transformer = transformer or NoOpParquetRecordTransformer()
        self.arrow_sink = arrow_sink
        self.metrics = ParquetLoaderMetrics()

    def run(self) -> ParquetLoaderMetrics:
        started = time.perf_counter()
        logger.info(
            "Iniciando Parquet loader. input_path=%s batch_size=%s output_mode=%s",
            self.config.input_path,
            self.config.batch_size,
            self.config.output_mode.value,
        )

        try:
            for file_path in self.discover_files():
                self._load_file_safely(file_path)
        finally:
            self.metrics.total_seconds += time.perf_counter() - started
            logger.info("Parquet loader finalizado. metrics=%s", json.dumps(self.metrics.snapshot()))

        return self.metrics

    def discover_files(self) -> Generator[Path, None, None]:
        input_path = self.config.input_path

        if input_path.is_file():
            self.metrics.files_seen += 1
            yield input_path
            return

        if not input_path.exists():
            raise FileNotFoundError(f"Caminho de entrada não encontrado: {input_path}")

        glob_fn = input_path.rglob if self.config.recursive else input_path.glob
        yielded: set[Path] = set()

        for pattern in self.config.file_patterns:
            for file_path in sorted(glob_fn(pattern)):
                if file_path.is_file() and file_path not in yielded:
                    yielded.add(file_path)
                    self.metrics.files_seen += 1
                    yield file_path

    def iter_arrow_batches(self, file_path: Path) -> Generator[Tuple[pa.RecordBatch, Dict[str, Any]], None, None]:
        self._validate_file_before_load(file_path)

        parquet_file = pq.ParquetFile(
            file_path,
            read_dictionary=self.config.read_dictionary,
            coerce_int96_timestamp_unit=self.config.coerce_int96_timestamp_unit,
        )

        if self.config.validate_schema:
            self._validate_schema(parquet_file.schema_arrow, file_path)

        for row_group_index in range(parquet_file.num_row_groups):
            self.metrics.row_groups_seen += 1

            table = parquet_file.read_row_group(
                row_group_index,
                columns=list(self.config.columns) if self.config.columns else None,
                use_threads=self.config.use_threads,
            )

            table = self._normalize_table_columns(table)
            table = self._apply_filters_if_possible(table)

            if table.num_rows == 0:
                continue

            for batch_index, batch in enumerate(table.to_batches(max_chunksize=self.config.batch_size), start=1):
                metadata = {
                    "source_file": str(file_path),
                    "file_name": file_path.name,
                    "file_size_bytes": file_path.stat().st_size if file_path.exists() else None,
                    "row_group": row_group_index,
                    "batch_index": batch_index,
                    "num_rows": batch.num_rows,
                    "columns": batch.schema.names,
                }
                self.metrics.rows_seen += batch.num_rows
                self.metrics.batches_emitted += 1
                yield batch, metadata

    def iter_records(self, file_path: Path) -> Generator[ParquetRecord, None, None]:
        global_index = 0

        for batch, metadata in self.iter_arrow_batches(file_path):
            rows = batch.to_pylist()
            for row in rows:
                global_index += 1
                yield ParquetRecord(
                    source_file=str(file_path),
                    record_index=global_index,
                    row_group=metadata.get("row_group"),
                    batch_index=metadata.get("batch_index"),
                    data=dict(row),
                    metadata=dict(metadata),
                )

    def iter_record_batches(self, file_path: Path) -> Generator[List[ParquetRecord], None, None]:
        batch: List[ParquetRecord] = []

        for record in self.iter_records(file_path):
            try:
                validated = self.validator.validate(record)
                self.metrics.rows_validated += 1

                transformed = self.transformer.transform(validated)
                self.metrics.rows_transformed += 1

                batch.append(transformed)

                if len(batch) >= self.config.batch_size:
                    yield batch
                    batch = []

            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._handle_invalid_record(record, exc)

        if batch:
            yield batch

    def _load_file_safely(self, file_path: Path) -> None:
        logger.info("Carregando arquivo Parquet. file=%s", file_path)
        started = time.perf_counter()

        try:
            if self.config.output_mode == OutputMode.ARROW_BATCH:
                self._load_as_arrow_batches(file_path)
            elif self.config.output_mode == OutputMode.PANDAS:
                self._load_as_pandas_batches(file_path)
            else:
                self._load_as_records(file_path)

            self.metrics.files_loaded += 1
            self.metrics.last_loaded_at = utc_now_iso()

            if self.config.archive_processed:
                self._archive_file(file_path)

            logger.info(
                "Arquivo Parquet carregado com sucesso. file=%s elapsed=%.3fs",
                file_path,
                time.perf_counter() - started,
            )

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.files_failed += 1
            logger.exception("Falha ao carregar arquivo Parquet. file=%s error=%s", file_path, exc)
            raise

    def _load_as_records(self, file_path: Path) -> None:
        for records in self.iter_record_batches(file_path):
            if self.config.dry_run:
                logger.info("Dry-run ativo. Batch Parquet não será persistido. size=%s", len(records))
            else:
                self.sink.write_batch(records)

            for record in records:
                record.touch(LoadedRecordStatus.WRITTEN)
            self.metrics.rows_written += len(records)

    def _load_as_arrow_batches(self, file_path: Path) -> None:
        if not self.arrow_sink:
            raise ValueError("output_mode=arrow_batch exige arrow_sink configurado.")

        for batch, metadata in self.iter_arrow_batches(file_path):
            if self.config.dry_run:
                logger.info("Dry-run ativo. Arrow batch não será persistido. rows=%s", batch.num_rows)
            else:
                self.arrow_sink.write_arrow_batch(batch, metadata)
            self.metrics.rows_written += batch.num_rows

    def _load_as_pandas_batches(self, file_path: Path) -> None:
        if pd is None:
            raise RuntimeError("output_mode=pandas exige `pip install pandas`.")

        for batch, metadata in self.iter_arrow_batches(file_path):
            dataframe = batch.to_pandas()
            records = [
                ParquetRecord(
                    source_file=str(file_path),
                    record_index=int(i) + 1,
                    row_group=metadata.get("row_group"),
                    batch_index=metadata.get("batch_index"),
                    data=row.dropna().to_dict(),
                    metadata={**metadata, "output_mode": OutputMode.PANDAS.value},
                )
                for i, row in dataframe.iterrows()
            ]

            for record in records:
                try:
                    validated = self.validator.validate(record)
                    self.metrics.rows_validated += 1
                    transformed = self.transformer.transform(validated)
                    self.metrics.rows_transformed += 1
                    transformed.touch(LoadedRecordStatus.WRITTEN)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    self._handle_invalid_record(record, exc)

            if self.config.dry_run:
                logger.info("Dry-run ativo. Pandas batch não será persistido. size=%s", len(records))
            else:
                self.sink.write_batch(records)
            self.metrics.rows_written += len(records)

    def _validate_schema(self, schema: pa.Schema, file_path: Path) -> None:
        if not self.config.expected_columns:
            return

        actual_fields = {normalize_column_name(field.name, self.config.column_normalize_mode): field for field in schema}
        expected_names = {
            normalize_column_name(col.name, self.config.column_normalize_mode)
            for col in self.config.expected_columns
        }

        missing = []
        type_mismatches = []

        for expected in self.config.expected_columns:
            expected_name = normalize_column_name(expected.name, self.config.column_normalize_mode)
            actual = actual_fields.get(expected_name)

            if actual is None:
                if expected.required:
                    missing.append(expected.name)
                continue

            if expected.arrow_type and str(actual.type).lower() != expected.arrow_type.lower():
                type_mismatches.append(
                    {
                        "column": expected.name,
                        "expected": expected.arrow_type,
                        "actual": str(actual.type),
                    }
                )

        extras = set(actual_fields.keys()) - expected_names

        if missing or type_mismatches or (extras and not self.config.allow_extra_columns):
            raise ValueError(
                f"Schema Parquet inválido para {file_path}. "
                f"missing={missing} type_mismatches={type_mismatches} extras={sorted(extras)}"
            )

    def _normalize_table_columns(self, table: pa.Table) -> pa.Table:
        if self.config.column_normalize_mode == ColumnNormalizeMode.NONE:
            return table

        normalized = [normalize_column_name(name, self.config.column_normalize_mode) for name in table.column_names]
        return table.rename_columns(normalized)

    def _apply_filters_if_possible(self, table: pa.Table) -> pa.Table:
        if not self.config.filters:
            return table

        # Suporte simples a filtros em formato:
        # [{"column": "status", "op": "=", "value": "active"}]
        # Para filtros complexos, prefira usar pyarrow.dataset no composition root.
        try:
            mask = None
            for item in self.config.filters:
                column = normalize_column_name(str(item["column"]), self.config.column_normalize_mode)
                op = str(item.get("op", "=")).lower()
                value = item.get("value")

                if column not in table.column_names:
                    continue

                expr = build_arrow_filter_expression(table[column], op, value)
                mask = expr if mask is None else pc.and_(mask, expr)

            if mask is not None:
                return table.filter(mask)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Falha ao aplicar filtros em memória no Parquet. error=%s", exc)

        return table

    def _handle_invalid_record(self, record: ParquetRecord, exc: Exception) -> None:
        self.metrics.rows_failed += 1
        record.touch(LoadedRecordStatus.FAILED)

        if self.config.invalid_record_strategy == InvalidRecordStrategy.RAISE:
            raise exc

        if self.config.invalid_record_strategy == InvalidRecordStrategy.SKIP:
            self.metrics.rows_skipped += 1
            logger.warning(
                "Registro Parquet inválido ignorado. file=%s index=%s error=%s",
                record.source_file,
                record.record_index,
                exc,
            )
            return

        self._send_to_dlq(record, exc)

    def _send_to_dlq(self, record: ParquetRecord, exc: Exception) -> None:
        if not self.config.dlq_path:
            self.metrics.rows_skipped += 1
            logger.warning("DLQ Parquet não configurada. Registro será ignorado. error=%s", exc)
            return

        self.config.dlq_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": LoadedRecordStatus.SENT_TO_DLQ.value,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
            "failed_at": utc_now_iso(),
            "record": model_to_dict(record),
        }

        with self.config.dlq_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=json_default) + "\n")

        self.metrics.rows_sent_to_dlq += 1
        logger.warning(
            "Registro Parquet enviado para DLQ. file=%s index=%s dlq=%s error=%s",
            record.source_file,
            record.record_index,
            self.config.dlq_path,
            exc,
        )

    def _validate_file_before_load(self, file_path: Path) -> None:
        if not file_path.exists():
            raise FileNotFoundError(f"Arquivo Parquet não encontrado: {file_path}")

        size = file_path.stat().st_size
        if size == 0:
            message = f"Arquivo Parquet vazio: {file_path}"
            if self.config.fail_on_empty_file:
                raise ValueError(message)
            logger.warning(message)

        if self.config.max_file_size_mb is not None:
            max_bytes = self.config.max_file_size_mb * 1024 * 1024
            if size > max_bytes:
                raise ValueError(f"Arquivo excede limite de tamanho: {file_path} size={size} max={max_bytes}")

    def _archive_file(self, file_path: Path) -> None:
        if not self.config.archive_dir:
            return
        self.config.archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = self.config.archive_dir / f"{file_path.stem}.{timestamp}{file_path.suffix}"
        shutil.move(str(file_path), str(destination))
        logger.info("Arquivo Parquet arquivado. source=%s destination=%s", file_path, destination)


# =============================================================================
# Utilitários
# =============================================================================


def build_arrow_filter_expression(column: pa.ChunkedArray, op: str, value: Any) -> pa.Array:
    if op in {"=", "==", "eq"}:
        return pc.equal(column, value)
    if op in {"!=", "ne"}:
        return pc.not_equal(column, value)
    if op in {">", "gt"}:
        return pc.greater(column, value)
    if op in {">=", "gte"}:
        return pc.greater_equal(column, value)
    if op in {"<", "lt"}:
        return pc.less(column, value)
    if op in {"<=", "lte"}:
        return pc.less_equal(column, value)
    if op == "in":
        return pc.is_in(column, value_set=pa.array(value))
    if op in {"is_null", "null"}:
        return pc.is_null(column)
    if op in {"is_not_null", "not_null"}:
        return pc.invert(pc.is_null(column))
    raise ValueError(f"Operador de filtro não suportado: {op}")


def normalize_column_name(name: str, mode: ColumnNormalizeMode) -> str:
    value = name.strip().replace("\ufeff", "")

    if mode == ColumnNormalizeMode.NONE:
        return value
    if mode == ColumnNormalizeMode.LOWER:
        return value.lower()

    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def get_nested_value(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def parse_json_env(name: str, default: Any) -> Any:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Variável {name} não contém JSON válido") from exc


def parse_expected_columns_env(name: str) -> Tuple[ExpectedColumn, ...]:
    raw = os.getenv(name)
    if not raw:
        return tuple()

    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Variável {name} não contém JSON válido") from exc

    if not isinstance(items, list):
        raise ValueError(f"{name} deve ser uma lista JSON.")

    result: List[ExpectedColumn] = []
    for item in items:
        if isinstance(item, str):
            result.append(ExpectedColumn(name=item))
        elif isinstance(item, Mapping):
            result.append(
                ExpectedColumn(
                    name=str(item["name"]),
                    arrow_type=str(item["type"]) if item.get("type") else None,
                    required=bool(item.get("required", True)),
                )
            )
        else:
            raise ValueError(f"Item inválido em {name}: {item}")
    return tuple(result)


def model_to_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()  # type: ignore[no-any-return]
    return model.dict()  # type: ignore[no-any-return]


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, Path, Enum)):
        return str(value)
    if hasattr(value, "as_py"):
        return value.as_py()
    return str(value)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "sim", "s"}


# =============================================================================
# Bootstrap CLI simples
# =============================================================================


def main() -> None:
    config = ParquetLoaderConfig.from_env()
    output_path = os.getenv("PARQUET_OUTPUT_JSONL")

    sink: ParquetRecordSink
    if output_path:
        sink = JsonlParquetRecordSink(output_path)
    else:
        sink = LoggingParquetRecordSink()

    required_fields = [
        normalize_column_name(col.name, config.column_normalize_mode)
        for col in config.expected_columns
        if col.required
    ]

    validator: ParquetRecordValidator = (
        RequiredFieldsValidator(required_fields) if required_fields else NoOpParquetRecordValidator()
    )

    loader = EnterpriseParquetLoader(
        config=config,
        sink=sink,
        validator=validator,
        transformer=NoOpParquetRecordTransformer(),
    )
    loader.run()


if __name__ == "__main__":
    main()

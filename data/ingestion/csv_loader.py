"""
data/ingestion/csv_loader.py

CSV Loader enterprise para pipelines de ingestão.

Recursos principais:
- Leitura de CSV/TSV/TXT e arquivos compactados .gz.
- Streaming linha a linha para arquivos grandes.
- Detecção opcional de delimiter e encoding.
- Normalização de nomes de colunas.
- Validação por campos obrigatórios, tipos e validadores customizados.
- Transformação/enriquecimento plugável.
- Processamento em batches.
- DLQ local em JSONL para linhas inválidas.
- Métricas de arquivos, registros, batches, erros e latência.
- Logs estruturados.
- Arquivamento opcional de arquivos processados.
- Suporte a dry-run e sink JSONL/callback/memória.

Dependências recomendadas:
    pip install pydantic

Dependências opcionais:
    pip install charset-normalizer
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import os
import re
import shutil
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Generator, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple, Union

try:
    from pydantic import BaseModel, Field, ValidationError
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Dependência ausente: instale com `pip install pydantic`.") from exc

try:
    from charset_normalizer import from_path as charset_from_path
except ImportError:  # pragma: no cover
    charset_from_path = None  # type: ignore[assignment]


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


def build_logger(name: str = "data.ingestion.csv_loader") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ContextFilter(service_name=os.getenv("SERVICE_NAME", "csv-loader")))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = build_logger()


# =============================================================================
# Enums e modelos
# =============================================================================


class InvalidRowStrategy(str, Enum):
    RAISE = "raise"
    SKIP = "skip"
    SEND_TO_DLQ = "send_to_dlq"


class ColumnNormalizeMode(str, Enum):
    NONE = "none"
    LOWER = "lower"
    SNAKE_CASE = "snake_case"


class EmptyValueMode(str, Enum):
    KEEP = "keep"
    NONE = "none"
    DROP = "drop"


class LoadedRowStatus(str, Enum):
    LOADED = "loaded"
    VALIDATED = "validated"
    TRANSFORMED = "transformed"
    WRITTEN = "written"
    FAILED = "failed"
    SKIPPED = "skipped"
    SENT_TO_DLQ = "sent_to_dlq"


class CSVRecord(BaseModel):
    source_file: str
    row_number: int
    record_index: int
    data: Dict[str, Any] = Field(default_factory=dict)
    raw: Optional[Dict[str, Any]] = None
    loaded_at: str = Field(default_factory=lambda: utc_now_iso())
    status: LoadedRowStatus = LoadedRowStatus.LOADED
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def touch(self, status: Optional[LoadedRowStatus] = None) -> "CSVRecord":
        if status:
            self.status = status
        self.metadata["updated_at"] = utc_now_iso()
        return self


@dataclass(frozen=True)
class CSVLoaderConfig:
    input_path: Path
    delimiter: Optional[str] = None
    quotechar: str = '"'
    escapechar: Optional[str] = None
    encoding: Optional[str] = "utf-8"
    detect_encoding: bool = False
    detect_dialect: bool = True
    has_header: bool = True
    fieldnames: Optional[Tuple[str, ...]] = None
    batch_size: int = 1000
    recursive: bool = False
    file_patterns: Tuple[str, ...] = ("*.csv", "*.tsv", "*.txt", "*.csv.gz", "*.tsv.gz")
    column_normalize_mode: ColumnNormalizeMode = ColumnNormalizeMode.SNAKE_CASE
    empty_value_mode: EmptyValueMode = EmptyValueMode.NONE
    trim_values: bool = True
    preserve_raw_row: bool = False
    required_columns: Tuple[str, ...] = tuple()
    type_casts: Mapping[str, str] = None  # type: ignore[assignment]
    invalid_row_strategy: InvalidRowStrategy = InvalidRowStrategy.SEND_TO_DLQ
    dlq_path: Optional[Path] = Path("data/dlq/csv_loader_dlq.jsonl")
    archive_processed: bool = False
    archive_dir: Optional[Path] = Path("data/archive/csv")
    max_file_size_mb: Optional[int] = None
    fail_on_empty_file: bool = False
    dry_run: bool = False

    @staticmethod
    def from_env() -> "CSVLoaderConfig":
        input_path_raw = os.getenv("CSV_INPUT_PATH")
        if not input_path_raw:
            raise ValueError("CSV_INPUT_PATH é obrigatório.")

        required = tuple(x.strip() for x in os.getenv("CSV_REQUIRED_COLUMNS", "").split(",") if x.strip())
        type_casts = parse_json_env("CSV_TYPE_CASTS_JSON", default={})
        fieldnames_raw = os.getenv("CSV_FIELDNAMES")
        fieldnames = tuple(x.strip() for x in fieldnames_raw.split(",") if x.strip()) if fieldnames_raw else None
        dlq_raw = os.getenv("CSV_DLQ_PATH", "data/dlq/csv_loader_dlq.jsonl")
        archive_raw = os.getenv("CSV_ARCHIVE_DIR", "data/archive/csv")

        return CSVLoaderConfig(
            input_path=Path(input_path_raw),
            delimiter=os.getenv("CSV_DELIMITER") or None,
            quotechar=os.getenv("CSV_QUOTECHAR", '"'),
            escapechar=os.getenv("CSV_ESCAPECHAR") or None,
            encoding=os.getenv("CSV_ENCODING", "utf-8") or None,
            detect_encoding=env_bool("CSV_DETECT_ENCODING", False),
            detect_dialect=env_bool("CSV_DETECT_DIALECT", True),
            has_header=env_bool("CSV_HAS_HEADER", True),
            fieldnames=fieldnames,
            batch_size=int(os.getenv("CSV_BATCH_SIZE", "1000")),
            recursive=env_bool("CSV_RECURSIVE", False),
            file_patterns=tuple(
                x.strip()
                for x in os.getenv("CSV_FILE_PATTERNS", "*.csv,*.tsv,*.txt,*.csv.gz,*.tsv.gz").split(",")
                if x.strip()
            ),
            column_normalize_mode=ColumnNormalizeMode(os.getenv("CSV_COLUMN_NORMALIZE_MODE", ColumnNormalizeMode.SNAKE_CASE.value)),
            empty_value_mode=EmptyValueMode(os.getenv("CSV_EMPTY_VALUE_MODE", EmptyValueMode.NONE.value)),
            trim_values=env_bool("CSV_TRIM_VALUES", True),
            preserve_raw_row=env_bool("CSV_PRESERVE_RAW_ROW", False),
            required_columns=required,
            type_casts=type_casts,
            invalid_row_strategy=InvalidRowStrategy(os.getenv("CSV_INVALID_ROW_STRATEGY", InvalidRowStrategy.SEND_TO_DLQ.value)),
            dlq_path=Path(dlq_raw) if dlq_raw else None,
            archive_processed=env_bool("CSV_ARCHIVE_PROCESSED", False),
            archive_dir=Path(archive_raw) if archive_raw else None,
            max_file_size_mb=int(os.getenv("CSV_MAX_FILE_SIZE_MB")) if os.getenv("CSV_MAX_FILE_SIZE_MB") else None,
            fail_on_empty_file=env_bool("CSV_FAIL_ON_EMPTY_FILE", False),
            dry_run=env_bool("CSV_DRY_RUN", False),
        )


@dataclass
class CSVLoaderMetrics:
    files_seen: int = 0
    files_loaded: int = 0
    files_failed: int = 0
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


class CSVRecordValidator(Protocol):
    def validate(self, record: CSVRecord) -> CSVRecord:
        """Valida e retorna o registro CSV."""


class CSVRecordTransformer(Protocol):
    def transform(self, record: CSVRecord) -> CSVRecord:
        """Transforma/normaliza o registro CSV."""


class CSVRecordSink(Protocol):
    def write_batch(self, records: List[CSVRecord]) -> None:
        """Persiste um batch de registros CSV."""


# =============================================================================
# Implementações base
# =============================================================================


class NoOpCSVRecordValidator:
    def validate(self, record: CSVRecord) -> CSVRecord:
        return record.touch(LoadedRowStatus.VALIDATED)


class NoOpCSVRecordTransformer:
    def transform(self, record: CSVRecord) -> CSVRecord:
        return record.touch(LoadedRowStatus.TRANSFORMED)


class RequiredColumnsValidator:
    def __init__(self, required_columns: Sequence[str]) -> None:
        self.required_columns = list(required_columns)

    def validate(self, record: CSVRecord) -> CSVRecord:
        missing = [col for col in self.required_columns if record.data.get(col) in (None, "")]
        if missing:
            raise ValueError(f"Colunas obrigatórias ausentes/vazias: {missing}")
        return record.touch(LoadedRowStatus.VALIDATED)


class FunctionCSVTransformer:
    def __init__(self, fn: Callable[[CSVRecord], CSVRecord]) -> None:
        self.fn = fn

    def transform(self, record: CSVRecord) -> CSVRecord:
        return self.fn(record).touch(LoadedRowStatus.TRANSFORMED)


class LoggingCSVRecordSink:
    def write_batch(self, records: List[CSVRecord]) -> None:
        logger.info("Batch CSV recebido pelo sink. size=%s", len(records))


class JsonlCSVRecordSink:
    def __init__(self, output_path: Union[str, Path]) -> None:
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def write_batch(self, records: List[CSVRecord]) -> None:
        with self.output_path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(model_to_dict(record), ensure_ascii=False, default=json_default) + "\n")
        logger.info("Batch CSV gravado em JSONL. output=%s size=%s", self.output_path, len(records))


class CallbackCSVRecordSink:
    def __init__(self, callback: Callable[[List[CSVRecord]], None]) -> None:
        self.callback = callback

    def write_batch(self, records: List[CSVRecord]) -> None:
        self.callback(records)


class MemoryCSVRecordSink:
    def __init__(self) -> None:
        self.records: List[CSVRecord] = []

    def write_batch(self, records: List[CSVRecord]) -> None:
        self.records.extend(records)


# =============================================================================
# Loader principal
# =============================================================================


class EnterpriseCSVLoader:
    def __init__(
        self,
        config: CSVLoaderConfig,
        sink: Optional[CSVRecordSink] = None,
        validator: Optional[CSVRecordValidator] = None,
        transformer: Optional[CSVRecordTransformer] = None,
    ) -> None:
        self.config = config
        self.sink = sink or LoggingCSVRecordSink()
        self.validator = validator or self._default_validator()
        self.transformer = transformer or NoOpCSVRecordTransformer()
        self.metrics = CSVLoaderMetrics()

    def run(self) -> CSVLoaderMetrics:
        started = time.perf_counter()
        logger.info(
            "Iniciando CSV loader. input_path=%s batch_size=%s delimiter=%s",
            self.config.input_path,
            self.config.batch_size,
            self.config.delimiter,
        )

        try:
            for file_path in self.discover_files():
                self._load_file_safely(file_path)
        finally:
            self.metrics.total_seconds += time.perf_counter() - started
            logger.info("CSV loader finalizado. metrics=%s", json.dumps(self.metrics.snapshot()))

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

    def iter_records(self, file_path: Path) -> Generator[CSVRecord, None, None]:
        self._validate_file_before_load(file_path)
        encoding = self._resolve_encoding(file_path)
        dialect = self._resolve_dialect(file_path, encoding)

        with self._open_text_file(file_path, encoding) as handle:
            if self.config.has_header:
                reader = csv.DictReader(handle, dialect=dialect)
                original_fieldnames = list(reader.fieldnames or [])
                normalized_fieldnames = self._normalize_columns(original_fieldnames)
                reader.fieldnames = normalized_fieldnames
                start_row_number = 2
            else:
                fieldnames = list(self.config.fieldnames or [])
                if not fieldnames:
                    raise ValueError("CSV sem header exige CSV_FIELDNAMES/config.fieldnames.")
                normalized_fieldnames = self._normalize_columns(fieldnames)
                reader = csv.DictReader(handle, fieldnames=normalized_fieldnames, dialect=dialect)
                original_fieldnames = fieldnames
                start_row_number = 1

            for index, row in enumerate(reader, start=1):
                row_number = index + start_row_number - 1
                self.metrics.rows_seen += 1

                try:
                    cleaned = self._clean_row(row)
                    casted = self._cast_row(cleaned)
                    raw = dict(row) if self.config.preserve_raw_row else None

                    yield CSVRecord(
                        source_file=str(file_path),
                        row_number=row_number,
                        record_index=index,
                        data=casted,
                        raw=raw,
                        metadata={
                            "file_name": file_path.name,
                            "file_size_bytes": file_path.stat().st_size if file_path.exists() else None,
                            "encoding": encoding,
                            "delimiter": getattr(dialect, "delimiter", self.config.delimiter),
                            "original_fieldnames": original_fieldnames,
                            "normalized_fieldnames": normalized_fieldnames,
                        },
                    )
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    error_record = CSVRecord(
                        source_file=str(file_path),
                        row_number=row_number,
                        record_index=index,
                        data={"_invalid_row": dict(row)},
                        raw=dict(row),
                        status=LoadedRowStatus.FAILED,
                        metadata={"error": str(exc), "file_name": file_path.name},
                    )
                    self._handle_invalid_record(error_record, exc)

    def iter_batches(self, file_path: Path) -> Generator[List[CSVRecord], None, None]:
        batch: List[CSVRecord] = []

        for record in self.iter_records(file_path):
            try:
                validated = self.validator.validate(record)
                self.metrics.rows_validated += 1

                transformed = self.transformer.transform(validated)
                self.metrics.rows_transformed += 1

                batch.append(transformed)

                if len(batch) >= self.config.batch_size:
                    self.metrics.batches_emitted += 1
                    yield batch
                    batch = []

            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._handle_invalid_record(record, exc)

        if batch:
            self.metrics.batches_emitted += 1
            yield batch

    def _load_file_safely(self, file_path: Path) -> None:
        logger.info("Carregando arquivo CSV. file=%s", file_path)
        started = time.perf_counter()

        try:
            for batch in self.iter_batches(file_path):
                if self.config.dry_run:
                    logger.info("Dry-run ativo. Batch CSV não será persistido. size=%s", len(batch))
                else:
                    self.sink.write_batch(batch)
                self.metrics.rows_written += len(batch)

            self.metrics.files_loaded += 1
            self.metrics.last_loaded_at = utc_now_iso()

            if self.config.archive_processed:
                self._archive_file(file_path)

            logger.info(
                "Arquivo CSV carregado com sucesso. file=%s elapsed=%.3fs",
                file_path,
                time.perf_counter() - started,
            )

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.files_failed += 1
            logger.exception("Falha ao carregar arquivo CSV. file=%s error=%s", file_path, exc)
            raise

    def _default_validator(self) -> CSVRecordValidator:
        if self.config.required_columns:
            normalized = self._normalize_columns(list(self.config.required_columns))
            return RequiredColumnsValidator(normalized)
        return NoOpCSVRecordValidator()

    def _clean_row(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        cleaned: Dict[str, Any] = {}

        for key, value in row.items():
            if key is None:
                continue

            normalized_key = normalize_column_name(str(key), self.config.column_normalize_mode)
            normalized_value = value

            if isinstance(normalized_value, str) and self.config.trim_values:
                normalized_value = normalized_value.strip()

            if normalized_value == "":
                if self.config.empty_value_mode == EmptyValueMode.NONE:
                    normalized_value = None
                elif self.config.empty_value_mode == EmptyValueMode.DROP:
                    continue

            cleaned[normalized_key] = normalized_value

        return cleaned

    def _cast_row(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        type_casts = dict(self.config.type_casts or {})
        if not type_casts:
            return dict(row)

        output = dict(row)
        for field, cast_type in type_casts.items():
            normalized_field = normalize_column_name(field, self.config.column_normalize_mode)
            if normalized_field not in output:
                continue
            output[normalized_field] = cast_value(output[normalized_field], cast_type)
        return output

    def _handle_invalid_record(self, record: CSVRecord, exc: Exception) -> None:
        self.metrics.rows_failed += 1
        record.touch(LoadedRowStatus.FAILED)

        if self.config.invalid_row_strategy == InvalidRowStrategy.RAISE:
            raise exc

        if self.config.invalid_row_strategy == InvalidRowStrategy.SKIP:
            self.metrics.rows_skipped += 1
            logger.warning(
                "Linha CSV inválida ignorada. file=%s row=%s error=%s",
                record.source_file,
                record.row_number,
                exc,
            )
            return

        self._send_to_dlq(record, exc)

    def _send_to_dlq(self, record: CSVRecord, exc: Exception) -> None:
        if not self.config.dlq_path:
            self.metrics.rows_skipped += 1
            logger.warning("DLQ CSV não configurada. Linha será ignorada. error=%s", exc)
            return

        self.config.dlq_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": LoadedRowStatus.SENT_TO_DLQ.value,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
            "failed_at": utc_now_iso(),
            "record": model_to_dict(record),
        }

        with self.config.dlq_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=json_default) + "\n")

        self.metrics.rows_sent_to_dlq += 1
        logger.warning(
            "Linha CSV enviada para DLQ. file=%s row=%s dlq=%s error=%s",
            record.source_file,
            record.row_number,
            self.config.dlq_path,
            exc,
        )

    def _resolve_encoding(self, file_path: Path) -> str:
        if self.config.detect_encoding and charset_from_path is not None and not file_path.name.lower().endswith(".gz"):
            result = charset_from_path(str(file_path)).best()  # type: ignore[operator]
            if result and result.encoding:
                logger.info("Encoding detectado. file=%s encoding=%s", file_path, result.encoding)
                return str(result.encoding)
        return self.config.encoding or "utf-8"

    def _resolve_dialect(self, file_path: Path, encoding: str) -> csv.Dialect:
        if self.config.delimiter:
            class CustomDialect(csv.excel):
                delimiter = self.config.delimiter or ","
                quotechar = self.config.quotechar
                escapechar = self.config.escapechar
                skipinitialspace = False

            return CustomDialect

        if self.config.detect_dialect:
            try:
                with self._open_text_file(file_path, encoding) as handle:
                    sample = handle.read(8192)
                dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
                logger.info("Dialeto CSV detectado. file=%s delimiter=%s", file_path, repr(dialect.delimiter))
                return dialect
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Falha ao detectar dialeto CSV. Usando excel padrão. file=%s error=%s", file_path, exc)

        if file_path.name.lower().endswith((".tsv", ".tsv.gz")):
            class TSVDialect(csv.excel_tab):
                quotechar = self.config.quotechar
                escapechar = self.config.escapechar

            return TSVDialect

        return csv.excel

    def _normalize_columns(self, columns: Sequence[str]) -> List[str]:
        return [normalize_column_name(col, self.config.column_normalize_mode) for col in columns]

    def _validate_file_before_load(self, file_path: Path) -> None:
        if not file_path.exists():
            raise FileNotFoundError(f"Arquivo CSV não encontrado: {file_path}")

        size = file_path.stat().st_size
        if size == 0:
            message = f"Arquivo CSV vazio: {file_path}"
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
        logger.info("Arquivo CSV arquivado. source=%s destination=%s", file_path, destination)

    def _open_text_file(self, file_path: Path, encoding: str):
        if file_path.name.lower().endswith(".gz"):
            return gzip.open(file_path, "rt", encoding=encoding, newline="")
        return file_path.open("r", encoding=encoding, newline="")


# =============================================================================
# Utilitários
# =============================================================================


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


def cast_value(value: Any, cast_type: str) -> Any:
    if value is None:
        return None

    cast_type = cast_type.lower().strip()

    if cast_type in {"str", "string"}:
        return str(value)
    if cast_type in {"int", "integer"}:
        return int(str(value).replace(".", "").replace(",", ".") if "," in str(value) else value)
    if cast_type in {"float", "double", "decimal"}:
        return float(str(value).replace(".", "").replace(",", ".") if "," in str(value) else value)
    if cast_type in {"bool", "boolean"}:
        return str(value).strip().lower() in {"1", "true", "yes", "y", "sim", "s"}
    if cast_type in {"date", "datetime"}:
        return parse_datetime_like(str(value))

    raise ValueError(f"Tipo de cast não suportado: {cast_type}")


def parse_datetime_like(value: str) -> str:
    cleaned = value.strip()
    formats = [
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y",
        "%d/%m/%Y %H:%M:%S",
        "%m/%d/%Y",
        "%m/%d/%Y %H:%M:%S",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(cleaned).isoformat()
    except ValueError as exc:
        raise ValueError(f"Data inválida: {value}") from exc


def parse_json_env(name: str, default: Any) -> Any:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Variável {name} não contém JSON válido") from exc


def model_to_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()  # type: ignore[no-any-return]
    return model.dict()  # type: ignore[no-any-return]


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, Path, Enum)):
        return str(value)
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
    config = CSVLoaderConfig.from_env()
    output_path = os.getenv("CSV_OUTPUT_JSONL")

    sink: CSVRecordSink
    if output_path:
        sink = JsonlCSVRecordSink(output_path)
    else:
        sink = LoggingCSVRecordSink()

    loader = EnterpriseCSVLoader(
        config=config,
        sink=sink,
        validator=None,
        transformer=NoOpCSVRecordTransformer(),
    )
    loader.run()


if __name__ == "__main__":
    main()

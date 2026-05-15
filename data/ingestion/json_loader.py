"""
data/ingestion/json_loader.py

Loader JSON enterprise para pipelines de ingestão.

Recursos principais:
- Suporte a JSON, JSONL/NDJSON e arquivos .gz.
- Leitura eficiente com streaming para grandes volumes.
- Descoberta de arquivos por diretório, padrão e recursividade.
- Validação estrutural com Pydantic e validadores customizados.
- Transformação/normalização por hooks extensíveis.
- Processamento em batches.
- DLQ local em JSONL para registros inválidos.
- Métricas internas de arquivos, registros, batches, erros e latência.
- Logs estruturados.
- Arquivamento opcional de arquivos processados.
- Controle de tamanho máximo, arquivo vazio e estratégia de erro.

Dependências recomendadas:
    pip install pydantic ijson

Observação:
- `ijson` é opcional, mas recomendado para streaming de arrays JSON muito grandes.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Generator, Iterable, List, Mapping, Optional, Protocol, Tuple, Union

try:
    import ijson
except ImportError:  # pragma: no cover
    ijson = None  # type: ignore[assignment]

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


def build_logger(name: str = "data.ingestion.json_loader") -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ContextFilter(service_name=os.getenv("SERVICE_NAME", "json-loader")))

    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = build_logger()


# =============================================================================
# Enums e Models
# =============================================================================


class JSONFormat(str, Enum):
    AUTO = "auto"
    JSON = "json"
    JSONL = "jsonl"
    NDJSON = "ndjson"


class JSONRootMode(str, Enum):
    AUTO = "auto"
    OBJECT = "object"
    ARRAY = "array"
    ARRAY_FIELD = "array_field"


class InvalidRecordStrategy(str, Enum):
    RAISE = "raise"
    SKIP = "skip"
    SEND_TO_DLQ = "send_to_dlq"


class LoadedRecordStatus(str, Enum):
    LOADED = "loaded"
    VALIDATED = "validated"
    SKIPPED = "skipped"
    FAILED = "failed"
    SENT_TO_DLQ = "sent_to_dlq"


class JSONRecord(BaseModel):
    source_file: str
    record_index: int
    data: Dict[str, Any] = Field(default_factory=dict)
    loaded_at: str = Field(default_factory=lambda: utc_now_iso())
    metadata: Dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class JSONLoaderConfig:
    input_path: Path
    format: JSONFormat = JSONFormat.AUTO
    root_mode: JSONRootMode = JSONRootMode.AUTO
    array_field: Optional[str] = None
    batch_size: int = 500
    encoding: str = "utf-8"
    recursive: bool = False
    file_patterns: Tuple[str, ...] = ("*.json", "*.jsonl", "*.ndjson", "*.json.gz", "*.jsonl.gz", "*.ndjson.gz")
    invalid_record_strategy: InvalidRecordStrategy = InvalidRecordStrategy.SEND_TO_DLQ
    dlq_path: Optional[Path] = None
    archive_processed: bool = False
    archive_dir: Optional[Path] = None
    max_file_size_mb: Optional[int] = None
    fail_on_empty_file: bool = False
    preserve_raw_line: bool = False
    skip_blank_lines: bool = True
    strict_jsonl: bool = False
    use_ijson: bool = True

    @staticmethod
    def from_env() -> "JSONLoaderConfig":
        input_path_raw = os.getenv("JSON_INPUT_PATH")
        if not input_path_raw:
            raise ValueError("JSON_INPUT_PATH é obrigatório.")

        return JSONLoaderConfig(
            input_path=Path(input_path_raw),
            format=JSONFormat(os.getenv("JSON_LOADER_FORMAT", JSONFormat.AUTO.value)),
            root_mode=JSONRootMode(os.getenv("JSON_ROOT_MODE", JSONRootMode.AUTO.value)),
            array_field=os.getenv("JSON_ARRAY_FIELD") or None,
            batch_size=int(os.getenv("JSON_BATCH_SIZE", "500")),
            encoding=os.getenv("JSON_ENCODING", "utf-8"),
            recursive=env_bool("JSON_RECURSIVE", False),
            file_patterns=tuple(
                item.strip()
                for item in os.getenv(
                    "JSON_FILE_PATTERNS",
                    "*.json,*.jsonl,*.ndjson,*.json.gz,*.jsonl.gz,*.ndjson.gz",
                ).split(",")
                if item.strip()
            ),
            invalid_record_strategy=InvalidRecordStrategy(
                os.getenv("JSON_INVALID_RECORD_STRATEGY", InvalidRecordStrategy.SEND_TO_DLQ.value)
            ),
            dlq_path=Path(os.getenv("JSON_DLQ_PATH", "data/dlq/json_loader_dlq.jsonl")),
            archive_processed=env_bool("JSON_ARCHIVE_PROCESSED", False),
            archive_dir=Path(os.getenv("JSON_ARCHIVE_DIR", "data/archive/json")),
            max_file_size_mb=int(os.getenv("JSON_MAX_FILE_SIZE_MB"))
            if os.getenv("JSON_MAX_FILE_SIZE_MB")
            else None,
            fail_on_empty_file=env_bool("JSON_FAIL_ON_EMPTY_FILE", False),
            preserve_raw_line=env_bool("JSON_PRESERVE_RAW_LINE", False),
            skip_blank_lines=env_bool("JSON_SKIP_BLANK_LINES", True),
            strict_jsonl=env_bool("JSON_STRICT_JSONL", False),
            use_ijson=env_bool("JSON_USE_IJSON", True),
        )


@dataclass
class JSONLoaderMetrics:
    files_seen: int = 0
    files_loaded: int = 0
    files_failed: int = 0
    records_seen: int = 0
    records_validated: int = 0
    records_skipped: int = 0
    records_failed: int = 0
    records_sent_to_dlq: int = 0
    batches_emitted: int = 0
    total_seconds: float = 0.0
    last_loaded_at: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "files_seen": self.files_seen,
            "files_loaded": self.files_loaded,
            "files_failed": self.files_failed,
            "records_seen": self.records_seen,
            "records_validated": self.records_validated,
            "records_skipped": self.records_skipped,
            "records_failed": self.records_failed,
            "records_sent_to_dlq": self.records_sent_to_dlq,
            "batches_emitted": self.batches_emitted,
            "total_seconds": round(self.total_seconds, 6),
            "last_loaded_at": self.last_loaded_at,
        }


# =============================================================================
# Protocols
# =============================================================================


class JSONRecordValidator(Protocol):
    def validate(self, record: JSONRecord) -> JSONRecord:
        """Valida e retorna o registro JSON."""


class JSONRecordTransformer(Protocol):
    def transform(self, record: JSONRecord) -> JSONRecord:
        """Transforma/normaliza um registro JSON."""


class JSONRecordSink(Protocol):
    def write_batch(self, records: List[JSONRecord]) -> None:
        """Persiste um batch de registros JSON."""


# =============================================================================
# Implementações base
# =============================================================================


class NoOpJSONRecordValidator:
    def validate(self, record: JSONRecord) -> JSONRecord:
        return record


class NoOpJSONRecordTransformer:
    def transform(self, record: JSONRecord) -> JSONRecord:
        return record


class LoggingJSONRecordSink:
    def write_batch(self, records: List[JSONRecord]) -> None:
        logger.info("Batch JSON recebido pelo sink. size=%s", len(records))


class RequiredFieldsValidator:
    """Validador simples para exigir campos dentro de record.data."""

    def __init__(self, required_fields: Iterable[str]) -> None:
        self.required_fields = list(required_fields)

    def validate(self, record: JSONRecord) -> JSONRecord:
        missing = [field for field in self.required_fields if not get_nested_value(record.data, field)]
        if missing:
            raise ValueError(f"Campos obrigatórios ausentes: {missing}")
        return record


class PydanticSchemaValidator:
    """Valida record.data usando um modelo Pydantic externo."""

    def __init__(self, schema_model: Any) -> None:
        self.schema_model = schema_model

    def validate(self, record: JSONRecord) -> JSONRecord:
        try:
            if hasattr(self.schema_model, "model_validate"):
                self.schema_model.model_validate(record.data)
            else:
                self.schema_model.parse_obj(record.data)
        except ValidationError as exc:
            raise ValueError(f"Schema inválido: {exc}") from exc
        return record


class FunctionTransformer:
    def __init__(self, fn: Callable[[JSONRecord], JSONRecord]) -> None:
        self.fn = fn

    def transform(self, record: JSONRecord) -> JSONRecord:
        return self.fn(record)


# =============================================================================
# Loader principal
# =============================================================================


class EnterpriseJSONLoader:
    def __init__(
        self,
        config: JSONLoaderConfig,
        sink: Optional[JSONRecordSink] = None,
        validator: Optional[JSONRecordValidator] = None,
        transformer: Optional[JSONRecordTransformer] = None,
    ) -> None:
        self.config = config
        self.sink = sink or LoggingJSONRecordSink()
        self.validator = validator or NoOpJSONRecordValidator()
        self.transformer = transformer or NoOpJSONRecordTransformer()
        self.metrics = JSONLoaderMetrics()

    def run(self) -> JSONLoaderMetrics:
        started = time.perf_counter()
        logger.info(
            "Iniciando JSON loader. input_path=%s format=%s root_mode=%s batch_size=%s",
            self.config.input_path,
            self.config.format.value,
            self.config.root_mode.value,
            self.config.batch_size,
        )

        try:
            for file_path in self.discover_files():
                self._load_file_safely(file_path)
        finally:
            self.metrics.total_seconds += time.perf_counter() - started
            logger.info("JSON loader finalizado. metrics=%s", json.dumps(self.metrics.snapshot()))

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

    def iter_records(self, file_path: Path) -> Generator[JSONRecord, None, None]:
        self._validate_file_before_load(file_path)
        detected_format = self._detect_format(file_path)

        if detected_format in {JSONFormat.JSONL, JSONFormat.NDJSON}:
            yield from self._iter_jsonl(file_path)
            return

        yield from self._iter_json(file_path)

    def iter_batches(self, file_path: Path) -> Generator[List[JSONRecord], None, None]:
        batch: List[JSONRecord] = []

        for record in self.iter_records(file_path):
            try:
                transformed = self.transformer.transform(record)
                validated = self.validator.validate(transformed)
                self.metrics.records_validated += 1
                batch.append(validated)

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
        logger.info("Carregando arquivo JSON. file=%s", file_path)
        file_started = time.perf_counter()

        try:
            for batch in self.iter_batches(file_path):
                self.sink.write_batch(batch)

            self.metrics.files_loaded += 1
            self.metrics.last_loaded_at = utc_now_iso()

            if self.config.archive_processed:
                self._archive_file(file_path)

            logger.info(
                "Arquivo JSON carregado com sucesso. file=%s elapsed=%.3fs",
                file_path,
                time.perf_counter() - file_started,
            )

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.files_failed += 1
            logger.exception("Falha ao carregar arquivo JSON. file=%s error=%s", file_path, exc)
            raise

    def _iter_jsonl(self, file_path: Path) -> Generator[JSONRecord, None, None]:
        with self._open_text_file(file_path) as handle:
            for line_number, line in enumerate(handle, start=1):
                raw_line = line.rstrip("\n")

                if self.config.skip_blank_lines and not raw_line.strip():
                    continue

                try:
                    payload = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    if self.config.strict_jsonl:
                        raise ValueError(f"JSONL inválido na linha {line_number}: {exc}") from exc
                    record = self._build_error_record(file_path, line_number, raw_line, exc)
                    self._handle_invalid_record(record, exc)
                    continue

                if not isinstance(payload, dict):
                    exc = ValueError(f"Linha JSONL deve ser objeto JSON. line={line_number}")
                    record = self._build_error_record(file_path, line_number, raw_line, exc, payload=payload)
                    self._handle_invalid_record(record, exc)
                    continue

                self.metrics.records_seen += 1
                metadata = {
                    "format": JSONFormat.JSONL.value,
                    "line_number": line_number,
                    "status": LoadedRecordStatus.LOADED.value,
                    "file_name": file_path.name,
                    "file_size_bytes": file_path.stat().st_size if file_path.exists() else None,
                }

                if self.config.preserve_raw_line:
                    metadata["raw_line"] = raw_line

                yield JSONRecord(
                    source_file=str(file_path),
                    record_index=line_number,
                    data=payload,
                    metadata=metadata,
                )

    def _iter_json(self, file_path: Path) -> Generator[JSONRecord, None, None]:
        root_mode = self.config.root_mode

        if root_mode == JSONRootMode.ARRAY_FIELD:
            if not self.config.array_field:
                raise ValueError("JSON_ARRAY_FIELD é obrigatório quando root_mode=array_field.")
            yield from self._iter_json_array_field(file_path, self.config.array_field)
            return

        if self.config.use_ijson and ijson is not None:
            detected_root = self._detect_root_mode(file_path) if root_mode == JSONRootMode.AUTO else root_mode

            if detected_root == JSONRootMode.ARRAY:
                yield from self._iter_json_array_stream(file_path)
                return

        yield from self._iter_json_full(file_path)

    def _iter_json_full(self, file_path: Path) -> Generator[JSONRecord, None, None]:
        with self._open_text_file(file_path) as handle:
            payload = json.load(handle)

        if isinstance(payload, list):
            for index, item in enumerate(payload, start=1):
                if not isinstance(item, dict):
                    exc = ValueError(f"Item do array JSON deve ser objeto. index={index}")
                    record = self._build_error_record(file_path, index, None, exc, payload=item)
                    self._handle_invalid_record(record, exc)
                    continue

                self.metrics.records_seen += 1
                yield self._build_record(file_path, index, item, JSONRootMode.ARRAY.value)
            return

        if isinstance(payload, dict):
            if self.config.root_mode == JSONRootMode.ARRAY_FIELD:
                array_field = self.config.array_field
                if not array_field:
                    raise ValueError("array_field não informado.")
                records = get_nested_value(payload, array_field)
                if not isinstance(records, list):
                    raise ValueError(f"Campo {array_field} não é uma lista.")
                for index, item in enumerate(records, start=1):
                    if not isinstance(item, dict):
                        exc = ValueError(f"Item do campo {array_field} deve ser objeto. index={index}")
                        record = self._build_error_record(file_path, index, None, exc, payload=item)
                        self._handle_invalid_record(record, exc)
                        continue
                    self.metrics.records_seen += 1
                    yield self._build_record(file_path, index, item, JSONRootMode.ARRAY_FIELD.value)
                return

            self.metrics.records_seen += 1
            yield self._build_record(file_path, 1, payload, JSONRootMode.OBJECT.value)
            return

        raise ValueError(f"Root JSON inválido em {file_path}. Esperado objeto ou array.")

    def _iter_json_array_stream(self, file_path: Path) -> Generator[JSONRecord, None, None]:
        with self._open_binary_file(file_path) as handle:
            for index, item in enumerate(ijson.items(handle, "item"), start=1):  # type: ignore[union-attr]
                if not isinstance(item, dict):
                    exc = ValueError(f"Item do array JSON deve ser objeto. index={index}")
                    record = self._build_error_record(file_path, index, None, exc, payload=item)
                    self._handle_invalid_record(record, exc)
                    continue

                self.metrics.records_seen += 1
                yield self._build_record(file_path, index, item, JSONRootMode.ARRAY.value)

    def _iter_json_array_field(self, file_path: Path, array_field: str) -> Generator[JSONRecord, None, None]:
        if self.config.use_ijson and ijson is not None:
            ijson_path = ".".join(array_field.split(".")) + ".item"
            with self._open_binary_file(file_path) as handle:
                for index, item in enumerate(ijson.items(handle, ijson_path), start=1):  # type: ignore[union-attr]
                    if not isinstance(item, dict):
                        exc = ValueError(f"Item de {array_field} deve ser objeto. index={index}")
                        record = self._build_error_record(file_path, index, None, exc, payload=item)
                        self._handle_invalid_record(record, exc)
                        continue
                    self.metrics.records_seen += 1
                    yield self._build_record(file_path, index, item, JSONRootMode.ARRAY_FIELD.value)
            return

        yield from self._iter_json_full(file_path)

    def _build_record(
        self,
        file_path: Path,
        index: int,
        payload: Dict[str, Any],
        root_mode: str,
    ) -> JSONRecord:
        return JSONRecord(
            source_file=str(file_path),
            record_index=index,
            data=payload,
            metadata={
                "format": JSONFormat.JSON.value,
                "root_mode": root_mode,
                "status": LoadedRecordStatus.LOADED.value,
                "file_name": file_path.name,
                "file_size_bytes": file_path.stat().st_size if file_path.exists() else None,
            },
        )

    def _build_error_record(
        self,
        file_path: Path,
        index: int,
        raw_line: Optional[str],
        exc: Exception,
        payload: Any = None,
    ) -> JSONRecord:
        data: Dict[str, Any]
        if isinstance(payload, dict):
            data = payload
        else:
            data = {"_invalid_payload": payload}

        metadata: Dict[str, Any] = {
            "status": LoadedRecordStatus.FAILED.value,
            "error": str(exc),
            "file_name": file_path.name,
            "file_size_bytes": file_path.stat().st_size if file_path.exists() else None,
        }

        if raw_line is not None:
            metadata["raw_line"] = raw_line

        return JSONRecord(
            source_file=str(file_path),
            record_index=index,
            data=data,
            metadata=metadata,
        )

    def _handle_invalid_record(self, record: JSONRecord, exc: Exception) -> None:
        self.metrics.records_failed += 1

        if self.config.invalid_record_strategy == InvalidRecordStrategy.RAISE:
            raise exc

        if self.config.invalid_record_strategy == InvalidRecordStrategy.SKIP:
            self.metrics.records_skipped += 1
            logger.warning(
                "Registro JSON inválido ignorado. file=%s index=%s error=%s",
                record.source_file,
                record.record_index,
                exc,
            )
            return

        self._send_to_dlq(record, exc)

    def _send_to_dlq(self, record: JSONRecord, exc: Exception) -> None:
        if not self.config.dlq_path:
            self.metrics.records_skipped += 1
            logger.warning("DLQ não configurada. Registro será ignorado. error=%s", exc)
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
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

        self.metrics.records_sent_to_dlq += 1
        logger.warning(
            "Registro JSON enviado para DLQ. file=%s index=%s dlq=%s error=%s",
            record.source_file,
            record.record_index,
            self.config.dlq_path,
            exc,
        )

    def _validate_file_before_load(self, file_path: Path) -> None:
        if not file_path.exists():
            raise FileNotFoundError(f"Arquivo JSON não encontrado: {file_path}")

        size = file_path.stat().st_size
        if size == 0:
            message = f"Arquivo JSON vazio: {file_path}"
            if self.config.fail_on_empty_file:
                raise ValueError(message)
            logger.warning(message)

        if self.config.max_file_size_mb is not None:
            max_bytes = self.config.max_file_size_mb * 1024 * 1024
            if size > max_bytes:
                raise ValueError(
                    f"Arquivo excede limite de tamanho: {file_path} "
                    f"size={size} max={max_bytes}"
                )

    def _archive_file(self, file_path: Path) -> None:
        if not self.config.archive_dir:
            return

        self.config.archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = self.config.archive_dir / f"{file_path.stem}.{timestamp}{file_path.suffix}"
        shutil.move(str(file_path), str(destination))
        logger.info("Arquivo JSON arquivado. source=%s destination=%s", file_path, destination)

    def _detect_format(self, file_path: Path) -> JSONFormat:
        if self.config.format != JSONFormat.AUTO:
            return self.config.format

        name = file_path.name.lower()
        if name.endswith(".jsonl") or name.endswith(".jsonl.gz"):
            return JSONFormat.JSONL
        if name.endswith(".ndjson") or name.endswith(".ndjson.gz"):
            return JSONFormat.NDJSON
        return JSONFormat.JSON

    def _detect_root_mode(self, file_path: Path) -> JSONRootMode:
        with self._open_text_file(file_path) as handle:
            while True:
                char = handle.read(1)
                if not char:
                    break
                if char.isspace():
                    continue
                if char == "[":
                    return JSONRootMode.ARRAY
                if char == "{":
                    return JSONRootMode.OBJECT
                break
        raise ValueError(f"Não foi possível detectar root JSON em {file_path}")

    def _open_text_file(self, file_path: Path):
        if file_path.name.lower().endswith(".gz"):
            return gzip.open(file_path, "rt", encoding=self.config.encoding)
        return file_path.open("r", encoding=self.config.encoding)

    def _open_binary_file(self, file_path: Path):
        if file_path.name.lower().endswith(".gz"):
            return gzip.open(file_path, "rb")
        return file_path.open("rb")


# =============================================================================
# Sinks úteis
# =============================================================================


class JsonlJSONRecordSink:
    def __init__(self, output_path: Union[str, Path]) -> None:
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def write_batch(self, records: List[JSONRecord]) -> None:
        with self.output_path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(model_to_dict(record), ensure_ascii=False, default=str) + "\n")

        logger.info("Batch JSON gravado em JSONL. output=%s size=%s", self.output_path, len(records))


class CallbackJSONRecordSink:
    def __init__(self, callback: Callable[[List[JSONRecord]], None]) -> None:
        self.callback = callback

    def write_batch(self, records: List[JSONRecord]) -> None:
        self.callback(records)


class MemoryJSONRecordSink:
    """Sink útil para testes."""

    def __init__(self) -> None:
        self.records: List[JSONRecord] = []

    def write_batch(self, records: List[JSONRecord]) -> None:
        self.records.extend(records)


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


def model_to_dict(model: BaseModel) -> Dict[str, Any]:
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
# Bootstrap CLI simples
# =============================================================================


def main() -> None:
    config = JSONLoaderConfig.from_env()

    output_path = os.getenv("JSON_OUTPUT_JSONL")
    sink: JSONRecordSink

    if output_path:
        sink = JsonlJSONRecordSink(output_path)
    else:
        sink = LoggingJSONRecordSink()

    loader = EnterpriseJSONLoader(
        config=config,
        sink=sink,
        validator=NoOpJSONRecordValidator(),
        transformer=NoOpJSONRecordTransformer(),
    )
    loader.run()


if __name__ == "__main__":
    main()

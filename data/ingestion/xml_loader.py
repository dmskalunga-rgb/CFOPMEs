"""
data/ingestion/xml_loader.py

Loader XML enterprise para pipelines de ingestão.

Recursos principais:
- Carregamento seguro de XML com proteção contra ataques comuns.
- Suporte a arquivos pequenos e grandes via streaming iterativo.
- Normalização de XML para dicionários Python.
- Extração por XPath/tag-alvo.
- Validação estrutural customizável.
- Pipeline orientado a batches.
- Hooks para persistência, auditoria e enriquecimento.
- Métricas internas de leitura, sucesso, erro, skip e latência.
- Logs estruturados.
- Tratamento avançado de encoding, namespaces e atributos.
- Suporte a DLQ local em JSONL para registros inválidos.

Dependências recomendadas:
    pip install defusedxml lxml pydantic

Observação:
- Para máxima segurança, este módulo usa defusedxml por padrão.
- Para XPath avançado e streaming robusto em arquivos grandes, lxml é usado quando disponível.
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
    from defusedxml import ElementTree as SafeElementTree
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Dependência ausente: instale com `pip install defusedxml`.") from exc

try:
    from lxml import etree as LxmlEtree
except ImportError:  # pragma: no cover
    LxmlEtree = None  # type: ignore[assignment]

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


def build_logger(name: str = "data.ingestion.xml_loader") -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ContextFilter(service_name=os.getenv("SERVICE_NAME", "xml-loader")))

    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = build_logger()


# =============================================================================
# Enums e Models
# =============================================================================


class LoaderMode(str, Enum):
    FULL_DOCUMENT = "full_document"
    STREAMING = "streaming"


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


class XMLRecord(BaseModel):
    source_file: str
    record_index: int
    tag: str
    attributes: Dict[str, Any] = Field(default_factory=dict)
    data: Dict[str, Any] = Field(default_factory=dict)
    text: Optional[str] = None
    namespace: Optional[str] = None
    loaded_at: str = Field(default_factory=lambda: utc_now_iso())
    metadata: Dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class XMLLoaderConfig:
    input_path: Path
    target_tag: Optional[str] = None
    mode: LoaderMode = LoaderMode.STREAMING
    batch_size: int = 500
    encoding: Optional[str] = None
    include_attributes: bool = True
    include_text: bool = True
    strip_namespaces: bool = True
    preserve_empty_values: bool = False
    invalid_record_strategy: InvalidRecordStrategy = InvalidRecordStrategy.SEND_TO_DLQ
    dlq_path: Optional[Path] = None
    archive_processed: bool = False
    archive_dir: Optional[Path] = None
    recursive: bool = False
    file_patterns: Tuple[str, ...] = ("*.xml", "*.xml.gz")
    max_file_size_mb: Optional[int] = None
    fail_on_empty_file: bool = False
    use_lxml_streaming: bool = True

    @staticmethod
    def from_env() -> "XMLLoaderConfig":
        input_path_raw = os.getenv("XML_INPUT_PATH")
        if not input_path_raw:
            raise ValueError("XML_INPUT_PATH é obrigatório.")

        return XMLLoaderConfig(
            input_path=Path(input_path_raw),
            target_tag=os.getenv("XML_TARGET_TAG") or None,
            mode=LoaderMode(os.getenv("XML_LOADER_MODE", LoaderMode.STREAMING.value)),
            batch_size=int(os.getenv("XML_BATCH_SIZE", "500")),
            encoding=os.getenv("XML_ENCODING") or None,
            include_attributes=env_bool("XML_INCLUDE_ATTRIBUTES", True),
            include_text=env_bool("XML_INCLUDE_TEXT", True),
            strip_namespaces=env_bool("XML_STRIP_NAMESPACES", True),
            preserve_empty_values=env_bool("XML_PRESERVE_EMPTY_VALUES", False),
            invalid_record_strategy=InvalidRecordStrategy(
                os.getenv("XML_INVALID_RECORD_STRATEGY", InvalidRecordStrategy.SEND_TO_DLQ.value)
            ),
            dlq_path=Path(os.getenv("XML_DLQ_PATH", "data/dlq/xml_loader_dlq.jsonl")),
            archive_processed=env_bool("XML_ARCHIVE_PROCESSED", False),
            archive_dir=Path(os.getenv("XML_ARCHIVE_DIR", "data/archive/xml")),
            recursive=env_bool("XML_RECURSIVE", False),
            file_patterns=tuple(
                item.strip()
                for item in os.getenv("XML_FILE_PATTERNS", "*.xml,*.xml.gz").split(",")
                if item.strip()
            ),
            max_file_size_mb=int(os.getenv("XML_MAX_FILE_SIZE_MB"))
            if os.getenv("XML_MAX_FILE_SIZE_MB")
            else None,
            fail_on_empty_file=env_bool("XML_FAIL_ON_EMPTY_FILE", False),
            use_lxml_streaming=env_bool("XML_USE_LXML_STREAMING", True),
        )


@dataclass
class XMLLoaderMetrics:
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


class XMLRecordValidator(Protocol):
    def validate(self, record: XMLRecord) -> XMLRecord:
        """Valida e retorna o registro XML."""


class XMLRecordSink(Protocol):
    def write_batch(self, records: List[XMLRecord]) -> None:
        """Persiste um batch de registros."""


class XMLRecordTransformer(Protocol):
    def transform(self, record: XMLRecord) -> XMLRecord:
        """Transforma/normaliza um registro."""


# =============================================================================
# Implementações base
# =============================================================================


class NoOpXMLRecordValidator:
    def validate(self, record: XMLRecord) -> XMLRecord:
        return record


class NoOpXMLRecordTransformer:
    def transform(self, record: XMLRecord) -> XMLRecord:
        return record


class LoggingXMLRecordSink:
    def write_batch(self, records: List[XMLRecord]) -> None:
        logger.info("Batch XML recebido pelo sink. size=%s", len(records))


class RequiredFieldsValidator:
    """Validador simples para exigir campos dentro de record.data."""

    def __init__(self, required_fields: Iterable[str]) -> None:
        self.required_fields = list(required_fields)

    def validate(self, record: XMLRecord) -> XMLRecord:
        missing = [field for field in self.required_fields if not record.data.get(field)]
        if missing:
            raise ValueError(f"Campos obrigatórios ausentes: {missing}")
        return record


class FunctionTransformer:
    """Permite usar uma função simples como transformer."""

    def __init__(self, fn: Callable[[XMLRecord], XMLRecord]) -> None:
        self.fn = fn

    def transform(self, record: XMLRecord) -> XMLRecord:
        return self.fn(record)


# =============================================================================
# Loader principal
# =============================================================================


class EnterpriseXMLLoader:
    def __init__(
        self,
        config: XMLLoaderConfig,
        sink: Optional[XMLRecordSink] = None,
        validator: Optional[XMLRecordValidator] = None,
        transformer: Optional[XMLRecordTransformer] = None,
    ) -> None:
        self.config = config
        self.sink = sink or LoggingXMLRecordSink()
        self.validator = validator or NoOpXMLRecordValidator()
        self.transformer = transformer or NoOpXMLRecordTransformer()
        self.metrics = XMLLoaderMetrics()

    def run(self) -> XMLLoaderMetrics:
        started = time.perf_counter()
        logger.info(
            "Iniciando XML loader. input_path=%s mode=%s target_tag=%s batch_size=%s",
            self.config.input_path,
            self.config.mode.value,
            self.config.target_tag,
            self.config.batch_size,
        )

        try:
            for file_path in self.discover_files():
                self._load_file_safely(file_path)
        finally:
            self.metrics.total_seconds += time.perf_counter() - started
            logger.info("XML loader finalizado. metrics=%s", json.dumps(self.metrics.snapshot()))

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

        for pattern in self.config.file_patterns:
            for file_path in sorted(glob_fn(pattern)):
                if file_path.is_file():
                    self.metrics.files_seen += 1
                    yield file_path

    def iter_records(self, file_path: Path) -> Generator[XMLRecord, None, None]:
        self._validate_file_before_load(file_path)

        if self.config.mode == LoaderMode.FULL_DOCUMENT:
            yield from self._iter_full_document(file_path)
            return

        yield from self._iter_streaming(file_path)

    def iter_batches(self, file_path: Path) -> Generator[List[XMLRecord], None, None]:
        batch: List[XMLRecord] = []

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
        logger.info("Carregando arquivo XML. file=%s", file_path)
        file_started = time.perf_counter()

        try:
            for batch in self.iter_batches(file_path):
                self.sink.write_batch(batch)

            self.metrics.files_loaded += 1
            self.metrics.last_loaded_at = utc_now_iso()

            if self.config.archive_processed:
                self._archive_file(file_path)

            logger.info(
                "Arquivo XML carregado com sucesso. file=%s elapsed=%.3fs",
                file_path,
                time.perf_counter() - file_started,
            )

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.files_failed += 1
            logger.exception("Falha ao carregar arquivo XML. file=%s error=%s", file_path, exc)
            raise

    def _iter_full_document(self, file_path: Path) -> Generator[XMLRecord, None, None]:
        with self._open_file(file_path) as handle:
            tree = SafeElementTree.parse(handle)
            root = tree.getroot()

        target_tag = normalize_tag(self.config.target_tag) if self.config.target_tag else None
        elements = list(root.iter())

        record_index = 0
        for element in elements:
            current_tag = normalize_tag(element.tag, strip_namespace=self.config.strip_namespaces)

            if target_tag and current_tag != target_tag:
                continue

            record_index += 1
            self.metrics.records_seen += 1
            yield self._element_to_record(element, file_path, record_index)

    def _iter_streaming(self, file_path: Path) -> Generator[XMLRecord, None, None]:
        if self.config.use_lxml_streaming and LxmlEtree is not None:
            yield from self._iter_streaming_lxml(file_path)
            return

        yield from self._iter_streaming_stdlib(file_path)

    def _iter_streaming_lxml(self, file_path: Path) -> Generator[XMLRecord, None, None]:
        target_tag = normalize_tag(self.config.target_tag) if self.config.target_tag else None
        record_index = 0

        parser_kwargs = {
            "events": ("end",),
            "recover": False,
            "huge_tree": False,
            "resolve_entities": False,
            "remove_blank_text": False,
        }

        with self._open_file(file_path) as handle:
            context = LxmlEtree.iterparse(handle, **parser_kwargs)  # type: ignore[union-attr]

            for _, element in context:
                current_tag = normalize_tag(element.tag, strip_namespace=self.config.strip_namespaces)

                if target_tag and current_tag != target_tag:
                    self._clear_element(element)
                    continue

                record_index += 1
                self.metrics.records_seen += 1
                yield self._element_to_record(element, file_path, record_index)
                self._clear_element(element)

    def _iter_streaming_stdlib(self, file_path: Path) -> Generator[XMLRecord, None, None]:
        target_tag = normalize_tag(self.config.target_tag) if self.config.target_tag else None
        record_index = 0

        with self._open_file(file_path) as handle:
            context = SafeElementTree.iterparse(handle, events=("end",))

            for _, element in context:
                current_tag = normalize_tag(element.tag, strip_namespace=self.config.strip_namespaces)

                if target_tag and current_tag != target_tag:
                    element.clear()
                    continue

                record_index += 1
                self.metrics.records_seen += 1
                yield self._element_to_record(element, file_path, record_index)
                element.clear()

    def _element_to_record(self, element: Any, file_path: Path, record_index: int) -> XMLRecord:
        tag = normalize_tag(element.tag, strip_namespace=self.config.strip_namespaces)
        namespace = extract_namespace(element.tag)
        data = element_to_dict(
            element=element,
            strip_namespaces=self.config.strip_namespaces,
            include_attributes=self.config.include_attributes,
            include_text=self.config.include_text,
            preserve_empty_values=self.config.preserve_empty_values,
        )

        attributes = dict(element.attrib) if self.config.include_attributes else {}
        if self.config.strip_namespaces:
            attributes = {normalize_tag(k): v for k, v in attributes.items()}

        text = clean_text(element.text) if self.config.include_text else None

        return XMLRecord(
            source_file=str(file_path),
            record_index=record_index,
            tag=tag,
            attributes=attributes,
            data=data,
            text=text,
            namespace=namespace,
            metadata={
                "file_name": file_path.name,
                "file_size_bytes": file_path.stat().st_size if file_path.exists() else None,
                "loader_mode": self.config.mode.value,
                "status": LoadedRecordStatus.LOADED.value,
            },
        )

    def _handle_invalid_record(self, record: XMLRecord, exc: Exception) -> None:
        self.metrics.records_failed += 1

        if self.config.invalid_record_strategy == InvalidRecordStrategy.RAISE:
            raise exc

        if self.config.invalid_record_strategy == InvalidRecordStrategy.SKIP:
            self.metrics.records_skipped += 1
            logger.warning(
                "Registro XML inválido ignorado. file=%s index=%s error=%s",
                record.source_file,
                record.record_index,
                exc,
            )
            return

        self._send_to_dlq(record, exc)

    def _send_to_dlq(self, record: XMLRecord, exc: Exception) -> None:
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
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

        self.metrics.records_sent_to_dlq += 1
        logger.warning(
            "Registro XML enviado para DLQ. file=%s index=%s dlq=%s error=%s",
            record.source_file,
            record.record_index,
            self.config.dlq_path,
            exc,
        )

    def _validate_file_before_load(self, file_path: Path) -> None:
        if not file_path.exists():
            raise FileNotFoundError(f"Arquivo XML não encontrado: {file_path}")

        if file_path.stat().st_size == 0:
            message = f"Arquivo XML vazio: {file_path}"
            if self.config.fail_on_empty_file:
                raise ValueError(message)
            logger.warning(message)

        if self.config.max_file_size_mb is not None:
            max_bytes = self.config.max_file_size_mb * 1024 * 1024
            if file_path.stat().st_size > max_bytes:
                raise ValueError(
                    f"Arquivo excede limite de tamanho: {file_path} "
                    f"size={file_path.stat().st_size} max={max_bytes}"
                )

    def _archive_file(self, file_path: Path) -> None:
        if not self.config.archive_dir:
            return

        self.config.archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = self.config.archive_dir / f"{file_path.stem}.{timestamp}{file_path.suffix}"
        shutil.move(str(file_path), str(destination))
        logger.info("Arquivo XML arquivado. source=%s destination=%s", file_path, destination)

    def _open_file(self, file_path: Path):
        if file_path.suffix.lower() == ".gz":
            return gzip.open(file_path, "rb")
        return file_path.open("rb")

    @staticmethod
    def _clear_element(element: Any) -> None:
        element.clear()
        while element.getprevious() is not None:
            del element.getparent()[0]


# =============================================================================
# Conversão XML -> dict
# =============================================================================


def element_to_dict(
    element: Any,
    strip_namespaces: bool = True,
    include_attributes: bool = True,
    include_text: bool = True,
    preserve_empty_values: bool = False,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {}

    if include_attributes and getattr(element, "attrib", None):
        for key, value in element.attrib.items():
            attr_key = normalize_tag(key, strip_namespace=strip_namespaces)
            result[f"@{attr_key}"] = value

    children = list(element)

    if children:
        grouped: Dict[str, List[Any]] = {}

        for child in children:
            child_tag = normalize_tag(child.tag, strip_namespace=strip_namespaces)
            child_value = element_to_dict(
                child,
                strip_namespaces=strip_namespaces,
                include_attributes=include_attributes,
                include_text=include_text,
                preserve_empty_values=preserve_empty_values,
            )

            child_text = clean_text(child.text)
            if include_text and child_text and not child_value:
                child_value = child_text  # type: ignore[assignment]

            if child_value == {} and preserve_empty_values:
                child_value = None  # type: ignore[assignment]

            grouped.setdefault(child_tag, []).append(child_value)

        for key, values in grouped.items():
            result[key] = values[0] if len(values) == 1 else values

    text = clean_text(element.text)
    if include_text and text:
        if result:
            result["#text"] = text
        else:
            result["value"] = text

    if preserve_empty_values and not result:
        result["value"] = None

    return result


def normalize_tag(tag: Optional[str], strip_namespace: bool = True) -> str:
    if tag is None:
        return ""

    tag_str = str(tag)
    if strip_namespace and "}" in tag_str:
        return tag_str.split("}", 1)[1]
    return tag_str


def extract_namespace(tag: Optional[str]) -> Optional[str]:
    if not tag:
        return None
    tag_str = str(tag)
    if tag_str.startswith("{") and "}" in tag_str:
        return tag_str[1:].split("}", 1)[0]
    return None


def clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


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
# Exemplo de Sink para JSONL
# =============================================================================


class JsonlXMLRecordSink:
    def __init__(self, output_path: Union[str, Path]) -> None:
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def write_batch(self, records: List[XMLRecord]) -> None:
        with self.output_path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(model_to_dict(record), ensure_ascii=False) + "\n")

        logger.info("Batch XML gravado em JSONL. output=%s size=%s", self.output_path, len(records))


# =============================================================================
# Exemplo de Sink para callback externo
# =============================================================================


class CallbackXMLRecordSink:
    def __init__(self, callback: Callable[[List[XMLRecord]], None]) -> None:
        self.callback = callback

    def write_batch(self, records: List[XMLRecord]) -> None:
        self.callback(records)


# =============================================================================
# Bootstrap CLI simples
# =============================================================================


def main() -> None:
    config = XMLLoaderConfig.from_env()

    output_path = os.getenv("XML_OUTPUT_JSONL")
    sink: XMLRecordSink

    if output_path:
        sink = JsonlXMLRecordSink(output_path)
    else:
        sink = LoggingXMLRecordSink()

    loader = EnterpriseXMLLoader(
        config=config,
        sink=sink,
        validator=NoOpXMLRecordValidator(),
        transformer=NoOpXMLRecordTransformer(),
    )
    loader.run()


if __name__ == "__main__":
    main()

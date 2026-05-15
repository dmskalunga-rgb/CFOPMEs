"""
data/utils/deserializers.py

Enterprise-grade deserialization utilities.

Este módulo centraliza desserialização segura e extensível para payloads de
pipelines de dados, APIs, arquivos, mensageria, validação, auditoria e IA.

Capacidades principais:
- Desserialização JSON, JSONL/NDJSON, CSV, texto, bytes e YAML opcional.
- Pickle bloqueado por padrão por segurança.
- Validação de tamanho máximo de payload.
- Detecção automática por conteúdo, extensão ou MIME type.
- Hooks de validação e transformação pós-desserialização.
- Erros estruturados e rastreáveis.
- API orientada a configuração/política.
- Sem dependências externas obrigatórias.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import math
import mimetypes
import os
import pickle
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple, Union


logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
PathLike = Union[str, os.PathLike[str]]
DeserializerHook = Callable[[Any], Any]
ValidatorHook = Callable[[Any], bool]


class SerializationFormat(str, Enum):
    """Formatos suportados para desserialização."""

    JSON = "json"
    JSONL = "jsonl"
    NDJSON = "ndjson"
    CSV = "csv"
    TSV = "tsv"
    YAML = "yaml"
    TEXT = "text"
    BYTES = "bytes"
    BASE64 = "base64"
    PICKLE = "pickle"
    AUTO = "auto"
    UNKNOWN = "unknown"


class DeserializationStatus(str, Enum):
    """Status da operação de desserialização."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class DeserializationError(Exception):
    """Erro base para desserialização."""


class DeserializationSecurityError(DeserializationError):
    """Erro de segurança na desserialização."""


class DeserializationConfigurationError(DeserializationError):
    """Erro de configuração inválida."""


class PayloadTooLargeError(DeserializationSecurityError):
    """Payload excede limite configurado."""


class ValidationHookError(DeserializationError):
    """Hook de validação rejeitou o objeto desserializado."""


@dataclass(frozen=True)
class DeserializationPolicy:
    """Política de segurança para desserialização."""

    max_payload_bytes: int = 50 * 1024 * 1024
    allow_pickle: bool = False
    allow_yaml: bool = True
    allow_unknown_format: bool = False
    encoding: str = "utf-8"
    csv_delimiter: Optional[str] = None
    csv_as_dict: bool = True
    json_parse_float_as_decimal: bool = False
    strip_utf8_bom: bool = True
    fail_on_empty: bool = False
    allowed_formats: Optional[Tuple[SerializationFormat, ...]] = None

    def validate_format(self, fmt: SerializationFormat) -> None:
        if self.allowed_formats is not None and fmt not in self.allowed_formats:
            raise DeserializationSecurityError(f"Format is not allowed by policy: {fmt.value}")
        if fmt == SerializationFormat.PICKLE and not self.allow_pickle:
            raise DeserializationSecurityError("Pickle deserialization is disabled by policy")
        if fmt == SerializationFormat.YAML and not self.allow_yaml:
            raise DeserializationSecurityError("YAML deserialization is disabled by policy")
        if fmt == SerializationFormat.UNKNOWN and not self.allow_unknown_format:
            raise DeserializationSecurityError("Unknown deserialization format is not allowed")

    def validate_size(self, size: int) -> None:
        if size > self.max_payload_bytes:
            raise PayloadTooLargeError(f"Payload too large: {size} > {self.max_payload_bytes} bytes")


@dataclass(frozen=True)
class DeserializationResult:
    """Resultado estruturado de desserialização."""

    status: DeserializationStatus
    format: SerializationFormat
    value: Any = None
    source: Optional[str] = None
    size_bytes: int = 0
    record_count: Optional[int] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def ok(self) -> bool:
        return self.status == DeserializationStatus.SUCCEEDED

    def unwrap(self) -> Any:
        if not self.ok:
            raise DeserializationError(f"Deserialization failed: {self.error}")
        return self.value

    def to_dict(self) -> JsonDict:
        return {
            "status": self.status.value,
            "format": self.format.value,
            "source": self.source,
            "size_bytes": self.size_bytes,
            "record_count": self.record_count,
            "error": self.error,
            "error_type": self.error_type,
            "metadata": safe_json_value(dict(self.metadata)),
            "created_at": self.created_at.isoformat(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


class Deserializer(Protocol):
    """Contrato de desserializador."""

    def deserialize(self, payload: Union[str, bytes], *, policy: DeserializationPolicy) -> Any:
        """Desserializa payload."""


class JsonDeserializer:
    def deserialize(self, payload: Union[str, bytes], *, policy: DeserializationPolicy) -> Any:
        text = to_text(payload, encoding=policy.encoding, strip_bom=policy.strip_utf8_bom)
        if policy.fail_on_empty and not text.strip():
            raise DeserializationError("Empty JSON payload")
        return json.loads(text)


class JsonLinesDeserializer:
    def deserialize(self, payload: Union[str, bytes], *, policy: DeserializationPolicy) -> List[Any]:
        text = to_text(payload, encoding=policy.encoding, strip_bom=policy.strip_utf8_bom)
        if policy.fail_on_empty and not text.strip():
            raise DeserializationError("Empty JSONL payload")
        records: List[Any] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise DeserializationError(f"Invalid JSONL at line {line_number}: {exc}") from exc
        return records


class CsvDeserializer:
    def deserialize(self, payload: Union[str, bytes], *, policy: DeserializationPolicy) -> List[Mapping[str, Any]] | List[List[str]]:
        text = to_text(payload, encoding=policy.encoding, strip_bom=policy.strip_utf8_bom)
        if policy.fail_on_empty and not text.strip():
            raise DeserializationError("Empty CSV payload")
        delimiter = policy.csv_delimiter or infer_delimiter(text)
        stream = io.StringIO(text)
        if policy.csv_as_dict:
            reader = csv.DictReader(stream, delimiter=delimiter)
            return [dict(row) for row in reader]
        reader = csv.reader(stream, delimiter=delimiter)
        return [list(row) for row in reader]


class TextDeserializer:
    def deserialize(self, payload: Union[str, bytes], *, policy: DeserializationPolicy) -> str:
        return to_text(payload, encoding=policy.encoding, strip_bom=policy.strip_utf8_bom)


class BytesDeserializer:
    def deserialize(self, payload: Union[str, bytes], *, policy: DeserializationPolicy) -> bytes:
        return payload if isinstance(payload, bytes) else payload.encode(policy.encoding)


class Base64Deserializer:
    def deserialize(self, payload: Union[str, bytes], *, policy: DeserializationPolicy) -> bytes:
        raw = payload if isinstance(payload, bytes) else payload.encode(policy.encoding)
        return base64.b64decode(raw, validate=True)


class PickleDeserializer:
    def deserialize(self, payload: Union[str, bytes], *, policy: DeserializationPolicy) -> Any:
        if not policy.allow_pickle:
            raise DeserializationSecurityError("Pickle deserialization is disabled by policy")
        raw = payload if isinstance(payload, bytes) else payload.encode(policy.encoding)
        return pickle.loads(raw)


class YamlDeserializer:
    def deserialize(self, payload: Union[str, bytes], *, policy: DeserializationPolicy) -> Any:
        if not policy.allow_yaml:
            raise DeserializationSecurityError("YAML deserialization is disabled by policy")
        yaml_module = optional_import_yaml()
        text = to_text(payload, encoding=policy.encoding, strip_bom=policy.strip_utf8_bom)
        return yaml_module.safe_load(text)


class DeserializationManager:
    """Gerenciador enterprise para desserialização segura."""

    def __init__(
        self,
        *,
        policy: Optional[DeserializationPolicy] = None,
        deserializers: Optional[Mapping[SerializationFormat, Deserializer]] = None,
        validators: Optional[Sequence[ValidatorHook]] = None,
        transformers: Optional[Sequence[DeserializerHook]] = None,
    ) -> None:
        self.policy = policy or DeserializationPolicy()
        self.deserializers: Dict[SerializationFormat, Deserializer] = {
            SerializationFormat.JSON: JsonDeserializer(),
            SerializationFormat.JSONL: JsonLinesDeserializer(),
            SerializationFormat.NDJSON: JsonLinesDeserializer(),
            SerializationFormat.CSV: CsvDeserializer(),
            SerializationFormat.TSV: CsvDeserializer(),
            SerializationFormat.TEXT: TextDeserializer(),
            SerializationFormat.BYTES: BytesDeserializer(),
            SerializationFormat.BASE64: Base64Deserializer(),
            SerializationFormat.PICKLE: PickleDeserializer(),
            SerializationFormat.YAML: YamlDeserializer(),
        }
        if deserializers:
            self.deserializers.update(dict(deserializers))
        self.validators = tuple(validators or ())
        self.transformers = tuple(transformers or ())

    def deserialize(
        self,
        payload: Union[str, bytes],
        *,
        fmt: SerializationFormat = SerializationFormat.AUTO,
        source: Optional[str] = None,
        mime_type: Optional[str] = None,
        extension: Optional[str] = None,
    ) -> DeserializationResult:
        size = payload_size(payload, encoding=self.policy.encoding)
        try:
            self.policy.validate_size(size)
            actual_format = infer_format(payload, fmt=fmt, source=source, mime_type=mime_type, extension=extension)
            if actual_format == SerializationFormat.TSV:
                active_policy = DeserializationPolicy(**{**self.policy.__dict__, "csv_delimiter": "\t"})
            else:
                active_policy = self.policy
            active_policy.validate_format(actual_format)
            deserializer = self.deserializers.get(actual_format)
            if deserializer is None:
                raise DeserializationConfigurationError(f"No deserializer registered for format: {actual_format.value}")
            value = deserializer.deserialize(payload, policy=active_policy)
            value = self._apply_transformers(value)
            self._apply_validators(value)
            return DeserializationResult(
                status=DeserializationStatus.SUCCEEDED,
                format=actual_format,
                value=value,
                source=source,
                size_bytes=size,
                record_count=record_count(value),
                metadata={"mime_type": mime_type, "extension": extension},
            )
        except DeserializationSecurityError as exc:
            logger.warning("Deserialization blocked: %s", exc)
            return DeserializationResult(
                status=DeserializationStatus.BLOCKED,
                format=fmt if fmt != SerializationFormat.AUTO else SerializationFormat.UNKNOWN,
                source=source,
                size_bytes=size,
                error=str(exc),
                error_type=type(exc).__name__,
            )
        except Exception as exc:
            logger.debug("Deserialization failed", exc_info=True)
            return DeserializationResult(
                status=DeserializationStatus.FAILED,
                format=fmt if fmt != SerializationFormat.AUTO else SerializationFormat.UNKNOWN,
                source=source,
                size_bytes=size,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    def deserialize_file(
        self,
        path: PathLike,
        *,
        fmt: SerializationFormat = SerializationFormat.AUTO,
        mime_type: Optional[str] = None,
    ) -> DeserializationResult:
        file_path = Path(path)
        if not file_path.exists() or not file_path.is_file():
            return DeserializationResult(
                status=DeserializationStatus.FAILED,
                format=fmt,
                source=str(file_path),
                error=f"File does not exist: {file_path}",
                error_type="FileNotFoundError",
            )
        self.policy.validate_size(file_path.stat().st_size)
        payload = file_path.read_bytes()
        guessed_mime = mime_type or mimetypes.guess_type(str(file_path))[0]
        return self.deserialize(
            payload,
            fmt=fmt,
            source=str(file_path),
            mime_type=guessed_mime,
            extension=file_path.suffix,
        )

    def register(self, fmt: SerializationFormat, deserializer: Deserializer) -> None:
        self.deserializers[fmt] = deserializer

    def _apply_transformers(self, value: Any) -> Any:
        current = value
        for transformer in self.transformers:
            current = transformer(current)
        return current

    def _apply_validators(self, value: Any) -> None:
        for validator in self.validators:
            if not validator(value):
                raise ValidationHookError(f"Validation hook rejected deserialized payload: {getattr(validator, '__name__', repr(validator))}")


def deserialize(
    payload: Union[str, bytes],
    *,
    fmt: SerializationFormat = SerializationFormat.AUTO,
    policy: Optional[DeserializationPolicy] = None,
    source: Optional[str] = None,
) -> DeserializationResult:
    """Atalho para desserializar payload."""
    return DeserializationManager(policy=policy).deserialize(payload, fmt=fmt, source=source)


def deserialize_file(
    path: PathLike,
    *,
    fmt: SerializationFormat = SerializationFormat.AUTO,
    policy: Optional[DeserializationPolicy] = None,
) -> DeserializationResult:
    """Atalho para desserializar arquivo."""
    return DeserializationManager(policy=policy).deserialize_file(path, fmt=fmt)


def loads_json(payload: Union[str, bytes], *, policy: Optional[DeserializationPolicy] = None) -> Any:
    """Desserializa JSON e retorna valor diretamente."""
    return deserialize(payload, fmt=SerializationFormat.JSON, policy=policy).unwrap()


def loads_jsonl(payload: Union[str, bytes], *, policy: Optional[DeserializationPolicy] = None) -> List[Any]:
    """Desserializa JSONL e retorna lista diretamente."""
    return deserialize(payload, fmt=SerializationFormat.JSONL, policy=policy).unwrap()


def loads_csv(payload: Union[str, bytes], *, policy: Optional[DeserializationPolicy] = None) -> Any:
    """Desserializa CSV e retorna registros diretamente."""
    return deserialize(payload, fmt=SerializationFormat.CSV, policy=policy).unwrap()


def infer_format(
    payload: Union[str, bytes],
    *,
    fmt: SerializationFormat = SerializationFormat.AUTO,
    source: Optional[str] = None,
    mime_type: Optional[str] = None,
    extension: Optional[str] = None,
) -> SerializationFormat:
    """Infere formato a partir de fmt explícito, extensão, MIME ou conteúdo."""
    if fmt != SerializationFormat.AUTO:
        return fmt
    if extension:
        by_ext = format_from_extension(extension)
        if by_ext != SerializationFormat.UNKNOWN:
            return by_ext
    if source:
        by_ext = format_from_extension(Path(source).suffix)
        if by_ext != SerializationFormat.UNKNOWN:
            return by_ext
    if mime_type:
        by_mime = format_from_mime(mime_type)
        if by_mime != SerializationFormat.UNKNOWN:
            return by_mime
    return format_from_content(payload)


def format_from_extension(extension: str) -> SerializationFormat:
    ext = extension.lower().strip()
    if not ext:
        return SerializationFormat.UNKNOWN
    if not ext.startswith("."):
        ext = "." + ext
    mapping = {
        ".json": SerializationFormat.JSON,
        ".jsonl": SerializationFormat.JSONL,
        ".ndjson": SerializationFormat.NDJSON,
        ".csv": SerializationFormat.CSV,
        ".tsv": SerializationFormat.TSV,
        ".yaml": SerializationFormat.YAML,
        ".yml": SerializationFormat.YAML,
        ".txt": SerializationFormat.TEXT,
        ".log": SerializationFormat.TEXT,
        ".bin": SerializationFormat.BYTES,
        ".b64": SerializationFormat.BASE64,
        ".pickle": SerializationFormat.PICKLE,
        ".pkl": SerializationFormat.PICKLE,
    }
    return mapping.get(ext, SerializationFormat.UNKNOWN)


def format_from_mime(mime_type: str) -> SerializationFormat:
    mime = mime_type.lower().split(";", 1)[0].strip()
    mapping = {
        "application/json": SerializationFormat.JSON,
        "application/x-ndjson": SerializationFormat.JSONL,
        "application/jsonlines": SerializationFormat.JSONL,
        "text/csv": SerializationFormat.CSV,
        "text/tab-separated-values": SerializationFormat.TSV,
        "application/x-yaml": SerializationFormat.YAML,
        "text/yaml": SerializationFormat.YAML,
        "text/plain": SerializationFormat.TEXT,
        "application/octet-stream": SerializationFormat.BYTES,
    }
    return mapping.get(mime, SerializationFormat.UNKNOWN)


def format_from_content(payload: Union[str, bytes]) -> SerializationFormat:
    text = to_text(payload, errors="ignore").lstrip("\ufeff").strip()
    if not text:
        return SerializationFormat.TEXT
    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        return SerializationFormat.JSON
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        jsonl_like = 0
        for line in lines[:20]:
            if (line.startswith("{") and line.endswith("}")) or (line.startswith("[") and line.endswith("]")):
                jsonl_like += 1
        if jsonl_like / min(len(lines), 20) >= 0.8:
            return SerializationFormat.JSONL
    first_line = lines[0] if lines else text
    if "," in first_line:
        return SerializationFormat.CSV
    if "\t" in first_line:
        return SerializationFormat.TSV
    if text.startswith(("---", "- ")) or ":" in first_line:
        return SerializationFormat.YAML
    return SerializationFormat.TEXT


def to_text(payload: Union[str, bytes], *, encoding: str = "utf-8", errors: str = "strict", strip_bom: bool = True) -> str:
    text = payload.decode(encoding, errors=errors) if isinstance(payload, bytes) else str(payload)
    if strip_bom and text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    return text


def payload_size(payload: Union[str, bytes], *, encoding: str = "utf-8") -> int:
    return len(payload) if isinstance(payload, bytes) else len(payload.encode(encoding))


def record_count(value: Any) -> Optional[int]:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, tuple):
        return len(value)
    if isinstance(value, Mapping):
        return 1
    if isinstance(value, str):
        return len(value.splitlines()) if value else 0
    return None


def infer_delimiter(text: str) -> str:
    sample = text[:4096]
    candidates = [",", ";", "\t", "|"]
    counts = {candidate: sample.count(candidate) for candidate in candidates}
    return max(counts.items(), key=lambda item: item[1])[0] if any(counts.values()) else ","


def optional_import_yaml() -> Any:
    try:
        import yaml  # type: ignore
    except Exception as exc:
        raise DeserializationConfigurationError("YAML support requires PyYAML installed: pip install pyyaml") from exc
    return yaml


def safe_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): safe_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [safe_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


__all__ = [
    "Base64Deserializer",
    "BytesDeserializer",
    "CsvDeserializer",
    "Deserializer",
    "DeserializationConfigurationError",
    "DeserializationError",
    "DeserializationManager",
    "DeserializationPolicy",
    "DeserializationResult",
    "DeserializationSecurityError",
    "DeserializationStatus",
    "DeserializerHook",
    "JsonDeserializer",
    "JsonLinesDeserializer",
    "PayloadTooLargeError",
    "PickleDeserializer",
    "SerializationFormat",
    "TextDeserializer",
    "ValidationHookError",
    "ValidatorHook",
    "YamlDeserializer",
    "deserialize",
    "deserialize_file",
    "format_from_content",
    "format_from_extension",
    "format_from_mime",
    "infer_delimiter",
    "infer_format",
    "loads_csv",
    "loads_json",
    "loads_jsonl",
    "optional_import_yaml",
    "payload_size",
    "record_count",
    "safe_json_value",
    "to_text",
]

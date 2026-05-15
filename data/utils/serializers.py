"""
data/utils/serializers.py

Enterprise-grade serialization utilities.

Este módulo centraliza serialização segura, extensível e padronizada para
payloads de pipelines de dados, APIs, arquivos, mensageria, validação,
auditoria, métricas e IA.

Capacidades principais:
- Serialização JSON, JSONL/NDJSON, CSV, TSV, texto, bytes, base64 e YAML opcional.
- Pickle bloqueado por padrão por segurança.
- Conversão JSON-safe para dataclasses, datetime, date, Path, Enum, sets e objetos comuns.
- Política de segurança com limite de payload, formatos permitidos e encoding.
- Detecção/formatação por formato explícito, extensão ou MIME type.
- Hooks de transformação e validação antes da serialização.
- Resultado estruturado e serializável.
- Escrita atômica opcional em arquivo.
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
import tempfile
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple, Union


logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
PathLike = Union[str, os.PathLike[str]]
SerializerHook = Callable[[Any], Any]
ValidatorHook = Callable[[Any], bool]


class SerializationFormat(str, Enum):
    """Formatos suportados para serialização."""

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


class SerializationStatus(str, Enum):
    """Status da operação de serialização."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class SerializationError(Exception):
    """Erro base para serialização."""


class SerializationSecurityError(SerializationError):
    """Erro de segurança na serialização."""


class SerializationConfigurationError(SerializationError):
    """Erro de configuração inválida."""


class PayloadTooLargeError(SerializationSecurityError):
    """Payload serializado excede limite configurado."""


class ValidationHookError(SerializationError):
    """Hook de validação rejeitou o objeto antes da serialização."""


@dataclass(frozen=True)
class SerializationPolicy:
    """Política de segurança e comportamento para serialização."""

    max_payload_bytes: int = 50 * 1024 * 1024
    allow_pickle: bool = False
    allow_yaml: bool = True
    allow_unknown_format: bool = False
    encoding: str = "utf-8"
    ensure_ascii: bool = False
    sort_keys: bool = True
    indent: Optional[int] = None
    csv_delimiter: Optional[str] = None
    csv_include_header: bool = True
    fail_on_empty: bool = False
    atomic_file_write: bool = True
    allowed_formats: Optional[Tuple[SerializationFormat, ...]] = None

    def validate_format(self, fmt: SerializationFormat) -> None:
        if self.allowed_formats is not None and fmt not in self.allowed_formats:
            raise SerializationSecurityError(f"Format is not allowed by policy: {fmt.value}")
        if fmt == SerializationFormat.PICKLE and not self.allow_pickle:
            raise SerializationSecurityError("Pickle serialization is disabled by policy")
        if fmt == SerializationFormat.YAML and not self.allow_yaml:
            raise SerializationSecurityError("YAML serialization is disabled by policy")
        if fmt == SerializationFormat.UNKNOWN and not self.allow_unknown_format:
            raise SerializationSecurityError("Unknown serialization format is not allowed")

    def validate_size(self, size: int) -> None:
        if size > self.max_payload_bytes:
            raise PayloadTooLargeError(f"Serialized payload too large: {size} > {self.max_payload_bytes} bytes")


@dataclass(frozen=True)
class SerializationResult:
    """Resultado estruturado de serialização."""

    status: SerializationStatus
    format: SerializationFormat
    payload: Optional[Union[str, bytes]] = None
    destination: Optional[str] = None
    size_bytes: int = 0
    record_count: Optional[int] = None
    mime_type: Optional[str] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def ok(self) -> bool:
        return self.status == SerializationStatus.SUCCEEDED

    def unwrap(self) -> Union[str, bytes]:
        if not self.ok or self.payload is None:
            raise SerializationError(f"Serialization failed: {self.error}")
        return self.payload

    def to_dict(self, *, include_payload: bool = False) -> JsonDict:
        payload_preview: Optional[str] = None
        if include_payload and self.payload is not None:
            payload_preview = self.payload.decode("utf-8", errors="replace") if isinstance(self.payload, bytes) else self.payload
        return {
            "status": self.status.value,
            "format": self.format.value,
            "destination": self.destination,
            "size_bytes": self.size_bytes,
            "record_count": self.record_count,
            "mime_type": self.mime_type,
            "error": self.error,
            "error_type": self.error_type,
            "metadata": safe_json_value(dict(self.metadata)),
            "created_at": self.created_at.isoformat(),
            "payload": payload_preview,
        }

    def to_json(self, indent: int = 2, *, include_payload: bool = False) -> str:
        return json.dumps(self.to_dict(include_payload=include_payload), ensure_ascii=False, indent=indent, default=str)


class Serializer(Protocol):
    """Contrato de serializador."""

    def serialize(self, value: Any, *, policy: SerializationPolicy) -> Union[str, bytes]:
        """Serializa valor."""


class JsonSerializer:
    def serialize(self, value: Any, *, policy: SerializationPolicy) -> str:
        safe = safe_json_value(value)
        if policy.fail_on_empty and safe in (None, "", [], {}):
            raise SerializationError("Empty JSON value")
        return json.dumps(
            safe,
            ensure_ascii=policy.ensure_ascii,
            sort_keys=policy.sort_keys,
            indent=policy.indent,
            default=str,
        )


class JsonLinesSerializer:
    def serialize(self, value: Any, *, policy: SerializationPolicy) -> str:
        records = normalize_records(value)
        if policy.fail_on_empty and not records:
            raise SerializationError("Empty JSONL value")
        return "\n".join(
            json.dumps(
                safe_json_value(record),
                ensure_ascii=policy.ensure_ascii,
                sort_keys=policy.sort_keys,
                separators=(",", ":") if policy.indent is None else None,
                default=str,
            )
            for record in records
        ) + ("\n" if records else "")


class CsvSerializer:
    def serialize(self, value: Any, *, policy: SerializationPolicy) -> str:
        records = normalize_records(value)
        if policy.fail_on_empty and not records:
            raise SerializationError("Empty CSV value")
        delimiter = policy.csv_delimiter or ","
        output = io.StringIO()
        if not records:
            return ""

        if all(isinstance(record, Mapping) for record in records):
            fieldnames = collect_fieldnames(records)
            writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter=delimiter, extrasaction="ignore")
            if policy.csv_include_header:
                writer.writeheader()
            for record in records:
                writer.writerow({key: scalar_to_string(record.get(key)) for key in fieldnames})
            return output.getvalue()

        writer = csv.writer(output, delimiter=delimiter)
        for record in records:
            if isinstance(record, (list, tuple)):
                writer.writerow([scalar_to_string(item) for item in record])
            else:
                writer.writerow([scalar_to_string(record)])
        return output.getvalue()


class TextSerializer:
    def serialize(self, value: Any, *, policy: SerializationPolicy) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(safe_json_value(value), ensure_ascii=policy.ensure_ascii, sort_keys=policy.sort_keys, default=str)


class BytesSerializer:
    def serialize(self, value: Any, *, policy: SerializationPolicy) -> bytes:
        if isinstance(value, bytes):
            return value
        if isinstance(value, bytearray):
            return bytes(value)
        if isinstance(value, str):
            return value.encode(policy.encoding)
        return json.dumps(safe_json_value(value), ensure_ascii=policy.ensure_ascii, sort_keys=policy.sort_keys, default=str).encode(policy.encoding)


class Base64Serializer:
    def serialize(self, value: Any, *, policy: SerializationPolicy) -> str:
        raw = BytesSerializer().serialize(value, policy=policy)
        return base64.b64encode(raw).decode("ascii")


class PickleSerializer:
    def serialize(self, value: Any, *, policy: SerializationPolicy) -> bytes:
        if not policy.allow_pickle:
            raise SerializationSecurityError("Pickle serialization is disabled by policy")
        return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)


class YamlSerializer:
    def serialize(self, value: Any, *, policy: SerializationPolicy) -> str:
        if not policy.allow_yaml:
            raise SerializationSecurityError("YAML serialization is disabled by policy")
        yaml_module = optional_import_yaml()
        return yaml_module.safe_dump(safe_json_value(value), allow_unicode=True, sort_keys=policy.sort_keys)


class SerializationManager:
    """Gerenciador enterprise para serialização segura."""

    def __init__(
        self,
        *,
        policy: Optional[SerializationPolicy] = None,
        serializers: Optional[Mapping[SerializationFormat, Serializer]] = None,
        validators: Optional[Sequence[ValidatorHook]] = None,
        transformers: Optional[Sequence[SerializerHook]] = None,
    ) -> None:
        self.policy = policy or SerializationPolicy()
        self.serializers: Dict[SerializationFormat, Serializer] = {
            SerializationFormat.JSON: JsonSerializer(),
            SerializationFormat.JSONL: JsonLinesSerializer(),
            SerializationFormat.NDJSON: JsonLinesSerializer(),
            SerializationFormat.CSV: CsvSerializer(),
            SerializationFormat.TSV: CsvSerializer(),
            SerializationFormat.TEXT: TextSerializer(),
            SerializationFormat.BYTES: BytesSerializer(),
            SerializationFormat.BASE64: Base64Serializer(),
            SerializationFormat.PICKLE: PickleSerializer(),
            SerializationFormat.YAML: YamlSerializer(),
        }
        if serializers:
            self.serializers.update(dict(serializers))
        self.validators = tuple(validators or ())
        self.transformers = tuple(transformers or ())

    def serialize(
        self,
        value: Any,
        *,
        fmt: SerializationFormat = SerializationFormat.JSON,
        destination: Optional[PathLike] = None,
        mime_type: Optional[str] = None,
        extension: Optional[str] = None,
    ) -> SerializationResult:
        try:
            actual_format = infer_format(fmt=fmt, destination=destination, mime_type=mime_type, extension=extension)
            active_policy = self._policy_for_format(actual_format)
            active_policy.validate_format(actual_format)
            self._apply_validators(value)
            transformed = self._apply_transformers(value)
            serializer = self.serializers.get(actual_format)
            if serializer is None:
                raise SerializationConfigurationError(f"No serializer registered for format: {actual_format.value}")
            payload = serializer.serialize(transformed, policy=active_policy)
            size = payload_size(payload, encoding=active_policy.encoding)
            active_policy.validate_size(size)

            destination_str: Optional[str] = None
            if destination is not None:
                destination_path = Path(destination)
                write_payload(destination_path, payload, encoding=active_policy.encoding, atomic=active_policy.atomic_file_write)
                destination_str = str(destination_path)

            return SerializationResult(
                status=SerializationStatus.SUCCEEDED,
                format=actual_format,
                payload=payload,
                destination=destination_str,
                size_bytes=size,
                record_count=record_count(transformed),
                mime_type=mime_type or format_to_mime(actual_format),
                metadata={"extension": extension},
            )
        except SerializationSecurityError as exc:
            logger.warning("Serialization blocked: %s", exc)
            return SerializationResult(
                status=SerializationStatus.BLOCKED,
                format=fmt if fmt != SerializationFormat.AUTO else SerializationFormat.UNKNOWN,
                destination=str(destination) if destination is not None else None,
                error=str(exc),
                error_type=type(exc).__name__,
            )
        except Exception as exc:
            logger.debug("Serialization failed", exc_info=True)
            return SerializationResult(
                status=SerializationStatus.FAILED,
                format=fmt if fmt != SerializationFormat.AUTO else SerializationFormat.UNKNOWN,
                destination=str(destination) if destination is not None else None,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    def serialize_file(
        self,
        value: Any,
        path: PathLike,
        *,
        fmt: SerializationFormat = SerializationFormat.AUTO,
        mime_type: Optional[str] = None,
    ) -> SerializationResult:
        file_path = Path(path)
        guessed_mime = mime_type or mimetypes.guess_type(str(file_path))[0]
        return self.serialize(value, fmt=fmt, destination=file_path, mime_type=guessed_mime, extension=file_path.suffix)

    def register(self, fmt: SerializationFormat, serializer: Serializer) -> None:
        self.serializers[fmt] = serializer

    def _policy_for_format(self, fmt: SerializationFormat) -> SerializationPolicy:
        if fmt == SerializationFormat.TSV:
            return SerializationPolicy(**{**self.policy.__dict__, "csv_delimiter": "\t"})
        return self.policy

    def _apply_transformers(self, value: Any) -> Any:
        current = value
        for transformer in self.transformers:
            current = transformer(current)
        return current

    def _apply_validators(self, value: Any) -> None:
        for validator in self.validators:
            if not validator(value):
                raise ValidationHookError(f"Validation hook rejected payload: {getattr(validator, '__name__', repr(validator))}")


def serialize(
    value: Any,
    *,
    fmt: SerializationFormat = SerializationFormat.JSON,
    policy: Optional[SerializationPolicy] = None,
    destination: Optional[PathLike] = None,
) -> SerializationResult:
    """Atalho para serializar payload."""
    return SerializationManager(policy=policy).serialize(value, fmt=fmt, destination=destination)


def serialize_file(
    value: Any,
    path: PathLike,
    *,
    fmt: SerializationFormat = SerializationFormat.AUTO,
    policy: Optional[SerializationPolicy] = None,
) -> SerializationResult:
    """Atalho para serializar e escrever arquivo."""
    return SerializationManager(policy=policy).serialize_file(value, path, fmt=fmt)


def dumps_json(value: Any, *, policy: Optional[SerializationPolicy] = None) -> str:
    """Serializa JSON e retorna string diretamente."""
    payload = serialize(value, fmt=SerializationFormat.JSON, policy=policy).unwrap()
    return payload.decode("utf-8") if isinstance(payload, bytes) else payload


def dumps_jsonl(value: Any, *, policy: Optional[SerializationPolicy] = None) -> str:
    """Serializa JSONL e retorna string diretamente."""
    payload = serialize(value, fmt=SerializationFormat.JSONL, policy=policy).unwrap()
    return payload.decode("utf-8") if isinstance(payload, bytes) else payload


def dumps_csv(value: Any, *, policy: Optional[SerializationPolicy] = None) -> str:
    """Serializa CSV e retorna string diretamente."""
    payload = serialize(value, fmt=SerializationFormat.CSV, policy=policy).unwrap()
    return payload.decode("utf-8") if isinstance(payload, bytes) else payload


def infer_format(
    *,
    fmt: SerializationFormat = SerializationFormat.AUTO,
    destination: Optional[PathLike] = None,
    mime_type: Optional[str] = None,
    extension: Optional[str] = None,
) -> SerializationFormat:
    """Infere formato a partir de fmt explícito, extensão, path ou MIME."""
    if fmt != SerializationFormat.AUTO:
        return fmt
    if extension:
        by_ext = format_from_extension(extension)
        if by_ext != SerializationFormat.UNKNOWN:
            return by_ext
    if destination:
        by_ext = format_from_extension(Path(destination).suffix)
        if by_ext != SerializationFormat.UNKNOWN:
            return by_ext
    if mime_type:
        by_mime = format_from_mime(mime_type)
        if by_mime != SerializationFormat.UNKNOWN:
            return by_mime
    return SerializationFormat.JSON


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


def format_to_mime(fmt: SerializationFormat) -> str:
    mapping = {
        SerializationFormat.JSON: "application/json",
        SerializationFormat.JSONL: "application/x-ndjson",
        SerializationFormat.NDJSON: "application/x-ndjson",
        SerializationFormat.CSV: "text/csv",
        SerializationFormat.TSV: "text/tab-separated-values",
        SerializationFormat.YAML: "application/x-yaml",
        SerializationFormat.TEXT: "text/plain",
        SerializationFormat.BYTES: "application/octet-stream",
        SerializationFormat.BASE64: "text/plain",
        SerializationFormat.PICKLE: "application/octet-stream",
    }
    return mapping.get(fmt, "application/octet-stream")


def safe_json_value(value: Any) -> Any:
    """Converte valores arbitrários para estrutura JSON-safe."""
    if is_dataclass(value) and not isinstance(value, type):
        return safe_json_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): safe_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [safe_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, bytearray):
        return base64.b64encode(bytes(value)).decode("ascii")
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def normalize_records(value: Any) -> List[Any]:
    """Normaliza valor em lista de registros para CSV/JSONL."""
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    if hasattr(value, "to_dict") and callable(value.to_dict):
        data = value.to_dict(orient="records") if "orient" in getattr(value.to_dict, "__code__", ()).co_varnames else value.to_dict()
        return normalize_records(data)
    return [value]


def collect_fieldnames(records: Sequence[Any]) -> List[str]:
    """Coleta fieldnames de registros mapping preservando ordem de aparição."""
    fieldnames: List[str] = []
    seen = set()
    for record in records:
        if not isinstance(record, Mapping):
            continue
        for key in record.keys():
            key_text = str(key)
            if key_text not in seen:
                seen.add(key_text)
                fieldnames.append(key_text)
    return fieldnames


def scalar_to_string(value: Any) -> str:
    """Converte valor escalar para string CSV-safe."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return json.dumps(safe_json_value(value), ensure_ascii=False, sort_keys=True, default=str)


def payload_size(payload: Union[str, bytes], *, encoding: str = "utf-8") -> int:
    return len(payload) if isinstance(payload, bytes) else len(payload.encode(encoding))


def record_count(value: Any) -> Optional[int]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return len(value)
    if isinstance(value, Mapping):
        return 1
    if isinstance(value, str):
        return len(value.splitlines()) if value else 0
    return None


def write_payload(path: Path, payload: Union[str, bytes], *, encoding: str = "utf-8", atomic: bool = True) -> None:
    """Escreve payload em arquivo, com escrita atômica opcional."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if isinstance(payload, bytes) else "w"
    if not atomic:
        with path.open(mode, encoding=None if isinstance(payload, bytes) else encoding) as file:  # type: ignore[arg-type]
            file.write(payload)  # type: ignore[arg-type]
        return

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, mode, encoding=None if isinstance(payload, bytes) else encoding) as file:  # type: ignore[arg-type]
            file.write(payload)  # type: ignore[arg-type]
            file.flush()
            os.fsync(file.fileno())
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def optional_import_yaml() -> Any:
    try:
        import yaml  # type: ignore
    except Exception as exc:
        raise SerializationConfigurationError("YAML support requires PyYAML installed: pip install pyyaml") from exc
    return yaml


__all__ = [
    "Base64Serializer",
    "BytesSerializer",
    "CsvSerializer",
    "JsonLinesSerializer",
    "JsonSerializer",
    "PayloadTooLargeError",
    "PickleSerializer",
    "SerializationConfigurationError",
    "SerializationError",
    "SerializationFormat",
    "SerializationManager",
    "SerializationPolicy",
    "SerializationResult",
    "SerializationSecurityError",
    "SerializationStatus",
    "Serializer",
    "SerializerHook",
    "TextSerializer",
    "ValidationHookError",
    "ValidatorHook",
    "YamlSerializer",
    "collect_fieldnames",
    "dumps_csv",
    "dumps_json",
    "dumps_jsonl",
    "format_from_extension",
    "format_from_mime",
    "format_to_mime",
    "infer_format",
    "normalize_records",
    "optional_import_yaml",
    "payload_size",
    "record_count",
    "safe_json_value",
    "scalar_to_string",
    "serialize",
    "serialize_file",
    "write_payload",
]

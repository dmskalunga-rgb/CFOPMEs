"""
ml/utils/serializers.py

Enterprise-grade serialization utilities for ML systems.

Features:
- JSON, YAML, Pickle and Joblib serialization
- Safe artifact metadata
- SHA256 hashing
- Atomic writes
- Optional gzip compression
- Versioned artifact manifests
- Pydantic/dataclass/numpy/pandas friendly conversion
- Security guard for unsafe pickle loading
"""

from __future__ import annotations

import dataclasses
import gzip
import hashlib
import json
import logging
import os
import pickle
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, MutableMapping

try:
    import joblib
except ImportError:  # pragma: no cover
    joblib = None  # type: ignore

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None  # type: ignore

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


logger = logging.getLogger(__name__)


class SerializationError(Exception):
    """Base exception for serialization failures."""


class UnsupportedSerializationFormat(SerializationError):
    """Raised when a format is not supported."""


class UnsafeDeserializationError(SerializationError):
    """Raised when unsafe deserialization is attempted."""


class ArtifactIntegrityError(SerializationError):
    """Raised when artifact hash verification fails."""


class SerializerFormat(str, Enum):
    JSON = "json"
    YAML = "yaml"
    PICKLE = "pickle"
    JOBLIB = "joblib"
    TEXT = "text"


@dataclass(frozen=True)
class SerializerOptions:
    encoding: str = "utf-8"
    indent: int = 2
    atomic_write: bool = True
    ensure_ascii: bool = False
    gzip_enabled: bool = False
    pickle_protocol: int = pickle.HIGHEST_PROTOCOL
    allow_pickle_load: bool = False
    create_parents: bool = True


@dataclass(frozen=True)
class ArtifactMetadata:
    name: str
    path: str
    format: str
    size_bytes: int
    sha256: str
    created_at: str
    serializer_version: str = "1.0.0"
    schema_version: str | None = None
    model_version: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "format": self.format,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "created_at": self.created_at,
            "serializer_version": self.serializer_version,
            "schema_version": self.schema_version,
            "model_version": self.model_version,
            "extra": to_jsonable(self.extra),
        }


@dataclass(frozen=True)
class SerializedArtifact:
    path: Path
    metadata: ArtifactMetadata


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    file_path = Path(path)
    digest = hashlib.sha256()

    with file_path.open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            digest.update(chunk)

    return digest.hexdigest()


def infer_format(path: str | Path, explicit: SerializerFormat | str | None = None) -> SerializerFormat:
    if explicit:
        return SerializerFormat(explicit)

    suffixes = [suffix.lower() for suffix in Path(path).suffixes]

    if ".gz" in suffixes:
        suffixes.remove(".gz")

    suffix = suffixes[-1] if suffixes else ""

    if suffix == ".json":
        return SerializerFormat.JSON

    if suffix in {".yaml", ".yml"}:
        return SerializerFormat.YAML

    if suffix in {".pkl", ".pickle"}:
        return SerializerFormat.PICKLE

    if suffix in {".joblib", ".jl"}:
        return SerializerFormat.JOBLIB

    if suffix in {".txt", ".log", ".md"}:
        return SerializerFormat.TEXT

    raise UnsupportedSerializationFormat(f"Unsupported format for path: {path}")


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Enum):
        return value.value

    if dataclasses.is_dataclass(value):
        return to_jsonable(dataclasses.asdict(value))

    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump())

    if hasattr(value, "dict") and callable(value.dict):
        return to_jsonable(value.dict())

    if np is not None:
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.bool_):
            return bool(value)

    if pd is not None:
        if isinstance(value, pd.DataFrame):
            return value.to_dict(orient="records")
        if isinstance(value, pd.Series):
            return value.tolist()
        if isinstance(value, pd.Timestamp):
            return value.isoformat()

    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(item) for item in value]

    return str(value)


def _open_writer(path: Path, options: SerializerOptions, mode: str):
    if options.gzip_enabled or path.suffix.lower() == ".gz":
        return gzip.open(path, mode)
    return path.open(mode)


def _open_reader(path: Path, options: SerializerOptions, mode: str):
    if options.gzip_enabled or path.suffix.lower() == ".gz":
        return gzip.open(path, mode)
    return path.open(mode)


def atomic_write_bytes(path: Path, writer: Callable[[Path], None]) -> None:
    ensure_parent(path)

    with tempfile.NamedTemporaryFile(
        delete=False,
        dir=str(path.parent),
        prefix=f".{path.name}.",
    ) as temp_file:
        temp_path = Path(temp_file.name)

    try:
        writer(temp_path)
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def write_text_file(
    path: str | Path,
    content: str,
    *,
    options: SerializerOptions | None = None,
) -> SerializedArtifact:
    opts = options or SerializerOptions()
    output = Path(path)

    if opts.create_parents:
        ensure_parent(output)

    def write(target: Path) -> None:
        with _open_writer(target, opts, "wt") as file:
            file.write(content)

    if opts.atomic_write:
        atomic_write_bytes(output, write)
    else:
        write(output)

    return build_serialized_artifact(output, SerializerFormat.TEXT)


def save_json(
    obj: Any,
    path: str | Path,
    *,
    options: SerializerOptions | None = None,
    metadata_extra: Mapping[str, Any] | None = None,
) -> SerializedArtifact:
    opts = options or SerializerOptions()
    output = Path(path)

    if opts.create_parents:
        ensure_parent(output)

    payload = to_jsonable(obj)

    def write(target: Path) -> None:
        with _open_writer(target, opts, "wt") as file:
            json.dump(
                payload,
                file,
                ensure_ascii=opts.ensure_ascii,
                indent=opts.indent,
                sort_keys=True,
            )

    if opts.atomic_write:
        atomic_write_bytes(output, write)
    else:
        write(output)

    return build_serialized_artifact(
        output,
        SerializerFormat.JSON,
        extra=metadata_extra,
    )


def load_json(
    path: str | Path,
    *,
    options: SerializerOptions | None = None,
    expected_sha256: str | None = None,
) -> Any:
    opts = options or SerializerOptions()
    input_path = Path(path)

    verify_sha256(input_path, expected_sha256)

    with _open_reader(input_path, opts, "rt") as file:
        return json.load(file)


def save_yaml(
    obj: Any,
    path: str | Path,
    *,
    options: SerializerOptions | None = None,
    metadata_extra: Mapping[str, Any] | None = None,
) -> SerializedArtifact:
    if yaml is None:
        raise SerializationError("PyYAML is not installed.")

    opts = options or SerializerOptions()
    output = Path(path)

    if opts.create_parents:
        ensure_parent(output)

    payload = to_jsonable(obj)

    def write(target: Path) -> None:
        with _open_writer(target, opts, "wt") as file:
            yaml.safe_dump(
                payload,
                file,
                sort_keys=False,
                allow_unicode=True,
            )

    if opts.atomic_write:
        atomic_write_bytes(output, write)
    else:
        write(output)

    return build_serialized_artifact(
        output,
        SerializerFormat.YAML,
        extra=metadata_extra,
    )


def load_yaml(
    path: str | Path,
    *,
    options: SerializerOptions | None = None,
    expected_sha256: str | None = None,
) -> Any:
    if yaml is None:
        raise SerializationError("PyYAML is not installed.")

    opts = options or SerializerOptions()
    input_path = Path(path)

    verify_sha256(input_path, expected_sha256)

    with _open_reader(input_path, opts, "rt") as file:
        return yaml.safe_load(file)


def save_pickle(
    obj: Any,
    path: str | Path,
    *,
    options: SerializerOptions | None = None,
    metadata_extra: Mapping[str, Any] | None = None,
) -> SerializedArtifact:
    opts = options or SerializerOptions()
    output = Path(path)

    if opts.create_parents:
        ensure_parent(output)

    def write(target: Path) -> None:
        with _open_writer(target, opts, "wb") as file:
            pickle.dump(obj, file, protocol=opts.pickle_protocol)

    if opts.atomic_write:
        atomic_write_bytes(output, write)
    else:
        write(output)

    return build_serialized_artifact(
        output,
        SerializerFormat.PICKLE,
        extra=metadata_extra,
    )


def load_pickle(
    path: str | Path,
    *,
    options: SerializerOptions | None = None,
    expected_sha256: str | None = None,
    trusted: bool = False,
) -> Any:
    opts = options or SerializerOptions()

    if not trusted and not opts.allow_pickle_load:
        raise UnsafeDeserializationError(
            "Pickle deserialization is unsafe. "
            "Pass trusted=True or SerializerOptions(allow_pickle_load=True) only for trusted artifacts."
        )

    input_path = Path(path)

    verify_sha256(input_path, expected_sha256)

    with _open_reader(input_path, opts, "rb") as file:
        return pickle.load(file)


def save_joblib(
    obj: Any,
    path: str | Path,
    *,
    compress: int | bool = 3,
    metadata_extra: Mapping[str, Any] | None = None,
) -> SerializedArtifact:
    if joblib is None:
        raise SerializationError("joblib is not installed.")

    output = Path(path)
    ensure_parent(output)

    with tempfile.NamedTemporaryFile(
        delete=False,
        dir=str(output.parent),
        prefix=f".{output.name}.",
    ) as temp_file:
        temp_path = Path(temp_file.name)

    try:
        joblib.dump(obj, temp_path, compress=compress)
        os.replace(temp_path, output)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    return build_serialized_artifact(
        output,
        SerializerFormat.JOBLIB,
        extra=metadata_extra,
    )


def load_joblib(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
) -> Any:
    if joblib is None:
        raise SerializationError("joblib is not installed.")

    input_path = Path(path)

    verify_sha256(input_path, expected_sha256)

    return joblib.load(input_path)


def build_serialized_artifact(
    path: str | Path,
    fmt: SerializerFormat,
    *,
    schema_version: str | None = None,
    model_version: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> SerializedArtifact:
    artifact_path = Path(path)
    stat = artifact_path.stat()

    metadata = ArtifactMetadata(
        name=artifact_path.name,
        path=str(artifact_path),
        format=fmt.value,
        size_bytes=stat.st_size,
        sha256=sha256_file(artifact_path),
        created_at=now_iso(),
        schema_version=schema_version,
        model_version=model_version,
        extra=extra or {},
    )

    logger.info(
        "serializer.artifact.created",
        extra={
            "path": str(artifact_path),
            "format": fmt.value,
            "size_bytes": metadata.size_bytes,
            "sha256": metadata.sha256,
        },
    )

    return SerializedArtifact(path=artifact_path, metadata=metadata)


def verify_sha256(path: str | Path, expected_sha256: str | None) -> None:
    if not expected_sha256:
        return

    actual = sha256_file(path)

    if actual != expected_sha256:
        raise ArtifactIntegrityError(
            f"SHA256 mismatch for '{path}'. Expected {expected_sha256}, got {actual}."
        )


def save_manifest(
    artifacts: Iterable[SerializedArtifact],
    path: str | Path,
    *,
    options: SerializerOptions | None = None,
    extra: Mapping[str, Any] | None = None,
) -> SerializedArtifact:
    manifest = {
        "created_at": now_iso(),
        "artifact_count": len(list(artifacts)),
        "artifacts": [artifact.metadata.to_dict() for artifact in artifacts],
        "extra": to_jsonable(extra or {}),
    }

    return save_json(manifest, path, options=options)


def save_artifact(
    obj: Any,
    path: str | Path,
    *,
    fmt: SerializerFormat | str | None = None,
    options: SerializerOptions | None = None,
    metadata_extra: Mapping[str, Any] | None = None,
) -> SerializedArtifact:
    resolved_format = infer_format(path, fmt)

    if resolved_format == SerializerFormat.JSON:
        return save_json(obj, path, options=options, metadata_extra=metadata_extra)

    if resolved_format == SerializerFormat.YAML:
        return save_yaml(obj, path, options=options, metadata_extra=metadata_extra)

    if resolved_format == SerializerFormat.PICKLE:
        return save_pickle(obj, path, options=options, metadata_extra=metadata_extra)

    if resolved_format == SerializerFormat.JOBLIB:
        return save_joblib(obj, path, metadata_extra=metadata_extra)

    if resolved_format == SerializerFormat.TEXT:
        return write_text_file(path, str(obj), options=options)

    raise UnsupportedSerializationFormat(f"Unsupported format: {resolved_format}")


def load_artifact(
    path: str | Path,
    *,
    fmt: SerializerFormat | str | None = None,
    options: SerializerOptions | None = None,
    expected_sha256: str | None = None,
    trusted_pickle: bool = False,
) -> Any:
    resolved_format = infer_format(path, fmt)

    if resolved_format == SerializerFormat.JSON:
        return load_json(path, options=options, expected_sha256=expected_sha256)

    if resolved_format == SerializerFormat.YAML:
        return load_yaml(path, options=options, expected_sha256=expected_sha256)

    if resolved_format == SerializerFormat.PICKLE:
        return load_pickle(
            path,
            options=options,
            expected_sha256=expected_sha256,
            trusted=trusted_pickle,
        )

    if resolved_format == SerializerFormat.JOBLIB:
        return load_joblib(path, expected_sha256=expected_sha256)

    if resolved_format == SerializerFormat.TEXT:
        opts = options or SerializerOptions()
        verify_sha256(path, expected_sha256)
        with _open_reader(Path(path), opts, "rt") as file:
            return file.read()

    raise UnsupportedSerializationFormat(f"Unsupported format: {resolved_format}")


class ArtifactRegistry:
    """
    Lightweight in-memory registry for serialized ML artifacts.
    Useful for pipeline execution, testing and metadata collection.
    """

    def __init__(self) -> None:
        self._items: MutableMapping[str, SerializedArtifact] = {}

    def register(self, key: str, artifact: SerializedArtifact) -> None:
        self._items[key] = artifact

    def get(self, key: str) -> SerializedArtifact:
        try:
            return self._items[key]
        except KeyError as exc:
            raise SerializationError(f"Artifact not found in registry: {key}") from exc

    def all(self) -> dict[str, SerializedArtifact]:
        return dict(self._items)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "created_at": now_iso(),
            "artifacts": {
                key: artifact.metadata.to_dict()
                for key, artifact in self._items.items()
            },
        }

    def save_manifest(
        self,
        path: str | Path,
        *,
        options: SerializerOptions | None = None,
    ) -> SerializedArtifact:
        return save_json(self.to_manifest(), path, options=options)


__all__ = [
    "ArtifactIntegrityError",
    "ArtifactMetadata",
    "ArtifactRegistry",
    "SerializedArtifact",
    "SerializationError",
    "SerializerFormat",
    "SerializerOptions",
    "UnsafeDeserializationError",
    "UnsupportedSerializationFormat",
    "atomic_write_bytes",
    "build_serialized_artifact",
    "infer_format",
    "load_artifact",
    "load_joblib",
    "load_json",
    "load_pickle",
    "load_yaml",
    "save_artifact",
    "save_joblib",
    "save_json",
    "save_manifest",
    "save_pickle",
    "save_yaml",
    "sha256_file",
    "to_jsonable",
    "verify_sha256",
    "write_text_file",
]
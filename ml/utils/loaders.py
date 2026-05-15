"""
ml/utils/loaders.py

Enterprise-grade loaders for ML projects.

Features:
- Load JSON, YAML, CSV, Parquet, Pickle and text files
- Load local configs and datasets
- Optional schema validation
- File hashing
- Safe path handling
- Retry support
- In-memory cache
- Structured logging
- Dataset metadata extraction
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import pickle
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Generic, Iterable, Mapping, MutableMapping, TypeVar

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None  # type: ignore

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


T = TypeVar("T")

logger = logging.getLogger(__name__)


class LoaderError(Exception):
    """Base exception for loader failures."""


class UnsupportedFormatError(LoaderError):
    """Raised when a file extension is unsupported."""


class FileValidationError(LoaderError):
    """Raised when loaded content fails validation."""


class UnsafePathError(LoaderError):
    """Raised when a path violates security constraints."""


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    delay_seconds: float = 0.25
    backoff_factor: float = 2.0
    retry_on: tuple[type[Exception], ...] = (OSError, TimeoutError)


@dataclass(frozen=True)
class LoaderOptions:
    base_dir: Path | None = None
    allow_outside_base_dir: bool = False
    use_cache: bool = True
    encoding: str = "utf-8"
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)


@dataclass(frozen=True)
class LoadedArtifact(Generic[T]):
    path: Path
    data: T
    size_bytes: int
    sha256: str
    loaded_at_epoch: float
    metadata: dict[str, Any] = field(default_factory=dict)


class LoaderCache:
    """Simple in-memory cache for loaded artifacts."""

    def __init__(self) -> None:
        self._store: MutableMapping[str, LoadedArtifact[Any]] = {}

    def get(self, key: str) -> LoadedArtifact[Any] | None:
        return self._store.get(key)

    def set(self, key: str, value: LoadedArtifact[Any]) -> None:
        self._store[key] = value

    def clear(self) -> None:
        self._store.clear()

    def keys(self) -> list[str]:
        return list(self._store.keys())


GLOBAL_LOADER_CACHE = LoaderCache()


def _retry(operation: Callable[[], T], policy: RetryPolicy) -> T:
    last_error: Exception | None = None
    delay = policy.delay_seconds

    for attempt in range(1, policy.attempts + 1):
        try:
            return operation()
        except policy.retry_on as exc:
            last_error = exc
            if attempt >= policy.attempts:
                break

            logger.warning(
                "loader.retry",
                extra={
                    "attempt": attempt,
                    "max_attempts": policy.attempts,
                    "error": str(exc),
                    "delay_seconds": delay,
                },
            )
            time.sleep(delay)
            delay *= policy.backoff_factor

    raise LoaderError(f"Operation failed after retries: {last_error}") from last_error


def resolve_safe_path(path: str | Path, options: LoaderOptions | None = None) -> Path:
    opts = options or LoaderOptions()
    raw_path = Path(path)

    if opts.base_dir and not raw_path.is_absolute():
        raw_path = opts.base_dir / raw_path

    resolved = raw_path.expanduser().resolve()

    if opts.base_dir and not opts.allow_outside_base_dir:
        base = opts.base_dir.expanduser().resolve()

        try:
            resolved.relative_to(base)
        except ValueError as exc:
            raise UnsafePathError(
                f"Path '{resolved}' is outside allowed base directory '{base}'."
            ) from exc

    return resolved


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    file_path = Path(path)
    digest = hashlib.sha256()

    with file_path.open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            digest.update(chunk)

    return digest.hexdigest()


def _cache_key(path: Path, loader_name: str) -> str:
    stat = path.stat()
    return f"{loader_name}:{path}:{stat.st_mtime_ns}:{stat.st_size}"


def _build_artifact(path: Path, data: T, metadata: dict[str, Any] | None = None) -> LoadedArtifact[T]:
    return LoadedArtifact(
        path=path,
        data=data,
        size_bytes=path.stat().st_size,
        sha256=sha256_file(path),
        loaded_at_epoch=time.time(),
        metadata=metadata or {},
    )


def _load_with_cache(
    path: Path,
    loader_name: str,
    options: LoaderOptions,
    load_fn: Callable[[], T],
    metadata_fn: Callable[[T], dict[str, Any]] | None = None,
) -> LoadedArtifact[T]:
    key = _cache_key(path, loader_name)

    if options.use_cache:
        cached = GLOBAL_LOADER_CACHE.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]

    data = _retry(load_fn, options.retry_policy)
    metadata = metadata_fn(data) if metadata_fn else {}

    artifact = _build_artifact(path, data, metadata)

    if options.use_cache:
        GLOBAL_LOADER_CACHE.set(key, artifact)

    logger.info(
        "loader.loaded",
        extra={
            "path": str(path),
            "loader": loader_name,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.sha256,
            "metadata": metadata,
        },
    )

    return artifact


def validate_mapping_keys(
    data: Mapping[str, Any],
    required_keys: Iterable[str],
) -> None:
    missing = [key for key in required_keys if key not in data]

    if missing:
        raise FileValidationError(f"Missing required keys: {missing}")


def load_json(
    path: str | Path,
    *,
    options: LoaderOptions | None = None,
    required_keys: Iterable[str] | None = None,
) -> LoadedArtifact[Any]:
    opts = options or LoaderOptions()
    file_path = resolve_safe_path(path, opts)

    def load() -> Any:
        with file_path.open("r", encoding=opts.encoding) as file:
            return json.load(file)

    artifact = _load_with_cache(file_path, "json", opts, load)

    if required_keys:
        if not isinstance(artifact.data, Mapping):
            raise FileValidationError("JSON root must be an object for key validation.")
        validate_mapping_keys(artifact.data, required_keys)

    return artifact


def load_yaml(
    path: str | Path,
    *,
    options: LoaderOptions | None = None,
    required_keys: Iterable[str] | None = None,
) -> LoadedArtifact[Any]:
    if yaml is None:
        raise LoaderError("PyYAML is not installed.")

    opts = options or LoaderOptions()
    file_path = resolve_safe_path(path, opts)

    def load() -> Any:
        with file_path.open("r", encoding=opts.encoding) as file:
            return yaml.safe_load(file)

    artifact = _load_with_cache(file_path, "yaml", opts, load)

    if required_keys:
        if not isinstance(artifact.data, Mapping):
            raise FileValidationError("YAML root must be an object for key validation.")
        validate_mapping_keys(artifact.data, required_keys)

    return artifact


def load_text(
    path: str | Path,
    *,
    options: LoaderOptions | None = None,
) -> LoadedArtifact[str]:
    opts = options or LoaderOptions()
    file_path = resolve_safe_path(path, opts)

    def load() -> str:
        return file_path.read_text(encoding=opts.encoding)

    return _load_with_cache(file_path, "text", opts, load, lambda data: {
        "characters": len(data),
        "lines": data.count("\n") + 1 if data else 0,
    })


def load_pickle(
    path: str | Path,
    *,
    options: LoaderOptions | None = None,
    trusted: bool = False,
) -> LoadedArtifact[Any]:
    if not trusted:
        raise LoaderError(
            "Pickle loading is unsafe. Pass trusted=True only for trusted artifacts."
        )

    opts = options or LoaderOptions()
    file_path = resolve_safe_path(path, opts)

    def load() -> Any:
        with file_path.open("rb") as file:
            return pickle.load(file)

    return _load_with_cache(file_path, "pickle", opts, load, lambda data: {
        "object_type": type(data).__name__,
    })


def load_csv(
    path: str | Path,
    *,
    options: LoaderOptions | None = None,
    as_dataframe: bool = True,
    delimiter: str = ",",
) -> LoadedArtifact[Any]:
    opts = options or LoaderOptions()
    file_path = resolve_safe_path(path, opts)

    def metadata(data: Any) -> dict[str, Any]:
        if pd is not None and hasattr(data, "shape"):
            return {
                "rows": int(data.shape[0]),
                "columns": int(data.shape[1]),
                "column_names": list(data.columns),
            }

        return {
            "rows": len(data),
        }

    def load() -> Any:
        if as_dataframe:
            if pd is None:
                raise LoaderError("pandas is required to load CSV as DataFrame.")
            return pd.read_csv(file_path, delimiter=delimiter)

        with file_path.open("r", encoding=opts.encoding, newline="") as file:
            return list(csv.DictReader(file, delimiter=delimiter))

    return _load_with_cache(file_path, "csv", opts, load, metadata)


def load_parquet(
    path: str | Path,
    *,
    options: LoaderOptions | None = None,
) -> LoadedArtifact[Any]:
    if pd is None:
        raise LoaderError("pandas is required to load Parquet files.")

    opts = options or LoaderOptions()
    file_path = resolve_safe_path(path, opts)

    def load() -> Any:
        return pd.read_parquet(file_path)

    def metadata(data: Any) -> dict[str, Any]:
        return {
            "rows": int(data.shape[0]),
            "columns": int(data.shape[1]),
            "column_names": list(data.columns),
        }

    return _load_with_cache(file_path, "parquet", opts, load, metadata)


def load_by_extension(
    path: str | Path,
    *,
    options: LoaderOptions | None = None,
    trusted_pickle: bool = False,
) -> LoadedArtifact[Any]:
    file_path = resolve_safe_path(path, options or LoaderOptions())
    suffix = file_path.suffix.lower()

    if suffix == ".json":
        return load_json(file_path, options=options)

    if suffix in {".yaml", ".yml"}:
        return load_yaml(file_path, options=options)

    if suffix in {".txt", ".log", ".md"}:
        return load_text(file_path, options=options)

    if suffix == ".csv":
        return load_csv(file_path, options=options)

    if suffix == ".parquet":
        return load_parquet(file_path, options=options)

    if suffix in {".pkl", ".pickle"}:
        return load_pickle(file_path, options=options, trusted=trusted_pickle)

    raise UnsupportedFormatError(f"Unsupported file format: {suffix}")


def load_many(
    paths: Iterable[str | Path],
    *,
    options: LoaderOptions | None = None,
    trusted_pickle: bool = False,
    continue_on_error: bool = False,
) -> dict[str, LoadedArtifact[Any]]:
    artifacts: dict[str, LoadedArtifact[Any]] = {}

    for path in paths:
        try:
            artifact = load_by_extension(
                path,
                options=options,
                trusted_pickle=trusted_pickle,
            )
            artifacts[str(path)] = artifact
        except Exception:
            if not continue_on_error:
                raise
            logger.exception("loader.load_many.failed", extra={"path": str(path)})

    return artifacts


def load_config(
    path: str | Path,
    *,
    options: LoaderOptions | None = None,
    required_keys: Iterable[str] | None = None,
) -> LoadedArtifact[Mapping[str, Any]]:
    file_path = resolve_safe_path(path, options or LoaderOptions())
    suffix = file_path.suffix.lower()

    if suffix == ".json":
        artifact = load_json(file_path, options=options, required_keys=required_keys)
    elif suffix in {".yaml", ".yml"}:
        artifact = load_yaml(file_path, options=options, required_keys=required_keys)
    else:
        raise UnsupportedFormatError("Config must be JSON or YAML.")

    if not isinstance(artifact.data, Mapping):
        raise FileValidationError("Config root must be a mapping/object.")

    return artifact  # type: ignore[return-value]


def ensure_files_exist(paths: Iterable[str | Path], *, base_dir: Path | None = None) -> None:
    missing: list[str] = []

    for path in paths:
        full_path = Path(path)

        if base_dir and not full_path.is_absolute():
            full_path = base_dir / full_path

        if not full_path.exists():
            missing.append(str(full_path))

    if missing:
        raise FileNotFoundError(f"Missing files: {missing}")


__all__ = [
    "LoadedArtifact",
    "LoaderCache",
    "LoaderError",
    "LoaderOptions",
    "RetryPolicy",
    "UnsupportedFormatError",
    "FileValidationError",
    "UnsafePathError",
    "GLOBAL_LOADER_CACHE",
    "load_json",
    "load_yaml",
    "load_text",
    "load_pickle",
    "load_csv",
    "load_parquet",
    "load_config",
    "load_by_extension",
    "load_many",
    "sha256_file",
    "ensure_files_exist",
    "resolve_safe_path",
]
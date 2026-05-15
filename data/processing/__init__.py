"""
data/processing/__init__.py

Enterprise-grade data processing package initializer.

Purpose
-------
This package centralizes reusable processing primitives for enterprise data
platforms: batch processing, stream processing, transformations, enrichment,
normalization, deduplication, quality gates, partitioning, orchestration hooks,
lineage, metrics and operational telemetry.

The package is designed to be imported safely even when optional submodules or
third-party dependencies are not installed. Heavy modules are exposed through
lazy imports to reduce startup cost and avoid import-time failures in workers,
CLIs, notebooks and tests.

Design principles
-----------------
- Stable public API for processing services.
- Dependency-light package root.
- Lazy imports for optional/heavy components.
- Explicit version metadata.
- Shared processing constants and defaults.
- Common exception hierarchy.
- Safe optional telemetry integration.
- Clear extension points for custom processors.

Typical usage
-------------
from data.processing import ProcessingMode, ProcessingStatus, get_package_info

info = get_package_info()

# Heavy classes are resolved lazily when present:
# from data.processing import BatchProcessor, StreamProcessor
"""

from __future__ import annotations

import importlib
import logging
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple

__version__ = "1.0.0"
__package_name__ = "data.processing"
__author__ = "Data Platform Engineering"

logger = logging.getLogger(__name__)


class ProcessingMode(str, Enum):
    """Supported processing execution modes."""

    BATCH = "batch"
    STREAM = "stream"
    MICRO_BATCH = "micro_batch"
    REAL_TIME = "real_time"
    BACKFILL = "backfill"
    REPLAY = "replay"
    AD_HOC = "ad_hoc"


class ProcessingStatus(str, Enum):
    """Common processing lifecycle status."""

    PENDING = "pending"
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    RETRYING = "retrying"
    DEAD_LETTERED = "dead_lettered"


class ProcessingPriority(str, Enum):
    """Operational priority for processing jobs."""

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    BACKGROUND = "background"


class ProcessingGuarantee(str, Enum):
    """Delivery/processing guarantee semantics."""

    AT_MOST_ONCE = "at_most_once"
    AT_LEAST_ONCE = "at_least_once"
    EXACTLY_ONCE = "exactly_once"
    BEST_EFFORT = "best_effort"


class DataFormat(str, Enum):
    """Common data formats handled by processing pipelines."""

    JSON = "json"
    JSONL = "jsonl"
    CSV = "csv"
    PARQUET = "parquet"
    AVRO = "avro"
    ORC = "orc"
    DELTA = "delta"
    ICEBERG = "iceberg"
    XML = "xml"
    TEXT = "text"
    BINARY = "binary"
    UNKNOWN = "unknown"


class ProcessingError(Exception):
    """Base exception for data processing package errors."""


class ProcessingConfigurationError(ProcessingError):
    """Raised when processing configuration is invalid."""


class ProcessingValidationError(ProcessingError):
    """Raised when input or output validation fails."""


class ProcessingExecutionError(ProcessingError):
    """Raised when execution fails."""


class ProcessingDependencyError(ProcessingError):
    """Raised when optional dependency or submodule is unavailable."""


class ProcessingStateError(ProcessingError):
    """Raised when a processor is used in an invalid lifecycle state."""


@dataclass(frozen=True)
class ProcessingDefaults:
    """Package-wide default settings."""

    default_mode: ProcessingMode = ProcessingMode.BATCH
    default_status: ProcessingStatus = ProcessingStatus.PENDING
    default_priority: ProcessingPriority = ProcessingPriority.NORMAL
    default_guarantee: ProcessingGuarantee = ProcessingGuarantee.AT_LEAST_ONCE
    default_format: DataFormat = DataFormat.JSON
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0
    batch_size: int = 10_000
    micro_batch_seconds: int = 60
    checkpoint_enabled: bool = True
    telemetry_enabled: bool = True
    strict_validation: bool = True
    dead_letter_enabled: bool = True

    @classmethod
    def from_env(cls) -> "ProcessingDefaults":
        """Build defaults from environment variables."""
        return cls(
            default_mode=ProcessingMode(os.getenv("PROCESSING_DEFAULT_MODE", ProcessingMode.BATCH.value)),
            default_priority=ProcessingPriority(os.getenv("PROCESSING_DEFAULT_PRIORITY", ProcessingPriority.NORMAL.value)),
            default_guarantee=ProcessingGuarantee(os.getenv("PROCESSING_DEFAULT_GUARANTEE", ProcessingGuarantee.AT_LEAST_ONCE.value)),
            default_format=DataFormat(os.getenv("PROCESSING_DEFAULT_FORMAT", DataFormat.JSON.value)),
            max_retries=int_env("PROCESSING_MAX_RETRIES", 3),
            retry_backoff_seconds=float_env("PROCESSING_RETRY_BACKOFF_SECONDS", 1.0),
            batch_size=int_env("PROCESSING_BATCH_SIZE", 10_000),
            micro_batch_seconds=int_env("PROCESSING_MICRO_BATCH_SECONDS", 60),
            checkpoint_enabled=bool_env("PROCESSING_CHECKPOINT_ENABLED", True),
            telemetry_enabled=bool_env("PROCESSING_TELEMETRY_ENABLED", True),
            strict_validation=bool_env("PROCESSING_STRICT_VALIDATION", True),
            dead_letter_enabled=bool_env("PROCESSING_DEAD_LETTER_ENABLED", True),
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        for key, value in list(data.items()):
            if isinstance(value, Enum):
                data[key] = value.value
        return data


@dataclass(frozen=True)
class ProcessingPackageInfo:
    """Package metadata and runtime feature discovery."""

    package_name: str
    version: str
    author: str
    defaults: Dict[str, Any]
    available_components: Dict[str, bool] = field(default_factory=dict)
    environment: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_DEFAULTS: Optional[ProcessingDefaults] = None


# Public lazy import map.
# Add new processing modules here as the package grows.
_LAZY_IMPORTS: Dict[str, Tuple[str, str]] = {
    # Core processors
    "BatchProcessor": (".batch_processor", "BatchProcessor"),
    "StreamProcessor": (".stream_processor", "StreamProcessor"),
    "MicroBatchProcessor": (".micro_batch_processor", "MicroBatchProcessor"),
    "PipelineProcessor": (".pipeline_processor", "PipelineProcessor"),
    # Transformations
    "TransformationEngine": (".transformation_engine", "TransformationEngine"),
    "DataNormalizer": (".data_normalizer", "DataNormalizer"),
    "DataEnricher": (".data_enricher", "DataEnricher"),
    "DeduplicationEngine": (".deduplication_engine", "DeduplicationEngine"),
    # Operational components
    "CheckpointManager": (".checkpoint_manager", "CheckpointManager"),
    "DeadLetterQueue": (".dead_letter_queue", "DeadLetterQueue"),
    "ProcessingMetrics": (".processing_metrics", "ProcessingMetrics"),
    "ProcessingAudit": (".processing_audit", "ProcessingAudit"),
    "LineageTracker": (".lineage_tracker", "LineageTracker"),
    # Contracts/configs, when available
    "ProcessorConfig": (".processor_config", "ProcessorConfig"),
    "ProcessorContext": (".processor_context", "ProcessorContext"),
    "ProcessingResult": (".processing_result", "ProcessingResult"),
}


__all__ = [
    "DataFormat",
    "ProcessingConfigurationError",
    "ProcessingDefaults",
    "ProcessingDependencyError",
    "ProcessingError",
    "ProcessingExecutionError",
    "ProcessingGuarantee",
    "ProcessingMode",
    "ProcessingPackageInfo",
    "ProcessingPriority",
    "ProcessingStateError",
    "ProcessingStatus",
    "ProcessingValidationError",
    "available_components",
    "configure_defaults",
    "get_defaults",
    "get_package_info",
    "is_component_available",
    "load_component",
    "safe_component",
]


def __getattr__(name: str) -> Any:
    """Resolve public heavy components lazily."""
    if name in _LAZY_IMPORTS:
        return load_component(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_IMPORTS))


def configure_defaults(defaults: ProcessingDefaults) -> ProcessingDefaults:
    """Override package-wide processing defaults."""
    global _DEFAULTS
    _DEFAULTS = defaults
    return _DEFAULTS


def get_defaults() -> ProcessingDefaults:
    """Return package-wide processing defaults."""
    global _DEFAULTS
    if _DEFAULTS is None:
        _DEFAULTS = ProcessingDefaults.from_env()
    return _DEFAULTS


def load_component(name: str) -> Any:
    """
    Load a lazily exported component by public name.

    Raises
    ------
    ProcessingDependencyError
        If the target module/class cannot be imported.
    """
    if name not in _LAZY_IMPORTS:
        raise ProcessingDependencyError(f"Unknown processing component: {name}")
    module_name, attribute_name = _LAZY_IMPORTS[name]
    try:
        module = importlib.import_module(module_name, package=__name__)
        component = getattr(module, attribute_name)
        globals()[name] = component
        return component
    except Exception as exc:
        raise ProcessingDependencyError(
            f"Could not load processing component {name} from {module_name}.{attribute_name}: {exc}"
        ) from exc


def safe_component(name: str, default: Any = None) -> Any:
    """Load component if available, otherwise return default."""
    try:
        return load_component(name)
    except ProcessingDependencyError:
        logger.debug("Processing component unavailable: %s", name, exc_info=True)
        return default


def is_component_available(name: str) -> bool:
    """Return True if a lazy component can be imported."""
    if name not in _LAZY_IMPORTS:
        return False
    try:
        load_component(name)
        return True
    except ProcessingDependencyError:
        return False


def available_components() -> Dict[str, bool]:
    """Return availability map for known lazy components."""
    return {name: is_component_available(name) for name in sorted(_LAZY_IMPORTS)}


def get_package_info(*, check_components: bool = False) -> ProcessingPackageInfo:
    """Return package metadata and runtime feature information."""
    return ProcessingPackageInfo(
        package_name=__package_name__,
        version=__version__,
        author=__author__,
        defaults=get_defaults().to_dict(),
        available_components=available_components() if check_components else {name: False for name in sorted(_LAZY_IMPORTS)},
        environment={
            "PROCESSING_DEFAULT_MODE": os.getenv("PROCESSING_DEFAULT_MODE"),
            "PROCESSING_BATCH_SIZE": os.getenv("PROCESSING_BATCH_SIZE"),
            "PROCESSING_TELEMETRY_ENABLED": os.getenv("PROCESSING_TELEMETRY_ENABLED"),
        },
    )


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}

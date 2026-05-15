"""
data/__init__.py

Enterprise Data Platform package initializer.

This package exposes the main public API for a modular enterprise data platform,
including ingestion, processing, validation, quality, governance, security,
observability, orchestration, AI/RAG utilities and shared helpers.

Design goals:
- Safe package imports with optional dependency isolation
- Central package metadata and versioning
- Stable public exports for platform modules
- Runtime environment metadata helpers
- Lightweight health/build information
- Enterprise-friendly logging bootstrap
- Backward-compatible lazy imports

Usage:
    import data

    print(data.__version__)
    print(data.package_info())
    print(data.health_info())
"""

from __future__ import annotations

import importlib
import logging
import os
import platform
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import ModuleType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


# =============================================================================
# Package metadata
# =============================================================================

__title__ = "data"
__package_name__ = "data"
__description__ = "Enterprise-grade modular data platform package."
__version__ = os.getenv("APP_VERSION", "1.0.0")
__author__ = os.getenv("APP_AUTHOR", "Data Platform Team")
__license__ = os.getenv("APP_LICENSE", "Proprietary")
__homepage__ = os.getenv("APP_HOMEPAGE", "")
__environment__ = os.getenv("APP_ENV", "development")
__service_name__ = os.getenv("APP_SERVICE_NAME", "data-core")
__build_id__ = os.getenv("BUILD_ID", "local")
__build_commit_sha__ = os.getenv("BUILD_COMMIT_SHA", "unknown")
__build_branch__ = os.getenv("BUILD_BRANCH", "local")
__deployment_id__ = os.getenv("DEPLOYMENT_ID", "local")


# =============================================================================
# Logging
# =============================================================================

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def configure_package_logging(
    *,
    level: str | int | None = None,
    json_format: bool | None = None,
    force: bool = False,
) -> None:
    """
    Configure basic package logging.

    This function is intentionally conservative. Production applications should
    usually configure logging centrally in their entrypoint, not inside a library.
    """
    selected_level = level or os.getenv("APP_LOG_LEVEL", "INFO")
    selected_json = json_format
    if selected_json is None:
        selected_json = os.getenv("LOG_FORMAT", "text").lower() == "json"

    if selected_json:
        fmt = (
            '{"timestamp":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","message":"%(message)s"}'
        )
    else:
        fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"

    logging.basicConfig(level=selected_level, format=fmt, force=force)


# =============================================================================
# Enums / data models
# =============================================================================


class PackageStatus(str, Enum):
    """Runtime package status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PackageInfo:
    """Serializable package metadata."""

    title: str
    package_name: str
    description: str
    version: str
    author: str
    license: str
    homepage: str
    environment: str
    service_name: str
    build_id: str
    build_commit_sha: str
    build_branch: str
    deployment_id: str
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HealthInfo:
    """Serializable package health/runtime metadata."""

    status: PackageStatus
    package_name: str
    version: str
    environment: str
    service_name: str
    python_version: str
    platform: str
    executable: str
    cwd: str
    optional_modules: Dict[str, bool]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


# =============================================================================
# Public module registry
# =============================================================================

_PUBLIC_SUBPACKAGES: Tuple[str, ...] = (
    "ai",
    "governance",
    "ingestion",
    "observability",
    "orchestration",
    "processing",
    "quality",
    "security",
    "utils",
    "validation",
)

_OPTIONAL_IMPORT_CHECKS: Tuple[str, ...] = (
    "pandas",
    "numpy",
    "pyarrow",
    "sqlalchemy",
    "redis",
    "kafka",
    "pydantic",
    "fastapi",
)

_LAZY_EXPORTS: Dict[str, str] = {
    # Quality public shortcuts
    "QualityScoringEngine": "data.quality.quality_scoring",
    "QualityScoreInput": "data.quality.quality_scoring",
    "QualityScoringConfig": "data.quality.quality_scoring",
    "QualityRuleRegistry": "data.quality.quality_rules",
    "QualityRuleFactory": "data.quality.quality_rules",
    "QualityMetricRegistry": "data.quality.quality_metrics",
    "create_standard_registry": "data.quality.quality_metrics",
    "SchemaDriftChecker": "data.quality.schema_drift_checker",
    "UniquenessChecker": "data.quality.uniqueness_checker",
}


# =============================================================================
# Public helpers
# =============================================================================


def utc_now_iso() -> str:
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def package_info() -> Dict[str, Any]:
    """Return package metadata as a dictionary."""
    return PackageInfo(
        title=__title__,
        package_name=__package_name__,
        description=__description__,
        version=__version__,
        author=__author__,
        license=__license__,
        homepage=__homepage__,
        environment=__environment__,
        service_name=__service_name__,
        build_id=__build_id__,
        build_commit_sha=__build_commit_sha__,
        build_branch=__build_branch__,
        deployment_id=__deployment_id__,
    ).to_dict()


def optional_dependency_status(modules: Sequence[str] | None = None) -> Dict[str, bool]:
    """Return availability status for optional third-party modules."""
    selected_modules = tuple(modules or _OPTIONAL_IMPORT_CHECKS)
    status: Dict[str, bool] = {}
    for module_name in selected_modules:
        try:
            importlib.import_module(module_name)
            status[module_name] = True
        except Exception:
            status[module_name] = False
    return status


def available_subpackages() -> Dict[str, bool]:
    """Return import availability for public data platform subpackages."""
    result: Dict[str, bool] = {}
    for package in _PUBLIC_SUBPACKAGES:
        module_name = f"{__package_name__}.{package}"
        try:
            importlib.import_module(module_name)
            result[package] = True
        except Exception:
            result[package] = False
    return result


def health_info(*, include_optional_dependencies: bool = True) -> Dict[str, Any]:
    """Return lightweight runtime health information for diagnostics."""
    optional_modules = optional_dependency_status() if include_optional_dependencies else {}
    subpackages = available_subpackages()

    missing_core_subpackages = [name for name, ok in subpackages.items() if not ok]
    if missing_core_subpackages:
        status = PackageStatus.DEGRADED
    else:
        status = PackageStatus.HEALTHY

    return HealthInfo(
        status=status,
        package_name=__package_name__,
        version=__version__,
        environment=__environment__,
        service_name=__service_name__,
        python_version=sys.version,
        platform=platform.platform(),
        executable=sys.executable,
        cwd=os.getcwd(),
        optional_modules=optional_modules,
        metadata={
            "subpackages": subpackages,
            "missing_core_subpackages": missing_core_subpackages,
            "build": {
                "build_id": __build_id__,
                "commit_sha": __build_commit_sha__,
                "branch": __build_branch__,
                "deployment_id": __deployment_id__,
            },
        },
    ).to_dict()


def require_optional_dependency(module_name: str, *, install_hint: Optional[str] = None) -> ModuleType:
    """
    Import an optional dependency or raise a clear RuntimeError.

    Example:
        pandas = require_optional_dependency("pandas", install_hint="pip install pandas")
    """
    try:
        return importlib.import_module(module_name)
    except Exception as exc:
        hint = f" Install it with: {install_hint}." if install_hint else ""
        raise RuntimeError(f"Optional dependency '{module_name}' is required but not installed.{hint}") from exc


def safe_import(module_name: str, *, default: Any = None) -> Any:
    """Safely import a module and return default on failure."""
    try:
        return importlib.import_module(module_name)
    except Exception:
        return default


def validate_runtime_environment(required_env_vars: Sequence[str] | None = None) -> Dict[str, Any]:
    """
    Validate required runtime environment variables.

    Returns a structured report instead of raising, making it useful for startup
    health checks, CI validations, and operational dashboards.
    """
    required = list(required_env_vars or [])
    missing = [name for name in required if not os.getenv(name)]
    return {
        "valid": not missing,
        "missing": missing,
        "checked": required,
        "environment": __environment__,
        "service_name": __service_name__,
        "generated_at": utc_now_iso(),
    }


# =============================================================================
# Lazy attribute loading
# =============================================================================


def __getattr__(name: str) -> Any:
    """
    Lazy-load selected public exports.

    This keeps `import data` lightweight while still offering convenient access
    to common enterprise objects.
    """
    if name in _LAZY_EXPORTS:
        module = importlib.import_module(_LAZY_EXPORTS[name])
        value = getattr(module, name)
        globals()[name] = value
        return value

    if name in _PUBLIC_SUBPACKAGES:
        module = importlib.import_module(f"{__package_name__}.{name}")
        globals()[name] = module
        return module

    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def __dir__() -> List[str]:
    """Return interactive-friendly module attributes."""
    return sorted(set(globals()) | set(_LAZY_EXPORTS) | set(_PUBLIC_SUBPACKAGES))


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "__title__",
    "__package_name__",
    "__description__",
    "__version__",
    "__author__",
    "__license__",
    "__homepage__",
    "__environment__",
    "__service_name__",
    "__build_id__",
    "__build_commit_sha__",
    "__build_branch__",
    "__deployment_id__",
    "PackageStatus",
    "PackageInfo",
    "HealthInfo",
    "configure_package_logging",
    "utc_now_iso",
    "package_info",
    "health_info",
    "optional_dependency_status",
    "available_subpackages",
    "require_optional_dependency",
    "safe_import",
    "validate_runtime_environment",
    *_PUBLIC_SUBPACKAGES,
    *_LAZY_EXPORTS.keys(),
]


# =============================================================================
# Optional startup diagnostics
# =============================================================================

if os.getenv("APP_ENABLE_IMPORT_DIAGNOSTICS", "false").lower() in {"1", "true", "yes", "y"}:
    logger.info("data package loaded: %s", package_info())

"""
data/integrations/__init__.py

Enterprise integrations package initializer.

This package centralizes external system connectors used by the data platform,
including PostgreSQL, Supabase and future integrations such as object storage,
message brokers, SaaS APIs, warehouses, lakehouse catalogs and governance tools.

Design goals:
- Lightweight import of `data.integrations`
- Lazy loading of connector classes to avoid mandatory optional dependencies
- Stable public API for integration factories
- Runtime diagnostics for connector availability
- Safe environment-based connector creation
- Consistent metadata for health checks and observability

Examples:
    from data.integrations import PostgresConnector, SupabaseConnector

    pg = PostgresConnector.from_env()
    supabase = SupabaseConnector.from_env()

    print(integration_health())
"""

from __future__ import annotations

import importlib
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import ModuleType
from typing import Any, Dict, List, Mapping, Optional, Sequence


__all__ = [
    "__version__",
    "IntegrationStatus",
    "IntegrationInfo",
    "IntegrationHealth",
    "utc_now_iso",
    "safe_import",
    "optional_dependency_status",
    "available_connectors",
    "integration_info",
    "integration_health",
    "create_connector",
    "create_postgres_connector",
    "create_supabase_connector",
    "PostgresConnector",
    "PostgresConfig",
    "PostgresConnectorError",
    "SupabaseConnector",
    "SupabaseConfig",
    "SupabaseConnectorError",
]


__version__ = os.getenv("APP_VERSION", "1.0.0")
__package_name__ = "data.integrations"
__description__ = "Enterprise connector layer for external data platform integrations."


class IntegrationStatus(str, Enum):
    """Integration package health status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IntegrationInfo:
    """Serializable integration package metadata."""

    package_name: str
    version: str
    description: str
    connectors: List[str]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IntegrationHealth:
    """Serializable integration health payload."""

    status: IntegrationStatus
    package_name: str
    version: str
    connector_modules: Dict[str, bool]
    optional_dependencies: Dict[str, bool]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


_CONNECTOR_MODULES: Dict[str, str] = {
    "postgres": "data.integrations.postgres_connector",
    "supabase": "data.integrations.supabase_connector",
}

_LAZY_EXPORTS: Dict[str, str] = {
    # PostgreSQL
    "PostgresConnector": "data.integrations.postgres_connector",
    "PostgresConfig": "data.integrations.postgres_connector",
    "PostgresConnectorError": "data.integrations.postgres_connector",
    "PostgresConfigurationError": "data.integrations.postgres_connector",
    "PostgresExecutionError": "data.integrations.postgres_connector",
    "create_postgres_connector_from_env": "data.integrations.postgres_connector",
    "query_postgres": "data.integrations.postgres_connector",
    # Supabase
    "SupabaseConnector": "data.integrations.supabase_connector",
    "SupabaseConfig": "data.integrations.supabase_connector",
    "SupabaseConnectorError": "data.integrations.supabase_connector",
    "SupabaseConfigurationError": "data.integrations.supabase_connector",
    "SupabaseRequestError": "data.integrations.supabase_connector",
    "create_supabase_connector_from_env": "data.integrations.supabase_connector",
}

_OPTIONAL_DEPENDENCIES: Dict[str, Sequence[str]] = {
    "postgres": ("sqlalchemy", "psycopg2"),
    "supabase": ("requests",),
    "dataframe": ("pandas",),
}


def utc_now_iso() -> str:
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def safe_import(module_name: str, *, default: Any = None) -> Any:
    """Safely import a module and return default on failure."""
    try:
        return importlib.import_module(module_name)
    except Exception:
        return default


def optional_dependency_status(groups: Optional[Sequence[str]] = None) -> Dict[str, bool]:
    """Return availability for optional integration dependencies."""
    selected_groups = groups or tuple(_OPTIONAL_DEPENDENCIES)
    modules: List[str] = []
    for group in selected_groups:
        modules.extend(_OPTIONAL_DEPENDENCIES.get(group, ()))

    status: Dict[str, bool] = {}
    for module_name in sorted(set(modules)):
        try:
            importlib.import_module(module_name)
            status[module_name] = True
        except Exception:
            status[module_name] = False
    return status


def available_connectors() -> Dict[str, bool]:
    """Return import availability for connector modules."""
    result: Dict[str, bool] = {}
    for connector_name, module_name in _CONNECTOR_MODULES.items():
        try:
            importlib.import_module(module_name)
            result[connector_name] = True
        except Exception:
            result[connector_name] = False
    return result


def integration_info() -> Dict[str, Any]:
    """Return integration package metadata."""
    return IntegrationInfo(
        package_name=__package_name__,
        version=__version__,
        description=__description__,
        connectors=sorted(_CONNECTOR_MODULES),
    ).to_dict()


def integration_health(*, include_optional_dependencies: bool = True) -> Dict[str, Any]:
    """Return integration package health diagnostics."""
    connector_modules = available_connectors()
    optional_dependencies = optional_dependency_status() if include_optional_dependencies else {}

    if all(connector_modules.values()):
        status = IntegrationStatus.HEALTHY
    elif any(connector_modules.values()):
        status = IntegrationStatus.DEGRADED
    else:
        status = IntegrationStatus.ERROR

    return IntegrationHealth(
        status=status,
        package_name=__package_name__,
        version=__version__,
        connector_modules=connector_modules,
        optional_dependencies=optional_dependencies,
        metadata={
            "environment": os.getenv("APP_ENV", "development"),
            "service_name": os.getenv("APP_SERVICE_NAME", "data-core"),
        },
    ).to_dict()


def create_postgres_connector(*args: Any, **kwargs: Any) -> Any:
    """Create a PostgreSQL connector using PostgresConnector.from_env by default."""
    module = importlib.import_module("data.integrations.postgres_connector")
    if args or kwargs:
        return module.PostgresConnector(*args, **kwargs)
    return module.PostgresConnector.from_env()


def create_supabase_connector(*args: Any, **kwargs: Any) -> Any:
    """Create a Supabase connector using SupabaseConnector.from_env by default."""
    module = importlib.import_module("data.integrations.supabase_connector")
    if args or kwargs:
        return module.SupabaseConnector(*args, **kwargs)
    return module.SupabaseConnector.from_env()


def create_connector(name: str, *args: Any, **kwargs: Any) -> Any:
    """
    Generic connector factory.

    Supported names:
    - postgres
    - supabase
    """
    normalized = name.strip().lower().replace("-", "_")
    if normalized in {"postgres", "postgresql", "pg"}:
        return create_postgres_connector(*args, **kwargs)
    if normalized in {"supabase", "supa"}:
        return create_supabase_connector(*args, **kwargs)
    raise ValueError(f"Unsupported connector: {name!r}. Available: {sorted(_CONNECTOR_MODULES)}")


def __getattr__(name: str) -> Any:
    """Lazy-load connector exports."""
    if name in _LAZY_EXPORTS:
        module = importlib.import_module(_LAZY_EXPORTS[name])
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def __dir__() -> List[str]:
    """Return interactive-friendly module attributes."""
    return sorted(set(globals()) | set(_LAZY_EXPORTS))

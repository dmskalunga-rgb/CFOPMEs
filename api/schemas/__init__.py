#!/usr/bin/env python3
"""
api/schemas/__init__.py

Enterprise-grade schema package initializer.

Objetivo:
- Centralizar exports públicos dos schemas da API.
- Padronizar versionamento, metadados, aliases, tipos comuns e helpers de descoberta.
- Evitar imports quebrados quando módulos opcionais ainda não existem.
- Permitir importação limpa em routers/services:

    from api.schemas import ApiResponse, ErrorResponse, PaginationParams

    from api.schemas import get_schema_registry

Padrões:
- Schemas comuns ficam disponíveis diretamente neste pacote.
- Schemas específicos por domínio podem ser importados de forma lazy/safe.
- O registry informa quais módulos foram carregados com sucesso.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Type

try:
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Dependência ausente. Instale com: pip install pydantic") from exc


LOGGER = logging.getLogger(__name__)
SCHEMAS_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc


class ApiStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"
    ACCEPTED = "accepted"


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Currency(str, Enum):
    BRL = "BRL"
    USD = "USD"
    EUR = "EUR"


class ErrorDetail(BaseModel):
    code: str
    message: str
    field: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    status: ApiStatus = ApiStatus.ERROR
    request_id: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(tz=DEFAULT_TIMEZONE).isoformat())
    error: ErrorDetail


class ApiResponse(BaseModel):
    status: ApiStatus = ApiStatus.SUCCESS
    request_id: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(tz=DEFAULT_TIMEZONE).isoformat())
    version: str = SCHEMAS_VERSION
    data: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PaginationParams(BaseModel):
    limit: int = Field(default=100, ge=1, le=10_000)
    offset: int = Field(default=0, ge=0)


class PaginationMeta(BaseModel):
    total: int = 0
    returned: int = 0
    limit: int = 100
    offset: int = 0
    has_next: bool = False


class SortParams(BaseModel):
    sort_by: Optional[str] = None
    direction: SortDirection = SortDirection.DESC


class FilterCondition(BaseModel):
    field: str
    op: str = "eq"
    value: Any = None


class HealthPayload(BaseModel):
    status: str = "ok"
    service: Optional[str] = None
    version: str = SCHEMAS_VERSION
    timestamp: str = Field(default_factory=lambda: datetime.now(tz=DEFAULT_TIMEZONE).isoformat())
    metadata: Dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class SchemaModuleInfo:
    name: str
    import_path: str
    loaded: bool
    error: Optional[str] = None


OPTIONAL_SCHEMA_MODULES = [
    "auth",
    "analytics",
    "cashflow",
    "documents",
    "finance",
    "fraud",
    "health",
    "nlp",
    "payroll",
    "reports",
    "revenue",
    "ueba",
]


_schema_registry: Dict[str, SchemaModuleInfo] = {}
_loaded_symbols: Dict[str, Any] = {}


def _safe_import_schema_module(module_name: str) -> Optional[Any]:
    import_path = f"api.schemas.{module_name}"
    try:
        module = importlib.import_module(import_path)
        _schema_registry[module_name] = SchemaModuleInfo(name=module_name, import_path=import_path, loaded=True)
        return module
    except ModuleNotFoundError as exc:
        _schema_registry[module_name] = SchemaModuleInfo(name=module_name, import_path=import_path, loaded=False, error=str(exc))
        return None
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Falha ao importar schema module %s: %s", import_path, exc)
        _schema_registry[module_name] = SchemaModuleInfo(name=module_name, import_path=import_path, loaded=False, error=str(exc))
        return None


def load_optional_schemas() -> Dict[str, SchemaModuleInfo]:
    """Tenta carregar módulos de schema opcionais e atualiza registry."""
    for module_name in OPTIONAL_SCHEMA_MODULES:
        module = _safe_import_schema_module(module_name)
        if module is None:
            continue
        exported = getattr(module, "__all__", [])
        for symbol_name in exported:
            if hasattr(module, symbol_name):
                _loaded_symbols[symbol_name] = getattr(module, symbol_name)
    return get_schema_registry()


def get_schema_registry() -> Dict[str, SchemaModuleInfo]:
    """Retorna registry dos módulos de schema carregados/tentados."""
    return dict(_schema_registry)


def get_loaded_schema_symbols() -> Dict[str, Any]:
    """Retorna símbolos carregados via __all__ dos módulos opcionais."""
    return dict(_loaded_symbols)


def schema_package_metadata() -> Dict[str, Any]:
    return {
        "schemas_version": SCHEMAS_VERSION,
        "optional_modules": OPTIONAL_SCHEMA_MODULES,
        "registry": {key: info.__dict__ for key, info in _schema_registry.items()},
        "loaded_symbol_count": len(_loaded_symbols),
    }


def build_success_response(
    data: Optional[Mapping[str, Any]] = None,
    request_id: Optional[str] = None,
    warnings: Optional[List[str]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> ApiResponse:
    return ApiResponse(
        status=ApiStatus.SUCCESS,
        request_id=request_id,
        data=dict(data or {}),
        warnings=list(warnings or []),
        metadata=dict(metadata or {}),
    )


def build_error_response(
    code: str,
    message: str,
    request_id: Optional[str] = None,
    field: Optional[str] = None,
    details: Optional[Mapping[str, Any]] = None,
) -> ErrorResponse:
    return ErrorResponse(
        request_id=request_id,
        error=ErrorDetail(
            code=code,
            message=message,
            field=field,
            details=dict(details or {}),
        ),
    )


# Load optional modules once on package import, but safely.
load_optional_schemas()


__all__ = [
    "SCHEMAS_VERSION",
    "ApiStatus",
    "SortDirection",
    "RiskLevel",
    "Currency",
    "ErrorDetail",
    "ErrorResponse",
    "ApiResponse",
    "PaginationParams",
    "PaginationMeta",
    "SortParams",
    "FilterCondition",
    "HealthPayload",
    "SchemaModuleInfo",
    "OPTIONAL_SCHEMA_MODULES",
    "load_optional_schemas",
    "get_schema_registry",
    "get_loaded_schema_symbols",
    "schema_package_metadata",
    "build_success_response",
    "build_error_response",
]

# Add lazy-loaded symbols to module globals and __all__.
globals().update(_loaded_symbols)
__all__.extend([name for name in _loaded_symbols if name not in __all__])

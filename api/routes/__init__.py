"""
api/routes package

Enterprise-grade route registry for FastAPI.

Objetivo:
- Centralizar o registro de routers da API.
- Padronizar prefixos, tags, versionamento e inclusão segura no app principal.
- Permitir descoberta de módulos de rota disponíveis sem quebrar startup quando um router opcional falhar.
- Suportar arquitetura modular por domínio: health, auth, inference, fraud, alerts, finance, tenants etc.

Uso:
    from fastapi import FastAPI
    from api.routes import include_routers

    app = FastAPI()
    include_routers(app)

Também é possível registrar somente alguns domínios:
    include_routers(app, enabled=["health", "inference"])
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence

try:
    from fastapi import APIRouter, FastAPI
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Dependências ausentes. Instale com: pip install fastapi") from exc


__version__ = "1.0.0"
__package_name__ = "api.routes"

logger = logging.getLogger(__name__)


class RouteStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    OPTIONAL = "optional"


@dataclass(frozen=True)
class RouteDescriptor:
    key: str
    module_path: str
    router_attr: str = "router"
    prefix: str = ""
    tags: List[str] = None  # type: ignore[assignment]
    status: RouteStatus = RouteStatus.ENABLED
    description: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            object.__setattr__(self, "status", RouteStatus(self.status))
        if self.tags is None:
            object.__setattr__(self, "tags", [self.key])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "module_path": self.module_path,
            "router_attr": self.router_attr,
            "prefix": self.prefix,
            "tags": self.tags,
            "status": self.status.value,
            "description": self.description,
        }


ROUTE_REGISTRY: Dict[str, RouteDescriptor] = {
    "health": RouteDescriptor(
        key="health",
        module_path="api.routes.health",
        prefix="",
        tags=["system"],
        status=RouteStatus.OPTIONAL,
        description="Health, readiness and metadata endpoints.",
    ),
    "auth": RouteDescriptor(
        key="auth",
        module_path="api.routes.auth",
        prefix="/v1/auth",
        tags=["auth"],
        status=RouteStatus.OPTIONAL,
        description="Authentication, token and identity endpoints.",
    ),
    "inference": RouteDescriptor(
        key="inference",
        module_path="api.routes.inference",
        prefix="/v1/inference",
        tags=["inference"],
        status=RouteStatus.OPTIONAL,
        description="AI inference endpoints.",
    ),
    "alerts": RouteDescriptor(
        key="alerts",
        module_path="api.routes.alerts",
        prefix="/v1/alerts",
        tags=["alerts"],
        status=RouteStatus.OPTIONAL,
        description="Anomaly and financial alert endpoints.",
    ),
    "fraud": RouteDescriptor(
        key="fraud",
        module_path="api.routes.fraud",
        prefix="/v1/fraud",
        tags=["fraud"],
        status=RouteStatus.OPTIONAL,
        description="Fraud and realtime risk endpoints.",
    ),
    "finance": RouteDescriptor(
        key="finance",
        module_path="api.routes.finance",
        prefix="/v1/finance",
        tags=["finance"],
        status=RouteStatus.OPTIONAL,
        description="Finance, cashflow, revenue and payroll endpoints.",
    ),
    "tenants": RouteDescriptor(
        key="tenants",
        module_path="api.routes.tenants",
        prefix="/v1/tenants",
        tags=["tenants"],
        status=RouteStatus.OPTIONAL,
        description="Tenant management and tenant metadata endpoints.",
    ),
}


class RouteRegistryError(Exception):
    """Base route registry exception."""


class RouteNotFoundError(RouteRegistryError):
    """Raised when a route key is not registered."""


class RouterLoadError(RouteRegistryError):
    """Raised when a router cannot be loaded."""


__all__ = [
    "RouteStatus",
    "RouteDescriptor",
    "ROUTE_REGISTRY",
    "available_routes",
    "describe_routes",
    "describe_route",
    "load_router",
    "include_router",
    "include_routers",
    "register_route",
]


def register_route(descriptor: RouteDescriptor, overwrite: bool = False) -> None:
    """Registra um novo descriptor de rota em runtime."""

    if descriptor.key in ROUTE_REGISTRY and not overwrite:
        raise RouteRegistryError(f"Route already registered: {descriptor.key}")
    ROUTE_REGISTRY[descriptor.key] = descriptor


def available_routes(include_disabled: bool = False) -> List[str]:
    """Lista as chaves das rotas registradas."""

    result: List[str] = []
    for key, descriptor in ROUTE_REGISTRY.items():
        if descriptor.status == RouteStatus.DISABLED and not include_disabled:
            continue
        result.append(key)
    return sorted(result)


def describe_route(key: str) -> Dict[str, Any]:
    """Retorna metadados de uma rota registrada."""

    if key not in ROUTE_REGISTRY:
        raise RouteNotFoundError(f"Route not registered: {key}")
    return ROUTE_REGISTRY[key].to_dict()


def describe_routes(include_disabled: bool = False) -> Dict[str, Any]:
    """Retorna resumo do registry de rotas."""

    keys = available_routes(include_disabled=include_disabled)
    return {
        "package": __package_name__,
        "version": __version__,
        "route_count": len(keys),
        "routes": {key: ROUTE_REGISTRY[key].to_dict() for key in keys},
    }


def load_router(key: str) -> APIRouter:
    """Carrega o APIRouter associado a uma chave registrada."""

    if key not in ROUTE_REGISTRY:
        raise RouteNotFoundError(f"Route not registered: {key}")

    descriptor = ROUTE_REGISTRY[key]
    if descriptor.status == RouteStatus.DISABLED:
        raise RouterLoadError(f"Route disabled: {key}")

    try:
        module = importlib.import_module(descriptor.module_path)
        router = getattr(module, descriptor.router_attr)
    except Exception as exc:  # noqa: BLE001
        raise RouterLoadError(f"Failed to load router '{key}' from {descriptor.module_path}: {exc}") from exc

    if not isinstance(router, APIRouter):
        raise RouterLoadError(f"Attribute {descriptor.router_attr} in {descriptor.module_path} is not an APIRouter")
    return router


def include_router(app: FastAPI, key: str, *, strict: bool = False) -> bool:
    """
    Inclui uma rota específica no FastAPI app.

    Retorna True se incluída, False se ignorada por ser opcional/indisponível.
    """

    descriptor = ROUTE_REGISTRY.get(key)
    if descriptor is None:
        if strict:
            raise RouteNotFoundError(f"Route not registered: {key}")
        logger.warning("route_not_registered", extra={"route_key": key})
        return False

    if descriptor.status == RouteStatus.DISABLED:
        logger.info("route_disabled", extra={"route_key": key})
        return False

    try:
        router = load_router(key)
        app.include_router(router, prefix=descriptor.prefix, tags=descriptor.tags)
        logger.info(
            "route_included",
            extra={"route_key": key, "prefix": descriptor.prefix, "tags": descriptor.tags},
        )
        return True
    except Exception as exc:  # noqa: BLE001
        if strict or descriptor.status == RouteStatus.ENABLED:
            raise
        logger.warning(
            "optional_route_skipped",
            extra={"route_key": key, "module_path": descriptor.module_path, "error": str(exc)},
        )
        return False


def include_routers(
    app: FastAPI,
    enabled: Optional[Sequence[str]] = None,
    disabled: Optional[Sequence[str]] = None,
    *,
    strict: bool = False,
) -> List[str]:
    """
    Inclui routers registrados no app.

    Args:
        app: instância FastAPI.
        enabled: lista opcional de chaves permitidas. Se None, tenta todas.
        disabled: lista opcional de chaves a ignorar.
        strict: se True, falha quando rota opcional não carregar.

    Returns:
        Lista de rotas incluídas com sucesso.
    """

    enabled_set = set(enabled) if enabled else set(available_routes())
    disabled_set = set(disabled or [])
    included: List[str] = []

    for key in sorted(enabled_set):
        if key in disabled_set:
            logger.info("route_disabled_by_config", extra={"route_key": key})
            continue
        if include_router(app, key, strict=strict):
            included.append(key)

    app.state.route_registry = describe_routes(include_disabled=True)
    app.state.routes_included = included
    return included

#!/usr/bin/env python3
"""
api/routes/health.py

Enterprise-grade Health API Router.

Objetivo:
- Expor endpoints de saúde para Kubernetes, load balancers, API gateways e observabilidade.
- Separar liveness, readiness e startup checks.
- Padronizar resposta de status, uptime, versão, ambiente, build info e dependências.
- Permitir registro de checks customizados de banco, cache, filas, storage, modelos e serviços externos.
- Evitar vazamento de segredos em respostas públicas.

Endpoints:
    GET /health
    GET /health/live
    GET /health/ready
    GET /health/startup
    GET /health/dependencies
    GET /health/build
    GET /health/deep

Integração:
    from fastapi import FastAPI
    from api.routes.health import router as health_router

    app.include_router(health_router)

Variáveis de ambiente:
    API_NAME=Enterprise AI API
    API_VERSION=1.0.0
    API_ENV=development|staging|production
    API_BUILD_SHA=git-sha
    API_BUILD_TIME=2026-01-01T00:00:00Z
    API_REGION=sa-east-1
    API_HEALTH_DEEP_ENABLED=true|false
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

try:
    from api.auth.dependencies import require_scopes
except Exception:  # pragma: no cover
    def require_scopes(*_: str, **__: Any) -> Any:  # type: ignore
        async def dependency() -> Any:
            return None
        return dependency


LOGGER = logging.getLogger(__name__)
ROUTER_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc
STARTED_AT = datetime.now(tz=DEFAULT_TIMEZONE)
STARTED_MONOTONIC = time.monotonic()

APP_NAME = os.getenv("API_NAME", "Enterprise AI API")
APP_VERSION = os.getenv("API_VERSION", "1.0.0")
APP_ENV = os.getenv("API_ENV", "development")
APP_REGION = os.getenv("API_REGION", "local")
BUILD_SHA = os.getenv("API_BUILD_SHA", "unknown")
BUILD_TIME = os.getenv("API_BUILD_TIME", "unknown")
DEEP_HEALTH_ENABLED = os.getenv("API_HEALTH_DEEP_ENABLED", "true").lower() in {"1", "true", "yes", "sim"}

router = APIRouter(prefix="/health", tags=["health"])


class HealthState(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class ComponentType(str, Enum):
    INTERNAL = "internal"
    DATABASE = "database"
    CACHE = "cache"
    QUEUE = "queue"
    STORAGE = "storage"
    MODEL = "model"
    EXTERNAL_SERVICE = "external_service"
    CONFIGURATION = "configuration"


class HealthCheckResult(BaseModel):
    name: str
    status: HealthState
    component_type: ComponentType = ComponentType.INTERNAL
    latency_ms: float = 0.0
    message: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    checked_at: str = Field(default_factory=lambda: datetime.now(tz=DEFAULT_TIMEZONE).isoformat())


class HealthResponse(BaseModel):
    status: HealthState
    service: str
    version: str
    environment: str
    region: str
    timestamp: str
    uptime_seconds: float
    request_id: str
    checks: List[HealthCheckResult] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BuildInfoResponse(BaseModel):
    service: str
    version: str
    router_version: str
    environment: str
    region: str
    build_sha: str
    build_time: str
    python_version: str
    platform: str
    started_at: str
    uptime_seconds: float


@dataclass
class HealthCheck:
    name: str
    callback: Callable[[], Any]
    component_type: ComponentType = ComponentType.INTERNAL
    critical: bool = True
    timeout_seconds: float = 3.0
    details_public: bool = True


class HealthRegistry:
    def __init__(self) -> None:
        self._checks: Dict[str, HealthCheck] = {}

    def register(self, check: HealthCheck) -> None:
        if not check.name or not check.name.strip():
            raise ValueError("health check name is required")
        self._checks[check.name] = check

    def unregister(self, name: str) -> None:
        self._checks.pop(name, None)

    def list(self) -> List[HealthCheck]:
        return list(self._checks.values())

    async def run_all(self, include_non_critical: bool = True) -> List[HealthCheckResult]:
        checks = [check for check in self.list() if include_non_critical or check.critical]
        results: List[HealthCheckResult] = []
        for check in checks:
            results.append(await run_check(check))
        return results


health_registry = HealthRegistry()


@router.get("", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Resumo público de saúde da API."""
    request_id = request_id_from_request(request)
    checks = [basic_process_check()]
    return build_response(request_id=request_id, checks=checks)


@router.get("/live", response_model=HealthResponse)
async def liveness(request: Request) -> HealthResponse:
    """Liveness probe: indica se o processo está vivo."""
    request_id = request_id_from_request(request)
    checks = [basic_process_check(), event_loop_check()]
    response = build_response(request_id=request_id, checks=checks)
    if response.status == HealthState.FAIL:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=response.dict())
    return response


@router.get("/ready", response_model=HealthResponse)
async def readiness(request: Request, response: Response) -> HealthResponse:
    """Readiness probe: indica se a API está pronta para receber tráfego."""
    request_id = request_id_from_request(request)
    checks = [basic_process_check(), config_check()]
    checks.extend(await health_registry.run_all(include_non_critical=False))
    payload = build_response(request_id=request_id, checks=checks)
    if payload.status == HealthState.FAIL:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return payload


@router.get("/startup", response_model=HealthResponse)
async def startup(request: Request, response: Response) -> HealthResponse:
    """Startup probe: indica se a aplicação terminou inicialização mínima."""
    request_id = request_id_from_request(request)
    checks = [startup_check(), config_check()]
    payload = build_response(request_id=request_id, checks=checks)
    if payload.status == HealthState.FAIL:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return payload


@router.get("/dependencies", response_model=HealthResponse, dependencies=[Depends(require_scopes("health:read"))])
async def dependencies_health(request: Request, response: Response) -> HealthResponse:
    """Executa checks de dependências registradas."""
    request_id = request_id_from_request(request)
    checks = await health_registry.run_all(include_non_critical=True)
    payload = build_response(request_id=request_id, checks=checks, metadata={"dependency_count": len(checks)})
    if payload.status == HealthState.FAIL:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return payload


@router.get("/deep", response_model=HealthResponse, dependencies=[Depends(require_scopes("health:read"))])
async def deep_health(request: Request, response: Response) -> HealthResponse:
    """Health check completo para observabilidade interna."""
    if not DEEP_HEALTH_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deep health disabled")
    request_id = request_id_from_request(request)
    checks = [basic_process_check(), event_loop_check(), config_check(), startup_check()]
    checks.extend(await health_registry.run_all(include_non_critical=True))
    payload = build_response(
        request_id=request_id,
        checks=checks,
        metadata={
            "deep": True,
            "registered_checks": [check.name for check in health_registry.list()],
            "python_executable": sys.executable,
        },
    )
    if payload.status == HealthState.FAIL:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return payload


@router.get("/build", response_model=BuildInfoResponse)
async def build_info() -> BuildInfoResponse:
    """Informações seguras de build/runtime."""
    return BuildInfoResponse(
        service=APP_NAME,
        version=APP_VERSION,
        router_version=ROUTER_VERSION,
        environment=APP_ENV,
        region=APP_REGION,
        build_sha=BUILD_SHA,
        build_time=BUILD_TIME,
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        started_at=STARTED_AT.isoformat(),
        uptime_seconds=uptime_seconds(),
    )


def register_health_check(
    name: str,
    callback: Callable[[], Any],
    component_type: ComponentType = ComponentType.INTERNAL,
    critical: bool = True,
    timeout_seconds: float = 3.0,
    details_public: bool = True,
) -> None:
    """Registra um check customizado para uso por outros módulos."""
    health_registry.register(
        HealthCheck(
            name=name,
            callback=callback,
            component_type=component_type,
            critical=critical,
            timeout_seconds=timeout_seconds,
            details_public=details_public,
        )
    )


def unregister_health_check(name: str) -> None:
    health_registry.unregister(name)


async def run_check(check: HealthCheck) -> HealthCheckResult:
    started = time.perf_counter()
    try:
        if asyncio.iscoroutinefunction(check.callback):
            raw = await asyncio.wait_for(check.callback(), timeout=check.timeout_seconds)
        else:
            raw = await asyncio.wait_for(asyncio.to_thread(check.callback), timeout=check.timeout_seconds)
        latency_ms = elapsed_ms(started)
        return normalize_check_result(check, raw, latency_ms)
    except asyncio.TimeoutError:
        return HealthCheckResult(
            name=check.name,
            status=HealthState.FAIL if check.critical else HealthState.WARN,
            component_type=check.component_type,
            latency_ms=elapsed_ms(started),
            message=f"timeout after {check.timeout_seconds}s",
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Health check failed: %s", check.name)
        return HealthCheckResult(
            name=check.name,
            status=HealthState.FAIL if check.critical else HealthState.WARN,
            component_type=check.component_type,
            latency_ms=elapsed_ms(started),
            message=str(exc),
            details={"error_type": exc.__class__.__name__} if check.details_public else {},
        )


def normalize_check_result(check: HealthCheck, raw: Any, latency_ms: float) -> HealthCheckResult:
    if isinstance(raw, HealthCheckResult):
        return raw.copy(update={"latency_ms": latency_ms})
    if isinstance(raw, bool):
        return HealthCheckResult(
            name=check.name,
            status=HealthState.PASS if raw else (HealthState.FAIL if check.critical else HealthState.WARN),
            component_type=check.component_type,
            latency_ms=latency_ms,
        )
    if isinstance(raw, dict):
        raw_status = raw.get("status", HealthState.PASS.value)
        try:
            state = HealthState(raw_status)
        except ValueError:
            state = HealthState.PASS if bool(raw_status) else HealthState.FAIL
        details = dict(raw.get("details", {})) if check.details_public else {}
        return HealthCheckResult(
            name=str(raw.get("name") or check.name),
            status=state,
            component_type=ComponentType(raw.get("component_type", check.component_type.value)),
            latency_ms=latency_ms,
            message=raw.get("message"),
            details=sanitize_details(details),
        )
    return HealthCheckResult(
        name=check.name,
        status=HealthState.PASS,
        component_type=check.component_type,
        latency_ms=latency_ms,
        details={"value": str(raw)[:200]} if check.details_public else {},
    )


def build_response(
    request_id: str,
    checks: Sequence[HealthCheckResult],
    metadata: Optional[Dict[str, Any]] = None,
) -> HealthResponse:
    status_value = aggregate_status(checks)
    return HealthResponse(
        status=status_value,
        service=APP_NAME,
        version=APP_VERSION,
        environment=APP_ENV,
        region=APP_REGION,
        timestamp=datetime.now(tz=DEFAULT_TIMEZONE).isoformat(),
        uptime_seconds=uptime_seconds(),
        request_id=request_id,
        checks=list(checks),
        metadata={
            "router_version": ROUTER_VERSION,
            "build_sha": BUILD_SHA,
            "check_count": len(checks),
            **(metadata or {}),
        },
    )


def aggregate_status(checks: Sequence[HealthCheckResult]) -> HealthState:
    if any(check.status == HealthState.FAIL for check in checks):
        return HealthState.FAIL
    if any(check.status == HealthState.WARN for check in checks):
        return HealthState.WARN
    return HealthState.PASS


def basic_process_check() -> HealthCheckResult:
    return HealthCheckResult(
        name="process",
        status=HealthState.PASS,
        component_type=ComponentType.INTERNAL,
        details={
            "pid": os.getpid(),
            "python_version": sys.version.split()[0],
            "uptime_seconds": uptime_seconds(),
        },
    )


def event_loop_check() -> HealthCheckResult:
    return HealthCheckResult(
        name="event_loop",
        status=HealthState.PASS,
        component_type=ComponentType.INTERNAL,
        details={"loop": "responsive"},
    )


def startup_check() -> HealthCheckResult:
    uptime = uptime_seconds()
    return HealthCheckResult(
        name="startup",
        status=HealthState.PASS if uptime >= 0 else HealthState.FAIL,
        component_type=ComponentType.INTERNAL,
        details={"started_at": STARTED_AT.isoformat(), "uptime_seconds": uptime},
    )


def config_check() -> HealthCheckResult:
    warnings: List[str] = []
    if APP_ENV == "production" and BUILD_SHA == "unknown":
        warnings.append("build_sha_unknown")
    if APP_ENV == "production" and os.getenv("API_ENABLE_DOCS", "false").lower() in {"1", "true", "yes"}:
        warnings.append("docs_enabled_in_production")
    return HealthCheckResult(
        name="configuration",
        status=HealthState.WARN if warnings else HealthState.PASS,
        component_type=ComponentType.CONFIGURATION,
        message=";".join(warnings) if warnings else None,
        details={
            "environment": APP_ENV,
            "region": APP_REGION,
            "build_sha_known": BUILD_SHA != "unknown",
        },
    )


def sanitize_details(details: Mapping[str, Any]) -> Dict[str, Any]:
    sensitive = {"password", "secret", "token", "api_key", "apikey", "authorization", "cookie", "dsn", "connection_string"}
    sanitized: Dict[str, Any] = {}
    for key, value in details.items():
        key_text = str(key)
        lower = key_text.lower()
        if any(item in lower for item in sensitive):
            sanitized[key_text] = "[REDACTED]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            sanitized[key_text] = value
        else:
            sanitized[key_text] = str(value)[:500]
    return sanitized


def request_id_from_request(request: Request) -> str:
    return getattr(request.state, "request_id", None) or request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"


def uptime_seconds() -> float:
    return round(time.monotonic() - STARTED_MONOTONIC, 2)


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 4)


__all__ = [
    "router",
    "HealthState",
    "ComponentType",
    "HealthCheckResult",
    "HealthResponse",
    "BuildInfoResponse",
    "HealthCheck",
    "HealthRegistry",
    "register_health_check",
    "unregister_health_check",
]

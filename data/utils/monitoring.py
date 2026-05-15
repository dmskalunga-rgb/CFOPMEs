"""
data/utils/monitoring.py

Enterprise-grade monitoring utilities.

Este módulo centraliza recursos de monitoramento operacional para pipelines de
dados, jobs batch/streaming, APIs internas, validações, ingestão, IA e serviços
corporativos.

Capacidades principais:
- Health checks síncronos e assíncronos.
- Probes de readiness, liveness e startup.
- Heartbeat com TTL para detectar jobs travados.
- Snapshots de runtime, memória básica, CPU load quando disponível e uptime.
- Alertas estruturados com severidade, deduplicação e sinks plugáveis.
- Registry de checks com timeout, criticidade e tags.
- Agregação de status global: healthy, degraded, unhealthy.
- Exportação JSON-safe para APIs, logs, dashboards e auditoria.
- Sem dependências externas obrigatórias.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import platform
import socket
import statistics
import threading
import time
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple, Union


logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
HealthCheckFn = Callable[[], "HealthCheckResult"]
AsyncHealthCheckFn = Callable[[], Awaitable["HealthCheckResult"]]


class HealthStatus(str, Enum):
    """Status de saúde operacional."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


class ProbeType(str, Enum):
    """Tipos de probes operacionais."""

    LIVENESS = "LIVENESS"
    READINESS = "READINESS"
    STARTUP = "STARTUP"
    DEPENDENCY = "DEPENDENCY"
    CUSTOM = "CUSTOM"


class AlertSeverity(str, Enum):
    """Severidade de alertas."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AlertStatus(str, Enum):
    """Status de ciclo de vida de alerta."""

    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    SUPPRESSED = "SUPPRESSED"


class MonitoringError(Exception):
    """Erro base de monitoramento."""


class HealthCheckTimeoutError(MonitoringError):
    """Health check excedeu timeout."""


class MonitoringConfigurationError(MonitoringError):
    """Configuração inválida de monitoramento."""


class AlertSink(Protocol):
    """Contrato para destino de alertas."""

    def emit(self, alert: Mapping[str, Any]) -> None:
        """Emite alerta serializado."""


class MonitoringSink(Protocol):
    """Contrato para destino de eventos/snapshots de monitoramento."""

    def emit(self, event: Mapping[str, Any]) -> None:
        """Emite evento de monitoramento."""


@dataclass(frozen=True)
class MonitoringContext:
    """Contexto padrão de monitoramento."""

    service_name: str = "data-platform"
    service_version: Optional[str] = None
    environment: str = "production"
    instance_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    hostname: str = field(default_factory=socket.gethostname)
    process_id: int = field(default_factory=os.getpid)
    region: Optional[str] = None
    tenant_id: Optional[str] = None
    pipeline_name: Optional[str] = None
    dataset_name: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def tags(self, extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        tags = {
            "service": self.service_name,
            "version": self.service_version or "unknown",
            "environment": self.environment,
            "instance": self.instance_id,
            "hostname": self.hostname,
            "pid": str(self.process_id),
            "region": self.region or "unknown",
            "tenant": self.tenant_id or "default",
            "pipeline": self.pipeline_name or "unknown",
            "dataset": self.dataset_name or "unknown",
        }
        if extra:
            tags.update({str(k): str(v) for k, v in extra.items()})
        return tags

    def to_dict(self) -> JsonDict:
        return {
            "service_name": self.service_name,
            "service_version": self.service_version,
            "environment": self.environment,
            "instance_id": self.instance_id,
            "hostname": self.hostname,
            "process_id": self.process_id,
            "region": self.region,
            "tenant_id": self.tenant_id,
            "pipeline_name": self.pipeline_name,
            "dataset_name": self.dataset_name,
            "metadata": safe_json_value(dict(self.metadata)),
        }


@dataclass(frozen=True)
class HealthCheckResult:
    """Resultado de health check."""

    name: str
    status: HealthStatus
    message: str = ""
    critical: bool = True
    duration_ms: float = 0.0
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: Mapping[str, str] = field(default_factory=dict)
    details: Mapping[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == HealthStatus.HEALTHY

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "critical": self.critical,
            "duration_ms": self.duration_ms,
            "checked_at": self.checked_at.isoformat(),
            "tags": dict(self.tags),
            "details": safe_json_value(dict(self.details)),
            "error": self.error,
        }

    @staticmethod
    def healthy(name: str, message: str = "ok", **kwargs: Any) -> "HealthCheckResult":
        return HealthCheckResult(name=name, status=HealthStatus.HEALTHY, message=message, **kwargs)

    @staticmethod
    def degraded(name: str, message: str, **kwargs: Any) -> "HealthCheckResult":
        return HealthCheckResult(name=name, status=HealthStatus.DEGRADED, message=message, **kwargs)

    @staticmethod
    def unhealthy(name: str, message: str, **kwargs: Any) -> "HealthCheckResult":
        return HealthCheckResult(name=name, status=HealthStatus.UNHEALTHY, message=message, **kwargs)


@dataclass(frozen=True)
class HealthCheckSpec:
    """Definição registrada de health check."""

    name: str
    check: Union[HealthCheckFn, AsyncHealthCheckFn]
    probe_type: ProbeType = ProbeType.CUSTOM
    critical: bool = True
    timeout_seconds: Optional[float] = 10.0
    enabled: bool = True
    tags: Mapping[str, str] = field(default_factory=dict)
    description: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.name:
            raise MonitoringConfigurationError("HealthCheckSpec.name is required")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise MonitoringConfigurationError("timeout_seconds must be positive or None")


@dataclass(frozen=True)
class HealthReport:
    """Relatório agregado de saúde."""

    status: HealthStatus
    context: MonitoringContext
    results: Tuple[HealthCheckResult, ...]
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == HealthStatus.HEALTHY

    def summary(self) -> str:
        counts = Counter(result.status.value for result in self.results)
        return (
            f"HealthReport(service={self.context.service_name}, status={self.status.value}, "
            f"checks={len(self.results)}, healthy={counts.get('HEALTHY', 0)}, "
            f"degraded={counts.get('DEGRADED', 0)}, unhealthy={counts.get('UNHEALTHY', 0)}, "
            f"duration_ms={self.duration_ms:.2f})"
        )

    def to_dict(self) -> JsonDict:
        return {
            "status": self.status.value,
            "context": self.context.to_dict(),
            "results": [result.to_dict() for result in self.results],
            "generated_at": self.generated_at.isoformat(),
            "duration_ms": self.duration_ms,
            "summary": self.summary(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class RuntimeSnapshot:
    """Snapshot leve de runtime do processo."""

    context: MonitoringContext
    captured_at: datetime
    uptime_seconds: float
    python_version: str
    platform: str
    cwd: str
    load_average: Optional[Tuple[float, float, float]] = None
    memory_rss_bytes: Optional[int] = None
    open_fd_count: Optional[int] = None
    thread_count: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "context": self.context.to_dict(),
            "captured_at": self.captured_at.isoformat(),
            "uptime_seconds": self.uptime_seconds,
            "python_version": self.python_version,
            "platform": self.platform,
            "cwd": self.cwd,
            "load_average": list(self.load_average) if self.load_average else None,
            "memory_rss_bytes": self.memory_rss_bytes,
            "open_fd_count": self.open_fd_count,
            "thread_count": self.thread_count,
            "metadata": safe_json_value(dict(self.metadata)),
        }


@dataclass(frozen=True)
class Alert:
    """Alerta operacional estruturado."""

    title: str
    message: str
    severity: AlertSeverity
    context: MonitoringContext
    status: AlertStatus = AlertStatus.OPEN
    alert_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    dedupe_key: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: Optional[str] = None
    tags: Mapping[str, str] = field(default_factory=dict)
    details: Mapping[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        return self.dedupe_key or f"{self.context.service_name}:{self.title}:{self.severity.value}"

    def to_dict(self) -> JsonDict:
        return {
            "alert_id": self.alert_id,
            "title": self.title,
            "message": self.message,
            "severity": self.severity.value,
            "status": self.status.value,
            "dedupe_key": self.dedupe_key,
            "created_at": self.created_at.isoformat(),
            "source": self.source,
            "tags": dict(self.tags),
            "details": safe_json_value(dict(self.details)),
            "context": self.context.to_dict(),
        }


class HealthMonitor:
    """Registry e executor de health checks."""

    def __init__(
        self,
        *,
        context: Optional[MonitoringContext] = None,
        sinks: Optional[Sequence[MonitoringSink]] = None,
        alert_manager: Optional["AlertManager"] = None,
    ) -> None:
        self.context = context or MonitoringContext()
        self.sinks = list(sinks or [])
        self.alert_manager = alert_manager
        self._checks: MutableMapping[str, HealthCheckSpec] = {}
        self._lock = threading.RLock()
        self._started_monotonic = time.monotonic()

    def register(self, spec: HealthCheckSpec) -> None:
        with self._lock:
            if spec.name in self._checks:
                raise MonitoringConfigurationError(f"Health check already registered: {spec.name}")
            self._checks[spec.name] = spec

    def unregister(self, name: str) -> None:
        with self._lock:
            self._checks.pop(name, None)

    def list_checks(self, *, enabled_only: bool = True, probe_type: Optional[ProbeType] = None) -> Tuple[HealthCheckSpec, ...]:
        with self._lock:
            checks = list(self._checks.values())
        if enabled_only:
            checks = [check for check in checks if check.enabled]
        if probe_type is not None:
            checks = [check for check in checks if check.probe_type == probe_type]
        return tuple(checks)

    def run(self, *, probe_type: Optional[ProbeType] = None) -> HealthReport:
        started = time.perf_counter()
        results: List[HealthCheckResult] = []
        for spec in self.list_checks(probe_type=probe_type):
            results.append(self._run_single(spec))
        status = aggregate_health_status(results)
        report = HealthReport(
            status=status,
            context=self.context,
            results=tuple(results),
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )
        self._emit("health_report", report.to_dict())
        self._maybe_alert(report)
        return report

    async def run_async(self, *, probe_type: Optional[ProbeType] = None, concurrency: int = 25) -> HealthReport:
        if concurrency <= 0:
            raise MonitoringConfigurationError("concurrency must be positive")
        started = time.perf_counter()
        semaphore = asyncio.Semaphore(concurrency)

        async def _run(spec: HealthCheckSpec) -> HealthCheckResult:
            async with semaphore:
                return await self._run_single_async(spec)

        specs = self.list_checks(probe_type=probe_type)
        results = list(await asyncio.gather(*[_run(spec) for spec in specs])) if specs else []
        status = aggregate_health_status(results)
        report = HealthReport(
            status=status,
            context=self.context,
            results=tuple(results),
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )
        self._emit("health_report", report.to_dict())
        self._maybe_alert(report)
        return report

    def snapshot(self) -> RuntimeSnapshot:
        snapshot = RuntimeSnapshot(
            context=self.context,
            captured_at=datetime.now(timezone.utc),
            uptime_seconds=time.monotonic() - self._started_monotonic,
            python_version=platform.python_version(),
            platform=platform.platform(),
            cwd=str(Path.cwd()),
            load_average=get_load_average(),
            memory_rss_bytes=get_memory_rss_bytes(),
            open_fd_count=get_open_fd_count(),
            thread_count=threading.active_count(),
        )
        self._emit("runtime_snapshot", snapshot.to_dict())
        return snapshot

    def _run_single(self, spec: HealthCheckSpec) -> HealthCheckResult:
        started = time.perf_counter()
        try:
            if is_async_callable(spec.check):
                result = run_coroutine_sync(self._run_single_async(spec))
            else:
                if spec.timeout_seconds is None:
                    result = spec.check()  # type: ignore[misc]
                else:
                    result = run_with_timeout(spec.check, spec.timeout_seconds)
            return normalize_check_result(result, spec, started)
        except Exception as exc:
            return HealthCheckResult(
                name=spec.name,
                status=HealthStatus.UNHEALTHY if spec.critical else HealthStatus.DEGRADED,
                message="health check failed",
                critical=spec.critical,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                tags=spec.tags,
                error=str(exc),
                details={"error_type": type(exc).__name__},
            )

    async def _run_single_async(self, spec: HealthCheckSpec) -> HealthCheckResult:
        started = time.perf_counter()
        try:
            if is_async_callable(spec.check):
                coro = spec.check()  # type: ignore[misc]
            else:
                coro = asyncio.to_thread(spec.check)  # type: ignore[arg-type]
            result = await asyncio.wait_for(coro, timeout=spec.timeout_seconds) if spec.timeout_seconds else await coro
            return normalize_check_result(result, spec, started)
        except asyncio.TimeoutError:
            return HealthCheckResult(
                name=spec.name,
                status=HealthStatus.UNHEALTHY if spec.critical else HealthStatus.DEGRADED,
                message=f"health check timed out after {spec.timeout_seconds}s",
                critical=spec.critical,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                tags=spec.tags,
                error="timeout",
            )
        except Exception as exc:
            return HealthCheckResult(
                name=spec.name,
                status=HealthStatus.UNHEALTHY if spec.critical else HealthStatus.DEGRADED,
                message="health check failed",
                critical=spec.critical,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                tags=spec.tags,
                error=str(exc),
                details={"error_type": type(exc).__name__},
            )

    def _emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        event = {
            "event_id": str(uuid.uuid4()),
            "event_name": event_name,
            "emitted_at": datetime.now(timezone.utc).isoformat(),
            "context": self.context.to_dict(),
            "payload": safe_json_value(dict(payload)),
        }
        for sink in self.sinks:
            try:
                sink.emit(event)
            except Exception:
                logger.exception("Monitoring sink failed")

    def _maybe_alert(self, report: HealthReport) -> None:
        if not self.alert_manager:
            return
        if report.status == HealthStatus.HEALTHY:
            return
        severity = AlertSeverity.CRITICAL if report.status == HealthStatus.UNHEALTHY else AlertSeverity.WARNING
        failed = [item for item in report.results if item.status != HealthStatus.HEALTHY]
        self.alert_manager.emit(
            Alert(
                title=f"Health status {report.status.value}: {self.context.service_name}",
                message=report.summary(),
                severity=severity,
                context=self.context,
                dedupe_key=f"health:{self.context.service_name}:{report.status.value}",
                source="health_monitor",
                details={"failed_checks": [item.to_dict() for item in failed]},
            )
        )


class HeartbeatMonitor:
    """Controle de heartbeat com TTL para jobs e workers."""

    def __init__(self, *, ttl_seconds: float = 300.0) -> None:
        if ttl_seconds <= 0:
            raise MonitoringConfigurationError("ttl_seconds must be positive")
        self.ttl_seconds = ttl_seconds
        self._beats: MutableMapping[str, datetime] = {}
        self._lock = threading.RLock()

    def beat(self, key: str) -> None:
        with self._lock:
            self._beats[key] = datetime.now(timezone.utc)

    def remove(self, key: str) -> None:
        with self._lock:
            self._beats.pop(key, None)

    def stale(self) -> Dict[str, datetime]:
        now = datetime.now(timezone.utc)
        with self._lock:
            return {
                key: value
                for key, value in self._beats.items()
                if (now - value).total_seconds() > self.ttl_seconds
            }

    def status(self) -> HealthCheckResult:
        stale_items = self.stale()
        if stale_items:
            return HealthCheckResult.degraded(
                "heartbeat",
                f"{len(stale_items)} heartbeat(s) stale",
                critical=False,
                details={"stale": {k: v.isoformat() for k, v in stale_items.items()}},
            )
        return HealthCheckResult.healthy("heartbeat", "all heartbeats fresh", critical=False)


class AlertManager:
    """Gerenciador de alertas com deduplicação em memória."""

    def __init__(
        self,
        *,
        sinks: Optional[Sequence[AlertSink]] = None,
        dedupe_ttl_seconds: float = 300.0,
        max_alerts: int = 10_000,
    ) -> None:
        if dedupe_ttl_seconds <= 0:
            raise MonitoringConfigurationError("dedupe_ttl_seconds must be positive")
        self.sinks = list(sinks or [])
        self.dedupe_ttl_seconds = dedupe_ttl_seconds
        self.max_alerts = max_alerts
        self._alerts: Deque[Alert] = deque(maxlen=max_alerts)
        self._dedupe: MutableMapping[str, datetime] = {}
        self._lock = threading.RLock()

    @property
    def alerts(self) -> Tuple[Alert, ...]:
        with self._lock:
            return tuple(self._alerts)

    def emit(self, alert: Alert) -> bool:
        now = datetime.now(timezone.utc)
        key = alert.key()
        with self._lock:
            previous = self._dedupe.get(key)
            if previous and (now - previous).total_seconds() < self.dedupe_ttl_seconds:
                return False
            self._dedupe[key] = now
            self._alerts.append(alert)
        payload = alert.to_dict()
        for sink in self.sinks:
            try:
                sink.emit(payload)
            except Exception:
                logger.exception("Alert sink failed")
        return True

    def summary(self) -> Mapping[str, Any]:
        alerts = self.alerts
        return {
            "total": len(alerts),
            "by_severity": dict(Counter(alert.severity.value for alert in alerts)),
            "by_status": dict(Counter(alert.status.value for alert in alerts)),
        }


class InMemoryMonitoringSink:
    """Sink de eventos de monitoramento em memória."""

    def __init__(self, max_events: int = 100_000) -> None:
        self.events: Deque[Mapping[str, Any]] = deque(maxlen=max_events)
        self._lock = threading.RLock()

    def emit(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            self.events.append(dict(event))

    def list(self) -> List[Mapping[str, Any]]:
        with self._lock:
            return list(self.events)


class InMemoryAlertSink:
    """Sink de alertas em memória."""

    def __init__(self, max_alerts: int = 100_000) -> None:
        self.alerts: Deque[Mapping[str, Any]] = deque(maxlen=max_alerts)
        self._lock = threading.RLock()

    def emit(self, alert: Mapping[str, Any]) -> None:
        with self._lock:
            self.alerts.append(dict(alert))

    def list(self) -> List[Mapping[str, Any]]:
        with self._lock:
            return list(self.alerts)


class LoggingAlertSink:
    """Sink de alerta para logging."""

    def __init__(self, logger_name: str = "data.monitoring.alerts") -> None:
        self.logger = logging.getLogger(logger_name)

    def emit(self, alert: Mapping[str, Any]) -> None:
        severity = str(alert.get("severity", "INFO"))
        text = json.dumps(safe_json_value(dict(alert)), ensure_ascii=False, sort_keys=True, default=str)
        if severity == AlertSeverity.CRITICAL.value:
            self.logger.critical(text)
        elif severity == AlertSeverity.ERROR.value:
            self.logger.error(text)
        elif severity == AlertSeverity.WARNING.value:
            self.logger.warning(text)
        else:
            self.logger.info(text)


class LoggingMonitoringSink:
    """Sink de monitoramento para logging."""

    def __init__(self, logger_name: str = "data.monitoring") -> None:
        self.logger = logging.getLogger(logger_name)

    def emit(self, event: Mapping[str, Any]) -> None:
        self.logger.info(json.dumps(safe_json_value(dict(event)), ensure_ascii=False, sort_keys=True, default=str))


# =============================================================================
# Built-in health checks
# =============================================================================

def process_alive_check(name: str = "process_alive") -> HealthCheckResult:
    return HealthCheckResult.healthy(
        name,
        "process is alive",
        critical=True,
        details={"pid": os.getpid(), "hostname": socket.gethostname()},
    )


def disk_space_check(path: Union[str, Path] = ".", *, min_free_bytes: int = 1024 * 1024 * 1024, name: str = "disk_space") -> HealthCheckResult:
    usage = os.statvfs(str(path)) if hasattr(os, "statvfs") else None
    if usage is None:
        return HealthCheckResult.degraded(name, "disk check is not supported on this platform", critical=False)
    free = usage.f_bavail * usage.f_frsize
    status = HealthStatus.HEALTHY if free >= min_free_bytes else HealthStatus.UNHEALTHY
    return HealthCheckResult(
        name=name,
        status=status,
        message="disk space ok" if status == HealthStatus.HEALTHY else "disk space below threshold",
        critical=True,
        details={"path": str(path), "free_bytes": free, "min_free_bytes": min_free_bytes},
    )


def directory_writable_check(path: Union[str, Path], *, name: str = "directory_writable") -> HealthCheckResult:
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / f".monitoring_probe_{uuid.uuid4().hex}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return HealthCheckResult.healthy(name, "directory is writable", details={"path": str(target)})
    except Exception as exc:
        return HealthCheckResult.unhealthy(name, "directory is not writable", error=str(exc), details={"path": str(target)})


def tcp_connect_check(host: str, port: int, *, timeout_seconds: float = 3.0, name: Optional[str] = None) -> HealthCheckResult:
    check_name = name or f"tcp_{host}_{port}"
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return HealthCheckResult.healthy(
                check_name,
                "tcp connection ok",
                duration_ms=(time.perf_counter() - started) * 1000.0,
                details={"host": host, "port": port},
            )
    except Exception as exc:
        return HealthCheckResult.unhealthy(
            check_name,
            "tcp connection failed",
            duration_ms=(time.perf_counter() - started) * 1000.0,
            error=str(exc),
            details={"host": host, "port": port},
        )


# =============================================================================
# Utility functions
# =============================================================================

def aggregate_health_status(results: Sequence[HealthCheckResult]) -> HealthStatus:
    if not results:
        return HealthStatus.UNKNOWN
    if any(result.status == HealthStatus.UNHEALTHY and result.critical for result in results):
        return HealthStatus.UNHEALTHY
    if any(result.status in {HealthStatus.UNHEALTHY, HealthStatus.DEGRADED} for result in results):
        return HealthStatus.DEGRADED
    if all(result.status == HealthStatus.HEALTHY for result in results):
        return HealthStatus.HEALTHY
    return HealthStatus.UNKNOWN


def normalize_check_result(result: HealthCheckResult, spec: HealthCheckSpec, started_perf: float) -> HealthCheckResult:
    return HealthCheckResult(
        name=result.name or spec.name,
        status=result.status,
        message=result.message,
        critical=spec.critical,
        duration_ms=result.duration_ms or (time.perf_counter() - started_perf) * 1000.0,
        checked_at=result.checked_at,
        tags={**dict(spec.tags), **dict(result.tags)},
        details=result.details,
        error=result.error,
    )


def is_async_callable(value: Any) -> bool:
    return asyncio.iscoroutinefunction(value)


def run_coroutine_sync(coro: Awaitable[Any]) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    if loop.is_running():
        raise MonitoringError("Cannot run async health check synchronously while event loop is running")
    return loop.run_until_complete(coro)


def run_with_timeout(func: Callable[[], HealthCheckResult], timeout_seconds: float) -> HealthCheckResult:
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise HealthCheckTimeoutError(f"health check timed out after {timeout_seconds}s") from exc


def get_load_average() -> Optional[Tuple[float, float, float]]:
    try:
        return tuple(float(v) for v in os.getloadavg())  # type: ignore[return-value]
    except Exception:
        return None


def get_memory_rss_bytes() -> Optional[int]:
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        value = int(usage.ru_maxrss)
        if platform.system().lower() == "darwin":
            return value
        return value * 1024
    except Exception:
        return None


def get_open_fd_count() -> Optional[int]:
    proc_fd = Path("/proc/self/fd")
    try:
        if proc_fd.exists():
            return len(list(proc_fd.iterdir()))
    except Exception:
        return None
    return None


def safe_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): safe_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset, deque)):
        return [safe_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def build_default_monitor(
    *,
    service_name: str = "data-platform",
    service_version: Optional[str] = None,
    environment: str = "production",
    enable_logging: bool = True,
) -> HealthMonitor:
    context = MonitoringContext(service_name=service_name, service_version=service_version, environment=environment)
    alert_sinks: List[AlertSink] = [LoggingAlertSink()] if enable_logging else []
    monitoring_sinks: List[MonitoringSink] = [LoggingMonitoringSink()] if enable_logging else []
    alert_manager = AlertManager(sinks=alert_sinks)
    monitor = HealthMonitor(context=context, sinks=monitoring_sinks, alert_manager=alert_manager)
    monitor.register(HealthCheckSpec(name="process_alive", check=process_alive_check, probe_type=ProbeType.LIVENESS, critical=True))
    monitor.register(HealthCheckSpec(name="disk_space", check=lambda: disk_space_check("."), probe_type=ProbeType.READINESS, critical=False))
    return monitor


__all__ = [
    "Alert",
    "AlertManager",
    "AlertSeverity",
    "AlertSink",
    "AlertStatus",
    "HealthCheckFn",
    "AsyncHealthCheckFn",
    "HealthCheckResult",
    "HealthCheckSpec",
    "HealthCheckTimeoutError",
    "HealthMonitor",
    "HealthReport",
    "HealthStatus",
    "HeartbeatMonitor",
    "InMemoryAlertSink",
    "InMemoryMonitoringSink",
    "LoggingAlertSink",
    "LoggingMonitoringSink",
    "MonitoringConfigurationError",
    "MonitoringContext",
    "MonitoringError",
    "MonitoringSink",
    "ProbeType",
    "RuntimeSnapshot",
    "aggregate_health_status",
    "build_default_monitor",
    "directory_writable_check",
    "disk_space_check",
    "get_load_average",
    "get_memory_rss_bytes",
    "get_open_fd_count",
    "is_async_callable",
    "normalize_check_result",
    "process_alive_check",
    "run_coroutine_sync",
    "run_with_timeout",
    "safe_json_value",
    "tcp_connect_check",
]

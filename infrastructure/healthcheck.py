# kwanza-ai-core/infrastructure/healthcheck.py
from __future__ import annotations

import abc
import asyncio
import contextlib
import inspect
import logging
import os
import platform
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Iterable, Mapping, Optional, Protocol, Sequence


class HealthStatus(str, Enum):
    PASSING = "passing"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class HealthCheckType(str, Enum):
    LIVENESS = "liveness"
    READINESS = "readiness"
    STARTUP = "startup"
    DEPENDENCY = "dependency"
    DIAGNOSTIC = "diagnostic"


class HealthSeverity(int, Enum):
    INFO = 10
    WARNING = 50
    CRITICAL = 100


@dataclass(frozen=True)
class HealthCheckContext:
    service_name: str
    environment: str
    instance_id: str
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HealthCheckResult:
    name: str
    status: HealthStatus
    check_type: HealthCheckType
    severity: HealthSeverity = HealthSeverity.CRITICAL
    message: str = ""
    latency_ms: float = 0.0
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HealthReport:
    service_name: str
    environment: str
    instance_id: str
    status: HealthStatus
    generated_at: str
    uptime_seconds: float
    checks: list[HealthCheckResult]
    summary: Dict[str, int]
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_healthy(self) -> bool:
        return self.status in {HealthStatus.PASSING, HealthStatus.WARNING}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service": self.service_name,
            "environment": self.environment,
            "instance_id": self.instance_id,
            "status": self.status.value,
            "healthy": self.is_healthy,
            "generated_at": self.generated_at,
            "uptime_seconds": self.uptime_seconds,
            "summary": self.summary,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status.value,
                    "type": check.check_type.value,
                    "severity": check.severity.name.lower(),
                    "message": check.message,
                    "latency_ms": round(check.latency_ms, 2),
                    "checked_at": check.checked_at,
                    "metadata": check.metadata,
                }
                for check in self.checks
            ],
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class HealthCheckConfig:
    service_name: str = "kwanza-ai-core"
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    instance_id: str = field(default_factory=lambda: os.getenv("INSTANCE_ID", str(uuid.uuid4())))
    default_timeout_seconds: float = 5.0
    cache_ttl_seconds: float = 2.0
    fail_on_warning: bool = False
    include_system_info: bool = True
    max_concurrent_checks: int = 20


class MetricsSink(Protocol):
    def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None: ...

    def timing(
        self,
        name: str,
        value_ms: float,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None: ...


class NoopMetricsSink:
    def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        return None

    def timing(
        self,
        name: str,
        value_ms: float,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        return None


class HealthCheckError(RuntimeError):
    pass


HealthCheckCallable = Callable[
    [HealthCheckContext],
    HealthCheckResult | Awaitable[HealthCheckResult],
]


@dataclass(frozen=True)
class RegisteredHealthCheck:
    name: str
    check_type: HealthCheckType
    callback: HealthCheckCallable
    timeout_seconds: float
    severity: HealthSeverity
    enabled: bool = True
    tags: Dict[str, str] = field(default_factory=dict)


class BaseHealthCheck(abc.ABC):
    name: str
    check_type: HealthCheckType = HealthCheckType.DIAGNOSTIC
    severity: HealthSeverity = HealthSeverity.CRITICAL
    timeout_seconds: float = 5.0

    @abc.abstractmethod
    async def run(self, context: HealthCheckContext) -> HealthCheckResult:
        raise NotImplementedError


class HealthCheckRegistry:
    def __init__(self) -> None:
        self._checks: Dict[str, RegisteredHealthCheck] = {}

    def register(
        self,
        name: str,
        callback: HealthCheckCallable,
        *,
        check_type: HealthCheckType = HealthCheckType.DIAGNOSTIC,
        timeout_seconds: float = 5.0,
        severity: HealthSeverity = HealthSeverity.CRITICAL,
        enabled: bool = True,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        if not name:
            raise ValueError("Health check name cannot be empty")

        self._checks[name] = RegisteredHealthCheck(
            name=name,
            callback=callback,
            check_type=check_type,
            timeout_seconds=timeout_seconds,
            severity=severity,
            enabled=enabled,
            tags=dict(tags or {}),
        )

    def register_class(self, check: BaseHealthCheck, enabled: bool = True) -> None:
        self.register(
            check.name,
            check.run,
            check_type=check.check_type,
            timeout_seconds=check.timeout_seconds,
            severity=check.severity,
            enabled=enabled,
        )

    def unregister(self, name: str) -> None:
        self._checks.pop(name, None)

    def list(
        self,
        check_types: Optional[Iterable[HealthCheckType]] = None,
        include_disabled: bool = False,
    ) -> list[RegisteredHealthCheck]:
        allowed = set(check_types or [])

        checks = list(self._checks.values())

        if allowed:
            checks = [check for check in checks if check.check_type in allowed]

        if not include_disabled:
            checks = [check for check in checks if check.enabled]

        return checks


class HealthCheckManager:
    def __init__(
        self,
        config: Optional[HealthCheckConfig] = None,
        metrics: Optional[MetricsSink] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or HealthCheckConfig()
        self.metrics = metrics or NoopMetricsSink()
        self.logger = logger or logging.getLogger("kwanza.infrastructure.healthcheck")
        self.registry = HealthCheckRegistry()
        self._started_at = time.monotonic()
        self._cache: Dict[str, tuple[float, HealthReport]] = {}
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_checks)

        self.register_defaults()

    def register(
        self,
        name: str,
        callback: HealthCheckCallable,
        *,
        check_type: HealthCheckType = HealthCheckType.DIAGNOSTIC,
        timeout_seconds: Optional[float] = None,
        severity: HealthSeverity = HealthSeverity.CRITICAL,
        enabled: bool = True,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.registry.register(
            name=name,
            callback=callback,
            check_type=check_type,
            timeout_seconds=timeout_seconds or self.config.default_timeout_seconds,
            severity=severity,
            enabled=enabled,
            tags=tags,
        )

    def register_defaults(self) -> None:
        self.register(
            "process_liveness",
            self._process_liveness,
            check_type=HealthCheckType.LIVENESS,
            severity=HealthSeverity.CRITICAL,
        )
        self.register(
            "runtime_info",
            self._runtime_info,
            check_type=HealthCheckType.DIAGNOSTIC,
            severity=HealthSeverity.INFO,
        )

    async def check_all(
        self,
        check_types: Optional[Sequence[HealthCheckType]] = None,
        *,
        use_cache: bool = True,
    ) -> HealthReport:
        cache_key = ",".join(sorted([item.value for item in check_types or []])) or "all"

        if use_cache:
            cached = self._get_cached(cache_key)
            if cached is not None:
                return cached

        started = time.monotonic()
        checks = self.registry.list(check_types=check_types)

        context = HealthCheckContext(
            service_name=self.config.service_name,
            environment=self.config.environment,
            instance_id=self.config.instance_id,
        )

        results = await asyncio.gather(
            *[self._run_registered_check(check, context) for check in checks],
            return_exceptions=False,
        )

        status = self._aggregate_status(results)
        report = HealthReport(
            service_name=self.config.service_name,
            environment=self.config.environment,
            instance_id=self.config.instance_id,
            status=status,
            generated_at=datetime.now(timezone.utc).isoformat(),
            uptime_seconds=self.uptime_seconds,
            checks=results,
            summary=self._summary(results),
            metadata=self._metadata(),
        )

        elapsed_ms = (time.monotonic() - started) * 1000
        self.metrics.timing("healthcheck.all.latency_ms", elapsed_ms, tags=self._tags(status))
        self.metrics.increment("healthcheck.all.count", tags=self._tags(status))

        self._set_cached(cache_key, report)
        return report

    async def liveness(self) -> HealthReport:
        return await self.check_all([HealthCheckType.LIVENESS])

    async def readiness(self) -> HealthReport:
        return await self.check_all(
            [
                HealthCheckType.LIVENESS,
                HealthCheckType.READINESS,
                HealthCheckType.DEPENDENCY,
            ]
        )

    async def startup(self) -> HealthReport:
        return await self.check_all([HealthCheckType.STARTUP])

    async def diagnostics(self) -> HealthReport:
        return await self.check_all([HealthCheckType.DIAGNOSTIC])

    @property
    def uptime_seconds(self) -> float:
        return max(0.0, time.monotonic() - self._started_at)

    async def _run_registered_check(
        self,
        registered: RegisteredHealthCheck,
        context: HealthCheckContext,
    ) -> HealthCheckResult:
        async with self._semaphore:
            started = time.monotonic()

            try:
                result = registered.callback(context)

                if inspect.isawaitable(result):
                    result = await asyncio.wait_for(
                        result,
                        timeout=registered.timeout_seconds,
                    )

                elapsed_ms = (time.monotonic() - started) * 1000

                normalized = HealthCheckResult(
                    name=result.name or registered.name,
                    status=result.status,
                    check_type=registered.check_type,
                    severity=registered.severity,
                    message=result.message,
                    latency_ms=elapsed_ms,
                    metadata={
                        **result.metadata,
                        "tags": registered.tags,
                    },
                )

                self.metrics.increment(
                    "healthcheck.check.count",
                    tags=self._check_tags(normalized),
                )
                self.metrics.timing(
                    "healthcheck.check.latency_ms",
                    elapsed_ms,
                    tags=self._check_tags(normalized),
                )

                return normalized

            except asyncio.TimeoutError:
                elapsed_ms = (time.monotonic() - started) * 1000
                return HealthCheckResult(
                    name=registered.name,
                    status=HealthStatus.CRITICAL,
                    check_type=registered.check_type,
                    severity=registered.severity,
                    message=f"Health check timed out after {registered.timeout_seconds}s",
                    latency_ms=elapsed_ms,
                    metadata={"timeout_seconds": registered.timeout_seconds},
                )

            except Exception as exc:
                elapsed_ms = (time.monotonic() - started) * 1000
                self.logger.exception("Health check failed: %s", registered.name)

                return HealthCheckResult(
                    name=registered.name,
                    status=HealthStatus.CRITICAL,
                    check_type=registered.check_type,
                    severity=registered.severity,
                    message=str(exc),
                    latency_ms=elapsed_ms,
                    metadata={"error": repr(exc)},
                )

    def _aggregate_status(self, results: Iterable[HealthCheckResult]) -> HealthStatus:
        results = list(results)

        if not results:
            return HealthStatus.UNKNOWN

        critical = any(
            result.status == HealthStatus.CRITICAL
            and result.severity == HealthSeverity.CRITICAL
            for result in results
        )

        warning = any(result.status == HealthStatus.WARNING for result in results)

        if critical:
            return HealthStatus.CRITICAL

        if warning:
            return HealthStatus.CRITICAL if self.config.fail_on_warning else HealthStatus.WARNING

        if any(result.status == HealthStatus.UNKNOWN for result in results):
            return HealthStatus.WARNING

        return HealthStatus.PASSING

    def _summary(self, results: Iterable[HealthCheckResult]) -> Dict[str, int]:
        summary = {status.value: 0 for status in HealthStatus}

        for result in results:
            summary[result.status.value] += 1

        return summary

    async def _process_liveness(self, context: HealthCheckContext) -> HealthCheckResult:
        return HealthCheckResult(
            name="process_liveness",
            status=HealthStatus.PASSING,
            check_type=HealthCheckType.LIVENESS,
            severity=HealthSeverity.CRITICAL,
            message="Process is alive",
            metadata={
                "service": context.service_name,
                "instance_id": context.instance_id,
            },
        )

    async def _runtime_info(self, context: HealthCheckContext) -> HealthCheckResult:
        metadata: Dict[str, Any] = {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "pid": os.getpid(),
        }

        if self.config.include_system_info:
            metadata.update(
                {
                    "machine": platform.machine(),
                    "system": platform.system(),
                    "release": platform.release(),
                }
            )

        return HealthCheckResult(
            name="runtime_info",
            status=HealthStatus.PASSING,
            check_type=HealthCheckType.DIAGNOSTIC,
            severity=HealthSeverity.INFO,
            message="Runtime information collected",
            metadata=metadata,
        )

    def _get_cached(self, key: str) -> Optional[HealthReport]:
        item = self._cache.get(key)

        if item is None:
            return None

        expires_at, report = item

        if time.time() >= expires_at:
            self._cache.pop(key, None)
            return None

        return report

    def _set_cached(self, key: str, report: HealthReport) -> None:
        self._cache[key] = (time.time() + self.config.cache_ttl_seconds, report)

    def _metadata(self) -> Dict[str, Any]:
        return {
            "host": platform.node(),
            "pid": os.getpid(),
            "manager": "HealthCheckManager",
        }

    def _tags(self, status: HealthStatus) -> Dict[str, str]:
        return {
            "service": self.config.service_name,
            "environment": self.config.environment,
            "status": status.value,
        }

    def _check_tags(self, result: HealthCheckResult) -> Dict[str, str]:
        return {
            "service": self.config.service_name,
            "environment": self.config.environment,
            "check": result.name,
            "type": result.check_type.value,
            "status": result.status.value,
            "severity": result.severity.name.lower(),
        }


class DatabaseHealthCheck(BaseHealthCheck):
    name = "database"
    check_type = HealthCheckType.DEPENDENCY
    severity = HealthSeverity.CRITICAL
    timeout_seconds = 5.0

    def __init__(self, database: Any) -> None:
        self.database = database

    async def run(self, context: HealthCheckContext) -> HealthCheckResult:
        started = time.monotonic()

        health = await self.database.health()
        status = HealthStatus.PASSING if str(health.state).endswith("CONNECTED") else HealthStatus.CRITICAL

        return HealthCheckResult(
            name=self.name,
            status=status,
            check_type=self.check_type,
            severity=self.severity,
            message=getattr(health, "message", "Database health checked"),
            latency_ms=(time.monotonic() - started) * 1000,
            metadata={
                "driver": str(getattr(health, "driver", "")),
                "state": str(getattr(health, "state", "")),
            },
        )


class CacheHealthCheck(BaseHealthCheck):
    name = "cache"
    check_type = HealthCheckType.DEPENDENCY
    severity = HealthSeverity.WARNING
    timeout_seconds = 3.0

    def __init__(self, cache: Any) -> None:
        self.cache = cache

    async def run(self, context: HealthCheckContext) -> HealthCheckResult:
        key = f"healthcheck:{context.instance_id}"
        value = {"ok": True, "request_id": context.request_id}

        await self.cache.set(key, value, ttl_seconds=10)
        cached = await self.cache.get(key)
        await self.cache.delete(key)

        if cached:
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.PASSING,
                check_type=self.check_type,
                severity=self.severity,
                message="Cache read/write/delete successful",
            )

        return HealthCheckResult(
            name=self.name,
            status=HealthStatus.WARNING,
            check_type=self.check_type,
            severity=self.severity,
            message="Cache did not return expected value",
        )


class EventBusHealthCheck(BaseHealthCheck):
    name = "event_bus"
    check_type = HealthCheckType.DEPENDENCY
    severity = HealthSeverity.WARNING
    timeout_seconds = 3.0

    def __init__(self, event_bus: Any) -> None:
        self.event_bus = event_bus

    async def run(self, context: HealthCheckContext) -> HealthCheckResult:
        running = bool(getattr(self.event_bus, "_running", False))

        return HealthCheckResult(
            name=self.name,
            status=HealthStatus.PASSING if running else HealthStatus.WARNING,
            check_type=self.check_type,
            severity=self.severity,
            message="Event bus running" if running else "Event bus is not running",
        )


class HttpEndpointHealthCheck(BaseHealthCheck):
    check_type = HealthCheckType.DEPENDENCY
    severity = HealthSeverity.WARNING
    timeout_seconds = 5.0

    def __init__(
        self,
        name: str,
        url: str,
        expected_status: int = 200,
        timeout_seconds: float = 5.0,
        severity: HealthSeverity = HealthSeverity.WARNING,
    ) -> None:
        self.name = name
        self.url = url
        self.expected_status = expected_status
        self.timeout_seconds = timeout_seconds
        self.severity = severity

    async def run(self, context: HealthCheckContext) -> HealthCheckResult:
        try:
            import httpx
        except ImportError as exc:
            raise HealthCheckError("httpx is required for HttpEndpointHealthCheck") from exc

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(self.url)

        passing = response.status_code == self.expected_status

        return HealthCheckResult(
            name=self.name,
            status=HealthStatus.PASSING if passing else HealthStatus.WARNING,
            check_type=self.check_type,
            severity=self.severity,
            message=f"HTTP status {response.status_code}",
            metadata={
                "url": self.url,
                "expected_status": self.expected_status,
                "actual_status": response.status_code,
            },
        )


class CompositeHealthCheck(BaseHealthCheck):
    def __init__(
        self,
        name: str,
        checks: Sequence[BaseHealthCheck],
        check_type: HealthCheckType = HealthCheckType.DIAGNOSTIC,
        severity: HealthSeverity = HealthSeverity.CRITICAL,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.name = name
        self.checks = list(checks)
        self.check_type = check_type
        self.severity = severity
        self.timeout_seconds = timeout_seconds

    async def run(self, context: HealthCheckContext) -> HealthCheckResult:
        results = await asyncio.gather(
            *[check.run(context) for check in self.checks],
            return_exceptions=True,
        )

        normalized: list[HealthCheckResult] = []

        for result in results:
            if isinstance(result, Exception):
                normalized.append(
                    HealthCheckResult(
                        name="composite_child",
                        status=HealthStatus.CRITICAL,
                        check_type=self.check_type,
                        severity=HealthSeverity.CRITICAL,
                        message=str(result),
                    )
                )
            else:
                normalized.append(result)

        status = HealthStatus.PASSING

        if any(item.status == HealthStatus.CRITICAL for item in normalized):
            status = HealthStatus.CRITICAL
        elif any(item.status == HealthStatus.WARNING for item in normalized):
            status = HealthStatus.WARNING

        return HealthCheckResult(
            name=self.name,
            status=status,
            check_type=self.check_type,
            severity=self.severity,
            message=f"Composite health check executed with {len(normalized)} checks",
            metadata={
                "children": [
                    {
                        "name": item.name,
                        "status": item.status.value,
                        "message": item.message,
                    }
                    for item in normalized
                ]
            },
        )


def health_check(
    manager: HealthCheckManager,
    name: str,
    *,
    check_type: HealthCheckType = HealthCheckType.DIAGNOSTIC,
    timeout_seconds: Optional[float] = None,
    severity: HealthSeverity = HealthSeverity.CRITICAL,
    enabled: bool = True,
    tags: Optional[Mapping[str, str]] = None,
) -> Callable[[HealthCheckCallable], HealthCheckCallable]:
    def decorator(func: HealthCheckCallable) -> HealthCheckCallable:
        manager.register(
            name=name,
            callback=func,
            check_type=check_type,
            timeout_seconds=timeout_seconds,
            severity=severity,
            enabled=enabled,
            tags=tags,
        )
        return func

    return decorator


def build_health_manager_from_env() -> HealthCheckManager:
    config = HealthCheckConfig(
        service_name=os.getenv("HEALTH_SERVICE_NAME", os.getenv("APP_NAME", "kwanza-ai-core")),
        environment=os.getenv("APP_ENV", "development"),
        instance_id=os.getenv("INSTANCE_ID", str(uuid.uuid4())),
        default_timeout_seconds=float(os.getenv("HEALTH_DEFAULT_TIMEOUT_SECONDS", "5")),
        cache_ttl_seconds=float(os.getenv("HEALTH_CACHE_TTL_SECONDS", "2")),
        fail_on_warning=os.getenv("HEALTH_FAIL_ON_WARNING", "false").lower() == "true",
        include_system_info=os.getenv("HEALTH_INCLUDE_SYSTEM_INFO", "true").lower() == "true",
        max_concurrent_checks=int(os.getenv("HEALTH_MAX_CONCURRENT_CHECKS", "20")),
    )
    return HealthCheckManager(config=config)


def simple_passing_check(
    name: str,
    message: str = "OK",
    check_type: HealthCheckType = HealthCheckType.DIAGNOSTIC,
) -> HealthCheckCallable:
    async def _check(context: HealthCheckContext) -> HealthCheckResult:
        return HealthCheckResult(
            name=name,
            status=HealthStatus.PASSING,
            check_type=check_type,
            message=message,
        )

    return _check
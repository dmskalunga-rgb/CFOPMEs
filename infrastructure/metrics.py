# kwanza-ai-core/infrastructure/metrics.py
from __future__ import annotations

import abc
import asyncio
import contextlib
import json
import logging
import math
import os
import statistics
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import wraps
from typing import Any, Awaitable, Callable, Dict, Iterable, Mapping, Optional, Protocol, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class MetricType(str, Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    TIMER = "timer"


@dataclass(frozen=True)
class MetricsConfig:
    namespace: str = "kwanza_ai"
    service_name: str = "kwanza-ai-core"
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    instance_id: str = field(default_factory=lambda: os.getenv("INSTANCE_ID", "local"))
    enabled: bool = True
    default_histogram_buckets: tuple[float, ...] = (
        1,
        5,
        10,
        25,
        50,
        100,
        250,
        500,
        1000,
        2500,
        5000,
        10000,
    )
    max_histogram_samples: int = 20_000
    export_timestamp: bool = True


@dataclass(frozen=True)
class MetricIdentity:
    name: str
    metric_type: MetricType
    description: str = ""
    unit: str = ""
    labels: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class MetricSample:
    name: str
    metric_type: MetricType
    value: float
    labels: Dict[str, str]
    timestamp: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class MetricsError(RuntimeError):
    pass


class MetricValidationError(MetricsError):
    pass


class Metric:
    def __init__(self, identity: MetricIdentity) -> None:
        self.identity = identity
        self._lock = threading.RLock()

    @abc.abstractmethod
    def collect(self) -> list[MetricSample]:
        raise NotImplementedError

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _normalize_labels(self, labels: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        return {str(k): str(v) for k, v in dict(labels or {}).items()}


class Counter(Metric):
    def __init__(self, identity: MetricIdentity) -> None:
        super().__init__(identity)
        self._values: Dict[tuple[tuple[str, str], ...], float] = {}

    def increment(self, value: float = 1.0, labels: Optional[Mapping[str, str]] = None) -> None:
        if value < 0:
            raise MetricValidationError("Counter cannot be incremented by negative value")

        key = labels_key(labels)

        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + value

    def collect(self) -> list[MetricSample]:
        with self._lock:
            return [
                MetricSample(
                    name=self.identity.name,
                    metric_type=MetricType.COUNTER,
                    value=value,
                    labels=dict(key),
                    timestamp=self._timestamp(),
                )
                for key, value in self._values.items()
            ]


class Gauge(Metric):
    def __init__(self, identity: MetricIdentity) -> None:
        super().__init__(identity)
        self._values: Dict[tuple[tuple[str, str], ...], float] = {}

    def set(self, value: float, labels: Optional[Mapping[str, str]] = None) -> None:
        key = labels_key(labels)

        with self._lock:
            self._values[key] = float(value)

    def increment(self, value: float = 1.0, labels: Optional[Mapping[str, str]] = None) -> None:
        key = labels_key(labels)

        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + value

    def decrement(self, value: float = 1.0, labels: Optional[Mapping[str, str]] = None) -> None:
        self.increment(-value, labels)

    def collect(self) -> list[MetricSample]:
        with self._lock:
            return [
                MetricSample(
                    name=self.identity.name,
                    metric_type=MetricType.GAUGE,
                    value=value,
                    labels=dict(key),
                    timestamp=self._timestamp(),
                )
                for key, value in self._values.items()
            ]


class Histogram(Metric):
    def __init__(
        self,
        identity: MetricIdentity,
        buckets: Iterable[float],
        max_samples: int = 20_000,
    ) -> None:
        super().__init__(identity)
        self.buckets = tuple(sorted(float(bucket) for bucket in buckets))
        self.max_samples = max_samples
        self._samples: Dict[tuple[tuple[str, str], ...], list[float]] = {}

    def observe(self, value: float, labels: Optional[Mapping[str, str]] = None) -> None:
        if math.isnan(value) or math.isinf(value):
            raise MetricValidationError("Histogram value must be finite")

        key = labels_key(labels)

        with self._lock:
            bucket = self._samples.setdefault(key, [])
            bucket.append(float(value))

            if len(bucket) > self.max_samples:
                del bucket[: len(bucket) - self.max_samples]

    def collect(self) -> list[MetricSample]:
        result: list[MetricSample] = []

        with self._lock:
            for key, values in self._samples.items():
                labels = dict(key)

                if not values:
                    continue

                sorted_values = sorted(values)
                count = len(sorted_values)
                total = sum(sorted_values)

                result.extend(
                    [
                        MetricSample(
                            name=f"{self.identity.name}_count",
                            metric_type=MetricType.HISTOGRAM,
                            value=count,
                            labels=labels,
                            timestamp=self._timestamp(),
                        ),
                        MetricSample(
                            name=f"{self.identity.name}_sum",
                            metric_type=MetricType.HISTOGRAM,
                            value=total,
                            labels=labels,
                            timestamp=self._timestamp(),
                        ),
                        MetricSample(
                            name=f"{self.identity.name}_avg",
                            metric_type=MetricType.HISTOGRAM,
                            value=total / count,
                            labels=labels,
                            timestamp=self._timestamp(),
                        ),
                        MetricSample(
                            name=f"{self.identity.name}_min",
                            metric_type=MetricType.HISTOGRAM,
                            value=sorted_values[0],
                            labels=labels,
                            timestamp=self._timestamp(),
                        ),
                        MetricSample(
                            name=f"{self.identity.name}_max",
                            metric_type=MetricType.HISTOGRAM,
                            value=sorted_values[-1],
                            labels=labels,
                            timestamp=self._timestamp(),
                        ),
                    ]
                )

                for percentile in (50, 75, 90, 95, 99):
                    result.append(
                        MetricSample(
                            name=f"{self.identity.name}_p{percentile}",
                            metric_type=MetricType.HISTOGRAM,
                            value=percentile_value(sorted_values, percentile),
                            labels=labels,
                            timestamp=self._timestamp(),
                        )
                    )

                for bucket in self.buckets:
                    result.append(
                        MetricSample(
                            name=f"{self.identity.name}_bucket",
                            metric_type=MetricType.HISTOGRAM,
                            value=sum(1 for item in sorted_values if item <= bucket),
                            labels={**labels, "le": str(bucket)},
                            timestamp=self._timestamp(),
                        )
                    )

                result.append(
                    MetricSample(
                        name=f"{self.identity.name}_bucket",
                        metric_type=MetricType.HISTOGRAM,
                        value=count,
                        labels={**labels, "le": "+Inf"},
                        timestamp=self._timestamp(),
                    )
                )

        return result


class Timer(Histogram):
    def time(self, labels: Optional[Mapping[str, str]] = None) -> "TimerContext":
        return TimerContext(self, labels)


class TimerContext:
    def __init__(self, timer: Timer, labels: Optional[Mapping[str, str]] = None) -> None:
        self.timer = timer
        self.labels = labels
        self.started = 0.0

    def __enter__(self) -> "TimerContext":
        self.started = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        elapsed_ms = (time.monotonic() - self.started) * 1000
        self.timer.observe(elapsed_ms, self.labels)

    async def __aenter__(self) -> "TimerContext":
        self.started = time.monotonic()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        elapsed_ms = (time.monotonic() - self.started) * 1000
        self.timer.observe(elapsed_ms, self.labels)


class MetricsRegistry:
    def __init__(self, config: Optional[MetricsConfig] = None) -> None:
        self.config = config or MetricsConfig()
        self._metrics: Dict[str, Metric] = {}
        self._lock = threading.RLock()

    def counter(self, name: str, description: str = "", labels: Iterable[str] = ()) -> Counter:
        return self._get_or_create(
            name,
            MetricType.COUNTER,
            lambda identity: Counter(identity),
            description,
            labels,
        )

    def gauge(self, name: str, description: str = "", labels: Iterable[str] = ()) -> Gauge:
        return self._get_or_create(
            name,
            MetricType.GAUGE,
            lambda identity: Gauge(identity),
            description,
            labels,
        )

    def histogram(
        self,
        name: str,
        description: str = "",
        labels: Iterable[str] = (),
        buckets: Optional[Iterable[float]] = None,
    ) -> Histogram:
        return self._get_or_create(
            name,
            MetricType.HISTOGRAM,
            lambda identity: Histogram(
                identity,
                buckets or self.config.default_histogram_buckets,
                self.config.max_histogram_samples,
            ),
            description,
            labels,
        )

    def timer(
        self,
        name: str,
        description: str = "",
        labels: Iterable[str] = (),
        buckets: Optional[Iterable[float]] = None,
    ) -> Timer:
        return self._get_or_create(
            name,
            MetricType.TIMER,
            lambda identity: Timer(
                identity,
                buckets or self.config.default_histogram_buckets,
                self.config.max_histogram_samples,
            ),
            description,
            labels,
        )

    def collect(self) -> list[MetricSample]:
        with self._lock:
            samples: list[MetricSample] = []

            for metric in self._metrics.values():
                samples.extend(metric.collect())

            return [
                MetricSample(
                    name=self._full_name(sample.name),
                    metric_type=sample.metric_type,
                    value=sample.value,
                    labels={
                        "service": self.config.service_name,
                        "environment": self.config.environment,
                        "instance_id": self.config.instance_id,
                        **sample.labels,
                    },
                    timestamp=sample.timestamp,
                    metadata=sample.metadata,
                )
                for sample in samples
            ]

    def reset(self) -> None:
        with self._lock:
            self._metrics.clear()

    def _get_or_create(
        self,
        name: str,
        metric_type: MetricType,
        factory: Callable[[MetricIdentity], Metric],
        description: str,
        labels: Iterable[str],
    ) -> Any:
        validate_metric_name(name)

        with self._lock:
            if name in self._metrics:
                metric = self._metrics[name]

                if metric.identity.metric_type != metric_type:
                    raise MetricValidationError(
                        f"Metric {name} already exists as {metric.identity.metric_type.value}"
                    )

                return metric

            identity = MetricIdentity(
                name=name,
                metric_type=metric_type,
                description=description,
                labels=tuple(labels),
            )

            metric = factory(identity)
            self._metrics[name] = metric
            return metric

    def _full_name(self, name: str) -> str:
        namespace = self.config.namespace.strip("_")
        clean_name = name.strip("_")
        return f"{namespace}_{clean_name}" if namespace else clean_name


class MetricsSink(Protocol):
    def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None: ...

    def gauge(
        self,
        name: str,
        value: float,
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

    def gauge(
        self,
        name: str,
        value: float,
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


class InMemoryMetricsSink:
    def __init__(self, registry: Optional[MetricsRegistry] = None) -> None:
        self.registry = registry or MetricsRegistry()

    def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.registry.counter(name).increment(value, labels=tags)

    def gauge(
        self,
        name: str,
        value: float,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.registry.gauge(name).set(value, labels=tags)

    def timing(
        self,
        name: str,
        value_ms: float,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.registry.timer(name).observe(value_ms, labels=tags)


class MetricsExporter(abc.ABC):
    @abc.abstractmethod
    def export(self, samples: Iterable[MetricSample]) -> str:
        raise NotImplementedError


class JsonMetricsExporter(MetricsExporter):
    def export(self, samples: Iterable[MetricSample]) -> str:
        return json.dumps(
            [
                {
                    "name": sample.name,
                    "type": sample.metric_type.value,
                    "value": sample.value,
                    "labels": sample.labels,
                    "timestamp": sample.timestamp,
                    "metadata": sample.metadata,
                }
                for sample in samples
            ],
            ensure_ascii=False,
            indent=2,
            default=str,
        )


class PrometheusMetricsExporter(MetricsExporter):
    def export(self, samples: Iterable[MetricSample]) -> str:
        lines: list[str] = []

        for sample in samples:
            labels = prometheus_labels(sample.labels)
            lines.append(f"{sanitize_prometheus_name(sample.name)}{labels} {sample.value}")

        return "\n".join(lines) + "\n"


class MetricsManager:
    def __init__(
        self,
        config: Optional[MetricsConfig] = None,
        registry: Optional[MetricsRegistry] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or MetricsConfig()
        self.registry = registry or MetricsRegistry(self.config)
        self.sink = InMemoryMetricsSink(self.registry)
        self.logger = logger or logging.getLogger("kwanza.infrastructure.metrics")
        self.json_exporter = JsonMetricsExporter()
        self.prometheus_exporter = PrometheusMetricsExporter()

    def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        if self.config.enabled:
            self.sink.increment(name, value, tags)

    def gauge(
        self,
        name: str,
        value: float,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        if self.config.enabled:
            self.sink.gauge(name, value, tags)

    def timing(
        self,
        name: str,
        value_ms: float,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        if self.config.enabled:
            self.sink.timing(name, value_ms, tags)

    def timer(self, name: str, tags: Optional[Mapping[str, str]] = None) -> TimerContext:
        metric = self.registry.timer(name)
        return metric.time(tags)

    def collect(self) -> list[MetricSample]:
        return self.registry.collect()

    def export_json(self) -> str:
        return self.json_exporter.export(self.collect())

    def export_prometheus(self) -> str:
        return self.prometheus_exporter.export(self.collect())

    def reset(self) -> None:
        self.registry.reset()


def measured(
    metrics: MetricsManager | MetricsSink,
    metric_name: str,
    tags: Optional[Mapping[str, str]] = None,
) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        if asyncio.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                started = time.monotonic()

                try:
                    result = await func(*args, **kwargs)
                    metrics.increment(f"{metric_name}.success", tags=tags)
                    return result
                except Exception:
                    metrics.increment(f"{metric_name}.error", tags=tags)
                    raise
                finally:
                    metrics.timing(
                        f"{metric_name}.latency_ms",
                        (time.monotonic() - started) * 1000,
                        tags=tags,
                    )

            return async_wrapper  # type: ignore[return-value]

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()

            try:
                result = func(*args, **kwargs)
                metrics.increment(f"{metric_name}.success", tags=tags)
                return result
            except Exception:
                metrics.increment(f"{metric_name}.error", tags=tags)
                raise
            finally:
                metrics.timing(
                    f"{metric_name}.latency_ms",
                    (time.monotonic() - started) * 1000,
                    tags=tags,
                )

        return sync_wrapper  # type: ignore[return-value]

    return decorator


@contextlib.contextmanager
def measure_block(
    metrics: MetricsManager | MetricsSink,
    metric_name: str,
    tags: Optional[Mapping[str, str]] = None,
):
    started = time.monotonic()

    try:
        yield
        metrics.increment(f"{metric_name}.success", tags=tags)
    except Exception:
        metrics.increment(f"{metric_name}.error", tags=tags)
        raise
    finally:
        metrics.timing(
            f"{metric_name}.latency_ms",
            (time.monotonic() - started) * 1000,
            tags=tags,
        )


@contextlib.asynccontextmanager
async def measure_async_block(
    metrics: MetricsManager | MetricsSink,
    metric_name: str,
    tags: Optional[Mapping[str, str]] = None,
):
    started = time.monotonic()

    try:
        yield
        metrics.increment(f"{metric_name}.success", tags=tags)
    except Exception:
        metrics.increment(f"{metric_name}.error", tags=tags)
        raise
    finally:
        metrics.timing(
            f"{metric_name}.latency_ms",
            (time.monotonic() - started) * 1000,
            tags=tags,
        )


class PeriodicMetricsLogger:
    def __init__(
        self,
        manager: MetricsManager,
        interval_seconds: float = 60.0,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.manager = manager
        self.interval_seconds = interval_seconds
        self.logger = logger or logging.getLogger("kwanza.infrastructure.metrics.periodic")
        self._task: Optional[asyncio.Task[Any]] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._loop(), name="periodic-metrics-logger")

    async def stop(self) -> None:
        self._running = False

        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.interval_seconds)

            try:
                self.logger.info(
                    "Metrics snapshot",
                    extra={"metrics": json.loads(self.manager.export_json())},
                )
            except Exception:
                self.logger.exception("Failed to log metrics snapshot")


def labels_key(labels: Optional[Mapping[str, str]] = None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(k), str(v)) for k, v in dict(labels or {}).items()))


def percentile_value(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0

    if len(sorted_values) == 1:
        return sorted_values[0]

    index = (len(sorted_values) - 1) * (percentile / 100)
    lower = math.floor(index)
    upper = math.ceil(index)

    if lower == upper:
        return sorted_values[int(index)]

    lower_value = sorted_values[lower]
    upper_value = sorted_values[upper]

    return lower_value + (upper_value - lower_value) * (index - lower)


def validate_metric_name(name: str) -> None:
    if not name or not name.strip():
        raise MetricValidationError("Metric name cannot be empty")

    if not all(char.isalnum() or char in "._:" for char in name):
        raise MetricValidationError(
            "Metric name can only contain letters, numbers, dot, underscore and colon"
        )


def sanitize_prometheus_name(name: str) -> str:
    clean = []

    for char in name:
        if char.isalnum() or char == "_":
            clean.append(char)
        elif char in ".:-":
            clean.append("_")

    result = "".join(clean).strip("_")

    if result and result[0].isdigit():
        result = f"m_{result}"

    return result or "metric"


def prometheus_labels(labels: Mapping[str, str]) -> str:
    if not labels:
        return ""

    parts = [
        f'{sanitize_prometheus_name(key)}="{escape_prometheus_value(value)}"'
        for key, value in sorted(labels.items())
    ]

    return "{" + ",".join(parts) + "}"


def escape_prometheus_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def build_metrics_from_env() -> MetricsManager:
    config = MetricsConfig(
        namespace=os.getenv("METRICS_NAMESPACE", "kwanza_ai"),
        service_name=os.getenv("METRICS_SERVICE_NAME", os.getenv("APP_NAME", "kwanza-ai-core")),
        environment=os.getenv("APP_ENV", "development"),
        instance_id=os.getenv("INSTANCE_ID", "local"),
        enabled=os.getenv("METRICS_ENABLED", "true").lower() == "true",
        max_histogram_samples=int(os.getenv("METRICS_MAX_HISTOGRAM_SAMPLES", "20000")),
        export_timestamp=os.getenv("METRICS_EXPORT_TIMESTAMP", "true").lower() == "true",
    )

    return MetricsManager(config=config)


metrics = build_metrics_from_env()
"""
data/utils/performance.py

Enterprise-grade performance utilities.

Este módulo centraliza utilitários de performance para pipelines de dados,
validação, ingestão, IA, APIs internas e jobs operacionais.

Capacidades principais:
- Timers síncronos e assíncronos.
- Decorators para medir duração, throughput e latência.
- Benchmark repetível com warmup, percentis e estatísticas.
- Profiling leve por blocos nomeados.
- Medição de throughput por registros, bytes e batches.
- Snapshots de runtime com CPU time, memória RSS quando disponível e uptime.
- Comparação entre execuções/benchmarks.
- Sinks plugáveis para métricas.
- JSON-safe, thread-safe e sem dependências externas obrigatórias.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import gc
import json
import logging
import math
import os
import platform
import statistics
import threading
import time
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Deque,
    Dict,
    Generic,
    Iterable,
    Iterator,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    TypeVar,
    Union,
    cast,
)


logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")
JsonDict = Dict[str, Any]


class PerformanceStatus(str, Enum):
    """Status de uma medição/benchmark."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    DEGRADED = "DEGRADED"


class MetricUnit(str, Enum):
    """Unidades comuns de performance."""

    MILLISECONDS = "ms"
    SECONDS = "s"
    COUNT = "count"
    BYTES = "bytes"
    ROWS = "rows"
    RECORDS_PER_SECOND = "records/s"
    BYTES_PER_SECOND = "bytes/s"
    OPERATIONS_PER_SECOND = "ops/s"
    RATIO = "ratio"
    PERCENT = "percent"


class PerformanceRegressionLevel(str, Enum):
    """Classificação de regressão de performance."""

    NONE = "NONE"
    MINOR = "MINOR"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"


class PerformanceError(Exception):
    """Erro base do módulo de performance."""


class BenchmarkError(PerformanceError):
    """Erro durante benchmark."""


class MetricsSink(Protocol):
    """Contrato mínimo para publicação de métricas."""

    def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Incrementa contador."""

    def gauge(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Publica gauge."""

    def timing(self, name: str, value_ms: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Publica timing."""


@dataclass(frozen=True)
class PerformanceContext:
    """Contexto operacional de performance."""

    component: str
    operation: str
    service_name: str = "data-platform"
    environment: str = "production"
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: Optional[str] = None
    dataset_name: Optional[str] = None
    pipeline_name: Optional[str] = None
    tenant_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def tags(self, extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        tags = {
            "service": self.service_name,
            "environment": self.environment,
            "component": self.component,
            "operation": self.operation,
            "run_id": self.run_id,
            "dataset": self.dataset_name or "unknown",
            "pipeline": self.pipeline_name or "unknown",
            "tenant": self.tenant_id or "default",
        }
        if self.correlation_id:
            tags["correlation_id"] = self.correlation_id
        if extra:
            tags.update({str(k): str(v) for k, v in extra.items()})
        return tags

    def to_dict(self) -> JsonDict:
        return {
            "component": self.component,
            "operation": self.operation,
            "service_name": self.service_name,
            "environment": self.environment,
            "run_id": self.run_id,
            "correlation_id": self.correlation_id,
            "dataset_name": self.dataset_name,
            "pipeline_name": self.pipeline_name,
            "tenant_id": self.tenant_id,
            "metadata": safe_json_value(dict(self.metadata)),
        }


@dataclass(frozen=True)
class DurationMeasurement:
    """Medição de duração."""

    name: str
    duration_ms: float
    status: PerformanceStatus = PerformanceStatus.SUCCEEDED
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    context: Optional[PerformanceContext] = None
    error: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        return self.duration_ms / 1000.0

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "duration_ms": self.duration_ms,
            "duration_seconds": self.duration_seconds,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "context": self.context.to_dict() if self.context else None,
            "error": self.error,
            "metadata": safe_json_value(dict(self.metadata)),
        }


@dataclass(frozen=True)
class ThroughputMeasurement:
    """Medição de throughput."""

    name: str
    duration_ms: float
    records: Optional[int] = None
    bytes_processed: Optional[int] = None
    operations: Optional[int] = None
    batches: Optional[int] = None
    context: Optional[PerformanceContext] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        return max(self.duration_ms / 1000.0, 1e-12)

    @property
    def records_per_second(self) -> Optional[float]:
        return None if self.records is None else self.records / self.duration_seconds

    @property
    def bytes_per_second(self) -> Optional[float]:
        return None if self.bytes_processed is None else self.bytes_processed / self.duration_seconds

    @property
    def operations_per_second(self) -> Optional[float]:
        return None if self.operations is None else self.operations / self.duration_seconds

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "duration_ms": self.duration_ms,
            "duration_seconds": self.duration_seconds,
            "records": self.records,
            "bytes_processed": self.bytes_processed,
            "operations": self.operations,
            "batches": self.batches,
            "records_per_second": self.records_per_second,
            "bytes_per_second": self.bytes_per_second,
            "operations_per_second": self.operations_per_second,
            "context": self.context.to_dict() if self.context else None,
            "created_at": self.created_at.isoformat(),
            "metadata": safe_json_value(dict(self.metadata)),
        }


@dataclass(frozen=True)
class StatsSummary:
    """Resumo estatístico de valores numéricos."""

    count: int
    min: float
    max: float
    mean: float
    median: float
    stdev: float
    p50: float
    p75: float
    p90: float
    p95: float
    p99: float

    def to_dict(self) -> JsonDict:
        return {
            "count": self.count,
            "min": self.min,
            "max": self.max,
            "mean": self.mean,
            "median": self.median,
            "stdev": self.stdev,
            "p50": self.p50,
            "p75": self.p75,
            "p90": self.p90,
            "p95": self.p95,
            "p99": self.p99,
        }


@dataclass(frozen=True)
class BenchmarkResult(Generic[T]):
    """Resultado estruturado de benchmark."""

    name: str
    status: PerformanceStatus
    iterations: int
    warmup_iterations: int
    durations_ms: Tuple[float, ...]
    stats: StatsSummary
    result_sample: Optional[T] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    context: Optional[PerformanceContext] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def total_duration_ms(self) -> float:
        return sum(self.durations_ms)

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "status": self.status.value,
            "iterations": self.iterations,
            "warmup_iterations": self.warmup_iterations,
            "durations_ms": list(self.durations_ms),
            "stats": self.stats.to_dict(),
            "result_sample": safe_json_value(self.result_sample),
            "error": self.error,
            "error_type": self.error_type,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "total_duration_ms": self.total_duration_ms,
            "context": self.context.to_dict() if self.context else None,
            "metadata": safe_json_value(dict(self.metadata)),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class RuntimePerformanceSnapshot:
    """Snapshot leve de performance do processo."""

    captured_at: datetime
    uptime_seconds: float
    process_time_seconds: float
    perf_counter_seconds: float
    thread_count: int
    gc_counts: Tuple[int, int, int]
    memory_rss_bytes: Optional[int]
    load_average: Optional[Tuple[float, float, float]]
    python_version: str
    platform: str
    pid: int
    cwd: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "captured_at": self.captured_at.isoformat(),
            "uptime_seconds": self.uptime_seconds,
            "process_time_seconds": self.process_time_seconds,
            "perf_counter_seconds": self.perf_counter_seconds,
            "thread_count": self.thread_count,
            "gc_counts": list(self.gc_counts),
            "memory_rss_bytes": self.memory_rss_bytes,
            "load_average": list(self.load_average) if self.load_average else None,
            "python_version": self.python_version,
            "platform": self.platform,
            "pid": self.pid,
            "cwd": self.cwd,
            "metadata": safe_json_value(dict(self.metadata)),
        }


@dataclass(frozen=True)
class PerformanceComparison:
    """Comparação entre benchmark base e atual."""

    baseline_name: str
    current_name: str
    baseline_mean_ms: float
    current_mean_ms: float
    delta_ms: float
    delta_ratio: float
    regression_level: PerformanceRegressionLevel
    improved: bool
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "baseline_name": self.baseline_name,
            "current_name": self.current_name,
            "baseline_mean_ms": self.baseline_mean_ms,
            "current_mean_ms": self.current_mean_ms,
            "delta_ms": self.delta_ms,
            "delta_ratio": self.delta_ratio,
            "delta_percent": self.delta_ratio * 100.0,
            "regression_level": self.regression_level.value,
            "improved": self.improved,
            "metadata": safe_json_value(dict(self.metadata)),
        }


class PerformanceTimer:
    """Context manager síncrono para medir duração."""

    def __init__(
        self,
        name: str,
        *,
        context: Optional[PerformanceContext] = None,
        metrics_sink: Optional[MetricsSink] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        publish_metric_name: str = "performance.duration_ms",
    ) -> None:
        self.name = name
        self.context = context
        self.metrics_sink = metrics_sink
        self.metadata = dict(metadata or {})
        self.publish_metric_name = publish_metric_name
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self._start_perf: Optional[float] = None
        self.measurement: Optional[DurationMeasurement] = None

    def __enter__(self) -> "PerformanceTimer":
        self.started_at = datetime.now(timezone.utc)
        self._start_perf = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.finished_at = datetime.now(timezone.utc)
        duration_ms = self.elapsed_ms
        status = PerformanceStatus.FAILED if exc else PerformanceStatus.SUCCEEDED
        self.measurement = DurationMeasurement(
            name=self.name,
            duration_ms=duration_ms,
            status=status,
            started_at=self.started_at or self.finished_at,
            finished_at=self.finished_at,
            context=self.context,
            error=str(exc) if exc else None,
            metadata=self.metadata,
        )
        if self.metrics_sink:
            tags = self.context.tags({"timer": self.name, "status": status.value}) if self.context else {"timer": self.name, "status": status.value}
            self.metrics_sink.timing(self.publish_metric_name, duration_ms, tags=tags)
            self.metrics_sink.increment("performance.timer.executed", tags=tags)

    @property
    def elapsed_ms(self) -> float:
        if self._start_perf is None:
            return 0.0
        return (time.perf_counter() - self._start_perf) * 1000.0


class AsyncPerformanceTimer:
    """Context manager assíncrono para medir duração."""

    def __init__(self, name: str, *, context: Optional[PerformanceContext] = None, metrics_sink: Optional[MetricsSink] = None) -> None:
        self._timer = PerformanceTimer(name, context=context, metrics_sink=metrics_sink)

    async def __aenter__(self) -> "AsyncPerformanceTimer":
        self._timer.__enter__()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._timer.__exit__(exc_type, exc, tb)

    @property
    def measurement(self) -> Optional[DurationMeasurement]:
        return self._timer.measurement

    @property
    def elapsed_ms(self) -> float:
        return self._timer.elapsed_ms


class PerformanceProfiler:
    """Profiler leve por blocos nomeados, thread-safe."""

    def __init__(self, *, context: Optional[PerformanceContext] = None, max_measurements: int = 100_000) -> None:
        self.context = context
        self.max_measurements = max_measurements
        self._measurements: Deque[DurationMeasurement] = deque(maxlen=max_measurements)
        self._lock = threading.RLock()

    @contextlib.contextmanager
    def measure(self, name: str, **metadata: Any) -> Iterator[PerformanceTimer]:
        timer = PerformanceTimer(name, context=self.context, metadata=metadata)
        with timer:
            yield timer
        if timer.measurement:
            self.add(timer.measurement)

    def add(self, measurement: DurationMeasurement) -> None:
        with self._lock:
            self._measurements.append(measurement)

    def measurements(self) -> Tuple[DurationMeasurement, ...]:
        with self._lock:
            return tuple(self._measurements)

    def summary(self) -> Mapping[str, Any]:
        values_by_name: Dict[str, List[float]] = defaultdict(list)
        status_counts: Counter[str] = Counter()
        for measurement in self.measurements():
            values_by_name[measurement.name].append(measurement.duration_ms)
            status_counts[measurement.status.value] += 1
        return {
            "total_measurements": sum(len(v) for v in values_by_name.values()),
            "status_counts": dict(status_counts),
            "by_name": {name: summarize(values).to_dict() for name, values in values_by_name.items()},
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(safe_json_value(self.summary()), ensure_ascii=False, indent=indent, default=str)

    def clear(self) -> None:
        with self._lock:
            self._measurements.clear()


class ThroughputTracker:
    """Rastreador de throughput incremental."""

    def __init__(self, name: str, *, context: Optional[PerformanceContext] = None) -> None:
        self.name = name
        self.context = context
        self.started_at = datetime.now(timezone.utc)
        self._start_perf = time.perf_counter()
        self.records = 0
        self.bytes_processed = 0
        self.operations = 0
        self.batches = 0
        self._lock = threading.RLock()

    def add(self, *, records: int = 0, bytes_processed: int = 0, operations: int = 0, batches: int = 0) -> None:
        with self._lock:
            self.records += records
            self.bytes_processed += bytes_processed
            self.operations += operations
            self.batches += batches

    def snapshot(self) -> ThroughputMeasurement:
        with self._lock:
            return ThroughputMeasurement(
                name=self.name,
                duration_ms=(time.perf_counter() - self._start_perf) * 1000.0,
                records=self.records,
                bytes_processed=self.bytes_processed,
                operations=self.operations,
                batches=self.batches,
                context=self.context,
            )


class InMemoryMetricsSink:
    """Sink simples em memória compatível com MetricsSink."""

    def __init__(self) -> None:
        self.counters: MutableMapping[str, int] = defaultdict(int)
        self.gauges: MutableMapping[str, float] = {}
        self.timings: MutableMapping[str, List[float]] = defaultdict(list)
        self._lock = threading.RLock()

    def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        with self._lock:
            self.counters[metric_key(name, tags)] += value

    def gauge(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        with self._lock:
            self.gauges[metric_key(name, tags)] = float(value)

    def timing(self, name: str, value_ms: float, tags: Optional[Mapping[str, str]] = None) -> None:
        with self._lock:
            self.timings[metric_key(name, tags)].append(float(value_ms))


# =============================================================================
# Benchmark and decorators
# =============================================================================

def benchmark(
    func: Callable[..., T],
    *args: Any,
    name: Optional[str] = None,
    iterations: int = 10,
    warmup_iterations: int = 1,
    collect_gc: bool = False,
    context: Optional[PerformanceContext] = None,
    **kwargs: Any,
) -> BenchmarkResult[T]:
    """Executa benchmark repetível de função síncrona."""
    if iterations <= 0:
        raise BenchmarkError("iterations must be positive")
    if warmup_iterations < 0:
        raise BenchmarkError("warmup_iterations must be >= 0")
    bench_name = name or getattr(func, "__qualname__", "benchmark")
    started = datetime.now(timezone.utc)
    result_sample: Optional[T] = None

    try:
        for _ in range(warmup_iterations):
            func(*args, **kwargs)
        durations: List[float] = []
        for index in range(iterations):
            if collect_gc:
                gc.collect()
            start = time.perf_counter()
            result_sample = func(*args, **kwargs)
            durations.append((time.perf_counter() - start) * 1000.0)
        return BenchmarkResult(
            name=bench_name,
            status=PerformanceStatus.SUCCEEDED,
            iterations=iterations,
            warmup_iterations=warmup_iterations,
            durations_ms=tuple(durations),
            stats=summarize(durations),
            result_sample=result_sample,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            context=context,
        )
    except Exception as exc:
        return BenchmarkResult(
            name=bench_name,
            status=PerformanceStatus.FAILED,
            iterations=iterations,
            warmup_iterations=warmup_iterations,
            durations_ms=tuple(),
            stats=summarize([]),
            error=str(exc),
            error_type=type(exc).__name__,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            context=context,
        )


async def benchmark_async(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    name: Optional[str] = None,
    iterations: int = 10,
    warmup_iterations: int = 1,
    collect_gc: bool = False,
    context: Optional[PerformanceContext] = None,
    **kwargs: Any,
) -> BenchmarkResult[T]:
    """Executa benchmark repetível de função assíncrona."""
    if iterations <= 0:
        raise BenchmarkError("iterations must be positive")
    bench_name = name or getattr(func, "__qualname__", "benchmark_async")
    started = datetime.now(timezone.utc)
    result_sample: Optional[T] = None
    try:
        for _ in range(warmup_iterations):
            await func(*args, **kwargs)
        durations: List[float] = []
        for _ in range(iterations):
            if collect_gc:
                gc.collect()
            start = time.perf_counter()
            result_sample = await func(*args, **kwargs)
            durations.append((time.perf_counter() - start) * 1000.0)
        return BenchmarkResult(
            name=bench_name,
            status=PerformanceStatus.SUCCEEDED,
            iterations=iterations,
            warmup_iterations=warmup_iterations,
            durations_ms=tuple(durations),
            stats=summarize(durations),
            result_sample=result_sample,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            context=context,
        )
    except Exception as exc:
        return BenchmarkResult(
            name=bench_name,
            status=PerformanceStatus.FAILED,
            iterations=iterations,
            warmup_iterations=warmup_iterations,
            durations_ms=tuple(),
            stats=summarize([]),
            error=str(exc),
            error_type=type(exc).__name__,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            context=context,
        )


def measure_performance(
    *,
    name: Optional[str] = None,
    context: Optional[PerformanceContext] = None,
    metrics_sink: Optional[MetricsSink] = None,
    metric_name: str = "performance.function.duration_ms",
    log: bool = False,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator sync/async para medir duração de função."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        is_async = asyncio.iscoroutinefunction(func)
        timer_name = name or getattr(func, "__qualname__", "function")

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            async with AsyncPerformanceTimer(timer_name, context=context, metrics_sink=metrics_sink) as timer:
                try:
                    result = await cast(Callable[..., Awaitable[Any]], func)(*args, **kwargs)
                    return result
                finally:
                    if log:
                        logger.info("performance function=%s duration_ms=%.2f", timer_name, timer.elapsed_ms)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with PerformanceTimer(timer_name, context=context, metrics_sink=metrics_sink, publish_metric_name=metric_name) as timer:
                try:
                    return func(*args, **kwargs)
                finally:
                    if log:
                        logger.info("performance function=%s duration_ms=%.2f", timer_name, timer.elapsed_ms)

        return cast(Callable[..., T], async_wrapper if is_async else sync_wrapper)

    return decorator


def track_throughput(
    *,
    name: Optional[str] = None,
    records_arg: Optional[str] = None,
    bytes_arg: Optional[str] = None,
    metrics_sink: Optional[MetricsSink] = None,
    context: Optional[PerformanceContext] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator para estimar throughput a partir de argumentos informados."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        is_async = asyncio.iscoroutinefunction(func)
        op_name = name or getattr(func, "__qualname__", "operation")

        def extract_count(arg_name: Optional[str], kwargs: Mapping[str, Any], result: Any = None) -> Optional[int]:
            if arg_name is None:
                return None
            if arg_name == "result":
                try:
                    return len(result)
                except Exception:
                    return None
            value = kwargs.get(arg_name)
            if value is None:
                return None
            try:
                return int(value)
            except Exception:
                try:
                    return len(value)
                except Exception:
                    return None

        def publish(measurement: ThroughputMeasurement) -> None:
            if not metrics_sink:
                return
            tags = context.tags({"operation_name": op_name}) if context else {"operation_name": op_name}
            metrics_sink.timing("performance.throughput.duration_ms", measurement.duration_ms, tags=tags)
            if measurement.records_per_second is not None:
                metrics_sink.gauge("performance.throughput.records_per_second", measurement.records_per_second, tags=tags)
            if measurement.bytes_per_second is not None:
                metrics_sink.gauge("performance.throughput.bytes_per_second", measurement.bytes_per_second, tags=tags)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            result = await cast(Callable[..., Awaitable[Any]], func)(*args, **kwargs)
            duration = (time.perf_counter() - start) * 1000.0
            measurement = ThroughputMeasurement(
                name=op_name,
                duration_ms=duration,
                records=extract_count(records_arg, kwargs, result),
                bytes_processed=extract_count(bytes_arg, kwargs, result),
                operations=1,
                context=context,
            )
            publish(measurement)
            return result

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            result = func(*args, **kwargs)
            duration = (time.perf_counter() - start) * 1000.0
            measurement = ThroughputMeasurement(
                name=op_name,
                duration_ms=duration,
                records=extract_count(records_arg, kwargs, result),
                bytes_processed=extract_count(bytes_arg, kwargs, result),
                operations=1,
                context=context,
            )
            publish(measurement)
            return result

        return cast(Callable[..., T], async_wrapper if is_async else sync_wrapper)

    return decorator


# =============================================================================
# Utility functions
# =============================================================================

def summarize(values: Sequence[float]) -> StatsSummary:
    """Gera resumo estatístico com percentis."""
    if not values:
        return StatsSummary(count=0, min=0.0, max=0.0, mean=0.0, median=0.0, stdev=0.0, p50=0.0, p75=0.0, p90=0.0, p95=0.0, p99=0.0)
    ordered = sorted(float(v) for v in values)
    return StatsSummary(
        count=len(ordered),
        min=min(ordered),
        max=max(ordered),
        mean=statistics.mean(ordered),
        median=statistics.median(ordered),
        stdev=statistics.stdev(ordered) if len(ordered) > 1 else 0.0,
        p50=percentile(ordered, 0.50),
        p75=percentile(ordered, 0.75),
        p90=percentile(ordered, 0.90),
        p95=percentile(ordered, 0.95),
        p99=percentile(ordered, 0.99),
    )


def percentile(values: Sequence[float], q: float) -> float:
    """Calcula percentil por interpolação linear."""
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lower = int(pos)
    upper = min(lower + 1, len(ordered) - 1)
    weight = pos - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def compare_benchmarks(
    baseline: BenchmarkResult[Any],
    current: BenchmarkResult[Any],
    *,
    minor_threshold: float = 0.05,
    moderate_threshold: float = 0.15,
    severe_threshold: float = 0.30,
) -> PerformanceComparison:
    """Compara benchmark atual contra baseline pelo mean_ms."""
    baseline_mean = baseline.stats.mean
    current_mean = current.stats.mean
    delta_ms = current_mean - baseline_mean
    delta_ratio = 0.0 if baseline_mean == 0 else delta_ms / baseline_mean
    if delta_ratio <= minor_threshold:
        level = PerformanceRegressionLevel.NONE
    elif delta_ratio <= moderate_threshold:
        level = PerformanceRegressionLevel.MINOR
    elif delta_ratio <= severe_threshold:
        level = PerformanceRegressionLevel.MODERATE
    else:
        level = PerformanceRegressionLevel.SEVERE
    return PerformanceComparison(
        baseline_name=baseline.name,
        current_name=current.name,
        baseline_mean_ms=baseline_mean,
        current_mean_ms=current_mean,
        delta_ms=delta_ms,
        delta_ratio=delta_ratio,
        regression_level=level,
        improved=delta_ms < 0,
    )


def runtime_snapshot(started_monotonic: Optional[float] = None, *, metadata: Optional[Mapping[str, Any]] = None) -> RuntimePerformanceSnapshot:
    """Captura snapshot leve do runtime."""
    uptime = time.monotonic() - started_monotonic if started_monotonic is not None else 0.0
    return RuntimePerformanceSnapshot(
        captured_at=datetime.now(timezone.utc),
        uptime_seconds=uptime,
        process_time_seconds=time.process_time(),
        perf_counter_seconds=time.perf_counter(),
        thread_count=threading.active_count(),
        gc_counts=cast(Tuple[int, int, int], gc.get_count()),
        memory_rss_bytes=get_memory_rss_bytes(),
        load_average=get_load_average(),
        python_version=platform.python_version(),
        platform=platform.platform(),
        pid=os.getpid(),
        cwd=str(Path.cwd()),
        metadata=metadata or {},
    )


def get_memory_rss_bytes() -> Optional[int]:
    """Obtém memória RSS aproximada do processo quando disponível."""
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        value = int(usage.ru_maxrss)
        if platform.system().lower() == "darwin":
            return value
        return value * 1024
    except Exception:
        return None


def get_load_average() -> Optional[Tuple[float, float, float]]:
    """Obtém load average quando disponível."""
    try:
        return cast(Tuple[float, float, float], tuple(float(v) for v in os.getloadavg()))
    except Exception:
        return None


def metric_key(name: str, tags: Optional[Mapping[str, str]] = None) -> str:
    if not tags:
        return name
    tag_text = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
    return f"{name}|{tag_text}"


def safe_json_value(value: Any) -> Any:
    """Converte valores arbitrários para JSON-safe."""
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


__all__ = [
    "AsyncPerformanceTimer",
    "BenchmarkError",
    "BenchmarkResult",
    "DurationMeasurement",
    "InMemoryMetricsSink",
    "MetricUnit",
    "MetricsSink",
    "PerformanceComparison",
    "PerformanceContext",
    "PerformanceError",
    "PerformanceProfiler",
    "PerformanceRegressionLevel",
    "PerformanceStatus",
    "PerformanceTimer",
    "RuntimePerformanceSnapshot",
    "StatsSummary",
    "ThroughputMeasurement",
    "ThroughputTracker",
    "benchmark",
    "benchmark_async",
    "compare_benchmarks",
    "get_load_average",
    "get_memory_rss_bytes",
    "measure_performance",
    "metric_key",
    "percentile",
    "runtime_snapshot",
    "safe_json_value",
    "summarize",
    "track_throughput",
]

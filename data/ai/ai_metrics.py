"""
data/ai/ai_metrics.py

Módulo enterprise de métricas para IA/ML/LLM.

Objetivos:
- Coletar, agregar e exportar métricas operacionais e de qualidade para IA.
- Medir latência, throughput, tokens, custo, erros, qualidade, drift e fairness.
- Oferecer sinks plugáveis: JSONL, logging, memória, callback e Prometheus textfile.
- Suportar métricas por modelo, provider, task, tenant, usuário e pipeline.
- Fornecer janelas agregadas, percentis e snapshots.
- Integrar facilmente com pipelines, auditoria e governança.

Dependências recomendadas:
    pip install pydantic

Exemplo rápido:
    metrics = AIMetricsCollector.from_env()
    metrics.record_inference(
        model_name="enterprise-llm",
        provider="custom",
        latency_ms=250,
        input_tokens=100,
        output_tokens=50,
        success=True,
    )
    print(metrics.snapshot())
"""

from __future__ import annotations

import json
import logging
import math
import os
import socket
import statistics
import sys
import threading
import time
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple, Union

try:
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Dependência ausente: instale com `pip install pydantic`.") from exc


# =============================================================================
# Logging
# =============================================================================

LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(name)s | "
    "%(message)s | service=%(service)s host=%(host)s"
)


class ContextFilter(logging.Filter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name
        self.host = socket.gethostname()

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self.service_name
        record.host = self.host
        return True


def build_logger(name: str = "data.ai.ai_metrics") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ContextFilter(service_name=os.getenv("SERVICE_NAME", "ai-metrics")))

    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = build_logger()


# =============================================================================
# Enums
# =============================================================================


class MetricType(str, Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    TIMER = "timer"
    QUALITY = "quality"
    DRIFT = "drift"
    FAIRNESS = "fairness"
    COST = "cost"


class AIMetricEventType(str, Enum):
    INFERENCE = "inference"
    EMBEDDING = "embedding"
    RERANKING = "reranking"
    EVALUATION = "evaluation"
    TRAINING = "training"
    GUARDRAIL = "guardrail"
    GOVERNANCE = "governance"
    CUSTOM = "custom"


class MetricSinkType(str, Enum):
    JSONL = "jsonl"
    LOGGING = "logging"
    MEMORY = "memory"
    CALLBACK = "callback"
    PROMETHEUS_TEXTFILE = "prometheus_textfile"


class AggregationWindow(str, Enum):
    GLOBAL = "global"
    ROLLING = "rolling"


class ErrorCategory(str, Enum):
    NONE = "none"
    PROVIDER = "provider"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    VALIDATION = "validation"
    POLICY = "policy"
    INFRASTRUCTURE = "infrastructure"
    UNKNOWN = "unknown"


# =============================================================================
# Models
# =============================================================================


class MetricLabels(BaseModel):
    model_name: Optional[str] = None
    provider: Optional[str] = None
    model_version: Optional[str] = None
    task_type: Optional[str] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    pipeline_name: Optional[str] = None
    environment: str = Field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    service_name: str = Field(default_factory=lambda: os.getenv("SERVICE_NAME", "ai-metrics"))
    extra: Dict[str, Any] = Field(default_factory=dict)

    def key(self) -> str:
        payload = model_to_dict(self)
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=json_default)


class AIMetricEvent(BaseModel):
    metric_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: AIMetricEventType = AIMetricEventType.CUSTOM
    metric_type: MetricType = MetricType.COUNTER
    name: str
    value: float = 1.0
    unit: Optional[str] = None
    labels: MetricLabels = Field(default_factory=MetricLabels)
    timestamp: str = Field(default_factory=lambda: utc_now_iso())
    correlation_id: Optional[str] = None
    trace_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InferenceMetricsEvent(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    model_name: str
    provider: str = "custom"
    model_version: Optional[str] = None
    task_type: str = "inference"
    success: bool = True
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    error_category: ErrorCategory = ErrorCategory.NONE
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    quality_score: Optional[float] = None
    confidence_score: Optional[float] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    pipeline_name: Optional[str] = None
    correlation_id: Optional[str] = None
    trace_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: utc_now_iso())


class EvaluationMetricsEvent(BaseModel):
    evaluation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    evaluation_name: str
    model_name: Optional[str] = None
    provider: Optional[str] = None
    dataset_name: Optional[str] = None
    sample_count: int = 0
    metrics: Dict[str, float] = Field(default_factory=dict)
    passed: Optional[bool] = None
    tenant_id: Optional[str] = None
    pipeline_name: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: utc_now_iso())


class DriftMetricsEvent(BaseModel):
    drift_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    model_name: Optional[str] = None
    provider: Optional[str] = None
    feature_name: Optional[str] = None
    drift_score: float
    threshold: Optional[float] = None
    drift_detected: bool = False
    reference_window: Optional[str] = None
    current_window: Optional[str] = None
    method: Optional[str] = None
    tenant_id: Optional[str] = None
    pipeline_name: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: utc_now_iso())


class FairnessMetricsEvent(BaseModel):
    fairness_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    model_name: Optional[str] = None
    provider: Optional[str] = None
    protected_attribute: str
    group_name: Optional[str] = None
    metric_name: str
    metric_value: float
    threshold: Optional[float] = None
    violation: bool = False
    sample_count: int = 0
    tenant_id: Optional[str] = None
    pipeline_name: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: utc_now_iso())


@dataclass(frozen=True)
class AIMetricsConfig:
    enabled: bool = True
    sink_type: MetricSinkType = MetricSinkType.JSONL
    jsonl_path: Optional[Path] = Path("data/metrics/ai_metrics.jsonl")
    prometheus_textfile_path: Optional[Path] = Path("data/metrics/ai_metrics.prom")
    rolling_window_size: int = 10_000
    export_every_event: bool = True
    fail_silently: bool = True
    default_cost_per_1k_input_tokens: float = 0.0
    default_cost_per_1k_output_tokens: float = 0.0

    @staticmethod
    def from_env() -> "AIMetricsConfig":
        jsonl_raw = os.getenv("AI_METRICS_JSONL_PATH", "data/metrics/ai_metrics.jsonl")
        prom_raw = os.getenv("AI_METRICS_PROMETHEUS_TEXTFILE_PATH", "data/metrics/ai_metrics.prom")
        return AIMetricsConfig(
            enabled=env_bool("AI_METRICS_ENABLED", True),
            sink_type=MetricSinkType(os.getenv("AI_METRICS_SINK_TYPE", MetricSinkType.JSONL.value)),
            jsonl_path=Path(jsonl_raw) if jsonl_raw else None,
            prometheus_textfile_path=Path(prom_raw) if prom_raw else None,
            rolling_window_size=int(os.getenv("AI_METRICS_ROLLING_WINDOW_SIZE", "10000")),
            export_every_event=env_bool("AI_METRICS_EXPORT_EVERY_EVENT", True),
            fail_silently=env_bool("AI_METRICS_FAIL_SILENTLY", True),
            default_cost_per_1k_input_tokens=float(os.getenv("AI_METRICS_COST_PER_1K_INPUT", "0")),
            default_cost_per_1k_output_tokens=float(os.getenv("AI_METRICS_COST_PER_1K_OUTPUT", "0")),
        )


@dataclass
class CollectorMetrics:
    events_received: int = 0
    events_written: int = 0
    events_failed: int = 0
    snapshots_created: int = 0
    bytes_written: int = 0
    last_event_at: Optional[str] = None
    total_write_seconds: float = 0.0

    def snapshot(self) -> Dict[str, Any]:
        avg_write = self.total_write_seconds / self.events_written if self.events_written else 0.0
        return {
            "events_received": self.events_received,
            "events_written": self.events_written,
            "events_failed": self.events_failed,
            "snapshots_created": self.snapshots_created,
            "bytes_written": self.bytes_written,
            "last_event_at": self.last_event_at,
            "average_write_seconds": round(avg_write, 6),
            "total_write_seconds": round(self.total_write_seconds, 6),
        }


# =============================================================================
# Protocols
# =============================================================================


class AIMetricSink(Protocol):
    def write(self, event: AIMetricEvent) -> int:
        """Persiste evento de métrica e retorna bytes escritos/aproximados."""

    def write_snapshot(self, snapshot: Mapping[str, Any]) -> int:
        """Persiste snapshot agregado."""

    def close(self) -> None:
        """Fecha recursos."""


class AIMetricObserver(Protocol):
    def on_metric(self, event: AIMetricEvent) -> None:
        """Hook chamado após métrica ser registrada."""


# =============================================================================
# Sinks
# =============================================================================


class JsonlAIMetricSink:
    def __init__(self, path: Union[str, Path], flush_every_event: bool = True) -> None:
        self.path = Path(path)
        self.flush_every_event = flush_every_event
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._handle = self.path.open("a", encoding="utf-8")

    def write(self, event: AIMetricEvent) -> int:
        line = json.dumps({"kind": "metric", "event": model_to_dict(event)}, ensure_ascii=False, default=json_default) + "\n"
        with self._lock:
            self._handle.write(line)
            if self.flush_every_event:
                self._handle.flush()
        return len(line.encode("utf-8"))

    def write_snapshot(self, snapshot: Mapping[str, Any]) -> int:
        line = json.dumps({"kind": "snapshot", "snapshot": snapshot, "timestamp": utc_now_iso()}, ensure_ascii=False, default=json_default) + "\n"
        with self._lock:
            self._handle.write(line)
            if self.flush_every_event:
                self._handle.flush()
        return len(line.encode("utf-8"))

    def close(self) -> None:
        with self._lock:
            if not self._handle.closed:
                self._handle.flush()
                self._handle.close()


class LoggingAIMetricSink:
    def write(self, event: AIMetricEvent) -> int:
        payload = json.dumps(model_to_dict(event), ensure_ascii=False, default=json_default)
        logger.info("AI_METRIC_EVENT %s", payload)
        return len(payload.encode("utf-8"))

    def write_snapshot(self, snapshot: Mapping[str, Any]) -> int:
        payload = json.dumps(snapshot, ensure_ascii=False, default=json_default)
        logger.info("AI_METRIC_SNAPSHOT %s", payload)
        return len(payload.encode("utf-8"))

    def close(self) -> None:
        return None


class MemoryAIMetricSink:
    def __init__(self) -> None:
        self.events: List[AIMetricEvent] = []
        self.snapshots: List[Mapping[str, Any]] = []
        self._lock = threading.Lock()

    def write(self, event: AIMetricEvent) -> int:
        with self._lock:
            self.events.append(event)
        payload = json.dumps(model_to_dict(event), ensure_ascii=False, default=json_default)
        return len(payload.encode("utf-8"))

    def write_snapshot(self, snapshot: Mapping[str, Any]) -> int:
        with self._lock:
            self.snapshots.append(dict(snapshot))
        payload = json.dumps(snapshot, ensure_ascii=False, default=json_default)
        return len(payload.encode("utf-8"))

    def close(self) -> None:
        return None


class CallbackAIMetricSink:
    def __init__(self, callback: Callable[[Union[AIMetricEvent, Mapping[str, Any]]], None]) -> None:
        self.callback = callback

    def write(self, event: AIMetricEvent) -> int:
        self.callback(event)
        payload = json.dumps(model_to_dict(event), ensure_ascii=False, default=json_default)
        return len(payload.encode("utf-8"))

    def write_snapshot(self, snapshot: Mapping[str, Any]) -> int:
        self.callback(snapshot)
        payload = json.dumps(snapshot, ensure_ascii=False, default=json_default)
        return len(payload.encode("utf-8"))

    def close(self) -> None:
        return None


class PrometheusTextfileMetricSink:
    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last_snapshot: Dict[str, Any] = {}

    def write(self, event: AIMetricEvent) -> int:
        # Eventos individuais não são ideais para textfile. Mantém compatibilidade sem escrita pesada.
        return 0

    def write_snapshot(self, snapshot: Mapping[str, Any]) -> int:
        self._last_snapshot = dict(snapshot)
        content = snapshot_to_prometheus(snapshot)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with self._lock:
            temp_path.write_text(content, encoding="utf-8")
            temp_path.replace(self.path)
        return len(content.encode("utf-8"))

    def close(self) -> None:
        return None


# =============================================================================
# Aggregation
# =============================================================================


@dataclass
class MetricSeries:
    count: int = 0
    total: float = 0.0
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    values: Deque[float] = field(default_factory=deque)

    def add(self, value: float, maxlen: int) -> None:
        self.count += 1
        self.total += value
        self.min_value = value if self.min_value is None else min(self.min_value, value)
        self.max_value = value if self.max_value is None else max(self.max_value, value)
        if self.values.maxlen != maxlen:
            self.values = deque(self.values, maxlen=maxlen)
        self.values.append(value)

    def snapshot(self) -> Dict[str, Any]:
        values = list(self.values)
        return {
            "count": self.count,
            "total": round(self.total, 6),
            "avg": round(self.total / self.count, 6) if self.count else 0.0,
            "min": self.min_value,
            "max": self.max_value,
            "p50": percentile(values, 50),
            "p90": percentile(values, 90),
            "p95": percentile(values, 95),
            "p99": percentile(values, 99),
        }


class AIMetricAggregator:
    def __init__(self, rolling_window_size: int = 10_000) -> None:
        self.rolling_window_size = rolling_window_size
        self.series: Dict[str, MetricSeries] = defaultdict(MetricSeries)
        self.counters: Counter[str] = Counter()
        self.errors: Counter[str] = Counter()
        self.by_model: Counter[str] = Counter()
        self.by_provider: Counter[str] = Counter()
        self.by_task: Counter[str] = Counter()
        self._lock = threading.RLock()

    def add_event(self, event: AIMetricEvent) -> None:
        with self._lock:
            key = self._series_key(event)

            if event.metric_type in {MetricType.HISTOGRAM, MetricType.TIMER, MetricType.GAUGE, MetricType.QUALITY, MetricType.DRIFT, MetricType.FAIRNESS, MetricType.COST}:
                self.series[key].add(event.value, self.rolling_window_size)
            else:
                self.counters[key] += event.value

            if event.labels.model_name:
                self.by_model[event.labels.model_name] += 1
            if event.labels.provider:
                self.by_provider[event.labels.provider] += 1
            if event.labels.task_type:
                self.by_task[event.labels.task_type] += 1

            error_category = event.metadata.get("error_category")
            if error_category and error_category != ErrorCategory.NONE.value:
                self.errors[str(error_category)] += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "created_at": utc_now_iso(),
                "series": {key: series.snapshot() for key, series in self.series.items()},
                "counters": dict(self.counters),
                "errors": dict(self.errors),
                "by_model": dict(self.by_model),
                "by_provider": dict(self.by_provider),
                "by_task": dict(self.by_task),
            }

    @staticmethod
    def _series_key(event: AIMetricEvent) -> str:
        dimensions = {
            "name": event.name,
            "event_type": event.event_type.value,
            "metric_type": event.metric_type.value,
            "unit": event.unit,
            "model_name": event.labels.model_name,
            "provider": event.labels.provider,
            "task_type": event.labels.task_type,
            "pipeline_name": event.labels.pipeline_name,
            "environment": event.labels.environment,
            "service_name": event.labels.service_name,
        }
        return json.dumps(dimensions, sort_keys=True, ensure_ascii=False, default=json_default)


# =============================================================================
# Collector principal
# =============================================================================


class AIMetricsCollector:
    def __init__(
        self,
        config: Optional[AIMetricsConfig] = None,
        sink: Optional[AIMetricSink] = None,
        observers: Optional[Sequence[AIMetricObserver]] = None,
    ) -> None:
        self.config = config or AIMetricsConfig.from_env()
        self.metrics = CollectorMetrics()
        self.aggregator = AIMetricAggregator(rolling_window_size=self.config.rolling_window_size)
        self.sink = sink or self._build_sink()
        self.observers = list(observers or [])
        self._lock = threading.RLock()

    @classmethod
    def from_env(cls) -> "AIMetricsCollector":
        return cls(config=AIMetricsConfig.from_env())

    def record(self, event: AIMetricEvent) -> Optional[AIMetricEvent]:
        if not self.config.enabled:
            return None

        started = time.perf_counter()
        self.metrics.events_received += 1

        try:
            with self._lock:
                self.aggregator.add_event(event)
                bytes_written = 0
                if self.config.export_every_event:
                    bytes_written = self.sink.write(event)
                self.metrics.events_written += 1
                self.metrics.bytes_written += bytes_written
                self.metrics.last_event_at = event.timestamp
                self.metrics.total_write_seconds += time.perf_counter() - started

            self._notify_observers(event)
            return event

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.events_failed += 1
            logger.exception("Falha ao registrar métrica de IA. error=%s", exc)
            if not self.config.fail_silently:
                raise
            return None

    def record_inference(
        self,
        model_name: str,
        provider: str = "custom",
        model_version: Optional[str] = None,
        task_type: str = "inference",
        success: bool = True,
        latency_ms: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: Optional[int] = None,
        cost_usd: Optional[float] = None,
        error_category: ErrorCategory = ErrorCategory.NONE,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        quality_score: Optional[float] = None,
        confidence_score: Optional[float] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        pipeline_name: Optional[str] = None,
        correlation_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> List[AIMetricEvent]:
        total = total_tokens if total_tokens is not None else input_tokens + output_tokens
        cost = cost_usd if cost_usd is not None else self.estimate_cost(input_tokens, output_tokens)

        base_labels = MetricLabels(
            model_name=model_name,
            provider=provider,
            model_version=model_version,
            task_type=task_type,
            tenant_id=tenant_id,
            user_id=user_id,
            pipeline_name=pipeline_name,
        )

        common_metadata = {
            "success": success,
            "error_category": error_category.value,
            "error_type": error_type,
            "error_message": error_message,
            **dict(metadata or {}),
        }

        events = [
            AIMetricEvent(
                event_type=AIMetricEventType.INFERENCE,
                metric_type=MetricType.COUNTER,
                name="ai_inference_requests_total",
                value=1,
                unit="request",
                labels=base_labels,
                correlation_id=correlation_id,
                trace_id=trace_id,
                metadata=common_metadata,
            ),
            AIMetricEvent(
                event_type=AIMetricEventType.INFERENCE,
                metric_type=MetricType.TIMER,
                name="ai_inference_latency_ms",
                value=float(latency_ms),
                unit="ms",
                labels=base_labels,
                correlation_id=correlation_id,
                trace_id=trace_id,
                metadata=common_metadata,
            ),
            AIMetricEvent(
                event_type=AIMetricEventType.INFERENCE,
                metric_type=MetricType.COST,
                name="ai_inference_cost_usd",
                value=float(cost),
                unit="usd",
                labels=base_labels,
                correlation_id=correlation_id,
                trace_id=trace_id,
                metadata=common_metadata,
            ),
            AIMetricEvent(
                event_type=AIMetricEventType.INFERENCE,
                metric_type=MetricType.COUNTER,
                name="ai_inference_tokens_total",
                value=float(total),
                unit="token",
                labels=base_labels,
                correlation_id=correlation_id,
                trace_id=trace_id,
                metadata={**common_metadata, "input_tokens": input_tokens, "output_tokens": output_tokens},
            ),
        ]

        if not success:
            events.append(
                AIMetricEvent(
                    event_type=AIMetricEventType.INFERENCE,
                    metric_type=MetricType.COUNTER,
                    name="ai_inference_errors_total",
                    value=1,
                    unit="error",
                    labels=base_labels,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    metadata=common_metadata,
                )
            )

        if quality_score is not None:
            events.append(
                AIMetricEvent(
                    event_type=AIMetricEventType.INFERENCE,
                    metric_type=MetricType.QUALITY,
                    name="ai_inference_quality_score",
                    value=float(quality_score),
                    unit="score",
                    labels=base_labels,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    metadata=common_metadata,
                )
            )

        if confidence_score is not None:
            events.append(
                AIMetricEvent(
                    event_type=AIMetricEventType.INFERENCE,
                    metric_type=MetricType.QUALITY,
                    name="ai_inference_confidence_score",
                    value=float(confidence_score),
                    unit="score",
                    labels=base_labels,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    metadata=common_metadata,
                )
            )

        for event in events:
            self.record(event)

        return events

    def record_embedding(
        self,
        model_name: str,
        provider: str = "custom",
        texts_count: int = 0,
        dimensions: Optional[int] = None,
        latency_ms: float = 0.0,
        tokens: int = 0,
        success: bool = True,
        tenant_id: Optional[str] = None,
        pipeline_name: Optional[str] = None,
        correlation_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> List[AIMetricEvent]:
        labels = MetricLabels(
            model_name=model_name,
            provider=provider,
            task_type="embedding",
            tenant_id=tenant_id,
            pipeline_name=pipeline_name,
        )
        base_metadata = {"success": success, "texts_count": texts_count, "dimensions": dimensions, **dict(metadata or {})}

        events = [
            AIMetricEvent(AIMetricEventType.EMBEDDING, MetricType.COUNTER, "ai_embedding_requests_total", 1, "request", labels, correlation_id=correlation_id, metadata=base_metadata),
            AIMetricEvent(AIMetricEventType.EMBEDDING, MetricType.TIMER, "ai_embedding_latency_ms", latency_ms, "ms", labels, correlation_id=correlation_id, metadata=base_metadata),
            AIMetricEvent(AIMetricEventType.EMBEDDING, MetricType.COUNTER, "ai_embedding_texts_total", texts_count, "text", labels, correlation_id=correlation_id, metadata=base_metadata),
            AIMetricEvent(AIMetricEventType.EMBEDDING, MetricType.COUNTER, "ai_embedding_tokens_total", tokens, "token", labels, correlation_id=correlation_id, metadata=base_metadata),
        ]
        for event in events:
            self.record(event)
        return events

    def record_evaluation(self, event: EvaluationMetricsEvent) -> List[AIMetricEvent]:
        labels = MetricLabels(
            model_name=event.model_name,
            provider=event.provider,
            task_type="evaluation",
            tenant_id=event.tenant_id,
            pipeline_name=event.pipeline_name,
        )

        emitted: List[AIMetricEvent] = [
            AIMetricEvent(
                event_type=AIMetricEventType.EVALUATION,
                metric_type=MetricType.COUNTER,
                name="ai_evaluation_runs_total",
                value=1,
                unit="run",
                labels=labels,
                correlation_id=event.correlation_id,
                metadata={"evaluation_name": event.evaluation_name, "passed": event.passed, "sample_count": event.sample_count, **event.metadata},
            )
        ]

        for name, value in event.metrics.items():
            emitted.append(
                AIMetricEvent(
                    event_type=AIMetricEventType.EVALUATION,
                    metric_type=MetricType.QUALITY,
                    name=f"ai_evaluation_{sanitize_metric_name(name)}",
                    value=float(value),
                    unit="score",
                    labels=labels,
                    correlation_id=event.correlation_id,
                    metadata={"evaluation_name": event.evaluation_name, "dataset_name": event.dataset_name},
                )
            )

        for metric_event in emitted:
            self.record(metric_event)
        return emitted

    def record_drift(self, event: DriftMetricsEvent) -> AIMetricEvent:
        labels = MetricLabels(
            model_name=event.model_name,
            provider=event.provider,
            task_type="drift",
            tenant_id=event.tenant_id,
            pipeline_name=event.pipeline_name,
            extra={"feature_name": event.feature_name},
        )
        metric = AIMetricEvent(
            event_type=AIMetricEventType.EVALUATION,
            metric_type=MetricType.DRIFT,
            name="ai_drift_score",
            value=float(event.drift_score),
            unit="score",
            labels=labels,
            metadata={
                "threshold": event.threshold,
                "drift_detected": event.drift_detected,
                "reference_window": event.reference_window,
                "current_window": event.current_window,
                "method": event.method,
                **event.metadata,
            },
        )
        self.record(metric)
        return metric

    def record_fairness(self, event: FairnessMetricsEvent) -> AIMetricEvent:
        labels = MetricLabels(
            model_name=event.model_name,
            provider=event.provider,
            task_type="fairness",
            tenant_id=event.tenant_id,
            pipeline_name=event.pipeline_name,
            extra={"protected_attribute": event.protected_attribute, "group_name": event.group_name},
        )
        metric = AIMetricEvent(
            event_type=AIMetricEventType.EVALUATION,
            metric_type=MetricType.FAIRNESS,
            name=f"ai_fairness_{sanitize_metric_name(event.metric_name)}",
            value=float(event.metric_value),
            unit="score",
            labels=labels,
            metadata={
                "threshold": event.threshold,
                "violation": event.violation,
                "sample_count": event.sample_count,
                **event.metadata,
            },
        )
        self.record(metric)
        return metric

    def snapshot(self, export: bool = False) -> Dict[str, Any]:
        snapshot = {
            "collector": self.metrics.snapshot(),
            "aggregates": self.aggregator.snapshot(),
        }
        self.metrics.snapshots_created += 1
        if export:
            try:
                written = self.sink.write_snapshot(snapshot)
                self.metrics.bytes_written += written
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.metrics.events_failed += 1
                logger.exception("Falha ao exportar snapshot de métricas. error=%s", exc)
                if not self.config.fail_silently:
                    raise
        return snapshot

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1000.0 * self.config.default_cost_per_1k_input_tokens
            + output_tokens / 1000.0 * self.config.default_cost_per_1k_output_tokens
        )

    def close(self) -> None:
        self.sink.close()
        logger.info("AI metrics collector encerrado. metrics=%s", json.dumps(self.metrics.snapshot()))

    def _build_sink(self) -> AIMetricSink:
        if self.config.sink_type == MetricSinkType.LOGGING:
            return LoggingAIMetricSink()
        if self.config.sink_type == MetricSinkType.MEMORY:
            return MemoryAIMetricSink()
        if self.config.sink_type == MetricSinkType.PROMETHEUS_TEXTFILE:
            if not self.config.prometheus_textfile_path:
                raise ValueError("prometheus_textfile_path é obrigatório para sink Prometheus textfile.")
            return PrometheusTextfileMetricSink(self.config.prometheus_textfile_path)
        if self.config.sink_type == MetricSinkType.JSONL:
            if not self.config.jsonl_path:
                raise ValueError("jsonl_path é obrigatório para sink JSONL.")
            return JsonlAIMetricSink(self.config.jsonl_path, flush_every_event=self.config.export_every_event)
        raise ValueError("Sink CALLBACK exige passar sink explicitamente no construtor.")

    def _notify_observers(self, event: AIMetricEvent) -> None:
        for observer in self.observers:
            try:
                observer.on_metric(event)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Observer de métrica falhou. error=%s", exc)

    def __enter__(self) -> "AIMetricsCollector":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


# =============================================================================
# Timer helper
# =============================================================================


class InferenceTimer:
    def __init__(
        self,
        collector: AIMetricsCollector,
        model_name: str,
        provider: str = "custom",
        task_type: str = "inference",
        input_tokens: int = 0,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        pipeline_name: Optional[str] = None,
        correlation_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.collector = collector
        self.model_name = model_name
        self.provider = provider
        self.task_type = task_type
        self.input_tokens = input_tokens
        self.output_tokens = 0
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.pipeline_name = pipeline_name
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self.metadata = dict(metadata or {})
        self.started = 0.0

    def __enter__(self) -> "InferenceTimer":
        self.started = time.perf_counter()
        return self

    def set_output_tokens(self, value: int) -> None:
        self.output_tokens = value

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        latency_ms = (time.perf_counter() - self.started) * 1000
        self.collector.record_inference(
            model_name=self.model_name,
            provider=self.provider,
            task_type=self.task_type,
            success=exc is None,
            latency_ms=latency_ms,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            error_category=classify_exception(exc) if exc else ErrorCategory.NONE,
            error_type=exc.__class__.__name__ if exc else None,
            error_message=str(exc) if exc else None,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            pipeline_name=self.pipeline_name,
            correlation_id=self.correlation_id,
            metadata=self.metadata,
        )


# =============================================================================
# Utilitários
# =============================================================================


def classify_exception(exc: Optional[BaseException]) -> ErrorCategory:
    if exc is None:
        return ErrorCategory.NONE
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()

    if "timeout" in name or "timeout" in message:
        return ErrorCategory.TIMEOUT
    if "rate" in name or "429" in message or "limit" in message:
        return ErrorCategory.RATE_LIMIT
    if "validation" in name or "schema" in message:
        return ErrorCategory.VALIDATION
    if "policy" in name or "governance" in message:
        return ErrorCategory.POLICY
    if "connection" in name or "network" in message or "socket" in message:
        return ErrorCategory.INFRASTRUCTURE
    if "provider" in name or "api" in name:
        return ErrorCategory.PROVIDER
    return ErrorCategory.UNKNOWN


def percentile(values: Sequence[float], p: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    k = (len(ordered) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(ordered[int(k)], 6)
    d0 = ordered[f] * (c - k)
    d1 = ordered[c] * (k - f)
    return round(d0 + d1, 6)


def sanitize_metric_name(name: str) -> str:
    value = name.strip().lower()
    value = re_sub_non_alnum(value)
    return value.strip("_") or "metric"


def re_sub_non_alnum(value: str) -> str:
    import re

    value = re.sub(r"[^a-z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value


def snapshot_to_prometheus(snapshot: Mapping[str, Any]) -> str:
    lines: List[str] = []
    aggregates = snapshot.get("aggregates", {}) if isinstance(snapshot, Mapping) else {}
    counters = aggregates.get("counters", {}) if isinstance(aggregates, Mapping) else {}
    series = aggregates.get("series", {}) if isinstance(aggregates, Mapping) else {}

    lines.append("# HELP ai_metrics_counter_total AI metric counters")
    lines.append("# TYPE ai_metrics_counter_total counter")
    for key, value in counters.items():
        safe_key = prometheus_label_value(key)
        lines.append(f'ai_metrics_counter_total{{series="{safe_key}"}} {float(value)}')

    lines.append("# HELP ai_metrics_series_value AI metric aggregated series")
    lines.append("# TYPE ai_metrics_series_value gauge")
    for key, stats in series.items():
        safe_key = prometheus_label_value(key)
        if not isinstance(stats, Mapping):
            continue
        for stat_name in ["count", "total", "avg", "min", "max", "p50", "p90", "p95", "p99"]:
            value = stats.get(stat_name)
            if value is None:
                continue
            lines.append(f'ai_metrics_series_value{{series="{safe_key}",stat="{stat_name}"}} {float(value)}')

    lines.append("")
    return "\n".join(lines)


def prometheus_label_value(value: Any) -> str:
    text = str(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return text[:1000]


def model_to_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()  # type: ignore[no-any-return]
    return model.dict()  # type: ignore[no-any-return]


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, Path, Enum)):
        return str(value)
    return str(value)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "sim", "s"}


# =============================================================================
# Bootstrap CLI simples
# =============================================================================


def main() -> None:
    collector = AIMetricsCollector.from_env()
    collector.record_inference(
        model_name=os.getenv("AI_METRICS_EXAMPLE_MODEL", "example-model"),
        provider=os.getenv("AI_METRICS_EXAMPLE_PROVIDER", "custom"),
        latency_ms=245.5,
        input_tokens=120,
        output_tokens=80,
        success=True,
        quality_score=0.92,
        confidence_score=0.88,
        metadata={"example": True},
    )
    snapshot = collector.snapshot(export=True)
    logger.info("AI metrics snapshot: %s", json.dumps(snapshot, ensure_ascii=False, default=json_default))
    collector.close()


if __name__ == "__main__":
    main()

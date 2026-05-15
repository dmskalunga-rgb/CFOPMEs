#!/usr/bin/env python3
"""
data/ingestion/ingestion_metrics.py

Enterprise-grade ingestion metrics module.

Objetivo:
- Centralizar métricas operacionais da camada de ingestão.
- Medir volume, throughput, latência, falhas, skips, retries, dead-letter e status por fonte/tenant/pipeline.
- Gerar snapshots em JSON e exposição simples no formato Prometheus text exposition.
- Funcionar sem dependências externas obrigatórias.

Uso:
    from data.ingestion.ingestion_metrics import get_ingestion_metrics

    metrics = get_ingestion_metrics()
    metrics.record_batch(source="api", tenant_id="t1", accepted=100, processed=98, failed=2, latency_ms=230)
    print(metrics.snapshot().to_dict())
    print(metrics.to_prometheus())
"""

from __future__ import annotations

import json
import math
import statistics
import threading
import time
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Deque, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


METRICS_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc
DEFAULT_LATENCY_WINDOW = 10_000


class MetricStatus(str, Enum):
    ACCEPTED = "accepted"
    PROCESSED = "processed"
    SKIPPED = "skipped"
    FAILED = "failed"
    PARTIAL = "partial"
    RETRIED = "retried"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class MetricKey:
    source: str = "unknown"
    tenant_id: str = "global"
    pipeline: str = "default"
    mode: str = "unknown"

    def labels(self) -> Dict[str, str]:
        return {
            "source": safe_label(self.source),
            "tenant_id": safe_label(self.tenant_id),
            "pipeline": safe_label(self.pipeline),
            "mode": safe_label(self.mode),
        }

    def as_tuple(self) -> Tuple[str, str, str, str]:
        return (self.source, self.tenant_id, self.pipeline, self.mode)


@dataclass
class MetricBucket:
    key: MetricKey
    accepted: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    retried: int = 0
    dead_letter: int = 0
    batches: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    first_seen_at: str = field(default_factory=lambda: utc_now_iso())
    last_seen_at: str = field(default_factory=lambda: utc_now_iso())
    latencies_ms: Deque[float] = field(default_factory=lambda: deque(maxlen=DEFAULT_LATENCY_WINDOW))
    errors: Counter = field(default_factory=Counter)
    statuses: Counter = field(default_factory=Counter)

    def record(
        self,
        accepted: int = 0,
        processed: int = 0,
        skipped: int = 0,
        failed: int = 0,
        retried: int = 0,
        dead_letter: int = 0,
        latency_ms: Optional[float] = None,
        bytes_in: int = 0,
        bytes_out: int = 0,
        status: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        self.accepted += max(accepted, 0)
        self.processed += max(processed, 0)
        self.skipped += max(skipped, 0)
        self.failed += max(failed, 0)
        self.retried += max(retried, 0)
        self.dead_letter += max(dead_letter, 0)
        self.bytes_in += max(bytes_in, 0)
        self.bytes_out += max(bytes_out, 0)
        self.batches += 1
        self.last_seen_at = utc_now_iso()
        if latency_ms is not None:
            self.latencies_ms.append(float(latency_ms))
        if status:
            self.statuses[status] += 1
        if error:
            self.errors[normalize_error(error)] += 1

    @property
    def success_rate(self) -> float:
        total = self.processed + self.failed
        return 1.0 if total == 0 else self.processed / total

    @property
    def failure_rate(self) -> float:
        total = self.processed + self.failed
        return 0.0 if total == 0 else self.failed / total

    @property
    def avg_latency_ms(self) -> float:
        return round(statistics.mean(self.latencies_ms), 4) if self.latencies_ms else 0.0

    @property
    def p95_latency_ms(self) -> float:
        return percentile(self.latencies_ms, 95)

    @property
    def p99_latency_ms(self) -> float:
        return percentile(self.latencies_ms, 99)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "labels": self.key.labels(),
            "accepted": self.accepted,
            "processed": self.processed,
            "skipped": self.skipped,
            "failed": self.failed,
            "retried": self.retried,
            "dead_letter": self.dead_letter,
            "batches": self.batches,
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
            "success_rate": round(self.success_rate, 6),
            "failure_rate": round(self.failure_rate, 6),
            "avg_latency_ms": self.avg_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "p99_latency_ms": self.p99_latency_ms,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "statuses": dict(self.statuses),
            "top_errors": [{"error": key, "count": value} for key, value in self.errors.most_common(20)],
        }


@dataclass(frozen=True)
class IngestionMetricsSnapshot:
    snapshot_id: str
    version: str
    created_at: str
    uptime_seconds: float
    totals: Dict[str, Any]
    buckets: List[Dict[str, Any]]
    top_errors: List[Dict[str, Any]]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "version": self.version,
            "created_at": self.created_at,
            "uptime_seconds": self.uptime_seconds,
            "totals": self.totals,
            "buckets": self.buckets,
            "top_errors": self.top_errors,
            "metadata": self.metadata,
        }

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


class IngestionMetrics:
    def __init__(self, latency_window: int = DEFAULT_LATENCY_WINDOW) -> None:
        self.latency_window = latency_window
        self.started_at = time.monotonic()
        self.created_at = utc_now_iso()
        self._lock = threading.RLock()
        self._buckets: Dict[Tuple[str, str, str, str], MetricBucket] = {}
        self._global_errors: Counter = Counter()
        self._events: Deque[Dict[str, Any]] = deque(maxlen=10_000)

    def key(self, source: str = "unknown", tenant_id: Optional[str] = None, pipeline: str = "default", mode: str = "unknown") -> MetricKey:
        return MetricKey(source=source or "unknown", tenant_id=tenant_id or "global", pipeline=pipeline or "default", mode=mode or "unknown")

    def bucket(self, key: MetricKey) -> MetricBucket:
        bucket_key = key.as_tuple()
        if bucket_key not in self._buckets:
            self._buckets[bucket_key] = MetricBucket(key=key, latencies_ms=deque(maxlen=self.latency_window))
        return self._buckets[bucket_key]

    def record_batch(
        self,
        source: str = "unknown",
        tenant_id: Optional[str] = None,
        pipeline: str = "default",
        mode: str = "unknown",
        accepted: int = 0,
        processed: int = 0,
        skipped: int = 0,
        failed: int = 0,
        retried: int = 0,
        dead_letter: int = 0,
        latency_ms: Optional[float] = None,
        bytes_in: int = 0,
        bytes_out: int = 0,
        status: Optional[str] = None,
        error: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        with self._lock:
            key = self.key(source, tenant_id, pipeline, mode)
            bucket = self.bucket(key)
            bucket.record(
                accepted=accepted,
                processed=processed,
                skipped=skipped,
                failed=failed,
                retried=retried,
                dead_letter=dead_letter,
                latency_ms=latency_ms,
                bytes_in=bytes_in,
                bytes_out=bytes_out,
                status=status,
                error=error,
            )
            if error:
                self._global_errors[normalize_error(error)] += 1
            self._events.append(
                {
                    "event_id": f"met_{uuid.uuid4().hex[:16]}",
                    "created_at": utc_now_iso(),
                    "labels": key.labels(),
                    "accepted": accepted,
                    "processed": processed,
                    "skipped": skipped,
                    "failed": failed,
                    "retried": retried,
                    "dead_letter": dead_letter,
                    "latency_ms": latency_ms,
                    "status": status,
                    "error": normalize_error(error) if error else None,
                    "metadata": sanitize_metadata(metadata or {}),
                }
            )

    def record_result(
        self,
        result: Any,
        source: str = "unknown",
        tenant_id: Optional[str] = None,
        pipeline: str = "default",
        mode: str = "unknown",
        latency_ms: Optional[float] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        accepted = int(getattr(result, "accepted", 0) or 0)
        processed = int(getattr(result, "processed", 0) or 0)
        skipped = int(getattr(result, "skipped", 0) or 0)
        failed = int(getattr(result, "failed", 0) or 0)
        status = getattr(getattr(result, "status", None), "value", getattr(result, "status", None))
        errors = list(getattr(result, "errors", []) or [])
        error = errors[0] if errors else None
        self.record_batch(source, tenant_id, pipeline, mode, accepted, processed, skipped, failed, latency_ms=latency_ms, status=status, error=error, metadata=metadata)

    def timer(self, source: str = "unknown", tenant_id: Optional[str] = None, pipeline: str = "default", mode: str = "unknown") -> "IngestionMetricTimer":
        return IngestionMetricTimer(self, source=source, tenant_id=tenant_id, pipeline=pipeline, mode=mode)

    def snapshot(self, include_events: bool = False) -> IngestionMetricsSnapshot:
        with self._lock:
            buckets = [bucket.to_dict() for bucket in self._buckets.values()]
            totals = self._totals_locked()
            metadata = {"bucket_count": len(buckets)}
            if include_events:
                metadata["recent_events"] = list(self._events)
            return IngestionMetricsSnapshot(
                snapshot_id=f"ims_{uuid.uuid4().hex[:16]}",
                version=METRICS_VERSION,
                created_at=utc_now_iso(),
                uptime_seconds=round(time.monotonic() - self.started_at, 4),
                totals=totals,
                buckets=sorted(buckets, key=lambda item: (item["labels"]["source"], item["labels"]["tenant_id"], item["labels"]["pipeline"])),
                top_errors=[{"error": key, "count": value} for key, value in self._global_errors.most_common(50)],
                metadata=metadata,
            )

    def reset(self) -> None:
        with self._lock:
            self.started_at = time.monotonic()
            self.created_at = utc_now_iso()
            self._buckets.clear()
            self._global_errors.clear()
            self._events.clear()

    def to_prometheus(self, namespace: str = "enterprise_ingestion") -> str:
        with self._lock:
            lines: List[str] = []
            metric_defs = {
                "accepted_total": "Total accepted ingestion records",
                "processed_total": "Total processed ingestion records",
                "skipped_total": "Total skipped ingestion records",
                "failed_total": "Total failed ingestion records",
                "retried_total": "Total retried ingestion records",
                "dead_letter_total": "Total dead-letter ingestion records",
                "batches_total": "Total ingestion batches",
                "latency_avg_ms": "Average ingestion latency in milliseconds",
                "latency_p95_ms": "P95 ingestion latency in milliseconds",
                "latency_p99_ms": "P99 ingestion latency in milliseconds",
                "success_rate": "Ingestion success rate",
                "failure_rate": "Ingestion failure rate",
            }
            for name, help_text in metric_defs.items():
                lines.append(f"# HELP {namespace}_{name} {help_text}")
                lines.append(f"# TYPE {namespace}_{name} gauge")
            for bucket in self._buckets.values():
                labels = prometheus_labels(bucket.key.labels())
                values = {
                    "accepted_total": bucket.accepted,
                    "processed_total": bucket.processed,
                    "skipped_total": bucket.skipped,
                    "failed_total": bucket.failed,
                    "retried_total": bucket.retried,
                    "dead_letter_total": bucket.dead_letter,
                    "batches_total": bucket.batches,
                    "latency_avg_ms": bucket.avg_latency_ms,
                    "latency_p95_ms": bucket.p95_latency_ms,
                    "latency_p99_ms": bucket.p99_latency_ms,
                    "success_rate": bucket.success_rate,
                    "failure_rate": bucket.failure_rate,
                }
                for name, value in values.items():
                    lines.append(f"{namespace}_{name}{labels} {numeric(value)}")
            return "\n".join(lines) + "\n"

    def _totals_locked(self) -> Dict[str, Any]:
        buckets = list(self._buckets.values())
        accepted = sum(bucket.accepted for bucket in buckets)
        processed = sum(bucket.processed for bucket in buckets)
        skipped = sum(bucket.skipped for bucket in buckets)
        failed = sum(bucket.failed for bucket in buckets)
        retried = sum(bucket.retried for bucket in buckets)
        dead_letter = sum(bucket.dead_letter for bucket in buckets)
        batches = sum(bucket.batches for bucket in buckets)
        latencies = [latency for bucket in buckets for latency in bucket.latencies_ms]
        total = processed + failed
        return {
            "accepted": accepted,
            "processed": processed,
            "skipped": skipped,
            "failed": failed,
            "retried": retried,
            "dead_letter": dead_letter,
            "batches": batches,
            "bytes_in": sum(bucket.bytes_in for bucket in buckets),
            "bytes_out": sum(bucket.bytes_out for bucket in buckets),
            "success_rate": round(1.0 if total == 0 else processed / total, 6),
            "failure_rate": round(0.0 if total == 0 else failed / total, 6),
            "avg_latency_ms": round(statistics.mean(latencies), 4) if latencies else 0.0,
            "p95_latency_ms": percentile(latencies, 95),
            "p99_latency_ms": percentile(latencies, 99),
            "sources": dict(Counter(bucket.key.source for bucket in buckets)),
            "tenants": dict(Counter(bucket.key.tenant_id for bucket in buckets)),
            "pipelines": dict(Counter(bucket.key.pipeline for bucket in buckets)),
        }


class IngestionMetricTimer:
    def __init__(self, metrics: IngestionMetrics, source: str, tenant_id: Optional[str], pipeline: str, mode: str) -> None:
        self.metrics = metrics
        self.source = source
        self.tenant_id = tenant_id
        self.pipeline = pipeline
        self.mode = mode
        self.started_at = 0.0
        self.metadata: Dict[str, Any] = {}

    def __enter__(self) -> "IngestionMetricTimer":
        self.started_at = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        latency_ms = round((time.perf_counter() - self.started_at) * 1000, 4)
        if exc:
            self.metrics.record_batch(self.source, self.tenant_id, self.pipeline, self.mode, failed=1, latency_ms=latency_ms, status=MetricStatus.FAILED.value, error=str(exc), metadata=self.metadata)
        else:
            self.metrics.record_batch(self.source, self.tenant_id, self.pipeline, self.mode, accepted=1, processed=1, latency_ms=latency_ms, status=MetricStatus.PROCESSED.value, metadata=self.metadata)


def percentile(values: Iterable[float], percent: int) -> float:
    data = sorted(float(value) for value in values)
    if not data:
        return 0.0
    if len(data) == 1:
        return round(data[0], 4)
    index = (len(data) - 1) * (percent / 100)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return round(data[int(index)], 4)
    weight = index - lower
    return round(data[lower] * (1 - weight) + data[upper] * weight, 4)


def normalize_error(error: Optional[str]) -> str:
    if not error:
        return "unknown"
    text = str(error).strip().replace("\n", " ")
    return text[:300]


def sanitize_metadata(metadata: Mapping[str, Any]) -> Dict[str, Any]:
    sensitive = {"password", "secret", "token", "api_key", "apikey", "authorization", "cookie"}
    result: Dict[str, Any] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if any(item in key_text.lower() for item in sensitive):
            result[key_text] = "[REDACTED]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key_text] = value
        else:
            result[key_text] = str(value)[:500]
    return result


def safe_label(value: Any) -> str:
    text = str(value if value is not None else "unknown")
    return text.replace("\\", "_").replace("\n", "_").replace('"', "'")[:200]


def prometheus_labels(labels: Mapping[str, str]) -> str:
    if not labels:
        return ""
    joined = ",".join(f'{key}="{safe_label(value)}"' for key, value in sorted(labels.items()))
    return "{" + joined + "}"


def numeric(value: Any) -> str:
    try:
        return str(float(value))
    except Exception:
        return "0.0"


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()


_default_metrics: Optional[IngestionMetrics] = None
_default_lock = threading.Lock()


def get_ingestion_metrics() -> IngestionMetrics:
    global _default_metrics
    with _default_lock:
        if _default_metrics is None:
            _default_metrics = IngestionMetrics()
        return _default_metrics


def reset_ingestion_metrics() -> None:
    global _default_metrics
    with _default_lock:
        _default_metrics = IngestionMetrics()


def ingestion_metrics_health() -> Dict[str, Any]:
    metrics = get_ingestion_metrics()
    snapshot = metrics.snapshot()
    return {
        "status": "ok",
        "version": METRICS_VERSION,
        "uptime_seconds": snapshot.uptime_seconds,
        "totals": snapshot.totals,
        "checked_at": utc_now_iso(),
    }


__all__ = [
    "METRICS_VERSION",
    "MetricStatus",
    "MetricKey",
    "MetricBucket",
    "IngestionMetricsSnapshot",
    "IngestionMetrics",
    "IngestionMetricTimer",
    "get_ingestion_metrics",
    "reset_ingestion_metrics",
    "ingestion_metrics_health",
]

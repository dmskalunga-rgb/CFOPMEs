# ml/monitoring/metrics.py
"""
Enterprise ML Metrics Engine.

Métricas para:
- classificação
- regressão
- ranking
- embeddings
- inferência
- latência
- custo
- qualidade de dados
- saúde operacional
- drift
"""

from __future__ import annotations

import json
import math
import statistics
import time
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np


class MetricKind(str, Enum):
    MODEL = "model"
    DATA = "data"
    INFERENCE = "inference"
    SYSTEM = "system"
    COST = "cost"
    DRIFT = "drift"
    BUSINESS = "business"


class MetricSeverity(str, Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class MetricPoint:
    name: str
    value: float
    kind: MetricKind
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    labels: Dict[str, str] = field(default_factory=dict)
    severity: MetricSeverity = MetricSeverity.OK
    description: Optional[str] = None


@dataclass
class MetricReport:
    run_id: str
    generated_at: str
    metrics: List[MetricPoint]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["metrics"] = [asdict(m) for m in self.metrics]
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


class MetricsError(RuntimeError):
    pass


class ClassificationMetrics:
    @staticmethod
    def accuracy(y_true: Sequence[Any], y_pred: Sequence[Any]) -> float:
        ClassificationMetrics._validate_same_size(y_true, y_pred)
        return sum(a == b for a, b in zip(y_true, y_pred)) / len(y_true)

    @staticmethod
    def precision_recall_f1(
        y_true: Sequence[Any],
        y_pred: Sequence[Any],
        average: str = "macro",
    ) -> Dict[str, float]:
        ClassificationMetrics._validate_same_size(y_true, y_pred)

        labels = sorted(set(y_true) | set(y_pred), key=str)
        per_label: Dict[str, Dict[str, float]] = {}

        for label in labels:
            tp = sum(t == label and p == label for t, p in zip(y_true, y_pred))
            fp = sum(t != label and p == label for t, p in zip(y_true, y_pred))
            fn = sum(t == label and p != label for t, p in zip(y_true, y_pred))

            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            f1 = (
                2 * precision * recall / (precision + recall)
                if precision + recall
                else 0.0
            )

            per_label[str(label)] = {
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "support": float(sum(t == label for t in y_true)),
            }

        if average == "none":
            return {
                f"{label}_{metric}": value
                for label, values in per_label.items()
                for metric, value in values.items()
            }

        if average == "macro":
            return {
                "precision": float(np.mean([v["precision"] for v in per_label.values()])),
                "recall": float(np.mean([v["recall"] for v in per_label.values()])),
                "f1": float(np.mean([v["f1"] for v in per_label.values()])),
            }

        if average == "weighted":
            total = sum(v["support"] for v in per_label.values()) or 1.0
            return {
                "precision": sum(v["precision"] * v["support"] for v in per_label.values()) / total,
                "recall": sum(v["recall"] * v["support"] for v in per_label.values()) / total,
                "f1": sum(v["f1"] * v["support"] for v in per_label.values()) / total,
            }

        raise MetricsError(f"average inválido: {average}")

    @staticmethod
    def confusion_matrix(
        y_true: Sequence[Any],
        y_pred: Sequence[Any],
    ) -> Dict[str, Dict[str, int]]:
        ClassificationMetrics._validate_same_size(y_true, y_pred)

        labels = sorted(set(y_true) | set(y_pred), key=str)
        matrix = {str(t): {str(p): 0 for p in labels} for t in labels}

        for true, pred in zip(y_true, y_pred):
            matrix[str(true)][str(pred)] += 1

        return matrix

    @staticmethod
    def log_loss(
        y_true: Sequence[Any],
        probabilities: Sequence[Mapping[Any, float]],
    ) -> float:
        if len(y_true) != len(probabilities):
            raise MetricsError("y_true e probabilities precisam ter o mesmo tamanho.")

        eps = 1e-15
        losses = []

        for true, probs in zip(y_true, probabilities):
            p = float(probs.get(true, 0.0))
            p = min(max(p, eps), 1 - eps)
            losses.append(-math.log(p))

        return float(np.mean(losses))

    @staticmethod
    def _validate_same_size(a: Sequence[Any], b: Sequence[Any]) -> None:
        if len(a) == 0:
            raise MetricsError("Entrada vazia.")
        if len(a) != len(b):
            raise MetricsError("Entradas precisam ter o mesmo tamanho.")


class RegressionMetrics:
    @staticmethod
    def compute(y_true: Sequence[float], y_pred: Sequence[float]) -> Dict[str, float]:
        if len(y_true) == 0:
            raise MetricsError("Entrada vazia.")
        if len(y_true) != len(y_pred):
            raise MetricsError("Entradas precisam ter o mesmo tamanho.")

        yt = np.asarray(y_true, dtype=float)
        yp = np.asarray(y_pred, dtype=float)

        errors = yt - yp
        abs_errors = np.abs(errors)
        squared_errors = errors**2

        mae = float(np.mean(abs_errors))
        mse = float(np.mean(squared_errors))
        rmse = float(np.sqrt(mse))

        denom = np.where(np.abs(yt) < 1e-12, 1e-12, np.abs(yt))
        mape = float(np.mean(abs_errors / denom))

        ss_res = float(np.sum(squared_errors))
        ss_tot = float(np.sum((yt - np.mean(yt)) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot else 0.0

        return {
            "mae": mae,
            "mse": mse,
            "rmse": rmse,
            "mape": mape,
            "r2": float(r2),
            "median_absolute_error": float(np.median(abs_errors)),
            "p95_absolute_error": float(np.percentile(abs_errors, 95)),
        }


class RankingMetrics:
    @staticmethod
    def precision_at_k(relevance: Sequence[int], k: int) -> float:
        if k <= 0:
            raise MetricsError("k precisa ser maior que zero.")
        top = relevance[:k]
        return sum(1 for x in top if x > 0) / k

    @staticmethod
    def ndcg_at_k(relevance: Sequence[float], k: int) -> float:
        def dcg(values: Sequence[float]) -> float:
            return sum((2**rel - 1) / math.log2(i + 2) for i, rel in enumerate(values))

        actual = dcg(relevance[:k])
        ideal = dcg(sorted(relevance, reverse=True)[:k])
        return actual / ideal if ideal else 0.0

    @staticmethod
    def mean_reciprocal_rank(results: Sequence[Sequence[int]]) -> float:
        scores = []
        for row in results:
            rank = next((i + 1 for i, value in enumerate(row) if value > 0), None)
            scores.append(1 / rank if rank else 0.0)
        return float(np.mean(scores)) if scores else 0.0


class DataQualityMetrics:
    @staticmethod
    def profile(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        if not records:
            raise MetricsError("records vazio.")

        columns = sorted(set().union(*(r.keys() for r in records)))
        total = len(records)

        result: Dict[str, Any] = {
            "row_count": total,
            "column_count": len(columns),
            "columns": {},
        }

        for col in columns:
            values = [r.get(col) for r in records]
            nulls = sum(v is None for v in values)
            non_null = [v for v in values if v is not None]

            col_profile: Dict[str, Any] = {
                "null_count": nulls,
                "null_ratio": nulls / total,
                "distinct_count": len(set(map(str, non_null))),
                "distinct_ratio": len(set(map(str, non_null))) / max(len(non_null), 1),
            }

            if DataQualityMetrics._is_numeric(non_null):
                arr = np.asarray(non_null, dtype=float)
                col_profile.update(
                    {
                        "mean": float(np.mean(arr)),
                        "std": float(np.std(arr)),
                        "min": float(np.min(arr)),
                        "max": float(np.max(arr)),
                        "p50": float(np.percentile(arr, 50)),
                        "p95": float(np.percentile(arr, 95)),
                    }
                )

            result["columns"][col] = col_profile

        return result

    @staticmethod
    def duplicate_ratio(records: Sequence[Mapping[str, Any]]) -> float:
        encoded = [json.dumps(r, sort_keys=True, default=str) for r in records]
        return 1.0 - len(set(encoded)) / max(len(encoded), 1)

    @staticmethod
    def _is_numeric(values: Sequence[Any]) -> bool:
        if not values:
            return False
        try:
            [float(v) for v in values]
            return True
        except Exception:
            return False


class EmbeddingMetrics:
    @staticmethod
    def centroid_norm(embeddings: Sequence[Sequence[float]]) -> float:
        arr = np.asarray(embeddings, dtype=float)
        centroid = np.mean(arr, axis=0)
        return float(np.linalg.norm(centroid))

    @staticmethod
    def average_pairwise_cosine_sample(
        embeddings: Sequence[Sequence[float]],
        sample_size: int = 500,
    ) -> float:
        arr = np.asarray(embeddings, dtype=float)

        if arr.ndim != 2:
            raise MetricsError("embeddings precisa ser matriz 2D.")

        if len(arr) > sample_size:
            idx = np.random.default_rng(42).choice(len(arr), sample_size, replace=False)
            arr = arr[idx]

        norms = np.linalg.norm(arr, axis=1)
        arr = arr / np.clip(norms[:, None], 1e-12, None)

        sim = arr @ arr.T
        upper = sim[np.triu_indices_from(sim, k=1)]

        return float(np.mean(upper)) if len(upper) else 0.0


class LatencyTracker:
    def __init__(self, maxlen: int = 10_000) -> None:
        self.values: Deque[float] = deque(maxlen=maxlen)
        self.lock = Lock()

    def observe(self, latency_ms: float) -> None:
        with self.lock:
            self.values.append(float(latency_ms))

    def snapshot(self) -> Dict[str, float]:
        with self.lock:
            values = list(self.values)

        if not values:
            return {
                "count": 0.0,
                "avg_ms": 0.0,
                "p50_ms": 0.0,
                "p95_ms": 0.0,
                "p99_ms": 0.0,
                "max_ms": 0.0,
            }

        arr = np.asarray(values, dtype=float)

        return {
            "count": float(len(arr)),
            "avg_ms": float(np.mean(arr)),
            "p50_ms": float(np.percentile(arr, 50)),
            "p95_ms": float(np.percentile(arr, 95)),
            "p99_ms": float(np.percentile(arr, 99)),
            "max_ms": float(np.max(arr)),
        }


class ThroughputTracker:
    def __init__(self, window_seconds: int = 60) -> None:
        self.window_seconds = window_seconds
        self.events: Deque[float] = deque()
        self.lock = Lock()

    def increment(self, count: int = 1) -> None:
        now = time.time()
        with self.lock:
            for _ in range(count):
                self.events.append(now)
            self._compact(now)

    def rate_per_second(self) -> float:
        now = time.time()
        with self.lock:
            self._compact(now)
            return len(self.events) / self.window_seconds

    def _compact(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self.events and self.events[0] < cutoff:
            self.events.popleft()


class CostMetrics:
    @staticmethod
    def llm_cost(
        prompt_tokens: int,
        completion_tokens: int,
        prompt_price_per_1k: float,
        completion_price_per_1k: float,
    ) -> Dict[str, float]:
        prompt_cost = prompt_tokens / 1000 * prompt_price_per_1k
        completion_cost = completion_tokens / 1000 * completion_price_per_1k

        return {
            "prompt_tokens": float(prompt_tokens),
            "completion_tokens": float(completion_tokens),
            "total_tokens": float(prompt_tokens + completion_tokens),
            "prompt_cost": float(prompt_cost),
            "completion_cost": float(completion_cost),
            "total_cost": float(prompt_cost + completion_cost),
        }


class MetricsRegistry:
    def __init__(self) -> None:
        self._metrics: List[MetricPoint] = []
        self._lock = Lock()

    def record(
        self,
        name: str,
        value: float,
        kind: MetricKind,
        *,
        labels: Optional[Dict[str, str]] = None,
        severity: MetricSeverity = MetricSeverity.OK,
        description: Optional[str] = None,
    ) -> None:
        point = MetricPoint(
            name=name,
            value=float(value),
            kind=kind,
            labels=labels or {},
            severity=severity,
            description=description,
        )

        with self._lock:
            self._metrics.append(point)

    def extend(self, points: Iterable[MetricPoint]) -> None:
        with self._lock:
            self._metrics.extend(points)

    def snapshot(self) -> List[MetricPoint]:
        with self._lock:
            return list(self._metrics)

    def clear(self) -> None:
        with self._lock:
            self._metrics.clear()

    def report(
        self,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MetricReport:
        return MetricReport(
            run_id=run_id or self._generate_run_id(),
            generated_at=datetime.now(timezone.utc).isoformat(),
            metrics=self.snapshot(),
            metadata=metadata or {},
        )

    @staticmethod
    def _generate_run_id() -> str:
        return f"metrics-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"


class PrometheusExporter:
    @staticmethod
    def export(points: Sequence[MetricPoint]) -> str:
        lines: List[str] = []

        for point in points:
            name = PrometheusExporter._sanitize_name(point.name)
            labels = dict(point.labels)
            labels["kind"] = point.kind.value
            labels["severity"] = point.severity.value

            label_text = ",".join(
                f'{PrometheusExporter._sanitize_label(k)}="{str(v)}"'
                for k, v in sorted(labels.items())
            )

            if point.description:
                lines.append(f"# HELP {name} {point.description}")
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name}{{{label_text}}} {point.value}")

        return "\n".join(lines) + "\n"

    @staticmethod
    def _sanitize_name(name: str) -> str:
        out = "".join(ch if ch.isalnum() else "_" for ch in name.lower())
        if out and out[0].isdigit():
            out = "_" + out
        return out

    @staticmethod
    def _sanitize_label(name: str) -> str:
        return PrometheusExporter._sanitize_name(name)


class EnterpriseMLMetrics:
    def __init__(self, registry: Optional[MetricsRegistry] = None) -> None:
        self.registry = registry or MetricsRegistry()

    def record_classification(
        self,
        y_true: Sequence[Any],
        y_pred: Sequence[Any],
        *,
        labels: Optional[Dict[str, str]] = None,
        average: str = "macro",
    ) -> Dict[str, float]:
        metrics = ClassificationMetrics.precision_recall_f1(y_true, y_pred, average=average)
        metrics["accuracy"] = ClassificationMetrics.accuracy(y_true, y_pred)

        for name, value in metrics.items():
            self.registry.record(
                f"ml_classification_{name}",
                value,
                MetricKind.MODEL,
                labels=labels,
                description=f"Classification metric {name}",
            )

        return metrics

    def record_regression(
        self,
        y_true: Sequence[float],
        y_pred: Sequence[float],
        *,
        labels: Optional[Dict[str, str]] = None,
    ) -> Dict[str, float]:
        metrics = RegressionMetrics.compute(y_true, y_pred)

        for name, value in metrics.items():
            self.registry.record(
                f"ml_regression_{name}",
                value,
                MetricKind.MODEL,
                labels=labels,
                description=f"Regression metric {name}",
            )

        return metrics

    def record_data_quality(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        labels: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        profile = DataQualityMetrics.profile(records)

        self.registry.record(
            "ml_data_row_count",
            profile["row_count"],
            MetricKind.DATA,
            labels=labels,
        )
        self.registry.record(
            "ml_data_column_count",
            profile["column_count"],
            MetricKind.DATA,
            labels=labels,
        )
        self.registry.record(
            "ml_data_duplicate_ratio",
            DataQualityMetrics.duplicate_ratio(records),
            MetricKind.DATA,
            labels=labels,
        )

        for column, stats in profile["columns"].items():
            column_labels = {**(labels or {}), "column": column}

            self.registry.record(
                "ml_data_null_ratio",
                stats["null_ratio"],
                MetricKind.DATA,
                labels=column_labels,
            )
            self.registry.record(
                "ml_data_distinct_ratio",
                stats["distinct_ratio"],
                MetricKind.DATA,
                labels=column_labels,
            )

        return profile

    def record_latency_snapshot(
        self,
        tracker: LatencyTracker,
        *,
        labels: Optional[Dict[str, str]] = None,
    ) -> Dict[str, float]:
        snapshot = tracker.snapshot()

        for name, value in snapshot.items():
            self.registry.record(
                f"ml_inference_latency_{name}",
                value,
                MetricKind.INFERENCE,
                labels=labels,
            )

        return snapshot

    def record_throughput(
        self,
        tracker: ThroughputTracker,
        *,
        labels: Optional[Dict[str, str]] = None,
    ) -> float:
        rate = tracker.rate_per_second()

        self.registry.record(
            "ml_inference_throughput_per_second",
            rate,
            MetricKind.INFERENCE,
            labels=labels,
        )

        return rate

    def record_cost(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        prompt_price_per_1k: float,
        completion_price_per_1k: float,
        *,
        labels: Optional[Dict[str, str]] = None,
    ) -> Dict[str, float]:
        metrics = CostMetrics.llm_cost(
            prompt_tokens,
            completion_tokens,
            prompt_price_per_1k,
            completion_price_per_1k,
        )

        for name, value in metrics.items():
            self.registry.record(
                f"ml_cost_{name}",
                value,
                MetricKind.COST,
                labels=labels,
            )

        return metrics

    def record_drift_report(
        self,
        drift_report: Any,
        *,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        severity_score = {
            "none": 0,
            "low": 1,
            "medium": 2,
            "high": 3,
            "critical": 4,
        }.get(str(drift_report.overall_severity.value), 0)

        self.registry.record(
            "ml_drift_overall_severity",
            severity_score,
            MetricKind.DRIFT,
            labels=labels,
        )
        self.registry.record(
            "ml_drift_drifted_features",
            float(drift_report.drifted_features),
            MetricKind.DRIFT,
            labels=labels,
        )
        self.registry.record(
            "ml_drift_total_checks",
            float(drift_report.total_features),
            MetricKind.DRIFT,
            labels=labels,
        )


if __name__ == "__main__":
    metrics = EnterpriseMLMetrics()

    y_true = ["ok", "ok", "fail", "ok", "fail"]
    y_pred = ["ok", "fail", "fail", "ok", "ok"]

    metrics.record_classification(
        y_true,
        y_pred,
        labels={"model": "document_router", "env": "prod"},
    )

    latency = LatencyTracker()
    latency.observe(42)
    latency.observe(70)
    latency.observe(120)

    metrics.record_latency_snapshot(
        latency,
        labels={"service": "ml-api"},
    )

    report = metrics.registry.report(metadata={"pipeline": "realtime"})
    print(report.to_json())

    print(PrometheusExporter.export(report.metrics))
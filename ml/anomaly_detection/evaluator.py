# ml/anomaly_detection/evaluator.py
"""
Enterprise Anomaly Detection Evaluator.

Recursos:
- avaliação supervisionada e não supervisionada
- tuning automático de threshold
- métricas de classificação, ranking e negócio
- matriz de confusão
- precision@k, recall@k, average precision
- avaliação por segmento
- comparação entre modelos/detectores
- relatórios JSON/Markdown
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np


class EvaluatorError(RuntimeError):
    pass


class ThresholdStrategy(str, Enum):
    FIXED = "fixed"
    MAX_F1 = "max_f1"
    MAX_PRECISION_AT_RECALL = "max_precision_at_recall"
    MAX_RECALL_AT_PRECISION = "max_recall_at_precision"
    TOP_PERCENTILE = "top_percentile"
    COST_OPTIMIZED = "cost_optimized"


class AlertSeverity(str, Enum):
    NORMAL = "normal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ThresholdConfig:
    strategy: ThresholdStrategy = ThresholdStrategy.MAX_F1
    fixed_threshold: Optional[float] = None
    min_precision: float = 0.80
    min_recall: float = 0.70
    top_percentile: float = 0.05
    false_positive_cost: float = 1.0
    false_negative_cost: float = 10.0


@dataclass(frozen=True)
class ConfusionMatrix:
    tp: int
    fp: int
    tn: int
    fn: int

    def to_dict(self) -> Dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class ThresholdResult:
    threshold: float
    strategy: ThresholdStrategy
    metrics: Dict[str, float]
    confusion_matrix: ConfusionMatrix
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SegmentEvaluation:
    segment_name: str
    segment_value: str
    samples: int
    anomalies: int
    metrics: Dict[str, float]
    confusion_matrix: Optional[ConfusionMatrix] = None


@dataclass(frozen=True)
class AnomalyEvaluationReport:
    evaluation_id: str
    generated_at: str
    model_name: str
    model_version: str
    samples: int
    anomaly_count: Optional[int]
    threshold_result: Optional[ThresholdResult]
    metrics: Dict[str, float]
    segment_metrics: List[SegmentEvaluation]
    severity_distribution: Dict[str, int]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class ModelComparisonResult:
    comparison_id: str
    generated_at: str
    best_model: str
    ranking: List[Dict[str, Any]]
    reports: List[AnomalyEvaluationReport]

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=indent, default=str)


class AnomalyMetrics:
    @staticmethod
    def confusion(y_true: Sequence[int], y_pred: Sequence[int]) -> ConfusionMatrix:
        yt = np.asarray(y_true, dtype=int)
        yp = np.asarray(y_pred, dtype=int)

        if len(yt) != len(yp):
            raise EvaluatorError("y_true e y_pred precisam ter o mesmo tamanho.")

        return ConfusionMatrix(
            tp=int(np.sum((yt == 1) & (yp == 1))),
            fp=int(np.sum((yt == 0) & (yp == 1))),
            tn=int(np.sum((yt == 0) & (yp == 0))),
            fn=int(np.sum((yt == 1) & (yp == 0))),
        )

    @staticmethod
    def classification_metrics(cm: ConfusionMatrix) -> Dict[str, float]:
        precision = cm.tp / max(cm.tp + cm.fp, 1)
        recall = cm.tp / max(cm.tp + cm.fn, 1)
        specificity = cm.tn / max(cm.tn + cm.fp, 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-12)
        accuracy = (cm.tp + cm.tn) / max(cm.tp + cm.fp + cm.tn + cm.fn, 1)
        false_positive_rate = cm.fp / max(cm.fp + cm.tn, 1)
        false_negative_rate = cm.fn / max(cm.fn + cm.tp, 1)

        return {
            "accuracy": float(accuracy),
            "precision": float(precision),
            "recall": float(recall),
            "specificity": float(specificity),
            "f1": float(f1),
            "false_positive_rate": float(false_positive_rate),
            "false_negative_rate": float(false_negative_rate),
            "support_positive": float(cm.tp + cm.fn),
            "support_negative": float(cm.tn + cm.fp),
        }

    @staticmethod
    def precision_recall_curve(
        y_true: Sequence[int],
        scores: Sequence[float],
    ) -> List[Dict[str, float]]:
        yt = np.asarray(y_true, dtype=int)
        sc = np.asarray(scores, dtype=float)

        thresholds = np.unique(sc)
        result: List[Dict[str, float]] = []

        for threshold in sorted(thresholds):
            pred = (sc >= threshold).astype(int)
            cm = AnomalyMetrics.confusion(yt, pred)
            metrics = AnomalyMetrics.classification_metrics(cm)
            result.append({"threshold": float(threshold), **metrics})

        return result

    @staticmethod
    def average_precision(y_true: Sequence[int], scores: Sequence[float]) -> float:
        yt = np.asarray(y_true, dtype=int)
        sc = np.asarray(scores, dtype=float)

        order = np.argsort(-sc)
        yt_sorted = yt[order]

        positives = np.sum(yt_sorted == 1)
        if positives == 0:
            return 0.0

        precisions = []
        tp = 0

        for i, label in enumerate(yt_sorted, start=1):
            if label == 1:
                tp += 1
                precisions.append(tp / i)

        return float(np.sum(precisions) / positives)

    @staticmethod
    def roc_auc(y_true: Sequence[int], scores: Sequence[float]) -> float:
        yt = np.asarray(y_true, dtype=int)
        sc = np.asarray(scores, dtype=float)

        positives = sc[yt == 1]
        negatives = sc[yt == 0]

        if len(positives) == 0 or len(negatives) == 0:
            return 0.0

        wins = 0.0
        total = len(positives) * len(negatives)

        for p in positives:
            wins += np.sum(p > negatives)
            wins += 0.5 * np.sum(p == negatives)

        return float(wins / total)

    @staticmethod
    def precision_at_k(y_true: Sequence[int], scores: Sequence[float], k: int) -> float:
        if k <= 0:
            return 0.0

        yt = np.asarray(y_true, dtype=int)
        sc = np.asarray(scores, dtype=float)

        k = min(k, len(yt))
        order = np.argsort(-sc)[:k]

        return float(np.mean(yt[order]))

    @staticmethod
    def recall_at_k(y_true: Sequence[int], scores: Sequence[float], k: int) -> float:
        yt = np.asarray(y_true, dtype=int)
        positives = np.sum(yt == 1)

        if positives == 0:
            return 0.0

        sc = np.asarray(scores, dtype=float)
        k = min(k, len(yt))
        order = np.argsort(-sc)[:k]

        return float(np.sum(yt[order] == 1) / positives)

    @staticmethod
    def score_distribution(scores: Sequence[float]) -> Dict[str, float]:
        arr = np.asarray(scores, dtype=float)

        if len(arr) == 0:
            return {}

        return {
            "score_min": float(np.min(arr)),
            "score_max": float(np.max(arr)),
            "score_mean": float(np.mean(arr)),
            "score_std": float(np.std(arr)),
            "score_p50": float(np.percentile(arr, 50)),
            "score_p90": float(np.percentile(arr, 90)),
            "score_p95": float(np.percentile(arr, 95)),
            "score_p99": float(np.percentile(arr, 99)),
        }


class ThresholdOptimizer:
    def optimize(
        self,
        y_true: Sequence[int],
        scores: Sequence[float],
        config: ThresholdConfig,
    ) -> ThresholdResult:
        if len(y_true) != len(scores):
            raise EvaluatorError("y_true e scores precisam ter o mesmo tamanho.")

        if not scores:
            raise EvaluatorError("scores vazio.")

        if config.strategy == ThresholdStrategy.FIXED:
            if config.fixed_threshold is None:
                raise EvaluatorError("fixed_threshold obrigatório para estratégia FIXED.")
            return self._evaluate_threshold(y_true, scores, config.fixed_threshold, config)

        if config.strategy == ThresholdStrategy.TOP_PERCENTILE:
            threshold = float(np.percentile(scores, 100 * (1.0 - config.top_percentile)))
            return self._evaluate_threshold(y_true, scores, threshold, config)

        candidates = self._candidate_thresholds(scores)

        best: Optional[ThresholdResult] = None
        best_value = -float("inf")

        for threshold in candidates:
            current = self._evaluate_threshold(y_true, scores, threshold, config)
            metrics = current.metrics

            if config.strategy == ThresholdStrategy.MAX_F1:
                objective = metrics["f1"]

            elif config.strategy == ThresholdStrategy.MAX_PRECISION_AT_RECALL:
                objective = metrics["precision"] if metrics["recall"] >= config.min_recall else -1.0

            elif config.strategy == ThresholdStrategy.MAX_RECALL_AT_PRECISION:
                objective = metrics["recall"] if metrics["precision"] >= config.min_precision else -1.0

            elif config.strategy == ThresholdStrategy.COST_OPTIMIZED:
                cm = current.confusion_matrix
                cost = cm.fp * config.false_positive_cost + cm.fn * config.false_negative_cost
                objective = -float(cost)

            else:
                objective = metrics["f1"]

            if objective > best_value:
                best_value = objective
                best = current

        if best is None:
            threshold = float(np.percentile(scores, 95))
            best = self._evaluate_threshold(y_true, scores, threshold, config)

        return best

    def _evaluate_threshold(
        self,
        y_true: Sequence[int],
        scores: Sequence[float],
        threshold: float,
        config: ThresholdConfig,
    ) -> ThresholdResult:
        pred = (np.asarray(scores, dtype=float) >= threshold).astype(int)
        cm = AnomalyMetrics.confusion(y_true, pred)
        metrics = AnomalyMetrics.classification_metrics(cm)

        if config.strategy == ThresholdStrategy.COST_OPTIMIZED:
            metrics["business_cost"] = float(
                cm.fp * config.false_positive_cost + cm.fn * config.false_negative_cost
            )

        return ThresholdResult(
            threshold=float(threshold),
            strategy=config.strategy,
            metrics=metrics,
            confusion_matrix=cm,
            metadata={
                "config": asdict(config),
            },
        )

    @staticmethod
    def _candidate_thresholds(scores: Sequence[float]) -> List[float]:
        arr = np.asarray(scores, dtype=float)

        if len(arr) > 500:
            return [float(np.percentile(arr, p)) for p in np.linspace(1, 99, 199)]

        return [float(v) for v in sorted(np.unique(arr))]


class SeverityAssigner:
    def __init__(
        self,
        low: float = 0.50,
        medium: float = 0.70,
        high: float = 0.85,
        critical: float = 0.95,
    ) -> None:
        self.low = low
        self.medium = medium
        self.high = high
        self.critical = critical

    def assign(self, score: float) -> AlertSeverity:
        if score >= self.critical:
            return AlertSeverity.CRITICAL
        if score >= self.high:
            return AlertSeverity.HIGH
        if score >= self.medium:
            return AlertSeverity.MEDIUM
        if score >= self.low:
            return AlertSeverity.LOW
        return AlertSeverity.NORMAL

    def distribution(self, scores: Sequence[float]) -> Dict[str, int]:
        dist = {s.value: 0 for s in AlertSeverity}

        for score in scores:
            dist[self.assign(float(score)).value] += 1

        return dist


class SegmentEvaluator:
    def evaluate(
        self,
        y_true: Optional[Sequence[int]],
        scores: Sequence[float],
        segments: Mapping[str, Sequence[Any]],
        threshold: Optional[float],
    ) -> List[SegmentEvaluation]:
        results: List[SegmentEvaluation] = []
        n = len(scores)

        for segment_name, values in segments.items():
            if len(values) != n:
                raise EvaluatorError(f"Segmento {segment_name} possui tamanho inválido.")

            unique_values = sorted(set(str(v) for v in values))

            for segment_value in unique_values:
                idx = np.asarray([str(v) == segment_value for v in values])
                seg_scores = np.asarray(scores, dtype=float)[idx]

                if len(seg_scores) == 0:
                    continue

                metrics = AnomalyMetrics.score_distribution(seg_scores)
                cm = None
                anomaly_count = 0

                if y_true is not None and threshold is not None:
                    seg_true = np.asarray(y_true, dtype=int)[idx]
                    seg_pred = (seg_scores >= threshold).astype(int)
                    cm = AnomalyMetrics.confusion(seg_true, seg_pred)
                    metrics.update(AnomalyMetrics.classification_metrics(cm))
                    anomaly_count = int(np.sum(seg_true == 1))

                results.append(
                    SegmentEvaluation(
                        segment_name=segment_name,
                        segment_value=segment_value,
                        samples=int(len(seg_scores)),
                        anomalies=anomaly_count,
                        metrics=metrics,
                        confusion_matrix=cm,
                    )
                )

        return results


class EnterpriseAnomalyEvaluator:
    def __init__(
        self,
        threshold_config: Optional[ThresholdConfig] = None,
        severity_assigner: Optional[SeverityAssigner] = None,
    ) -> None:
        self.threshold_config = threshold_config or ThresholdConfig()
        self.threshold_optimizer = ThresholdOptimizer()
        self.severity_assigner = severity_assigner or SeverityAssigner()
        self.segment_evaluator = SegmentEvaluator()

    def evaluate(
        self,
        scores: Sequence[float],
        *,
        y_true: Optional[Sequence[int]] = None,
        threshold: Optional[float] = None,
        segments: Optional[Mapping[str, Sequence[Any]]] = None,
        model_name: str = "anomaly_detector",
        model_version: str = "1.0.0",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AnomalyEvaluationReport:
        if not scores:
            raise EvaluatorError("scores vazio.")

        threshold_result = None
        metrics = AnomalyMetrics.score_distribution(scores)

        if y_true is not None:
            if len(y_true) != len(scores):
                raise EvaluatorError("y_true e scores precisam ter o mesmo tamanho.")

            cfg = self.threshold_config

            if threshold is not None:
                cfg = ThresholdConfig(
                    strategy=ThresholdStrategy.FIXED,
                    fixed_threshold=threshold,
                )

            threshold_result = self.threshold_optimizer.optimize(y_true, scores, cfg)

            pred = (np.asarray(scores) >= threshold_result.threshold).astype(int)
            cm = AnomalyMetrics.confusion(y_true, pred)

            metrics.update(AnomalyMetrics.classification_metrics(cm))
            metrics["average_precision"] = AnomalyMetrics.average_precision(y_true, scores)
            metrics["roc_auc"] = AnomalyMetrics.roc_auc(y_true, scores)

            for k in [10, 50, 100]:
                if len(scores) >= k:
                    metrics[f"precision_at_{k}"] = AnomalyMetrics.precision_at_k(y_true, scores, k)
                    metrics[f"recall_at_{k}"] = AnomalyMetrics.recall_at_k(y_true, scores, k)

            anomaly_count = int(np.sum(np.asarray(y_true) == 1))
        else:
            anomaly_count = None

            if threshold is not None:
                pred = np.asarray(scores) >= threshold
                metrics["predicted_anomaly_rate"] = float(np.mean(pred))
                metrics["predicted_anomaly_count"] = float(np.sum(pred))

        segment_metrics = []

        if segments:
            segment_metrics = self.segment_evaluator.evaluate(
                y_true=y_true,
                scores=scores,
                segments=segments,
                threshold=threshold_result.threshold if threshold_result else threshold,
            )

        return AnomalyEvaluationReport(
            evaluation_id=str(uuid.uuid4()),
            generated_at=datetime.now(timezone.utc).isoformat(),
            model_name=model_name,
            model_version=model_version,
            samples=len(scores),
            anomaly_count=anomaly_count,
            threshold_result=threshold_result,
            metrics=metrics,
            segment_metrics=segment_metrics,
            severity_distribution=self.severity_assigner.distribution(scores),
            metadata=metadata or {},
        )

    def compare_models(
        self,
        model_scores: Mapping[str, Sequence[float]],
        *,
        y_true: Sequence[int],
        model_versions: Optional[Mapping[str, str]] = None,
        primary_metric: str = "average_precision",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ModelComparisonResult:
        reports: List[AnomalyEvaluationReport] = []

        for model_name, scores in model_scores.items():
            reports.append(
                self.evaluate(
                    scores,
                    y_true=y_true,
                    model_name=model_name,
                    model_version=(model_versions or {}).get(model_name, "1.0.0"),
                    metadata=metadata,
                )
            )

        ranking = sorted(
            [
                {
                    "model_name": report.model_name,
                    "model_version": report.model_version,
                    "primary_metric": primary_metric,
                    "score": report.metrics.get(primary_metric, 0.0),
                    "evaluation_id": report.evaluation_id,
                }
                for report in reports
            ],
            key=lambda x: x["score"],
            reverse=True,
        )

        return ModelComparisonResult(
            comparison_id=str(uuid.uuid4()),
            generated_at=datetime.now(timezone.utc).isoformat(),
            best_model=ranking[0]["model_name"] if ranking else "",
            ranking=ranking,
            reports=reports,
        )


class EvaluationReportWriter:
    def __init__(self, output_dir: str | Path = "artifacts/anomaly_detection/evaluations") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_json(self, report: AnomalyEvaluationReport | ModelComparisonResult) -> Path:
        report_id = getattr(report, "evaluation_id", None) or getattr(report, "comparison_id")
        path = self.output_dir / f"{report_id}.json"
        path.write_text(report.to_json(), encoding="utf-8")
        return path

    def write_markdown(self, report: AnomalyEvaluationReport) -> Path:
        path = self.output_dir / f"{report.evaluation_id}.md"

        lines = [
            "# Anomaly Detection Evaluation Report",
            "",
            f"- Evaluation ID: `{report.evaluation_id}`",
            f"- Model: `{report.model_name}:{report.model_version}`",
            f"- Samples: `{report.samples}`",
            f"- Anomaly count: `{report.anomaly_count}`",
            "",
            "## Metrics",
            "",
            "| Metric | Value |",
            "|---|---:|",
        ]

        for key, value in sorted(report.metrics.items()):
            lines.append(f"| {key} | {value:.6f} |")

        if report.threshold_result:
            lines.extend(
                [
                    "",
                    "## Threshold",
                    "",
                    f"- Strategy: `{report.threshold_result.strategy.value}`",
                    f"- Threshold: `{report.threshold_result.threshold:.6f}`",
                    "",
                    "## Confusion Matrix",
                    "",
                    "| TP | FP | TN | FN |",
                    "|---:|---:|---:|---:|",
                    (
                        f"| {report.threshold_result.confusion_matrix.tp} "
                        f"| {report.threshold_result.confusion_matrix.fp} "
                        f"| {report.threshold_result.confusion_matrix.tn} "
                        f"| {report.threshold_result.confusion_matrix.fn} |"
                    ),
                ]
            )

        path.write_text("\n".join(lines), encoding="utf-8")
        return path


if __name__ == "__main__":
    rng = np.random.default_rng(42)

    y_true = rng.choice([0, 1], size=1000, p=[0.94, 0.06])
    scores = rng.normal(0.25, 0.12, size=1000)
    scores[y_true == 1] += rng.normal(0.45, 0.15, size=int(np.sum(y_true == 1)))
    scores = np.clip(scores, 0, 1)

    segments = {
        "channel": rng.choice(["web", "mobile", "api"], size=1000),
        "region": rng.choice(["south", "north", "east"], size=1000),
    }

    evaluator = EnterpriseAnomalyEvaluator(
        ThresholdConfig(strategy=ThresholdStrategy.MAX_F1)
    )

    report = evaluator.evaluate(
        scores.tolist(),
        y_true=y_true.tolist(),
        segments=segments,
        model_name="isolation_forest_detector",
        model_version="1.0.0",
    )

    writer = EvaluationReportWriter()
    writer.write_json(report)
    writer.write_markdown(report)

    print(report.to_json())
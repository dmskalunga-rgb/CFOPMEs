"""
data/analytics/anomaly_analytics.py

Enterprise Anomaly Analytics Engine.

Recursos:
- Detecção de anomalias em métricas, KPIs e séries temporais
- Z-Score, Modified Z-Score, IQR, Rolling Baseline e Threshold Rules
- Severidade automática
- Explicabilidade do motivo da anomalia
- Suporte multi-tenant
- Auditoria e métricas plugáveis
- Batch detection
- Alert payload pronto para integração
- Sem dependências externas obrigatórias
"""

from __future__ import annotations

import json
import logging
import math
import statistics
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class AnomalyMethod(str, Enum):
    Z_SCORE = "z_score"
    MODIFIED_Z_SCORE = "modified_z_score"
    IQR = "iqr"
    ROLLING_BASELINE = "rolling_baseline"
    STATIC_THRESHOLD = "static_threshold"
    PERCENT_CHANGE = "percent_change"


class AnomalySeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AnomalyDirection(str, Enum):
    HIGH = "high"
    LOW = "low"
    BOTH = "both"


class AnomalyStatus(str, Enum):
    NORMAL = "normal"
    ANOMALOUS = "anomalous"
    INSUFFICIENT_DATA = "insufficient_data"
    ERROR = "error"


# =============================================================================
# Exceptions
# =============================================================================

class AnomalyAnalyticsError(Exception):
    """Erro base do módulo de anomalias."""


class AnomalyPolicyError(AnomalyAnalyticsError):
    """Erro na política de anomalia."""


class AnomalyDetectionError(AnomalyAnalyticsError):
    """Erro durante a detecção."""


# =============================================================================
# Protocols
# =============================================================================

class AuditBackend(Protocol):
    def write_event(self, event: Dict[str, Any]) -> None:
        ...


class MetricsBackend(Protocol):
    def increment(
        self,
        metric_name: str,
        value: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        ...

    def gauge(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        ...


# =============================================================================
# Backends
# =============================================================================

class LoggingAuditBackend:
    def write_event(self, event: Dict[str, Any]) -> None:
        logger.info("anomaly_audit=%s", json.dumps(event, ensure_ascii=False, default=str))


class LoggingMetricsBackend:
    def increment(
        self,
        metric_name: str,
        value: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("metric=%s value=%s tags=%s", metric_name, value, tags or {})

    def gauge(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("gauge=%s value=%s tags=%s", metric_name, value, tags or {})


# =============================================================================
# Models
# =============================================================================

@dataclass(frozen=True)
class DataPoint:
    timestamp: datetime
    value: float
    dimensions: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AnomalyPolicy:
    policy_id: str
    metric_id: str
    method: AnomalyMethod
    enabled: bool = True
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    direction: AnomalyDirection = AnomalyDirection.BOTH
    min_points: int = 10
    z_score_threshold: float = 3.0
    modified_z_score_threshold: float = 3.5
    iqr_multiplier: float = 1.5
    rolling_window: int = 14
    rolling_std_multiplier: float = 3.0
    static_min: Optional[float] = None
    static_max: Optional[float] = None
    percent_change_threshold: Optional[float] = None
    severity_thresholds: Dict[str, float] = field(default_factory=lambda: {
        "low": 1.0,
        "medium": 2.0,
        "high": 3.0,
        "critical": 5.0,
    })
    tags: Dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.policy_id:
            raise AnomalyPolicyError("policy_id é obrigatório")

        if not self.metric_id:
            raise AnomalyPolicyError("metric_id é obrigatório")

        if self.min_points < 2:
            raise AnomalyPolicyError("min_points deve ser >= 2")

        if self.method == AnomalyMethod.STATIC_THRESHOLD:
            if self.static_min is None and self.static_max is None:
                raise AnomalyPolicyError(
                    "STATIC_THRESHOLD exige static_min ou static_max"
                )

        if self.method == AnomalyMethod.PERCENT_CHANGE:
            if self.percent_change_threshold is None:
                raise AnomalyPolicyError(
                    "PERCENT_CHANGE exige percent_change_threshold"
                )


@dataclass(frozen=True)
class AnomalyDetectionContext:
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    triggered_by: Optional[str] = None
    correlation_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AnomalyResult:
    result_id: str
    metric_id: str
    policy_id: str
    timestamp: datetime
    value: float
    status: AnomalyStatus
    severity: AnomalySeverity
    score: Optional[float]
    method: AnomalyMethod
    reason: str
    expected_range: Optional[Tuple[Optional[float], Optional[float]]] = None
    baseline_value: Optional[float] = None
    dimensions: Dict[str, Any] = field(default_factory=dict)
    context: Optional[AnomalyDetectionContext] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_anomaly(self) -> bool:
        return self.status == AnomalyStatus.ANOMALOUS

    def to_alert_payload(self) -> Dict[str, Any]:
        return {
            "alert_id": self.result_id,
            "metric_id": self.metric_id,
            "policy_id": self.policy_id,
            "timestamp": self.timestamp.isoformat(),
            "value": self.value,
            "severity": self.severity.value,
            "score": self.score,
            "method": self.method.value,
            "reason": self.reason,
            "expected_range": self.expected_range,
            "baseline_value": self.baseline_value,
            "dimensions": self.dimensions,
            "metadata": self.metadata,
        }


# =============================================================================
# Repository
# =============================================================================

class AnomalyPolicyRepository:
    def __init__(self, policies: Optional[List[AnomalyPolicy]] = None) -> None:
        self._policies: Dict[str, AnomalyPolicy] = {}

        for policy in policies or []:
            self.save(policy)

    def save(self, policy: AnomalyPolicy) -> None:
        policy.validate()
        self._policies[policy.policy_id] = policy

    def get(self, policy_id: str) -> AnomalyPolicy:
        policy = self._policies.get(policy_id)
        if not policy:
            raise AnomalyPolicyError(f"Política não encontrada: {policy_id}")
        return policy

    def list_for_metric(
        self,
        metric_id: str,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> List[AnomalyPolicy]:
        policies = [
            policy
            for policy in self._policies.values()
            if policy.enabled and policy.metric_id == metric_id
        ]

        if tenant_id is not None:
            policies = [
                policy
                for policy in policies
                if policy.tenant_id is None or policy.tenant_id == tenant_id
            ]

        if domain is not None:
            policies = [
                policy
                for policy in policies
                if policy.domain is None or policy.domain == domain
            ]

        return policies


# =============================================================================
# Statistical Helpers
# =============================================================================

class StatisticalAnomalyDetector:
    @staticmethod
    def detect(
        points: List[DataPoint],
        current: DataPoint,
        policy: AnomalyPolicy,
    ) -> AnomalyResult:
        if len(points) < policy.min_points:
            return StatisticalAnomalyDetector._insufficient_data(current, policy)

        values = [point.value for point in points if math.isfinite(point.value)]

        if len(values) < policy.min_points:
            return StatisticalAnomalyDetector._insufficient_data(current, policy)

        if policy.method == AnomalyMethod.Z_SCORE:
            return StatisticalAnomalyDetector._z_score(values, current, policy)

        if policy.method == AnomalyMethod.MODIFIED_Z_SCORE:
            return StatisticalAnomalyDetector._modified_z_score(values, current, policy)

        if policy.method == AnomalyMethod.IQR:
            return StatisticalAnomalyDetector._iqr(values, current, policy)

        if policy.method == AnomalyMethod.ROLLING_BASELINE:
            return StatisticalAnomalyDetector._rolling_baseline(values, current, policy)

        if policy.method == AnomalyMethod.STATIC_THRESHOLD:
            return StatisticalAnomalyDetector._static_threshold(current, policy)

        if policy.method == AnomalyMethod.PERCENT_CHANGE:
            return StatisticalAnomalyDetector._percent_change(values, current, policy)

        raise AnomalyDetectionError(f"Método não suportado: {policy.method}")

    @staticmethod
    def _z_score(
        values: List[float],
        current: DataPoint,
        policy: AnomalyPolicy,
    ) -> AnomalyResult:
        mean = statistics.mean(values)
        std = statistics.stdev(values) if len(values) > 1 else 0.0

        if std == 0:
            return StatisticalAnomalyDetector._normal(
                current,
                policy,
                score=0,
                reason="Desvio padrão zero; sem variação histórica.",
                baseline=mean,
                expected_range=(mean, mean),
            )

        score = (current.value - mean) / std
        anomalous = StatisticalAnomalyDetector._direction_match(
            score,
            policy.direction,
        ) and abs(score) >= policy.z_score_threshold

        return StatisticalAnomalyDetector._result(
            current=current,
            policy=policy,
            anomalous=anomalous,
            score=score,
            baseline=mean,
            expected_range=(
                mean - policy.z_score_threshold * std,
                mean + policy.z_score_threshold * std,
            ),
            reason=(
                f"Z-score={score:.4f}, média={mean:.4f}, std={std:.4f}, "
                f"threshold={policy.z_score_threshold}"
            ),
        )

    @staticmethod
    def _modified_z_score(
        values: List[float],
        current: DataPoint,
        policy: AnomalyPolicy,
    ) -> AnomalyResult:
        median = statistics.median(values)
        deviations = [abs(value - median) for value in values]
        mad = statistics.median(deviations)

        if mad == 0:
            return StatisticalAnomalyDetector._normal(
                current,
                policy,
                score=0,
                reason="MAD zero; sem variação robusta suficiente.",
                baseline=median,
                expected_range=(median, median),
            )

        score = 0.6745 * (current.value - median) / mad
        anomalous = StatisticalAnomalyDetector._direction_match(
            score,
            policy.direction,
        ) and abs(score) >= policy.modified_z_score_threshold

        lower = median - (policy.modified_z_score_threshold * mad / 0.6745)
        upper = median + (policy.modified_z_score_threshold * mad / 0.6745)

        return StatisticalAnomalyDetector._result(
            current=current,
            policy=policy,
            anomalous=anomalous,
            score=score,
            baseline=median,
            expected_range=(lower, upper),
            reason=(
                f"Modified Z-score={score:.4f}, mediana={median:.4f}, "
                f"MAD={mad:.4f}, threshold={policy.modified_z_score_threshold}"
            ),
        )

    @staticmethod
    def _iqr(
        values: List[float],
        current: DataPoint,
        policy: AnomalyPolicy,
    ) -> AnomalyResult:
        sorted_values = sorted(values)
        q1 = StatisticalAnomalyDetector._percentile(sorted_values, 25)
        q3 = StatisticalAnomalyDetector._percentile(sorted_values, 75)
        iqr = q3 - q1

        lower = q1 - policy.iqr_multiplier * iqr
        upper = q3 + policy.iqr_multiplier * iqr

        anomalous_high = current.value > upper
        anomalous_low = current.value < lower

        anomalous = (
            policy.direction in {AnomalyDirection.BOTH, AnomalyDirection.HIGH}
            and anomalous_high
        ) or (
            policy.direction in {AnomalyDirection.BOTH, AnomalyDirection.LOW}
            and anomalous_low
        )

        score = 0.0
        if current.value > upper and iqr > 0:
            score = (current.value - upper) / iqr
        elif current.value < lower and iqr > 0:
            score = (lower - current.value) / iqr * -1

        return StatisticalAnomalyDetector._result(
            current=current,
            policy=policy,
            anomalous=anomalous,
            score=score,
            baseline=statistics.median(values),
            expected_range=(lower, upper),
            reason=(
                f"IQR={iqr:.4f}, Q1={q1:.4f}, Q3={q3:.4f}, "
                f"limites=({lower:.4f}, {upper:.4f})"
            ),
        )

    @staticmethod
    def _rolling_baseline(
        values: List[float],
        current: DataPoint,
        policy: AnomalyPolicy,
    ) -> AnomalyResult:
        window_values = values[-policy.rolling_window:]

        if len(window_values) < max(2, min(policy.min_points, policy.rolling_window)):
            return StatisticalAnomalyDetector._insufficient_data(current, policy)

        mean = statistics.mean(window_values)
        std = statistics.stdev(window_values)

        lower = mean - policy.rolling_std_multiplier * std
        upper = mean + policy.rolling_std_multiplier * std

        anomalous_high = current.value > upper
        anomalous_low = current.value < lower

        anomalous = (
            policy.direction in {AnomalyDirection.BOTH, AnomalyDirection.HIGH}
            and anomalous_high
        ) or (
            policy.direction in {AnomalyDirection.BOTH, AnomalyDirection.LOW}
            and anomalous_low
        )

        score = 0 if std == 0 else (current.value - mean) / std

        return StatisticalAnomalyDetector._result(
            current=current,
            policy=policy,
            anomalous=anomalous,
            score=score,
            baseline=mean,
            expected_range=(lower, upper),
            reason=(
                f"Rolling baseline média={mean:.4f}, std={std:.4f}, "
                f"window={policy.rolling_window}, limites=({lower:.4f}, {upper:.4f})"
            ),
        )

    @staticmethod
    def _static_threshold(
        current: DataPoint,
        policy: AnomalyPolicy,
    ) -> AnomalyResult:
        anomalous_high = policy.static_max is not None and current.value > policy.static_max
        anomalous_low = policy.static_min is not None and current.value < policy.static_min

        anomalous = (
            policy.direction in {AnomalyDirection.BOTH, AnomalyDirection.HIGH}
            and anomalous_high
        ) or (
            policy.direction in {AnomalyDirection.BOTH, AnomalyDirection.LOW}
            and anomalous_low
        )

        score = 0.0
        if anomalous_high and policy.static_max not in (None, 0):
            score = current.value / policy.static_max
        elif anomalous_low and policy.static_min not in (None, 0):
            score = current.value / policy.static_min

        return StatisticalAnomalyDetector._result(
            current=current,
            policy=policy,
            anomalous=anomalous,
            score=score,
            baseline=None,
            expected_range=(policy.static_min, policy.static_max),
            reason=(
                f"Static threshold min={policy.static_min}, "
                f"max={policy.static_max}, value={current.value}"
            ),
        )

    @staticmethod
    def _percent_change(
        values: List[float],
        current: DataPoint,
        policy: AnomalyPolicy,
    ) -> AnomalyResult:
        previous = values[-1]

        if previous == 0:
            return StatisticalAnomalyDetector._normal(
                current,
                policy,
                score=0,
                reason="Valor anterior zero; mudança percentual indefinida.",
                baseline=previous,
                expected_range=None,
            )

        change = ((current.value - previous) / abs(previous)) * 100
        threshold = policy.percent_change_threshold or 0

        anomalous = StatisticalAnomalyDetector._direction_match(
            change,
            policy.direction,
        ) and abs(change) >= threshold

        return StatisticalAnomalyDetector._result(
            current=current,
            policy=policy,
            anomalous=anomalous,
            score=change,
            baseline=previous,
            expected_range=(
                previous * (1 - threshold / 100),
                previous * (1 + threshold / 100),
            ),
            reason=(
                f"Percent change={change:.4f}%, previous={previous:.4f}, "
                f"threshold={threshold}%"
            ),
        )

    @staticmethod
    def _result(
        current: DataPoint,
        policy: AnomalyPolicy,
        anomalous: bool,
        score: Optional[float],
        baseline: Optional[float],
        expected_range: Optional[Tuple[Optional[float], Optional[float]]],
        reason: str,
    ) -> AnomalyResult:
        return AnomalyResult(
            result_id=str(uuid.uuid4()),
            metric_id=policy.metric_id,
            policy_id=policy.policy_id,
            timestamp=current.timestamp,
            value=current.value,
            status=AnomalyStatus.ANOMALOUS if anomalous else AnomalyStatus.NORMAL,
            severity=(
                StatisticalAnomalyDetector._severity(abs(score or 0), policy)
                if anomalous
                else AnomalySeverity.INFO
            ),
            score=score,
            method=policy.method,
            reason=reason,
            expected_range=expected_range,
            baseline_value=baseline,
            dimensions=current.dimensions,
            metadata=current.metadata,
        )

    @staticmethod
    def _normal(
        current: DataPoint,
        policy: AnomalyPolicy,
        score: Optional[float],
        reason: str,
        baseline: Optional[float],
        expected_range: Optional[Tuple[Optional[float], Optional[float]]],
    ) -> AnomalyResult:
        return StatisticalAnomalyDetector._result(
            current=current,
            policy=policy,
            anomalous=False,
            score=score,
            baseline=baseline,
            expected_range=expected_range,
            reason=reason,
        )

    @staticmethod
    def _insufficient_data(
        current: DataPoint,
        policy: AnomalyPolicy,
    ) -> AnomalyResult:
        return AnomalyResult(
            result_id=str(uuid.uuid4()),
            metric_id=policy.metric_id,
            policy_id=policy.policy_id,
            timestamp=current.timestamp,
            value=current.value,
            status=AnomalyStatus.INSUFFICIENT_DATA,
            severity=AnomalySeverity.INFO,
            score=None,
            method=policy.method,
            reason=f"Dados insuficientes. min_points={policy.min_points}",
            dimensions=current.dimensions,
            metadata=current.metadata,
        )

    @staticmethod
    def _direction_match(score: float, direction: AnomalyDirection) -> bool:
        if direction == AnomalyDirection.BOTH:
            return True
        if direction == AnomalyDirection.HIGH:
            return score > 0
        if direction == AnomalyDirection.LOW:
            return score < 0
        return False

    @staticmethod
    def _severity(score: float, policy: AnomalyPolicy) -> AnomalySeverity:
        thresholds = policy.severity_thresholds

        if score >= thresholds.get("critical", 5.0):
            return AnomalySeverity.CRITICAL
        if score >= thresholds.get("high", 3.0):
            return AnomalySeverity.HIGH
        if score >= thresholds.get("medium", 2.0):
            return AnomalySeverity.MEDIUM
        if score >= thresholds.get("low", 1.0):
            return AnomalySeverity.LOW

        return AnomalySeverity.INFO

    @staticmethod
    def _percentile(values: List[float], percentile: float) -> float:
        if not values:
            raise ValueError("Lista vazia")

        k = (len(values) - 1) * percentile / 100
        floor = math.floor(k)
        ceil = math.ceil(k)

        if floor == ceil:
            return values[int(k)]

        lower = values[floor] * (ceil - k)
        upper = values[ceil] * (k - floor)
        return lower + upper


# =============================================================================
# Engine
# =============================================================================

class AnomalyAnalyticsEngine:
    def __init__(
        self,
        policy_repository: AnomalyPolicyRepository,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
    ) -> None:
        self.policy_repository = policy_repository
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()

    def detect(
        self,
        metric_id: str,
        historical_points: List[DataPoint],
        current_point: DataPoint,
        context: Optional[AnomalyDetectionContext] = None,
    ) -> List[AnomalyResult]:
        context = context or AnomalyDetectionContext()

        policies = self.policy_repository.list_for_metric(
            metric_id=metric_id,
            tenant_id=context.tenant_id,
            domain=context.domain,
        )

        results: List[AnomalyResult] = []

        for policy in policies:
            try:
                result = StatisticalAnomalyDetector.detect(
                    points=historical_points,
                    current=current_point,
                    policy=policy,
                )
                result.context = context
                results.append(result)

                self._audit_result(result)
                self._emit_metrics(result)

            except Exception as exc:
                logger.exception("Erro na detecção de anomalia")
                error_result = AnomalyResult(
                    result_id=str(uuid.uuid4()),
                    metric_id=metric_id,
                    policy_id=policy.policy_id,
                    timestamp=current_point.timestamp,
                    value=current_point.value,
                    status=AnomalyStatus.ERROR,
                    severity=AnomalySeverity.HIGH,
                    score=None,
                    method=policy.method,
                    reason=str(exc),
                    dimensions=current_point.dimensions,
                    context=context,
                    metadata=current_point.metadata,
                )
                results.append(error_result)
                self._audit_result(error_result)
                self._emit_metrics(error_result)

        return results

    def detect_batch(
        self,
        metric_id: str,
        points: List[DataPoint],
        context: Optional[AnomalyDetectionContext] = None,
        warmup_points: int = 10,
    ) -> List[AnomalyResult]:
        if len(points) <= warmup_points:
            return []

        sorted_points = sorted(points, key=lambda p: p.timestamp)
        results: List[AnomalyResult] = []

        for index in range(warmup_points, len(sorted_points)):
            historical = sorted_points[:index]
            current = sorted_points[index]
            results.extend(
                self.detect(
                    metric_id=metric_id,
                    historical_points=historical,
                    current_point=current,
                    context=context,
                )
            )

        return results

    def anomalies_only(self, results: Iterable[AnomalyResult]) -> List[AnomalyResult]:
        return [result for result in results if result.is_anomaly()]

    def export_results_json(self, results: Iterable[AnomalyResult]) -> str:
        return json.dumps(
            [self._result_to_dict(result) for result in results],
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def build_alerts(self, results: Iterable[AnomalyResult]) -> List[Dict[str, Any]]:
        return [
            result.to_alert_payload()
            for result in results
            if result.is_anomaly()
        ]

    def _audit_result(self, result: AnomalyResult) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": "analytics.anomaly.detected"
                if result.is_anomaly()
                else "analytics.anomaly.evaluated",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "metric_id": result.metric_id,
                "policy_id": result.policy_id,
                "status": result.status.value,
                "severity": result.severity.value,
                "method": result.method.value,
                "score": result.score,
                "value": result.value,
                "reason": result.reason,
                "tenant_id": result.context.tenant_id if result.context else None,
                "domain": result.context.domain if result.context else None,
                "correlation_id": result.context.correlation_id if result.context else None,
                "dimensions": result.dimensions,
            }
        )

    def _emit_metrics(self, result: AnomalyResult) -> None:
        tags = {
            "metric_id": result.metric_id,
            "policy_id": result.policy_id,
            "status": result.status.value,
            "severity": result.severity.value,
            "method": result.method.value,
        }

        self.metrics_backend.increment("analytics.anomaly.evaluations.total", tags=tags)

        if result.is_anomaly():
            self.metrics_backend.increment("analytics.anomaly.detected.total", tags=tags)

        if result.score is not None:
            self.metrics_backend.gauge("analytics.anomaly.score", float(result.score), tags=tags)

    @staticmethod
    def _result_to_dict(result: AnomalyResult) -> Dict[str, Any]:
        data = asdict(result)
        data["timestamp"] = result.timestamp.isoformat()
        data["status"] = result.status.value
        data["severity"] = result.severity.value
        data["method"] = result.method.value

        if result.context:
            data["context"] = asdict(result.context)

        return data


# =============================================================================
# Default Policies
# =============================================================================

def build_default_anomaly_policies() -> List[AnomalyPolicy]:
    return [
        AnomalyPolicy(
            policy_id="anom-gross-revenue-zscore",
            metric_id="gross_revenue",
            method=AnomalyMethod.Z_SCORE,
            direction=AnomalyDirection.BOTH,
            min_points=14,
            z_score_threshold=3.0,
            domain="sales",
            tags={"kpi": "true", "finance": "true"},
        ),
        AnomalyPolicy(
            policy_id="anom-orders-rolling",
            metric_id="orders_count",
            method=AnomalyMethod.ROLLING_BASELINE,
            direction=AnomalyDirection.BOTH,
            min_points=14,
            rolling_window=14,
            rolling_std_multiplier=3.0,
            domain="sales",
            tags={"operations": "true"},
        ),
        AnomalyPolicy(
            policy_id="anom-conversion-rate-static",
            metric_id="conversion_rate",
            method=AnomalyMethod.STATIC_THRESHOLD,
            direction=AnomalyDirection.LOW,
            static_min=0.01,
            domain="digital",
            tags={"growth": "true"},
        ),
        AnomalyPolicy(
            policy_id="anom-error-rate-percent-change",
            metric_id="error_rate",
            method=AnomalyMethod.PERCENT_CHANGE,
            direction=AnomalyDirection.HIGH,
            min_points=2,
            percent_change_threshold=50.0,
            domain="platform",
            tags={"sre": "true"},
        ),
    ]


def create_default_anomaly_engine() -> AnomalyAnalyticsEngine:
    return AnomalyAnalyticsEngine(
        policy_repository=AnomalyPolicyRepository(
            build_default_anomaly_policies()
        )
    )


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    from datetime import timedelta

    engine = create_default_anomaly_engine()

    now = datetime.now(timezone.utc)

    historical = [
        DataPoint(
            timestamp=now - timedelta(days=20 - index),
            value=1000 + index * 10,
            dimensions={"store_id": "store-a"},
        )
        for index in range(20)
    ]

    current = DataPoint(
        timestamp=now,
        value=3000,
        dimensions={"store_id": "store-a"},
        metadata={"source": "sales_orders"},
    )

    results = engine.detect(
        metric_id="gross_revenue",
        historical_points=historical,
        current_point=current,
        context=AnomalyDetectionContext(
            tenant_id="tenant-default",
            domain="sales",
            triggered_by="daily-monitoring-job",
            correlation_id="corr-anomaly-001",
        ),
    )

    print(engine.export_results_json(results))
    print(json.dumps(engine.build_alerts(results), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    example_usage()
"""
data/analytics/trend_analysis.py

Enterprise Trend Analysis Engine.

Recursos:
- Análise de tendência em séries temporais
- Direção, força, momentum e aceleração
- Médias móveis simples e exponenciais
- Detecção de mudança de regime
- Sazonalidade simples
- Breakout detection
- Comparação entre períodos
- Insights automáticos
- Multi-tenant
- Auditoria e métricas plugáveis
- Exportação JSON
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

class TrendDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"
    VOLATILE = "volatile"
    UNKNOWN = "unknown"


class TrendStrength(str, Enum):
    VERY_WEAK = "very_weak"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    VERY_STRONG = "very_strong"


class TrendStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    INSUFFICIENT_DATA = "insufficient_data"


class TrendInsightSeverity(str, Enum):
    INFO = "info"
    POSITIVE = "positive"
    WARNING = "warning"
    CRITICAL = "critical"


class MovingAverageType(str, Enum):
    SIMPLE = "simple"
    EXPONENTIAL = "exponential"


class BreakoutDirection(str, Enum):
    ABOVE = "above"
    BELOW = "below"
    NONE = "none"


# =============================================================================
# Exceptions
# =============================================================================

class TrendAnalysisError(Exception):
    """Erro base de trend analysis."""


class TrendValidationError(TrendAnalysisError):
    """Erro de validação."""


class TrendComputationError(TrendAnalysisError):
    """Erro de cálculo."""


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
        logger.info(
            "trend_analysis_audit=%s",
            json.dumps(event, ensure_ascii=False, default=str),
        )


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
class TrendContext:
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    user_id: Optional[str] = None
    correlation_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrendPoint:
    timestamp: datetime
    value: float
    dimensions: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrendAnalysisConfig:
    metric_id: str
    min_points: int = 5
    short_window: int = 3
    long_window: int = 7
    momentum_window: int = 3
    breakout_std_multiplier: float = 2.0
    regime_change_threshold: float = 0.35
    volatility_threshold: float = 0.25
    seasonality_period: Optional[int] = None
    moving_average_type: MovingAverageType = MovingAverageType.SIMPLE
    tags: Dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.metric_id:
            raise TrendValidationError("metric_id é obrigatório")

        if self.min_points < 3:
            raise TrendValidationError("min_points precisa ser >= 3")

        if self.short_window <= 0 or self.long_window <= 0:
            raise TrendValidationError("short_window e long_window precisam ser > 0")

        if self.short_window >= self.long_window:
            raise TrendValidationError("short_window deve ser menor que long_window")

        if self.momentum_window <= 0:
            raise TrendValidationError("momentum_window precisa ser > 0")


@dataclass
class MovingAverageResult:
    window: int
    average_type: MovingAverageType
    values: List[Optional[float]]


@dataclass
class BreakoutResult:
    direction: BreakoutDirection
    value: float
    upper_bound: Optional[float]
    lower_bound: Optional[float]
    score: Optional[float]
    detected: bool


@dataclass
class RegimeChangeResult:
    detected: bool
    previous_mean: Optional[float]
    recent_mean: Optional[float]
    relative_change: Optional[float]
    reason: str


@dataclass
class SeasonalityResult:
    detected: bool
    period: Optional[int]
    seasonal_strength: Optional[float]
    seasonal_indices: Dict[int, float] = field(default_factory=dict)
    reason: str = ""


@dataclass
class TrendInsight:
    insight_id: str
    title: str
    message: str
    severity: TrendInsightSeverity
    metric_id: str
    recommendation: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TrendAnalysisResult:
    analysis_id: str
    metric_id: str
    status: TrendStatus
    computed_at: datetime
    direction: TrendDirection
    strength: TrendStrength
    slope: Optional[float]
    correlation: Optional[float]
    momentum: Optional[float]
    acceleration: Optional[float]
    volatility: Optional[float]
    short_moving_average: Optional[MovingAverageResult]
    long_moving_average: Optional[MovingAverageResult]
    breakout: Optional[BreakoutResult]
    regime_change: Optional[RegimeChangeResult]
    seasonality: Optional[SeasonalityResult]
    insights: List[TrendInsight]
    points_count: int
    context: Optional[TrendContext] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Math Helpers
# =============================================================================

class TrendMath:
    @staticmethod
    def clean_points(points: Iterable[TrendPoint]) -> List[TrendPoint]:
        return sorted(
            [
                point for point in points
                if isinstance(point.value, (int, float))
                and math.isfinite(float(point.value))
            ],
            key=lambda item: item.timestamp,
        )

    @staticmethod
    def values(points: List[TrendPoint]) -> List[float]:
        return [float(point.value) for point in points]

    @staticmethod
    def simple_moving_average(values: List[float], window: int) -> List[Optional[float]]:
        if window <= 0:
            raise TrendValidationError("window precisa ser > 0")

        output: List[Optional[float]] = []

        for index in range(len(values)):
            if index + 1 < window:
                output.append(None)
            else:
                subset = values[index + 1 - window:index + 1]
                output.append(sum(subset) / window)

        return output

    @staticmethod
    def exponential_moving_average(
        values: List[float],
        window: int,
    ) -> List[Optional[float]]:
        if window <= 0:
            raise TrendValidationError("window precisa ser > 0")

        if not values:
            return []

        alpha = 2 / (window + 1)
        output: List[Optional[float]] = []
        ema: Optional[float] = None

        for index, value in enumerate(values):
            if index + 1 < window:
                output.append(None)
                continue

            if ema is None:
                seed = values[index + 1 - window:index + 1]
                ema = sum(seed) / window
            else:
                ema = alpha * value + (1 - alpha) * ema

            output.append(ema)

        return output

    @staticmethod
    def moving_average(
        values: List[float],
        window: int,
        average_type: MovingAverageType,
    ) -> List[Optional[float]]:
        if average_type == MovingAverageType.SIMPLE:
            return TrendMath.simple_moving_average(values, window)

        if average_type == MovingAverageType.EXPONENTIAL:
            return TrendMath.exponential_moving_average(values, window)

        raise TrendValidationError(f"Tipo de média móvel inválido: {average_type}")

    @staticmethod
    def linear_regression(values: List[float]) -> Tuple[float, float, float]:
        if len(values) < 2:
            raise TrendValidationError("Regressão exige pelo menos 2 pontos")

        x_values = list(range(len(values)))
        x_mean = statistics.mean(x_values)
        y_mean = statistics.mean(values)

        numerator = sum(
            (x - x_mean) * (y - y_mean)
            for x, y in zip(x_values, values)
        )
        denominator = sum((x - x_mean) ** 2 for x in x_values)

        slope = numerator / denominator if denominator else 0.0
        intercept = y_mean - slope * x_mean

        correlation = TrendMath.pearson(x_values, values)

        return slope, intercept, correlation

    @staticmethod
    def pearson(x_values: List[float], y_values: List[float]) -> float:
        if len(x_values) != len(y_values) or len(x_values) < 2:
            return 0.0

        mean_x = statistics.mean(x_values)
        mean_y = statistics.mean(y_values)

        numerator = sum(
            (x - mean_x) * (y - mean_y)
            for x, y in zip(x_values, y_values)
        )
        denominator_x = math.sqrt(sum((x - mean_x) ** 2 for x in x_values))
        denominator_y = math.sqrt(sum((y - mean_y) ** 2 for y in y_values))

        denominator = denominator_x * denominator_y

        if denominator == 0:
            return 0.0

        return numerator / denominator

    @staticmethod
    def percent_change(previous: float, current: float) -> Optional[float]:
        if previous == 0:
            return None

        return ((current - previous) / abs(previous)) * 100

    @staticmethod
    def relative_change(previous: float, current: float) -> Optional[float]:
        if previous == 0:
            return None

        return (current - previous) / abs(previous)


# =============================================================================
# Analyzer
# =============================================================================

class TrendAnalyzer:
    @staticmethod
    def analyze(
        points: List[TrendPoint],
        config: TrendAnalysisConfig,
        context: Optional[TrendContext] = None,
    ) -> TrendAnalysisResult:
        config.validate()
        clean_points = TrendMath.clean_points(points)
        values = TrendMath.values(clean_points)

        if len(values) < config.min_points:
            return TrendAnalysisResult(
                analysis_id=str(uuid.uuid4()),
                metric_id=config.metric_id,
                status=TrendStatus.INSUFFICIENT_DATA,
                computed_at=datetime.now(timezone.utc),
                direction=TrendDirection.UNKNOWN,
                strength=TrendStrength.VERY_WEAK,
                slope=None,
                correlation=None,
                momentum=None,
                acceleration=None,
                volatility=None,
                short_moving_average=None,
                long_moving_average=None,
                breakout=None,
                regime_change=None,
                seasonality=None,
                insights=[],
                points_count=len(values),
                context=context,
                error=f"Dados insuficientes: {len(values)} pontos",
            )

        slope, _, correlation = TrendMath.linear_regression(values)
        direction = TrendAnalyzer._direction(values, slope, correlation, config)
        strength = TrendAnalyzer._strength(correlation)

        short_ma = MovingAverageResult(
            window=config.short_window,
            average_type=config.moving_average_type,
            values=TrendMath.moving_average(
                values,
                config.short_window,
                config.moving_average_type,
            ),
        )

        long_ma = MovingAverageResult(
            window=config.long_window,
            average_type=config.moving_average_type,
            values=TrendMath.moving_average(
                values,
                config.long_window,
                config.moving_average_type,
            ),
        )

        momentum = TrendAnalyzer._momentum(values, config.momentum_window)
        acceleration = TrendAnalyzer._acceleration(values, config.momentum_window)
        volatility = TrendAnalyzer._volatility(values)

        breakout = TrendAnalyzer._breakout(values, config)
        regime_change = TrendAnalyzer._regime_change(values, config)
        seasonality = TrendAnalyzer._seasonality(values, config)

        insights = TrendAnalyzer._build_insights(
            metric_id=config.metric_id,
            direction=direction,
            strength=strength,
            momentum=momentum,
            acceleration=acceleration,
            volatility=volatility,
            breakout=breakout,
            regime_change=regime_change,
            seasonality=seasonality,
        )

        return TrendAnalysisResult(
            analysis_id=str(uuid.uuid4()),
            metric_id=config.metric_id,
            status=TrendStatus.SUCCESS,
            computed_at=datetime.now(timezone.utc),
            direction=direction,
            strength=strength,
            slope=slope,
            correlation=correlation,
            momentum=momentum,
            acceleration=acceleration,
            volatility=volatility,
            short_moving_average=short_ma,
            long_moving_average=long_ma,
            breakout=breakout,
            regime_change=regime_change,
            seasonality=seasonality,
            insights=insights,
            points_count=len(values),
            context=context,
            metadata={
                "first_timestamp": clean_points[0].timestamp.isoformat(),
                "last_timestamp": clean_points[-1].timestamp.isoformat(),
                "first_value": values[0],
                "last_value": values[-1],
                "config": asdict(config),
            },
        )

    @staticmethod
    def _direction(
        values: List[float],
        slope: float,
        correlation: float,
        config: TrendAnalysisConfig,
    ) -> TrendDirection:
        volatility = TrendAnalyzer._volatility(values)

        if volatility is not None and volatility >= config.volatility_threshold:
            if abs(correlation) < 0.35:
                return TrendDirection.VOLATILE

        if abs(correlation) < 0.2 or math.isclose(slope, 0.0, abs_tol=1e-9):
            return TrendDirection.FLAT

        if slope > 0:
            return TrendDirection.UP

        if slope < 0:
            return TrendDirection.DOWN

        return TrendDirection.UNKNOWN

    @staticmethod
    def _strength(correlation: float) -> TrendStrength:
        absolute = abs(correlation)

        if absolute >= 0.9:
            return TrendStrength.VERY_STRONG
        if absolute >= 0.7:
            return TrendStrength.STRONG
        if absolute >= 0.5:
            return TrendStrength.MODERATE
        if absolute >= 0.3:
            return TrendStrength.WEAK

        return TrendStrength.VERY_WEAK

    @staticmethod
    def _momentum(values: List[float], window: int) -> Optional[float]:
        if len(values) < window + 1:
            return None

        previous = values[-window - 1]
        current = values[-1]

        return TrendMath.percent_change(previous, current)

    @staticmethod
    def _acceleration(values: List[float], window: int) -> Optional[float]:
        if len(values) < (window * 2) + 1:
            return None

        previous_slice = values[-(window * 2):-window]
        recent_slice = values[-window:]

        previous_slope, _, _ = TrendMath.linear_regression(previous_slice)
        recent_slope, _, _ = TrendMath.linear_regression(recent_slice)

        return recent_slope - previous_slope

    @staticmethod
    def _volatility(values: List[float]) -> Optional[float]:
        if len(values) < 2:
            return None

        mean = statistics.mean(values)
        std = statistics.stdev(values)

        if mean == 0:
            return None

        return abs(std / mean)

    @staticmethod
    def _breakout(
        values: List[float],
        config: TrendAnalysisConfig,
    ) -> BreakoutResult:
        if len(values) < config.long_window + 1:
            return BreakoutResult(
                direction=BreakoutDirection.NONE,
                value=values[-1],
                upper_bound=None,
                lower_bound=None,
                score=None,
                detected=False,
            )

        history = values[-config.long_window - 1:-1]
        current = values[-1]

        mean = statistics.mean(history)
        std = statistics.stdev(history) if len(history) > 1 else 0.0

        upper = mean + config.breakout_std_multiplier * std
        lower = mean - config.breakout_std_multiplier * std

        if std == 0:
            return BreakoutResult(
                direction=BreakoutDirection.NONE,
                value=current,
                upper_bound=upper,
                lower_bound=lower,
                score=0.0,
                detected=False,
            )

        score = (current - mean) / std

        if current > upper:
            return BreakoutResult(
                direction=BreakoutDirection.ABOVE,
                value=current,
                upper_bound=upper,
                lower_bound=lower,
                score=score,
                detected=True,
            )

        if current < lower:
            return BreakoutResult(
                direction=BreakoutDirection.BELOW,
                value=current,
                upper_bound=upper,
                lower_bound=lower,
                score=score,
                detected=True,
            )

        return BreakoutResult(
            direction=BreakoutDirection.NONE,
            value=current,
            upper_bound=upper,
            lower_bound=lower,
            score=score,
            detected=False,
        )

    @staticmethod
    def _regime_change(
        values: List[float],
        config: TrendAnalysisConfig,
    ) -> RegimeChangeResult:
        if len(values) < config.long_window * 2:
            return RegimeChangeResult(
                detected=False,
                previous_mean=None,
                recent_mean=None,
                relative_change=None,
                reason="Dados insuficientes para mudança de regime.",
            )

        previous = values[-config.long_window * 2:-config.long_window]
        recent = values[-config.long_window:]

        previous_mean = statistics.mean(previous)
        recent_mean = statistics.mean(recent)

        relative_change = TrendMath.relative_change(previous_mean, recent_mean)

        if relative_change is None:
            return RegimeChangeResult(
                detected=False,
                previous_mean=previous_mean,
                recent_mean=recent_mean,
                relative_change=None,
                reason="Média anterior zero; mudança relativa indefinida.",
            )

        detected = abs(relative_change) >= config.regime_change_threshold

        return RegimeChangeResult(
            detected=detected,
            previous_mean=previous_mean,
            recent_mean=recent_mean,
            relative_change=relative_change,
            reason=(
                f"Mudança relativa={relative_change:.4f}, "
                f"threshold={config.regime_change_threshold}"
            ),
        )

    @staticmethod
    def _seasonality(
        values: List[float],
        config: TrendAnalysisConfig,
    ) -> SeasonalityResult:
        period = config.seasonality_period

        if not period:
            return SeasonalityResult(
                detected=False,
                period=None,
                seasonal_strength=None,
                reason="Período sazonal não configurado.",
            )

        if len(values) < period * 2:
            return SeasonalityResult(
                detected=False,
                period=period,
                seasonal_strength=None,
                reason="Dados insuficientes para sazonalidade.",
            )

        overall_mean = statistics.mean(values)

        if overall_mean == 0:
            return SeasonalityResult(
                detected=False,
                period=period,
                seasonal_strength=None,
                reason="Média geral zero.",
            )

        buckets: Dict[int, List[float]] = {index: [] for index in range(period)}

        for index, value in enumerate(values):
            buckets[index % period].append(value)

        seasonal_indices: Dict[int, float] = {}

        for bucket, bucket_values in buckets.items():
            if bucket_values:
                seasonal_indices[bucket] = statistics.mean(bucket_values) / overall_mean

        if not seasonal_indices:
            return SeasonalityResult(
                detected=False,
                period=period,
                seasonal_strength=None,
                reason="Sem índices sazonais calculáveis.",
            )

        strength = statistics.stdev(seasonal_indices.values()) if len(seasonal_indices) > 1 else 0.0

        return SeasonalityResult(
            detected=strength >= 0.10,
            period=period,
            seasonal_strength=strength,
            seasonal_indices=seasonal_indices,
            reason=f"Força sazonal={strength:.4f}",
        )

    @staticmethod
    def _build_insights(
        metric_id: str,
        direction: TrendDirection,
        strength: TrendStrength,
        momentum: Optional[float],
        acceleration: Optional[float],
        volatility: Optional[float],
        breakout: BreakoutResult,
        regime_change: RegimeChangeResult,
        seasonality: SeasonalityResult,
    ) -> List[TrendInsight]:
        insights: List[TrendInsight] = []

        if direction == TrendDirection.UP:
            insights.append(
                TrendInsight(
                    insight_id=str(uuid.uuid4()),
                    title="Tendência de alta",
                    message=f"A métrica {metric_id} apresenta tendência de alta com força {strength.value}.",
                    severity=TrendInsightSeverity.POSITIVE,
                    metric_id=metric_id,
                    recommendation="Investigar os fatores positivos e reforçar os canais que impulsionam o crescimento.",
                )
            )

        elif direction == TrendDirection.DOWN:
            insights.append(
                TrendInsight(
                    insight_id=str(uuid.uuid4()),
                    title="Tendência de queda",
                    message=f"A métrica {metric_id} apresenta tendência de queda com força {strength.value}.",
                    severity=TrendInsightSeverity.WARNING,
                    metric_id=metric_id,
                    recommendation="Avaliar causas operacionais, comerciais, sazonalidade e mudanças recentes.",
                )
            )

        elif direction == TrendDirection.VOLATILE:
            insights.append(
                TrendInsight(
                    insight_id=str(uuid.uuid4()),
                    title="Alta volatilidade",
                    message=f"A métrica {metric_id} está volátil e sem direção linear clara.",
                    severity=TrendInsightSeverity.WARNING,
                    metric_id=metric_id,
                    recommendation="Usar suavização, segmentar por dimensão e investigar eventos extremos.",
                )
            )

        if momentum is not None and abs(momentum) >= 20:
            insights.append(
                TrendInsight(
                    insight_id=str(uuid.uuid4()),
                    title="Momentum relevante",
                    message=f"Momentum recente de {momentum:.2f}% identificado em {metric_id}.",
                    severity=TrendInsightSeverity.POSITIVE if momentum > 0 else TrendInsightSeverity.WARNING,
                    metric_id=metric_id,
                    recommendation="Comparar com campanhas, rupturas, preço, estoque e sazonalidade.",
                )
            )

        if acceleration is not None and abs(acceleration) > 0:
            insights.append(
                TrendInsight(
                    insight_id=str(uuid.uuid4()),
                    title="Mudança de aceleração",
                    message=f"Aceleração recente calculada em {acceleration:.4f}.",
                    severity=TrendInsightSeverity.INFO,
                    metric_id=metric_id,
                    recommendation="Monitorar se a aceleração se mantém nos próximos ciclos.",
                )
            )

        if volatility is not None and volatility >= 0.25:
            insights.append(
                TrendInsight(
                    insight_id=str(uuid.uuid4()),
                    title="Volatilidade elevada",
                    message=f"Coeficiente de variação aproximado de {volatility:.4f}.",
                    severity=TrendInsightSeverity.WARNING,
                    metric_id=metric_id,
                    recommendation="Aplicar análise por loja/produto/canal e revisar outliers.",
                )
            )

        if breakout.detected:
            insights.append(
                TrendInsight(
                    insight_id=str(uuid.uuid4()),
                    title="Breakout detectado",
                    message=f"Valor rompeu limite {breakout.direction.value} com score {breakout.score:.4f}.",
                    severity=(
                        TrendInsightSeverity.POSITIVE
                        if breakout.direction == BreakoutDirection.ABOVE
                        else TrendInsightSeverity.CRITICAL
                    ),
                    metric_id=metric_id,
                    recommendation="Validar se houve evento de negócio, falha de dados ou mudança estrutural.",
                )
            )

        if regime_change.detected:
            insights.append(
                TrendInsight(
                    insight_id=str(uuid.uuid4()),
                    title="Mudança de regime",
                    message=regime_change.reason,
                    severity=TrendInsightSeverity.WARNING,
                    metric_id=metric_id,
                    recommendation="Revisar modelo de baseline e separar períodos antes/depois da mudança.",
                )
            )

        if seasonality.detected:
            insights.append(
                TrendInsight(
                    insight_id=str(uuid.uuid4()),
                    title="Sazonalidade identificada",
                    message=f"Sazonalidade detectada com período {seasonality.period}.",
                    severity=TrendInsightSeverity.INFO,
                    metric_id=metric_id,
                    recommendation="Usar modelos e alertas que considerem o padrão sazonal.",
                )
            )

        if not insights:
            insights.append(
                TrendInsight(
                    insight_id=str(uuid.uuid4()),
                    title="Tendência estável",
                    message=f"Nenhuma tendência relevante foi identificada para {metric_id}.",
                    severity=TrendInsightSeverity.INFO,
                    metric_id=metric_id,
                    recommendation="Manter monitoramento periódico.",
                )
            )

        return insights


# =============================================================================
# Engine
# =============================================================================

class TrendAnalysisEngine:
    def __init__(
        self,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
    ) -> None:
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()

    def analyze(
        self,
        points: List[TrendPoint],
        config: TrendAnalysisConfig,
        context: Optional[TrendContext] = None,
    ) -> TrendAnalysisResult:
        context = context or TrendContext()

        try:
            result = TrendAnalyzer.analyze(points, config, context)

            self._audit("trend.analysis.completed", result)
            self._emit_metrics(result)

            return result

        except Exception as exc:
            logger.exception("Erro na análise de tendência")

            result = TrendAnalysisResult(
                analysis_id=str(uuid.uuid4()),
                metric_id=config.metric_id,
                status=TrendStatus.FAILED,
                computed_at=datetime.now(timezone.utc),
                direction=TrendDirection.UNKNOWN,
                strength=TrendStrength.VERY_WEAK,
                slope=None,
                correlation=None,
                momentum=None,
                acceleration=None,
                volatility=None,
                short_moving_average=None,
                long_moving_average=None,
                breakout=None,
                regime_change=None,
                seasonality=None,
                insights=[],
                points_count=len(points),
                context=context,
                error=str(exc),
            )

            self._audit("trend.analysis.failed", result)
            self._emit_metrics(result)

            return result

    def analyze_many(
        self,
        series: Dict[str, List[TrendPoint]],
        base_config: Optional[TrendAnalysisConfig] = None,
        context: Optional[TrendContext] = None,
    ) -> Dict[str, TrendAnalysisResult]:
        results: Dict[str, TrendAnalysisResult] = {}

        for metric_id, points in series.items():
            config = base_config or TrendAnalysisConfig(metric_id=metric_id)
            if config.metric_id != metric_id:
                config = TrendAnalysisConfig(
                    **{
                        **asdict(config),
                        "metric_id": metric_id,
                    }
                )

            results[metric_id] = self.analyze(points, config, context=context)

        return results

    def compare_periods(
        self,
        metric_id: str,
        previous_points: List[TrendPoint],
        current_points: List[TrendPoint],
        context: Optional[TrendContext] = None,
    ) -> Dict[str, Any]:
        previous_values = TrendMath.values(TrendMath.clean_points(previous_points))
        current_values = TrendMath.values(TrendMath.clean_points(current_points))

        if not previous_values or not current_values:
            raise TrendValidationError("Períodos precisam conter dados válidos")

        previous_mean = statistics.mean(previous_values)
        current_mean = statistics.mean(current_values)
        relative_change = TrendMath.relative_change(previous_mean, current_mean)

        payload = {
            "metric_id": metric_id,
            "previous_mean": previous_mean,
            "current_mean": current_mean,
            "relative_change": relative_change,
            "percentage_change": relative_change * 100 if relative_change is not None else None,
            "previous_points": len(previous_values),
            "current_points": len(current_values),
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": "trend.periods.compared",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "tenant_id": context.tenant_id if context else None,
                "domain": context.domain if context else None,
                "correlation_id": context.correlation_id if context else None,
                "details": payload,
            }
        )

        return payload

    def export_result_json(self, result: TrendAnalysisResult) -> str:
        return json.dumps(
            self._result_to_dict(result),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def export_many_json(self, results: Dict[str, TrendAnalysisResult]) -> str:
        return json.dumps(
            {
                key: self._result_to_dict(value)
                for key, value in results.items()
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def _audit(self, event_type: str, result: TrendAnalysisResult) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "analysis_id": result.analysis_id,
                "metric_id": result.metric_id,
                "status": result.status.value,
                "direction": result.direction.value,
                "strength": result.strength.value,
                "points_count": result.points_count,
                "tenant_id": result.context.tenant_id if result.context else None,
                "domain": result.context.domain if result.context else None,
                "user_id": result.context.user_id if result.context else None,
                "correlation_id": result.context.correlation_id if result.context else None,
                "error": result.error,
            }
        )

    def _emit_metrics(self, result: TrendAnalysisResult) -> None:
        tags = {
            "metric_id": result.metric_id,
            "status": result.status.value,
            "direction": result.direction.value,
            "strength": result.strength.value,
        }

        self.metrics_backend.increment("trend.analysis.total", tags=tags)

        if result.slope is not None:
            self.metrics_backend.gauge("trend.analysis.slope", result.slope, tags=tags)

        if result.momentum is not None:
            self.metrics_backend.gauge("trend.analysis.momentum", result.momentum, tags=tags)

        if result.volatility is not None:
            self.metrics_backend.gauge("trend.analysis.volatility", result.volatility, tags=tags)

    @staticmethod
    def _result_to_dict(result: TrendAnalysisResult) -> Dict[str, Any]:
        data = asdict(result)
        data["status"] = result.status.value
        data["computed_at"] = result.computed_at.isoformat()
        data["direction"] = result.direction.value
        data["strength"] = result.strength.value

        if data.get("short_moving_average"):
            data["short_moving_average"]["average_type"] = result.short_moving_average.average_type.value

        if data.get("long_moving_average"):
            data["long_moving_average"]["average_type"] = result.long_moving_average.average_type.value

        if data.get("breakout"):
            data["breakout"]["direction"] = result.breakout.direction.value

        for insight in data.get("insights", []):
            insight["severity"] = insight["severity"].value

        return data


# =============================================================================
# Factory
# =============================================================================

def create_default_trend_engine() -> TrendAnalysisEngine:
    return TrendAnalysisEngine()


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    from datetime import timedelta

    now = datetime.now(timezone.utc)

    points = [
        TrendPoint(
            timestamp=now - timedelta(days=20 - index),
            value=100 + index * 5 + (15 if index > 15 else 0),
            dimensions={"store_id": "store-a"},
        )
        for index in range(21)
    ]

    engine = create_default_trend_engine()

    result = engine.analyze(
        points=points,
        config=TrendAnalysisConfig(
            metric_id="gross_revenue",
            min_points=7,
            short_window=3,
            long_window=7,
            momentum_window=3,
            seasonality_period=7,
        ),
        context=TrendContext(
            tenant_id="tenant-default",
            domain="sales",
            user_id="analytics-admin",
            correlation_id="corr-trend-001",
        ),
    )

    print(engine.export_result_json(result))


if __name__ == "__main__":
    example_usage()
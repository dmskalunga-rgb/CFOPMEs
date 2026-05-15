"""
data/analytics/forecasting_engine.py

Enterprise Forecasting Engine.

Recursos:
- Forecast de séries temporais
- Modelos: naive, seasonal naive, moving average, weighted moving average,
  exponential smoothing e linear trend
- Backtesting
- Métricas: MAE, MAPE, RMSE, sMAPE, bias
- Intervalos de confiança simples
- Multi-tenant
- Auditoria e métricas plugáveis
- Registro de modelos
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
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class ForecastModelType(str, Enum):
    NAIVE = "naive"
    SEASONAL_NAIVE = "seasonal_naive"
    MOVING_AVERAGE = "moving_average"
    WEIGHTED_MOVING_AVERAGE = "weighted_moving_average"
    EXPONENTIAL_SMOOTHING = "exponential_smoothing"
    LINEAR_TREND = "linear_trend"


class ForecastFrequency(str, Enum):
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ForecastStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    INSUFFICIENT_DATA = "insufficient_data"


class ForecastMetric(str, Enum):
    MAE = "mae"
    MAPE = "mape"
    RMSE = "rmse"
    SMAPE = "smape"
    BIAS = "bias"


# =============================================================================
# Exceptions
# =============================================================================

class ForecastingError(Exception):
    """Erro base do forecasting engine."""


class ForecastValidationError(ForecastingError):
    """Erro de validação."""


class ForecastModelNotFound(ForecastingError):
    """Modelo não encontrado."""


class ForecastExecutionError(ForecastingError):
    """Erro de execução de forecast."""


# =============================================================================
# Protocols
# =============================================================================

class ForecastDataProvider(Protocol):
    def fetch_series(
        self,
        series_id: str,
        context: Optional["ForecastContext"] = None,
    ) -> List["TimeSeriesPoint"]:
        ...


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

class InMemoryForecastDataProvider:
    def __init__(
        self,
        series: Optional[Dict[str, List["TimeSeriesPoint"]]] = None,
    ) -> None:
        self.series = series or {}

    def fetch_series(
        self,
        series_id: str,
        context: Optional["ForecastContext"] = None,
    ) -> List["TimeSeriesPoint"]:
        points = list(self.series.get(series_id, []))

        if context and context.tenant_id:
            points = [
                point for point in points
                if point.tenant_id in {None, context.tenant_id}
            ]

        return sorted(points, key=lambda point: point.timestamp)


class LoggingAuditBackend:
    def write_event(self, event: Dict[str, Any]) -> None:
        logger.info(
            "forecasting_audit=%s",
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
class ForecastContext:
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    user_id: Optional[str] = None
    correlation_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TimeSeriesPoint:
    timestamp: datetime
    value: float
    tenant_id: Optional[str] = None
    dimensions: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ForecastModelDefinition:
    model_id: str
    name: str
    model_type: ForecastModelType
    frequency: ForecastFrequency
    horizon: int
    min_points: int = 10
    seasonal_period: int = 7
    moving_window: int = 7
    smoothing_alpha: float = 0.3
    confidence_level: float = 0.95
    enabled: bool = True
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    description: str = ""
    tags: Dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.model_id:
            raise ForecastValidationError("model_id é obrigatório")

        if not self.name:
            raise ForecastValidationError("name é obrigatório")

        if self.horizon <= 0:
            raise ForecastValidationError("horizon precisa ser maior que zero")

        if self.min_points < 2:
            raise ForecastValidationError("min_points precisa ser >= 2")

        if self.seasonal_period <= 0:
            raise ForecastValidationError("seasonal_period precisa ser maior que zero")

        if self.moving_window <= 0:
            raise ForecastValidationError("moving_window precisa ser maior que zero")

        if not 0 < self.smoothing_alpha <= 1:
            raise ForecastValidationError("smoothing_alpha precisa estar entre 0 e 1")

        if not 0 < self.confidence_level < 1:
            raise ForecastValidationError("confidence_level precisa estar entre 0 e 1")


@dataclass
class ForecastPoint:
    timestamp: datetime
    predicted_value: float
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    confidence_level: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ForecastResult:
    forecast_id: str
    series_id: str
    model_id: str
    model_type: ForecastModelType
    status: ForecastStatus
    generated_at: datetime
    points: List[ForecastPoint]
    training_points: int
    horizon: int
    frequency: ForecastFrequency
    error: Optional[str] = None
    metrics: Dict[str, float] = field(default_factory=dict)
    context: Optional[ForecastContext] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestResult:
    backtest_id: str
    series_id: str
    model_id: str
    model_type: ForecastModelType
    train_size: int
    test_size: int
    predictions: List[Tuple[datetime, float, float]]
    metrics: Dict[str, float]
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# Repository
# =============================================================================

class ForecastModelRepository:
    def __init__(
        self,
        models: Optional[List[ForecastModelDefinition]] = None,
    ) -> None:
        self._models: Dict[str, ForecastModelDefinition] = {}

        for model in models or []:
            self.save(model)

    def save(self, model: ForecastModelDefinition) -> None:
        model.validate()
        self._models[model.model_id] = model

    def get(self, model_id: str) -> ForecastModelDefinition:
        model = self._models.get(model_id)
        if not model:
            raise ForecastModelNotFound(model_id)
        return model

    def list_all(
        self,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
        enabled_only: bool = True,
    ) -> List[ForecastModelDefinition]:
        models = list(self._models.values())

        if enabled_only:
            models = [model for model in models if model.enabled]

        if tenant_id is not None:
            models = [
                model for model in models
                if model.tenant_id is None or model.tenant_id == tenant_id
            ]

        if domain is not None:
            models = [
                model for model in models
                if model.domain is None or model.domain == domain
            ]

        return models


# =============================================================================
# Forecast Algorithms
# =============================================================================

class ForecastAlgorithms:
    @staticmethod
    def predict(
        values: List[float],
        model: ForecastModelDefinition,
    ) -> List[float]:
        if len(values) < model.min_points:
            raise ForecastValidationError(
                f"Dados insuficientes. Recebido={len(values)}, mínimo={model.min_points}"
            )

        if model.model_type == ForecastModelType.NAIVE:
            return ForecastAlgorithms._naive(values, model.horizon)

        if model.model_type == ForecastModelType.SEASONAL_NAIVE:
            return ForecastAlgorithms._seasonal_naive(
                values,
                model.horizon,
                model.seasonal_period,
            )

        if model.model_type == ForecastModelType.MOVING_AVERAGE:
            return ForecastAlgorithms._moving_average(
                values,
                model.horizon,
                model.moving_window,
            )

        if model.model_type == ForecastModelType.WEIGHTED_MOVING_AVERAGE:
            return ForecastAlgorithms._weighted_moving_average(
                values,
                model.horizon,
                model.moving_window,
            )

        if model.model_type == ForecastModelType.EXPONENTIAL_SMOOTHING:
            return ForecastAlgorithms._exponential_smoothing(
                values,
                model.horizon,
                model.smoothing_alpha,
            )

        if model.model_type == ForecastModelType.LINEAR_TREND:
            return ForecastAlgorithms._linear_trend(values, model.horizon)

        raise ForecastExecutionError(f"Tipo de modelo não suportado: {model.model_type}")

    @staticmethod
    def _naive(values: List[float], horizon: int) -> List[float]:
        return [values[-1]] * horizon

    @staticmethod
    def _seasonal_naive(
        values: List[float],
        horizon: int,
        seasonal_period: int,
    ) -> List[float]:
        if len(values) < seasonal_period:
            raise ForecastValidationError("Dados insuficientes para seasonal naive")

        season = values[-seasonal_period:]
        return [season[index % seasonal_period] for index in range(horizon)]

    @staticmethod
    def _moving_average(
        values: List[float],
        horizon: int,
        window: int,
    ) -> List[float]:
        history = list(values)
        predictions: List[float] = []

        for _ in range(horizon):
            recent = history[-window:]
            prediction = sum(recent) / len(recent)
            predictions.append(prediction)
            history.append(prediction)

        return predictions

    @staticmethod
    def _weighted_moving_average(
        values: List[float],
        horizon: int,
        window: int,
    ) -> List[float]:
        history = list(values)
        predictions: List[float] = []

        for _ in range(horizon):
            recent = history[-window:]
            weights = list(range(1, len(recent) + 1))
            prediction = sum(v * w for v, w in zip(recent, weights)) / sum(weights)
            predictions.append(prediction)
            history.append(prediction)

        return predictions

    @staticmethod
    def _exponential_smoothing(
        values: List[float],
        horizon: int,
        alpha: float,
    ) -> List[float]:
        smoothed = values[0]

        for value in values[1:]:
            smoothed = alpha * value + (1 - alpha) * smoothed

        return [smoothed] * horizon

    @staticmethod
    def _linear_trend(values: List[float], horizon: int) -> List[float]:
        n = len(values)
        x_values = list(range(n))
        x_mean = sum(x_values) / n
        y_mean = sum(values) / n

        numerator = sum(
            (x - x_mean) * (y - y_mean)
            for x, y in zip(x_values, values)
        )
        denominator = sum((x - x_mean) ** 2 for x in x_values)

        slope = numerator / denominator if denominator else 0.0
        intercept = y_mean - slope * x_mean

        return [
            intercept + slope * (n + step)
            for step in range(1, horizon + 1)
        ]


# =============================================================================
# Metrics
# =============================================================================

class ForecastMetricsCalculator:
    @staticmethod
    def calculate(actual: List[float], predicted: List[float]) -> Dict[str, float]:
        if len(actual) != len(predicted):
            raise ForecastValidationError("actual e predicted precisam ter o mesmo tamanho")

        if not actual:
            return {}

        errors = [a - p for a, p in zip(actual, predicted)]
        abs_errors = [abs(error) for error in errors]
        squared_errors = [error ** 2 for error in errors]

        mae = sum(abs_errors) / len(abs_errors)
        rmse = math.sqrt(sum(squared_errors) / len(squared_errors))

        mape_values = [
            abs((a - p) / a) * 100
            for a, p in zip(actual, predicted)
            if a != 0
        ]

        smape_values = [
            (abs(p - a) / ((abs(a) + abs(p)) / 2)) * 100
            for a, p in zip(actual, predicted)
            if (abs(a) + abs(p)) != 0
        ]

        bias = sum(errors) / len(errors)

        return {
            ForecastMetric.MAE.value: mae,
            ForecastMetric.RMSE.value: rmse,
            ForecastMetric.MAPE.value: (
                sum(mape_values) / len(mape_values)
                if mape_values
                else 0.0
            ),
            ForecastMetric.SMAPE.value: (
                sum(smape_values) / len(smape_values)
                if smape_values
                else 0.0
            ),
            ForecastMetric.BIAS.value: bias,
        }


# =============================================================================
# Engine
# =============================================================================

class ForecastingEngine:
    def __init__(
        self,
        model_repository: ForecastModelRepository,
        data_provider: Optional[ForecastDataProvider] = None,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
    ) -> None:
        self.model_repository = model_repository
        self.data_provider = data_provider or InMemoryForecastDataProvider()
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()

    def forecast(
        self,
        series_id: str,
        model_id: str,
        context: Optional[ForecastContext] = None,
    ) -> ForecastResult:
        context = context or ForecastContext()
        model = self.model_repository.get(model_id)

        try:
            self._validate_model_access(model, context)

            series = self.data_provider.fetch_series(series_id, context)
            values = [point.value for point in series if math.isfinite(point.value)]

            if len(values) < model.min_points:
                return self._failed_result(
                    series_id=series_id,
                    model=model,
                    context=context,
                    status=ForecastStatus.INSUFFICIENT_DATA,
                    error=f"Dados insuficientes: {len(values)} pontos",
                    training_points=len(values),
                )

            predictions = ForecastAlgorithms.predict(values, model)
            residual_std = self._residual_std(values, model)

            forecast_points = self._build_forecast_points(
                series=series,
                predictions=predictions,
                model=model,
                residual_std=residual_std,
            )

            result = ForecastResult(
                forecast_id=str(uuid.uuid4()),
                series_id=series_id,
                model_id=model.model_id,
                model_type=model.model_type,
                status=ForecastStatus.SUCCESS,
                generated_at=datetime.now(timezone.utc),
                points=forecast_points,
                training_points=len(values),
                horizon=model.horizon,
                frequency=model.frequency,
                context=context,
                metadata={
                    "confidence_level": model.confidence_level,
                    "residual_std": residual_std,
                    "domain": model.domain,
                },
            )

            self._audit("forecast.generated", result)
            self._emit_success_metrics(result)

            return result

        except Exception as exc:
            logger.exception("Erro ao gerar forecast")
            result = self._failed_result(
                series_id=series_id,
                model=model,
                context=context,
                status=ForecastStatus.FAILED,
                error=str(exc),
                training_points=0,
            )
            self._audit("forecast.failed", result)
            self.metrics_backend.increment(
                "forecasting.forecast.failed",
                tags={"model_id": model_id, "series_id": series_id},
            )
            return result

    def backtest(
        self,
        series_id: str,
        model_id: str,
        test_size: int,
        context: Optional[ForecastContext] = None,
    ) -> BacktestResult:
        context = context or ForecastContext()
        model = self.model_repository.get(model_id)
        self._validate_model_access(model, context)

        series = self.data_provider.fetch_series(series_id, context)
        values = [point.value for point in series if math.isfinite(point.value)]

        if test_size <= 0:
            raise ForecastValidationError("test_size precisa ser maior que zero")

        if len(values) <= test_size + model.min_points:
            raise ForecastValidationError("Dados insuficientes para backtest")

        train_values = values[:-test_size]
        test_values = values[-test_size:]
        test_points = series[-test_size:]

        backtest_model = ForecastModelDefinition(
            **{
                **asdict(model),
                "horizon": test_size,
            }
        )

        predictions = ForecastAlgorithms.predict(train_values, backtest_model)
        metrics = ForecastMetricsCalculator.calculate(test_values, predictions)

        prediction_rows = [
            (point.timestamp, actual, predicted)
            for point, actual, predicted in zip(test_points, test_values, predictions)
        ]

        result = BacktestResult(
            backtest_id=str(uuid.uuid4()),
            series_id=series_id,
            model_id=model.model_id,
            model_type=model.model_type,
            train_size=len(train_values),
            test_size=test_size,
            predictions=prediction_rows,
            metrics=metrics,
        )

        self._audit_backtest(result, context)

        for metric_name, value in metrics.items():
            self.metrics_backend.gauge(
                f"forecasting.backtest.{metric_name}",
                value,
                tags={"model_id": model.model_id, "series_id": series_id},
            )

        return result

    def forecast_many(
        self,
        requests: Iterable[Tuple[str, str]],
        context: Optional[ForecastContext] = None,
    ) -> List[ForecastResult]:
        return [
            self.forecast(series_id=series_id, model_id=model_id, context=context)
            for series_id, model_id in requests
        ]

    def export_result_json(self, result: ForecastResult) -> str:
        return json.dumps(
            self._forecast_result_to_dict(result),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def export_backtest_json(self, result: BacktestResult) -> str:
        return json.dumps(
            self._backtest_result_to_dict(result),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    @staticmethod
    def _validate_model_access(
        model: ForecastModelDefinition,
        context: ForecastContext,
    ) -> None:
        if not model.enabled:
            raise ForecastValidationError(f"Modelo desabilitado: {model.model_id}")

        if model.tenant_id and context.tenant_id and model.tenant_id != context.tenant_id:
            raise ForecastValidationError("Tenant inválido para o modelo")

        if model.domain and context.domain and model.domain != context.domain:
            raise ForecastValidationError("Domínio inválido para o modelo")

    def _failed_result(
        self,
        series_id: str,
        model: ForecastModelDefinition,
        context: ForecastContext,
        status: ForecastStatus,
        error: str,
        training_points: int,
    ) -> ForecastResult:
        return ForecastResult(
            forecast_id=str(uuid.uuid4()),
            series_id=series_id,
            model_id=model.model_id,
            model_type=model.model_type,
            status=status,
            generated_at=datetime.now(timezone.utc),
            points=[],
            training_points=training_points,
            horizon=model.horizon,
            frequency=model.frequency,
            error=error,
            context=context,
        )

    def _build_forecast_points(
        self,
        series: List[TimeSeriesPoint],
        predictions: List[float],
        model: ForecastModelDefinition,
        residual_std: float,
    ) -> List[ForecastPoint]:
        last_timestamp = series[-1].timestamp
        timestamps = [
            self._next_timestamp(last_timestamp, model.frequency, step)
            for step in range(1, len(predictions) + 1)
        ]

        z_value = self._z_for_confidence(model.confidence_level)

        points: List[ForecastPoint] = []

        for index, prediction in enumerate(predictions):
            uncertainty = z_value * residual_std * math.sqrt(index + 1)

            points.append(
                ForecastPoint(
                    timestamp=timestamps[index],
                    predicted_value=prediction,
                    lower_bound=prediction - uncertainty,
                    upper_bound=prediction + uncertainty,
                    confidence_level=model.confidence_level,
                    metadata={"step": index + 1},
                )
            )

        return points

    def _residual_std(
        self,
        values: List[float],
        model: ForecastModelDefinition,
    ) -> float:
        if len(values) <= model.min_points:
            return 0.0

        test_size = min(max(3, model.horizon), max(1, len(values) // 4))

        if len(values) <= test_size + model.min_points:
            return statistics.stdev(values) if len(values) > 1 else 0.0

        train = values[:-test_size]
        actual = values[-test_size:]

        temp_model = ForecastModelDefinition(
            **{
                **asdict(model),
                "horizon": test_size,
            }
        )

        try:
            predicted = ForecastAlgorithms.predict(train, temp_model)
            errors = [a - p for a, p in zip(actual, predicted)]
            return statistics.stdev(errors) if len(errors) > 1 else abs(errors[0])
        except Exception:
            return statistics.stdev(values) if len(values) > 1 else 0.0

    @staticmethod
    def _next_timestamp(
        last_timestamp: datetime,
        frequency: ForecastFrequency,
        step: int,
    ) -> datetime:
        if frequency == ForecastFrequency.HOURLY:
            return last_timestamp + timedelta(hours=step)

        if frequency == ForecastFrequency.DAILY:
            return last_timestamp + timedelta(days=step)

        if frequency == ForecastFrequency.WEEKLY:
            return last_timestamp + timedelta(weeks=step)

        if frequency == ForecastFrequency.MONTHLY:
            return last_timestamp + timedelta(days=30 * step)

        raise ForecastValidationError(f"Frequência não suportada: {frequency}")

    @staticmethod
    def _z_for_confidence(confidence_level: float) -> float:
        if confidence_level >= 0.99:
            return 2.576
        if confidence_level >= 0.95:
            return 1.96
        if confidence_level >= 0.90:
            return 1.645
        return 1.28

    def _audit(self, event_type: str, result: ForecastResult) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "forecast_id": result.forecast_id,
                "series_id": result.series_id,
                "model_id": result.model_id,
                "model_type": result.model_type.value,
                "status": result.status.value,
                "horizon": result.horizon,
                "training_points": result.training_points,
                "tenant_id": result.context.tenant_id if result.context else None,
                "domain": result.context.domain if result.context else None,
                "correlation_id": result.context.correlation_id if result.context else None,
                "error": result.error,
            }
        )

    def _audit_backtest(
        self,
        result: BacktestResult,
        context: ForecastContext,
    ) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": "forecast.backtest.generated",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "backtest_id": result.backtest_id,
                "series_id": result.series_id,
                "model_id": result.model_id,
                "model_type": result.model_type.value,
                "train_size": result.train_size,
                "test_size": result.test_size,
                "metrics": result.metrics,
                "tenant_id": context.tenant_id,
                "domain": context.domain,
                "correlation_id": context.correlation_id,
            }
        )

    def _emit_success_metrics(self, result: ForecastResult) -> None:
        tags = {
            "series_id": result.series_id,
            "model_id": result.model_id,
            "model_type": result.model_type.value,
            "status": result.status.value,
        }

        self.metrics_backend.increment("forecasting.forecast.generated", tags=tags)
        self.metrics_backend.gauge(
            "forecasting.forecast.training_points",
            float(result.training_points),
            tags=tags,
        )
        self.metrics_backend.gauge(
            "forecasting.forecast.horizon",
            float(result.horizon),
            tags=tags,
        )

    @staticmethod
    def _forecast_result_to_dict(result: ForecastResult) -> Dict[str, Any]:
        data = asdict(result)

        data["model_type"] = result.model_type.value
        data["status"] = result.status.value
        data["frequency"] = result.frequency.value
        data["generated_at"] = result.generated_at.isoformat()

        for point in data["points"]:
            point["timestamp"] = point["timestamp"].isoformat()

        return data

    @staticmethod
    def _backtest_result_to_dict(result: BacktestResult) -> Dict[str, Any]:
        return {
            "backtest_id": result.backtest_id,
            "series_id": result.series_id,
            "model_id": result.model_id,
            "model_type": result.model_type.value,
            "train_size": result.train_size,
            "test_size": result.test_size,
            "metrics": result.metrics,
            "generated_at": result.generated_at.isoformat(),
            "predictions": [
                {
                    "timestamp": timestamp.isoformat(),
                    "actual": actual,
                    "predicted": predicted,
                    "error": actual - predicted,
                }
                for timestamp, actual, predicted in result.predictions
            ],
        }


# =============================================================================
# Default Models
# =============================================================================

def build_default_forecast_models() -> List[ForecastModelDefinition]:
    return [
        ForecastModelDefinition(
            model_id="daily-sales-moving-average",
            name="Daily Sales Moving Average",
            model_type=ForecastModelType.MOVING_AVERAGE,
            frequency=ForecastFrequency.DAILY,
            horizon=14,
            min_points=14,
            moving_window=7,
            confidence_level=0.95,
            domain="sales",
            tags={"business": "true", "sales": "true"},
        ),
        ForecastModelDefinition(
            model_id="daily-sales-seasonal-naive",
            name="Daily Sales Seasonal Naive",
            model_type=ForecastModelType.SEASONAL_NAIVE,
            frequency=ForecastFrequency.DAILY,
            horizon=14,
            min_points=21,
            seasonal_period=7,
            confidence_level=0.95,
            domain="sales",
            tags={"seasonality": "weekly"},
        ),
        ForecastModelDefinition(
            model_id="daily-demand-linear-trend",
            name="Daily Demand Linear Trend",
            model_type=ForecastModelType.LINEAR_TREND,
            frequency=ForecastFrequency.DAILY,
            horizon=30,
            min_points=20,
            confidence_level=0.95,
            domain="operations",
            tags={"demand": "true"},
        ),
        ForecastModelDefinition(
            model_id="hourly-traffic-exp-smoothing",
            name="Hourly Traffic Exponential Smoothing",
            model_type=ForecastModelType.EXPONENTIAL_SMOOTHING,
            frequency=ForecastFrequency.HOURLY,
            horizon=24,
            min_points=48,
            smoothing_alpha=0.35,
            confidence_level=0.90,
            domain="digital",
            tags={"traffic": "true"},
        ),
    ]


def create_default_forecasting_engine(
    series: Optional[Dict[str, List[TimeSeriesPoint]]] = None,
) -> ForecastingEngine:
    return ForecastingEngine(
        model_repository=ForecastModelRepository(build_default_forecast_models()),
        data_provider=InMemoryForecastDataProvider(series or {}),
    )


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    now = datetime.now(timezone.utc)

    sales_series = [
        TimeSeriesPoint(
            timestamp=now - timedelta(days=29 - index),
            value=1000 + index * 25 + (80 if index % 7 in {5, 6} else 0),
            tenant_id="tenant-default",
            dimensions={"store_id": "store-a"},
        )
        for index in range(30)
    ]

    engine = create_default_forecasting_engine(
        series={"gross_revenue": sales_series}
    )

    context = ForecastContext(
        tenant_id="tenant-default",
        domain="sales",
        user_id="analytics-admin",
        correlation_id="corr-forecast-001",
    )

    result = engine.forecast(
        series_id="gross_revenue",
        model_id="daily-sales-seasonal-naive",
        context=context,
    )

    print(engine.export_result_json(result))

    backtest = engine.backtest(
        series_id="gross_revenue",
        model_id="daily-sales-moving-average",
        test_size=7,
        context=context,
    )

    print(engine.export_backtest_json(backtest))


if __name__ == "__main__":
    example_usage()
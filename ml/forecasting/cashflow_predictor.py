# ml/forecasting/cashflow_predictor.py
"""
Enterprise Cashflow Predictor.

Recursos:
- previsão de fluxo de caixa diário/semanal/mensal
- agregação por conta, centro de custo, categoria e moeda
- modelos estatísticos com fallback robusto
- sazonalidade semanal/mensal
- cenários pessimista/base/otimista
- intervalos de confiança
- backtesting
- detecção simples de anomalias
- persistência do modelo
- interface para batch/realtime
"""

from __future__ import annotations

import json
import math
import pickle
import statistics
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np


try:
    from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.preprocessing import StandardScaler
except Exception:  # pragma: no cover
    GradientBoostingRegressor = None
    RandomForestRegressor = None
    StandardScaler = None
    TimeSeriesSplit = None
    mean_absolute_error = None
    mean_squared_error = None
    r2_score = None


class CashflowError(RuntimeError):
    pass


class CashflowFrequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class CashflowDirection(str, Enum):
    INFLOW = "inflow"
    OUTFLOW = "outflow"


class ForecastScenario(str, Enum):
    PESSIMISTIC = "pessimistic"
    BASE = "base"
    OPTIMISTIC = "optimistic"


class ForecastModelType(str, Enum):
    GRADIENT_BOOSTING = "gradient_boosting"
    RANDOM_FOREST = "random_forest"
    MOVING_AVERAGE = "moving_average"


@dataclass(frozen=True)
class CashflowPredictorConfig:
    model_name: str = "enterprise_cashflow_predictor"
    model_version: str = "1.0.0"

    frequency: CashflowFrequency = CashflowFrequency.DAILY
    horizon: int = 30

    lag_days: Sequence[int] = (1, 2, 3, 7, 14, 30)
    rolling_windows: Sequence[int] = (7, 14, 30)
    min_training_points: int = 60

    model_type: ForecastModelType = ForecastModelType.GRADIENT_BOOSTING
    confidence_z: float = 1.96

    pessimistic_multiplier: float = 0.85
    optimistic_multiplier: float = 1.15

    random_state: int = 42


@dataclass(frozen=True)
class CashflowTransaction:
    transaction_id: str
    transaction_date: str
    amount: float
    direction: CashflowDirection
    currency: str = "BRL"
    account_id: Optional[str] = None
    category: Optional[str] = None
    cost_center: Optional[str] = None
    description: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CashflowPoint:
    period: str
    inflow: float
    outflow: float
    net_cashflow: float
    opening_balance: Optional[float] = None
    closing_balance: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ForecastPoint:
    period: str
    scenario: ForecastScenario
    predicted_inflow: float
    predicted_outflow: float
    predicted_net_cashflow: float
    lower_bound: float
    upper_bound: float
    predicted_closing_balance: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BacktestResult:
    run_id: str
    generated_at: str
    folds: int
    metrics: Dict[str, float]
    fold_metrics: List[Dict[str, float]]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CashflowForecastReport:
    forecast_id: str
    model_name: str
    model_version: str
    generated_at: str
    frequency: CashflowFrequency
    horizon: int
    points: List[ForecastPoint]
    backtest: Optional[BacktestResult] = None
    anomalies: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class CashflowTrainingResult:
    model_name: str
    model_version: str
    trained_at: str
    samples: int
    feature_names: List[str]
    residual_std: float
    metrics: Dict[str, float]
    metadata: Dict[str, Any] = field(default_factory=dict)


class CashflowAggregator:
    def aggregate(
        self,
        transactions: Sequence[CashflowTransaction],
        frequency: CashflowFrequency,
        *,
        opening_balance: Optional[float] = None,
    ) -> List[CashflowPoint]:
        grouped: Dict[str, Dict[str, float]] = defaultdict(lambda: {"inflow": 0.0, "outflow": 0.0})

        for tx in transactions:
            period = self._period_key(tx.transaction_date, frequency)

            if tx.direction == CashflowDirection.INFLOW:
                grouped[period]["inflow"] += float(tx.amount)
            else:
                grouped[period]["outflow"] += abs(float(tx.amount))

        periods = sorted(grouped.keys())
        points: List[CashflowPoint] = []

        balance = opening_balance

        for period in periods:
            inflow = grouped[period]["inflow"]
            outflow = grouped[period]["outflow"]
            net = inflow - outflow

            opening = balance
            closing = None

            if balance is not None:
                closing = balance + net
                balance = closing

            points.append(
                CashflowPoint(
                    period=period,
                    inflow=inflow,
                    outflow=outflow,
                    net_cashflow=net,
                    opening_balance=opening,
                    closing_balance=closing,
                )
            )

        return points

    def fill_missing_periods(
        self,
        points: Sequence[CashflowPoint],
        frequency: CashflowFrequency,
    ) -> List[CashflowPoint]:
        if not points:
            return []

        by_period = {p.period: p for p in points}
        start = self._parse_period(points[0].period)
        end = self._parse_period(points[-1].period)

        result: List[CashflowPoint] = []
        current = start
        balance = points[0].opening_balance

        while current <= end:
            key = self._format_period(current, frequency)
            point = by_period.get(key)

            if point:
                result.append(point)
                balance = point.closing_balance
            else:
                opening = balance
                closing = balance
                result.append(
                    CashflowPoint(
                        period=key,
                        inflow=0.0,
                        outflow=0.0,
                        net_cashflow=0.0,
                        opening_balance=opening,
                        closing_balance=closing,
                        metadata={"imputed": True},
                    )
                )

            current = self._next_period(current, frequency)

        return result

    def future_periods(
        self,
        last_period: str,
        frequency: CashflowFrequency,
        horizon: int,
    ) -> List[str]:
        current = self._next_period(self._parse_period(last_period), frequency)
        periods: List[str] = []

        for _ in range(horizon):
            periods.append(self._format_period(current, frequency))
            current = self._next_period(current, frequency)

        return periods

    def _period_key(self, value: str, frequency: CashflowFrequency) -> str:
        dt = self._parse_date(value)

        if frequency == CashflowFrequency.DAILY:
            return dt.isoformat()

        if frequency == CashflowFrequency.WEEKLY:
            monday = dt - timedelta(days=dt.weekday())
            return monday.isoformat()

        if frequency == CashflowFrequency.MONTHLY:
            return f"{dt.year:04d}-{dt.month:02d}-01"

        raise CashflowError(f"Frequência inválida: {frequency}")

    @staticmethod
    def _parse_date(value: str) -> date:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()

    @staticmethod
    def _parse_period(value: str) -> date:
        return datetime.fromisoformat(value).date()

    @staticmethod
    def _format_period(value: date, frequency: CashflowFrequency) -> str:
        if frequency == CashflowFrequency.MONTHLY:
            return f"{value.year:04d}-{value.month:02d}-01"
        return value.isoformat()

    @staticmethod
    def _next_period(value: date, frequency: CashflowFrequency) -> date:
        if frequency == CashflowFrequency.DAILY:
            return value + timedelta(days=1)

        if frequency == CashflowFrequency.WEEKLY:
            return value + timedelta(days=7)

        if frequency == CashflowFrequency.MONTHLY:
            year = value.year + (value.month // 12)
            month = 1 if value.month == 12 else value.month + 1
            return date(year, month, 1)

        raise CashflowError(f"Frequência inválida: {frequency}")


class CashflowFeatureBuilder:
    def __init__(self, config: CashflowPredictorConfig) -> None:
        self.config = config
        self.feature_names: List[str] = []

    def build_supervised_dataset(
        self,
        points: Sequence[CashflowPoint],
        target: str,
    ) -> Tuple[np.ndarray, np.ndarray, List[str]]:
        values = np.asarray([getattr(p, target) for p in points], dtype=float)

        max_lag = max(self.config.lag_days or [1])
        rows: List[List[float]] = []
        y: List[float] = []

        self.feature_names = self._build_feature_names()

        for i in range(max_lag, len(points)):
            rows.append(self._features_for_index(points, values, i))
            y.append(values[i])

        return np.asarray(rows, dtype=float), np.asarray(y, dtype=float), self.feature_names

    def build_next_features(
        self,
        points: Sequence[CashflowPoint],
        predicted_values: Sequence[float],
    ) -> np.ndarray:
        combined_values = list(predicted_values)
        synthetic_points = list(points)

        for value in combined_values[len(points) :]:
            synthetic_points.append(
                CashflowPoint(
                    period="future",
                    inflow=value,
                    outflow=0.0,
                    net_cashflow=value,
                )
            )

        target_values = np.asarray(combined_values, dtype=float)
        index = len(target_values)

        features = self._features_from_values(target_values, index)
        return np.asarray([features], dtype=float)

    def _features_for_index(
        self,
        points: Sequence[CashflowPoint],
        values: np.ndarray,
        index: int,
    ) -> List[float]:
        base = self._features_from_values(values, index)
        dt = datetime.fromisoformat(points[index].period).date()

        base.extend(
            [
                float(dt.weekday()),
                float(dt.day),
                float(dt.month),
                1.0 if dt.weekday() >= 5 else 0.0,
                math.sin(2 * math.pi * dt.month / 12),
                math.cos(2 * math.pi * dt.month / 12),
            ]
        )

        return base

    def _features_from_values(self, values: Sequence[float], index: int) -> List[float]:
        features: List[float] = []

        for lag in self.config.lag_days:
            pos = index - lag
            features.append(float(values[pos]) if pos >= 0 else 0.0)

        for window in self.config.rolling_windows:
            start = max(0, index - window)
            window_values = list(values[start:index])

            if window_values:
                features.extend(
                    [
                        float(np.mean(window_values)),
                        float(np.std(window_values)),
                        float(np.min(window_values)),
                        float(np.max(window_values)),
                    ]
                )
            else:
                features.extend([0.0, 0.0, 0.0, 0.0])

        trend_window = list(values[max(0, index - 14):index])
        if len(trend_window) >= 2:
            features.append(float(trend_window[-1] - trend_window[0]))
        else:
            features.append(0.0)

        return features

    def _build_feature_names(self) -> List[str]:
        names: List[str] = []

        for lag in self.config.lag_days:
            names.append(f"lag_{lag}")

        for window in self.config.rolling_windows:
            names.extend(
                [
                    f"rolling_mean_{window}",
                    f"rolling_std_{window}",
                    f"rolling_min_{window}",
                    f"rolling_max_{window}",
                ]
            )

        names.extend(
            [
                "trend_14",
                "weekday",
                "day",
                "month",
                "is_weekend",
                "month_sin",
                "month_cos",
            ]
        )

        return names


class MovingAverageRegressor:
    def __init__(self, window: int = 7) -> None:
        self.window = window
        self.history: List[float] = []

    def fit(self, x: np.ndarray, y: np.ndarray) -> "MovingAverageRegressor":
        self.history = [float(v) for v in y]
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        if not self.history:
            return np.zeros(len(x), dtype=float)

        value = float(np.mean(self.history[-self.window:]))
        return np.asarray([value for _ in range(len(x))], dtype=float)


class CashflowAnomalyDetector:
    def detect(
        self,
        points: Sequence[CashflowPoint],
        *,
        z_threshold: float = 3.0,
    ) -> List[Dict[str, Any]]:
        values = np.asarray([p.net_cashflow for p in points], dtype=float)

        if len(values) < 10:
            return []

        mean = float(np.mean(values))
        std = float(np.std(values))

        if std <= 1e-12:
            return []

        anomalies: List[Dict[str, Any]] = []

        for point, value in zip(points, values):
            z = (value - mean) / std

            if abs(z) >= z_threshold:
                anomalies.append(
                    {
                        "period": point.period,
                        "net_cashflow": point.net_cashflow,
                        "z_score": float(z),
                        "severity": "high" if abs(z) >= 4 else "medium",
                    }
                )

        return anomalies


class EnterpriseCashflowPredictor:
    def __init__(self, config: Optional[CashflowPredictorConfig] = None) -> None:
        self.config = config or CashflowPredictorConfig()
        self.aggregator = CashflowAggregator()
        self.feature_builder = CashflowFeatureBuilder(self.config)
        self.anomaly_detector = CashflowAnomalyDetector()

        self.inflow_model: Any = None
        self.outflow_model: Any = None
        self.scaler_inflow: Any = None
        self.scaler_outflow: Any = None

        self.training_points: List[CashflowPoint] = []
        self.feature_names: List[str] = []
        self.residual_std_inflow: float = 0.0
        self.residual_std_outflow: float = 0.0
        self.is_trained: bool = False
        self.training_result: Optional[CashflowTrainingResult] = None

    def train(
        self,
        transactions: Sequence[CashflowTransaction],
        *,
        opening_balance: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CashflowTrainingResult:
        points = self.aggregator.aggregate(
            transactions,
            self.config.frequency,
            opening_balance=opening_balance,
        )
        points = self.aggregator.fill_missing_periods(points, self.config.frequency)

        if len(points) < self.config.min_training_points:
            raise CashflowError(
                f"Pontos insuficientes para treino: {len(points)} < {self.config.min_training_points}"
            )

        self.training_points = points

        x_in, y_in, feature_names = self.feature_builder.build_supervised_dataset(points, "inflow")
        x_out, y_out, _ = self.feature_builder.build_supervised_dataset(points, "outflow")

        self.feature_names = feature_names

        self.scaler_inflow = self._new_scaler()
        self.scaler_outflow = self._new_scaler()

        x_in_scaled = self.scaler_inflow.fit_transform(x_in) if self.scaler_inflow else x_in
        x_out_scaled = self.scaler_outflow.fit_transform(x_out) if self.scaler_outflow else x_out

        self.inflow_model = self._new_model()
        self.outflow_model = self._new_model()

        self.inflow_model.fit(x_in_scaled, y_in)
        self.outflow_model.fit(x_out_scaled, y_out)

        pred_in = self.inflow_model.predict(x_in_scaled)
        pred_out = self.outflow_model.predict(x_out_scaled)

        residual_in = y_in - pred_in
        residual_out = y_out - pred_out

        self.residual_std_inflow = float(np.std(residual_in))
        self.residual_std_outflow = float(np.std(residual_out))

        metrics = {
            **self._metrics("inflow", y_in, pred_in),
            **self._metrics("outflow", y_out, pred_out),
        }

        self.is_trained = True

        self.training_result = CashflowTrainingResult(
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            trained_at=datetime.now(timezone.utc).isoformat(),
            samples=len(points),
            feature_names=self.feature_names,
            residual_std=float(np.mean([self.residual_std_inflow, self.residual_std_outflow])),
            metrics=metrics,
            metadata=metadata or {},
        )

        return self.training_result

    def forecast(
        self,
        *,
        horizon: Optional[int] = None,
        opening_balance: Optional[float] = None,
        include_backtest: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CashflowForecastReport:
        if not self.is_trained:
            raise CashflowError("Modelo ainda não treinado.")

        horizon = horizon or self.config.horizon
        last_period = self.training_points[-1].period
        future_periods = self.aggregator.future_periods(last_period, self.config.frequency, horizon)

        base_points = self._forecast_base(future_periods, opening_balance)

        scenario_points: List[ForecastPoint] = []

        for point in base_points:
            scenario_points.append(point)
            scenario_points.append(self._scenario_point(point, ForecastScenario.PESSIMISTIC))
            scenario_points.append(self._scenario_point(point, ForecastScenario.OPTIMISTIC))

        backtest = self.backtest() if include_backtest else None
        anomalies = self.anomaly_detector.detect(self.training_points)

        return CashflowForecastReport(
            forecast_id=str(uuid.uuid4()),
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            generated_at=datetime.now(timezone.utc).isoformat(),
            frequency=self.config.frequency,
            horizon=horizon,
            points=scenario_points,
            backtest=backtest,
            anomalies=anomalies,
            metadata=metadata or {},
        )

    def predict_from_transactions(
        self,
        transactions: Sequence[CashflowTransaction],
        *,
        opening_balance: Optional[float] = None,
        horizon: Optional[int] = None,
    ) -> CashflowForecastReport:
        self.train(transactions, opening_balance=opening_balance)
        return self.forecast(horizon=horizon, opening_balance=opening_balance)

    def backtest(self, folds: int = 5) -> BacktestResult:
        if len(self.training_points) < self.config.min_training_points:
            raise CashflowError("Histórico insuficiente para backtest.")

        values_in = np.asarray([p.inflow for p in self.training_points], dtype=float)
        values_out = np.asarray([p.outflow for p in self.training_points], dtype=float)

        fold_metrics: List[Dict[str, float]] = []

        min_train = max(max(self.config.lag_days) + 10, 30)
        total = len(self.training_points)
        step = max((total - min_train) // max(folds, 1), 1)

        for fold in range(folds):
            train_end = min_train + fold * step
            test_end = min(train_end + step, total)

            if test_end <= train_end or train_end >= total:
                continue

            train_points = self.training_points[:train_end]
            test_points = self.training_points[train_end:test_end]

            temp = EnterpriseCashflowPredictor(self.config)
            synthetic_transactions = self._points_to_transactions(train_points)
            temp.train(synthetic_transactions)

            report = temp.forecast(horizon=len(test_points))

            base = [p for p in report.points if p.scenario == ForecastScenario.BASE]
            pred_net = np.asarray([p.predicted_net_cashflow for p in base], dtype=float)
            true_net = np.asarray([p.net_cashflow for p in test_points], dtype=float)

            fold_metrics.append(self._plain_metrics(true_net, pred_net))

        aggregated = {
            key: float(np.mean([m[key] for m in fold_metrics]))
            for key in fold_metrics[0].keys()
        } if fold_metrics else {}

        return BacktestResult(
            run_id=f"cashflow-backtest-{uuid.uuid4().hex[:8]}",
            generated_at=datetime.now(timezone.utc).isoformat(),
            folds=len(fold_metrics),
            metrics=aggregated,
            fold_metrics=fold_metrics,
        )

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "config": asdict(self.config),
            "inflow_model": self.inflow_model,
            "outflow_model": self.outflow_model,
            "scaler_inflow": self.scaler_inflow,
            "scaler_outflow": self.scaler_outflow,
            "training_points": self.training_points,
            "feature_names": self.feature_names,
            "residual_std_inflow": self.residual_std_inflow,
            "residual_std_outflow": self.residual_std_outflow,
            "is_trained": self.is_trained,
            "training_result": self.training_result,
        }

        with target.open("wb") as file:
            pickle.dump(payload, file)

        return target

    @classmethod
    def load(cls, path: str | Path) -> "EnterpriseCashflowPredictor":
        source = Path(path)

        if not source.exists():
            raise CashflowError(f"Modelo não encontrado: {source}")

        with source.open("rb") as file:
            payload = pickle.load(file)

        config = CashflowPredictorConfig(
            **{
                **payload["config"],
                "frequency": CashflowFrequency(payload["config"]["frequency"]),
                "model_type": ForecastModelType(payload["config"]["model_type"]),
            }
        )

        model = cls(config)
        model.inflow_model = payload["inflow_model"]
        model.outflow_model = payload["outflow_model"]
        model.scaler_inflow = payload["scaler_inflow"]
        model.scaler_outflow = payload["scaler_outflow"]
        model.training_points = payload["training_points"]
        model.feature_names = payload["feature_names"]
        model.residual_std_inflow = payload["residual_std_inflow"]
        model.residual_std_outflow = payload["residual_std_outflow"]
        model.is_trained = payload["is_trained"]
        model.training_result = payload["training_result"]

        return model

    def _forecast_base(
        self,
        future_periods: Sequence[str],
        opening_balance: Optional[float],
    ) -> List[ForecastPoint]:
        history_in = [p.inflow for p in self.training_points]
        history_out = [p.outflow for p in self.training_points]

        balance = (
            opening_balance
            if opening_balance is not None
            else self.training_points[-1].closing_balance
        )

        result: List[ForecastPoint] = []

        synthetic_points = list(self.training_points)

        for period in future_periods:
            x_in = self.feature_builder.build_next_features(
                synthetic_points,
                history_in,
            )
            x_out = self.feature_builder.build_next_features(
                synthetic_points,
                history_out,
            )

            if self.scaler_inflow:
                x_in = self.scaler_inflow.transform(x_in)
            if self.scaler_outflow:
                x_out = self.scaler_outflow.transform(x_out)

            predicted_inflow = max(0.0, float(self.inflow_model.predict(x_in)[0]))
            predicted_outflow = max(0.0, float(self.outflow_model.predict(x_out)[0]))
            net = predicted_inflow - predicted_outflow

            std = math.sqrt(self.residual_std_inflow**2 + self.residual_std_outflow**2)
            lower = net - self.config.confidence_z * std
            upper = net + self.config.confidence_z * std

            closing = None
            if balance is not None:
                closing = balance + net
                balance = closing

            result.append(
                ForecastPoint(
                    period=period,
                    scenario=ForecastScenario.BASE,
                    predicted_inflow=predicted_inflow,
                    predicted_outflow=predicted_outflow,
                    predicted_net_cashflow=net,
                    lower_bound=lower,
                    upper_bound=upper,
                    predicted_closing_balance=closing,
                )
            )

            synthetic_points.append(
                CashflowPoint(
                    period=period,
                    inflow=predicted_inflow,
                    outflow=predicted_outflow,
                    net_cashflow=net,
                    closing_balance=closing,
                )
            )

            history_in.append(predicted_inflow)
            history_out.append(predicted_outflow)

        return result

    def _scenario_point(
        self,
        base: ForecastPoint,
        scenario: ForecastScenario,
    ) -> ForecastPoint:
        if scenario == ForecastScenario.PESSIMISTIC:
            inflow = base.predicted_inflow * self.config.pessimistic_multiplier
            outflow = base.predicted_outflow * self.config.optimistic_multiplier
        elif scenario == ForecastScenario.OPTIMISTIC:
            inflow = base.predicted_inflow * self.config.optimistic_multiplier
            outflow = base.predicted_outflow * self.config.pessimistic_multiplier
        else:
            inflow = base.predicted_inflow
            outflow = base.predicted_outflow

        net = inflow - outflow

        return ForecastPoint(
            period=base.period,
            scenario=scenario,
            predicted_inflow=inflow,
            predicted_outflow=outflow,
            predicted_net_cashflow=net,
            lower_bound=base.lower_bound,
            upper_bound=base.upper_bound,
            predicted_closing_balance=None,
            metadata={"derived_from": "base_scenario"},
        )

    def _new_model(self) -> Any:
        if self.config.model_type == ForecastModelType.MOVING_AVERAGE:
            return MovingAverageRegressor(window=7)

        if self.config.model_type == ForecastModelType.RANDOM_FOREST:
            if RandomForestRegressor is None:
                return MovingAverageRegressor(window=7)

            return RandomForestRegressor(
                n_estimators=300,
                min_samples_leaf=2,
                random_state=self.config.random_state,
                n_jobs=-1,
            )

        if GradientBoostingRegressor is None:
            return MovingAverageRegressor(window=7)

        return GradientBoostingRegressor(
            random_state=self.config.random_state,
            n_estimators=250,
            learning_rate=0.05,
            max_depth=3,
        )

    def _new_scaler(self) -> Any:
        if StandardScaler is None or self.config.model_type == ForecastModelType.MOVING_AVERAGE:
            return None
        return StandardScaler()

    def _metrics(self, prefix: str, y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
        plain = self._plain_metrics(y_true, y_pred)
        return {f"{prefix}_{k}": v for k, v in plain.items()}

    def _plain_metrics(self, y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
        mae = float(np.mean(np.abs(y_true - y_pred)))
        rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))

        denom = np.clip(np.abs(y_true), 1e-6, None)
        mape = float(np.mean(np.abs((y_true - y_pred) / denom)))

        metrics = {
            "mae": mae,
            "rmse": rmse,
            "mape": mape,
        }

        if r2_score is not None:
            metrics["r2"] = float(r2_score(y_true, y_pred))

        return metrics

    def _points_to_transactions(self, points: Sequence[CashflowPoint]) -> List[CashflowTransaction]:
        txs: List[CashflowTransaction] = []

        for point in points:
            if point.inflow > 0:
                txs.append(
                    CashflowTransaction(
                        transaction_id=f"synthetic-in-{point.period}",
                        transaction_date=point.period,
                        amount=point.inflow,
                        direction=CashflowDirection.INFLOW,
                    )
                )

            if point.outflow > 0:
                txs.append(
                    CashflowTransaction(
                        transaction_id=f"synthetic-out-{point.period}",
                        transaction_date=point.period,
                        amount=point.outflow,
                        direction=CashflowDirection.OUTFLOW,
                    )
                )

        return txs


def generate_synthetic_cashflow(
    days: int = 365,
    seed: int = 42,
    start_date: str = "2025-01-01",
) -> List[CashflowTransaction]:
    rng = np.random.default_rng(seed)
    start = datetime.fromisoformat(start_date).date()
    transactions: List[CashflowTransaction] = []

    for i in range(days):
        current = start + timedelta(days=i)

        weekday_factor = 1.2 if current.weekday() < 5 else 0.7
        monthly_factor = 1.4 if current.day <= 5 else 1.0

        inflow = max(0.0, float(rng.normal(5000 * weekday_factor * monthly_factor, 1000)))
        outflow = max(0.0, float(rng.normal(3500 * weekday_factor, 900)))

        if current.day in {10, 20, 30}:
            outflow += float(rng.normal(8000, 1000))

        transactions.append(
            CashflowTransaction(
                transaction_id=f"in-{i}",
                transaction_date=current.isoformat(),
                amount=inflow,
                direction=CashflowDirection.INFLOW,
                category="sales",
            )
        )

        transactions.append(
            CashflowTransaction(
                transaction_id=f"out-{i}",
                transaction_date=current.isoformat(),
                amount=outflow,
                direction=CashflowDirection.OUTFLOW,
                category="operations",
            )
        )

    return transactions


if __name__ == "__main__":
    transactions = generate_synthetic_cashflow(days=420)

    predictor = EnterpriseCashflowPredictor(
        CashflowPredictorConfig(
            frequency=CashflowFrequency.DAILY,
            horizon=30,
            model_type=ForecastModelType.GRADIENT_BOOSTING,
        )
    )

    training = predictor.train(
        transactions,
        opening_balance=100_000,
        metadata={"dataset": "synthetic_demo"},
    )

    print(json.dumps(asdict(training), indent=2, ensure_ascii=False, default=str))

    report = predictor.forecast(
        horizon=30,
        include_backtest=True,
        metadata={"business_unit": "super_assis"},
    )

    print(report.to_json())
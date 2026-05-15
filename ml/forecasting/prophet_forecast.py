# ml/forecasting/prophet_forecast.py
"""
Enterprise Prophet Forecasting Module.

Recursos:
- Forecast com Prophet quando disponível
- Fallback robusto sem Prophet
- Sazonalidade diária/semanal/mensal/anual
- Feriados e eventos externos
- Regressors adicionais
- Backtesting temporal
- Cenários pessimista/base/otimista
- Intervalos de confiança
- Detecção simples de anomalias
- Persistência do modelo
- Relatórios JSON enterprise

Dependências opcionais:
    pip install prophet pandas scikit-learn
"""

from __future__ import annotations

import json
import math
import pickle
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np


try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None


try:
    from prophet import Prophet
except Exception:  # pragma: no cover
    Prophet = None


class ProphetForecastError(RuntimeError):
    pass


class ForecastFrequency(str, Enum):
    DAILY = "D"
    WEEKLY = "W"
    MONTHLY = "MS"
    HOURLY = "H"


class ForecastScenario(str, Enum):
    PESSIMISTIC = "pessimistic"
    BASE = "base"
    OPTIMISTIC = "optimistic"


class GrowthMode(str, Enum):
    LINEAR = "linear"
    LOGISTIC = "logistic"
    FLAT = "flat"


@dataclass(frozen=True)
class ProphetForecastConfig:
    model_name: str = "enterprise_prophet_forecaster"
    model_version: str = "1.0.0"

    frequency: ForecastFrequency = ForecastFrequency.DAILY
    horizon: int = 30

    growth: GrowthMode = GrowthMode.LINEAR
    yearly_seasonality: bool | str = "auto"
    weekly_seasonality: bool | str = "auto"
    daily_seasonality: bool | str = "auto"

    seasonality_mode: str = "additive"
    changepoint_prior_scale: float = 0.05
    seasonality_prior_scale: float = 10.0
    holidays_prior_scale: float = 10.0
    interval_width: float = 0.95

    include_monthly_seasonality: bool = True
    monthly_fourier_order: int = 5

    pessimistic_multiplier: float = 0.90
    optimistic_multiplier: float = 1.10

    min_training_points: int = 30
    backtest_folds: int = 5
    backtest_initial_ratio: float = 0.60


@dataclass(frozen=True)
class TimeSeriesRecord:
    ds: str
    y: float
    regressors: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HolidayEvent:
    holiday: str
    ds: str
    lower_window: int = 0
    upper_window: int = 1
    prior_scale: Optional[float] = None


@dataclass(frozen=True)
class ForecastPoint:
    ds: str
    scenario: ForecastScenario
    yhat: float
    yhat_lower: float
    yhat_upper: float
    trend: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProphetTrainingResult:
    run_id: str
    model_name: str
    model_version: str
    trained_at: str
    samples: int
    backend: str
    regressors: List[str]
    metrics: Dict[str, float]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProphetBacktestResult:
    run_id: str
    generated_at: str
    folds: int
    metrics: Dict[str, float]
    fold_metrics: List[Dict[str, float]]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProphetForecastReport:
    forecast_id: str
    model_name: str
    model_version: str
    generated_at: str
    frequency: ForecastFrequency
    horizon: int
    backend: str
    points: List[ForecastPoint]
    backtest: Optional[ProphetBacktestResult] = None
    anomalies: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)


class ProphetDataBuilder:
    def __init__(self, config: ProphetForecastConfig) -> None:
        self.config = config
        self.regressor_names: List[str] = []

    def to_dataframe(self, records: Sequence[TimeSeriesRecord]) -> Any:
        if pd is None:
            raise ProphetForecastError("pandas não está instalado.")

        if len(records) < self.config.min_training_points:
            raise ProphetForecastError(
                f"Pontos insuficientes: {len(records)} < {self.config.min_training_points}"
            )

        records_sorted = sorted(records, key=lambda r: r.ds)
        self.regressor_names = sorted(set().union(*(r.regressors.keys() for r in records_sorted)))

        rows: List[Dict[str, Any]] = []

        for record in records_sorted:
            row = {
                "ds": pd.to_datetime(record.ds),
                "y": float(record.y),
            }

            for name in self.regressor_names:
                row[name] = float(record.regressors.get(name, 0.0))

            rows.append(row)

        return pd.DataFrame(rows)

    def holidays_to_dataframe(self, holidays: Sequence[HolidayEvent]) -> Any:
        if pd is None:
            raise ProphetForecastError("pandas não está instalado.")

        if not holidays:
            return None

        rows = []
        for h in holidays:
            row = {
                "holiday": h.holiday,
                "ds": pd.to_datetime(h.ds),
                "lower_window": h.lower_window,
                "upper_window": h.upper_window,
            }

            if h.prior_scale is not None:
                row["prior_scale"] = h.prior_scale

            rows.append(row)

        return pd.DataFrame(rows)


class SeasonalFallbackForecaster:
    """
    Fallback estatístico simples:
    - tendência linear
    - sazonalidade semanal média
    - intervalo por resíduos
    """

    def __init__(self, frequency: ForecastFrequency) -> None:
        self.frequency = frequency
        self.history: List[TimeSeriesRecord] = []
        self.residual_std: float = 0.0
        self.trend_coef: Tuple[float, float] = (0.0, 0.0)
        self.weekday_effects: Dict[int, float] = {}

    def fit(self, records: Sequence[TimeSeriesRecord]) -> None:
        self.history = sorted(records, key=lambda r: r.ds)

        y = np.asarray([r.y for r in self.history], dtype=float)
        x = np.arange(len(y), dtype=float)

        if len(y) >= 2:
            slope, intercept = np.polyfit(x, y, deg=1)
        else:
            slope, intercept = 0.0, float(y[0]) if len(y) else 0.0

        self.trend_coef = (float(slope), float(intercept))

        detrended = y - (slope * x + intercept)

        effects: Dict[int, List[float]] = {}
        for record, residual in zip(self.history, detrended):
            weekday = datetime.fromisoformat(record.ds).weekday()
            effects.setdefault(weekday, []).append(float(residual))

        self.weekday_effects = {
            k: float(np.mean(v))
            for k, v in effects.items()
        }

        fitted = []
        for i, record in enumerate(self.history):
            weekday = datetime.fromisoformat(record.ds).weekday()
            fitted.append(slope * i + intercept + self.weekday_effects.get(weekday, 0.0))

        self.residual_std = float(np.std(y - np.asarray(fitted))) if len(y) else 0.0

    def predict(self, horizon: int, interval_z: float = 1.96) -> List[ForecastPoint]:
        if not self.history:
            raise ProphetForecastError("Fallback não treinado.")

        last_date = datetime.fromisoformat(self.history[-1].ds)
        slope, intercept = self.trend_coef

        points: List[ForecastPoint] = []

        for step in range(1, horizon + 1):
            future_date = self._add_period(last_date, step)
            idx = len(self.history) + step - 1
            weekday = future_date.weekday()

            yhat = slope * idx + intercept + self.weekday_effects.get(weekday, 0.0)
            lower = yhat - interval_z * self.residual_std
            upper = yhat + interval_z * self.residual_std

            points.append(
                ForecastPoint(
                    ds=future_date.date().isoformat(),
                    scenario=ForecastScenario.BASE,
                    yhat=float(yhat),
                    yhat_lower=float(lower),
                    yhat_upper=float(upper),
                    trend=float(slope * idx + intercept),
                    metadata={"backend": "seasonal_fallback"},
                )
            )

        return points

    def _add_period(self, base: datetime, step: int) -> datetime:
        if self.frequency == ForecastFrequency.DAILY:
            return base + timedelta(days=step)

        if self.frequency == ForecastFrequency.WEEKLY:
            return base + timedelta(weeks=step)

        if self.frequency == ForecastFrequency.HOURLY:
            return base + timedelta(hours=step)

        if self.frequency == ForecastFrequency.MONTHLY:
            month = base.month + step
            year = base.year + (month - 1) // 12
            month = ((month - 1) % 12) + 1
            return datetime(year, month, 1)

        return base + timedelta(days=step)


class ProphetMetrics:
    @staticmethod
    def compute(y_true: Sequence[float], y_pred: Sequence[float]) -> Dict[str, float]:
        yt = np.asarray(y_true, dtype=float)
        yp = np.asarray(y_pred, dtype=float)

        if len(yt) == 0:
            return {}

        mae = float(np.mean(np.abs(yt - yp)))
        rmse = float(np.sqrt(np.mean((yt - yp) ** 2)))
        denom = np.clip(np.abs(yt), 1e-6, None)
        mape = float(np.mean(np.abs((yt - yp) / denom)))

        return {
            "mae": mae,
            "rmse": rmse,
            "mape": mape,
        }


class ProphetAnomalyDetector:
    def detect(
        self,
        records: Sequence[TimeSeriesRecord],
        fitted: Sequence[float],
        *,
        z_threshold: float = 3.0,
    ) -> List[Dict[str, Any]]:
        if len(records) != len(fitted) or len(records) < 10:
            return []

        y = np.asarray([r.y for r in records], dtype=float)
        pred = np.asarray(fitted, dtype=float)
        residuals = y - pred

        std = float(np.std(residuals))
        if std <= 1e-12:
            return []

        anomalies: List[Dict[str, Any]] = []

        for record, residual in zip(records, residuals):
            z = float(residual / std)

            if abs(z) >= z_threshold:
                anomalies.append(
                    {
                        "ds": record.ds,
                        "actual": record.y,
                        "residual": float(residual),
                        "z_score": z,
                        "severity": "high" if abs(z) >= 4 else "medium",
                    }
                )

        return anomalies


class EnterpriseProphetForecaster:
    def __init__(self, config: Optional[ProphetForecastConfig] = None) -> None:
        self.config = config or ProphetForecastConfig()
        self.data_builder = ProphetDataBuilder(self.config)
        self.anomaly_detector = ProphetAnomalyDetector()

        self.model: Any = None
        self.fallback: Optional[SeasonalFallbackForecaster] = None

        self.regressor_names: List[str] = []
        self.training_records: List[TimeSeriesRecord] = []
        self.backend: str = "untrained"
        self.training_result: Optional[ProphetTrainingResult] = None
        self.is_trained = False

    def train(
        self,
        records: Sequence[TimeSeriesRecord],
        *,
        holidays: Optional[Sequence[HolidayEvent]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProphetTrainingResult:
        run_id = f"prophet-train-{uuid.uuid4().hex[:8]}"

        records_sorted = sorted(records, key=lambda r: r.ds)
        self.training_records = list(records_sorted)

        if Prophet is None or pd is None:
            result = self._train_fallback(run_id, records_sorted, metadata)
            return result

        df = self.data_builder.to_dataframe(records_sorted)
        self.regressor_names = list(self.data_builder.regressor_names)

        holidays_df = self.data_builder.holidays_to_dataframe(holidays or [])

        self.model = Prophet(
            growth=self.config.growth.value,
            yearly_seasonality=self.config.yearly_seasonality,
            weekly_seasonality=self.config.weekly_seasonality,
            daily_seasonality=self.config.daily_seasonality,
            seasonality_mode=self.config.seasonality_mode,
            changepoint_prior_scale=self.config.changepoint_prior_scale,
            seasonality_prior_scale=self.config.seasonality_prior_scale,
            holidays_prior_scale=self.config.holidays_prior_scale,
            interval_width=self.config.interval_width,
            holidays=holidays_df,
        )

        if self.config.include_monthly_seasonality:
            self.model.add_seasonality(
                name="monthly",
                period=30.5,
                fourier_order=self.config.monthly_fourier_order,
            )

        for regressor in self.regressor_names:
            self.model.add_regressor(regressor)

        self.model.fit(df)

        fitted = self.model.predict(df)
        metrics = ProphetMetrics.compute(df["y"].tolist(), fitted["yhat"].tolist())

        self.backend = "prophet"
        self.is_trained = True

        self.training_result = ProphetTrainingResult(
            run_id=run_id,
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            trained_at=datetime.now(timezone.utc).isoformat(),
            samples=len(records_sorted),
            backend=self.backend,
            regressors=self.regressor_names,
            metrics=metrics,
            metadata=metadata or {},
        )

        return self.training_result

    def forecast(
        self,
        *,
        horizon: Optional[int] = None,
        future_regressors: Optional[Sequence[Mapping[str, float]]] = None,
        include_backtest: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProphetForecastReport:
        if not self.is_trained:
            raise ProphetForecastError("Modelo ainda não treinado.")

        horizon = horizon or self.config.horizon

        if self.backend == "prophet":
            base_points = self._forecast_prophet(horizon, future_regressors)
            fitted_for_anomalies = self._fitted_prophet()
        else:
            base_points = self.fallback.predict(horizon) if self.fallback else []
            fitted_for_anomalies = self._fitted_fallback()

        points = self._with_scenarios(base_points)

        backtest = self.backtest() if include_backtest else None
        anomalies = self.anomaly_detector.detect(self.training_records, fitted_for_anomalies)

        return ProphetForecastReport(
            forecast_id=str(uuid.uuid4()),
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            generated_at=datetime.now(timezone.utc).isoformat(),
            frequency=self.config.frequency,
            horizon=horizon,
            backend=self.backend,
            points=points,
            backtest=backtest,
            anomalies=anomalies,
            metadata=metadata or {},
        )

    def backtest(self) -> ProphetBacktestResult:
        records = self.training_records

        if len(records) < self.config.min_training_points * 2:
            return ProphetBacktestResult(
                run_id=f"prophet-backtest-{uuid.uuid4().hex[:8]}",
                generated_at=datetime.now(timezone.utc).isoformat(),
                folds=0,
                metrics={},
                fold_metrics=[],
                metadata={"reason": "insufficient_data"},
            )

        folds = max(1, self.config.backtest_folds)
        initial = int(len(records) * self.config.backtest_initial_ratio)
        remaining = len(records) - initial
        step = max(1, remaining // folds)

        fold_metrics: List[Dict[str, float]] = []

        for fold in range(folds):
            train_end = initial + fold * step
            test_end = min(train_end + step, len(records))

            if test_end <= train_end:
                continue

            train_records = records[:train_end]
            test_records = records[train_end:test_end]

            temp = EnterpriseProphetForecaster(self.config)
            temp.train(train_records)

            report = temp.forecast(horizon=len(test_records))
            base_points = [p for p in report.points if p.scenario == ForecastScenario.BASE]

            y_true = [r.y for r in test_records]
            y_pred = [p.yhat for p in base_points[: len(y_true)]]

            fold_metrics.append(ProphetMetrics.compute(y_true, y_pred))

        aggregate = {}

        if fold_metrics:
            keys = sorted(set().union(*(m.keys() for m in fold_metrics)))
            aggregate = {
                key: float(np.mean([m.get(key, 0.0) for m in fold_metrics]))
                for key in keys
            }

        return ProphetBacktestResult(
            run_id=f"prophet-backtest-{uuid.uuid4().hex[:8]}",
            generated_at=datetime.now(timezone.utc).isoformat(),
            folds=len(fold_metrics),
            metrics=aggregate,
            fold_metrics=fold_metrics,
        )

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "config": asdict(self.config),
            "model": self.model,
            "fallback": self.fallback,
            "regressor_names": self.regressor_names,
            "training_records": self.training_records,
            "backend": self.backend,
            "training_result": self.training_result,
            "is_trained": self.is_trained,
        }

        with target.open("wb") as file:
            pickle.dump(payload, file)

        return target

    @classmethod
    def load(cls, path: str | Path) -> "EnterpriseProphetForecaster":
        source = Path(path)

        if not source.exists():
            raise ProphetForecastError(f"Arquivo não encontrado: {source}")

        with source.open("rb") as file:
            payload = pickle.load(file)

        raw = payload["config"]

        config = ProphetForecastConfig(
            **{
                **raw,
                "frequency": ForecastFrequency(raw["frequency"]),
                "growth": GrowthMode(raw["growth"]),
            }
        )

        obj = cls(config)
        obj.model = payload["model"]
        obj.fallback = payload["fallback"]
        obj.regressor_names = payload["regressor_names"]
        obj.training_records = payload["training_records"]
        obj.backend = payload["backend"]
        obj.training_result = payload["training_result"]
        obj.is_trained = payload["is_trained"]

        return obj

    def _train_fallback(
        self,
        run_id: str,
        records: Sequence[TimeSeriesRecord],
        metadata: Optional[Dict[str, Any]],
    ) -> ProphetTrainingResult:
        self.fallback = SeasonalFallbackForecaster(self.config.frequency)
        self.fallback.fit(records)

        fitted = self._fitted_fallback()
        metrics = ProphetMetrics.compute([r.y for r in records], fitted)

        self.backend = "seasonal_fallback"
        self.is_trained = True
        self.regressor_names = []

        self.training_result = ProphetTrainingResult(
            run_id=run_id,
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            trained_at=datetime.now(timezone.utc).isoformat(),
            samples=len(records),
            backend=self.backend,
            regressors=[],
            metrics=metrics,
            metadata=metadata or {},
        )

        return self.training_result

    def _forecast_prophet(
        self,
        horizon: int,
        future_regressors: Optional[Sequence[Mapping[str, float]]],
    ) -> List[ForecastPoint]:
        future = self.model.make_future_dataframe(
            periods=horizon,
            freq=self.config.frequency.value,
            include_history=False,
        )

        for regressor in self.regressor_names:
            values = []
            for i in range(horizon):
                value = 0.0
                if future_regressors and i < len(future_regressors):
                    value = float(future_regressors[i].get(regressor, 0.0))
                values.append(value)

            future[regressor] = values

        forecast = self.model.predict(future)

        points: List[ForecastPoint] = []

        for _, row in forecast.iterrows():
            points.append(
                ForecastPoint(
                    ds=str(row["ds"].date()),
                    scenario=ForecastScenario.BASE,
                    yhat=float(row["yhat"]),
                    yhat_lower=float(row["yhat_lower"]),
                    yhat_upper=float(row["yhat_upper"]),
                    trend=float(row["trend"]) if "trend" in row else None,
                    metadata={"backend": "prophet"},
                )
            )

        return points

    def _with_scenarios(self, base_points: Sequence[ForecastPoint]) -> List[ForecastPoint]:
        result: List[ForecastPoint] = []

        for base in base_points:
            result.append(base)

            result.append(
                ForecastPoint(
                    ds=base.ds,
                    scenario=ForecastScenario.PESSIMISTIC,
                    yhat=base.yhat * self.config.pessimistic_multiplier,
                    yhat_lower=base.yhat_lower * self.config.pessimistic_multiplier,
                    yhat_upper=base.yhat_upper * self.config.pessimistic_multiplier,
                    trend=base.trend,
                    metadata={"derived_from": "base"},
                )
            )

            result.append(
                ForecastPoint(
                    ds=base.ds,
                    scenario=ForecastScenario.OPTIMISTIC,
                    yhat=base.yhat * self.config.optimistic_multiplier,
                    yhat_lower=base.yhat_lower * self.config.optimistic_multiplier,
                    yhat_upper=base.yhat_upper * self.config.optimistic_multiplier,
                    trend=base.trend,
                    metadata={"derived_from": "base"},
                )
            )

        return result

    def _fitted_prophet(self) -> List[float]:
        if pd is None:
            return []

        df = self.data_builder.to_dataframe(self.training_records)

        for regressor in self.regressor_names:
            if regressor not in df:
                df[regressor] = 0.0

        fitted = self.model.predict(df)
        return [float(v) for v in fitted["yhat"].tolist()]

    def _fitted_fallback(self) -> List[float]:
        if not self.fallback:
            return []

        records = self.training_records
        slope, intercept = self.fallback.trend_coef
        fitted = []

        for i, record in enumerate(records):
            weekday = datetime.fromisoformat(record.ds).weekday()
            fitted.append(
                float(slope * i + intercept + self.fallback.weekday_effects.get(weekday, 0.0))
            )

        return fitted


def generate_synthetic_prophet_series(
    days: int = 365,
    seed: int = 42,
    start_date: str = "2025-01-01",
) -> List[TimeSeriesRecord]:
    rng = np.random.default_rng(seed)
    start = datetime.fromisoformat(start_date)
    records: List[TimeSeriesRecord] = []

    for i in range(days):
        current = start + timedelta(days=i)

        trend = 0.08 * i
        weekly = 20 * math.sin(2 * math.pi * i / 7)
        yearly = 35 * math.sin(2 * math.pi * i / 365)
        promo = 1.0 if current.day in {5, 15, 25} else 0.0
        noise = rng.normal(0, 8)

        y = 200 + trend + weekly + yearly + promo * 45 + noise

        records.append(
            TimeSeriesRecord(
                ds=current.date().isoformat(),
                y=float(y),
                regressors={
                    "promo": promo,
                    "is_weekend": 1.0 if current.weekday() >= 5 else 0.0,
                },
            )
        )

    return records


if __name__ == "__main__":
    records = generate_synthetic_prophet_series(days=420)

    holidays = [
        HolidayEvent(
            holiday="black_friday",
            ds="2025-11-28",
            lower_window=-2,
            upper_window=2,
        )
    ]

    forecaster = EnterpriseProphetForecaster(
        ProphetForecastConfig(
            frequency=ForecastFrequency.DAILY,
            horizon=30,
            include_monthly_seasonality=True,
        )
    )

    training = forecaster.train(
        records,
        holidays=holidays,
        metadata={"dataset": "synthetic_demo"},
    )

    print(json.dumps(asdict(training), indent=2, ensure_ascii=False, default=str))

    future_regressors = [
        {"promo": 1.0 if i in {4, 14, 24} else 0.0, "is_weekend": 0.0}
        for i in range(30)
    ]

    report = forecaster.forecast(
        horizon=30,
        future_regressors=future_regressors,
        include_backtest=True,
        metadata={"business_unit": "enterprise"},
    )

    print(report.to_json())
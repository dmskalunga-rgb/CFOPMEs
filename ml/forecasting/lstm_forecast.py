# ml/forecasting/lstm_forecast.py
"""
Enterprise LSTM Forecasting Module.

Recursos:
- Previsão univariada e multivariada
- Preparação automática de janelas temporais
- Normalização robusta
- LSTM com PyTorch quando disponível
- Fallback estatístico quando PyTorch não estiver instalado
- Early stopping
- Backtesting temporal
- Intervalos de confiança por resíduos
- Forecast multi-step
- Persistência de modelo
- Relatórios JSON enterprise
"""

from __future__ import annotations

import json
import math
import pickle
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np


try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
except Exception:  # pragma: no cover
    torch = None
    nn = None
    DataLoader = None
    TensorDataset = None


class LSTMForecastError(RuntimeError):
    pass


class ForecastMode(str, Enum):
    UNIVARIATE = "univariate"
    MULTIVARIATE = "multivariate"


class ForecastDevice(str, Enum):
    AUTO = "auto"
    CPU = "cpu"
    CUDA = "cuda"


@dataclass(frozen=True)
class LSTMForecastConfig:
    model_name: str = "enterprise_lstm_forecaster"
    model_version: str = "1.0.0"

    mode: ForecastMode = ForecastMode.UNIVARIATE
    lookback: int = 30
    horizon: int = 7

    hidden_size: int = 64
    num_layers: int = 2
    dropout: float = 0.15

    batch_size: int = 64
    epochs: int = 100
    learning_rate: float = 1e-3
    weight_decay: float = 1e-5

    validation_ratio: float = 0.2
    early_stopping_patience: int = 10
    gradient_clip_norm: float = 1.0

    confidence_z: float = 1.96
    random_state: int = 42

    device: ForecastDevice = ForecastDevice.AUTO


@dataclass(frozen=True)
class TimeSeriesPoint:
    timestamp: str
    target: float
    features: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ForecastPoint:
    timestamp: str
    step: int
    prediction: float
    lower_bound: float
    upper_bound: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LSTMTrainingResult:
    run_id: str
    model_name: str
    model_version: str
    trained_at: str
    samples: int
    train_loss: float
    validation_loss: Optional[float]
    residual_std: float
    feature_names: List[str]
    metrics: Dict[str, float]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LSTMForecastReport:
    forecast_id: str
    model_name: str
    model_version: str
    generated_at: str
    horizon: int
    points: List[ForecastPoint]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


class StandardTimeSeriesScaler:
    def __init__(self) -> None:
        self.mean_: Optional[np.ndarray] = None
        self.std_: Optional[np.ndarray] = None

    def fit(self, x: np.ndarray) -> "StandardTimeSeriesScaler":
        self.mean_ = np.mean(x, axis=0)
        self.std_ = np.std(x, axis=0)
        self.std_ = np.where(self.std_ < 1e-12, 1.0, self.std_)
        return self

    def transform(self, x: np.ndarray) -> np.ndarray:
        if self.mean_ is None or self.std_ is None:
            raise LSTMForecastError("Scaler não ajustado.")
        return (x - self.mean_) / self.std_

    def inverse_target(self, y: np.ndarray, target_index: int = 0) -> np.ndarray:
        if self.mean_ is None or self.std_ is None:
            raise LSTMForecastError("Scaler não ajustado.")
        return y * self.std_[target_index] + self.mean_[target_index]


class SequenceBuilder:
    def __init__(self, config: LSTMForecastConfig) -> None:
        self.config = config
        self.feature_names: List[str] = []

    def points_to_matrix(self, points: Sequence[TimeSeriesPoint]) -> Tuple[np.ndarray, List[str]]:
        if len(points) < self.config.lookback + self.config.horizon:
            raise LSTMForecastError(
                f"Série insuficiente: {len(points)} pontos. "
                f"Mínimo necessário: {self.config.lookback + self.config.horizon}"
            )

        feature_names = sorted(set().union(*(p.features.keys() for p in points))) if points else []

        if self.config.mode == ForecastMode.UNIVARIATE:
            self.feature_names = ["target"]
            matrix = [[float(p.target)] for p in points]
        else:
            self.feature_names = ["target", *feature_names]
            matrix = [
                [float(p.target), *[float(p.features.get(name, 0.0)) for name in feature_names]]
                for p in points
            ]

        return np.asarray(matrix, dtype=np.float32), self.feature_names

    def build_sequences(self, matrix: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        x_rows: List[np.ndarray] = []
        y_rows: List[np.ndarray] = []

        for i in range(self.config.lookback, len(matrix) - self.config.horizon + 1):
            x_rows.append(matrix[i - self.config.lookback:i])
            y_rows.append(matrix[i:i + self.config.horizon, 0])

        return np.asarray(x_rows, dtype=np.float32), np.asarray(y_rows, dtype=np.float32)


if torch is not None:

    class LSTMForecasterNet(nn.Module):
        def __init__(
            self,
            input_size: int,
            hidden_size: int,
            num_layers: int,
            dropout: float,
            horizon: int,
        ) -> None:
            super().__init__()

            self.lstm = nn.LSTM(
                input_size=input_size,
                hidden_size=hidden_size,
                num_layers=num_layers,
                dropout=dropout if num_layers > 1 else 0.0,
                batch_first=True,
            )

            self.head = nn.Sequential(
                nn.Linear(hidden_size, hidden_size),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_size, horizon),
            )

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            output, _ = self.lstm(x)
            last = output[:, -1, :]
            return self.head(last)

else:

    class LSTMForecasterNet:  # type: ignore
        pass


class MovingAverageFallback:
    def __init__(self, lookback: int, horizon: int) -> None:
        self.lookback = lookback
        self.horizon = horizon
        self.history: List[float] = []
        self.residual_std: float = 0.0

    def fit(self, values: Sequence[float]) -> None:
        self.history = [float(v) for v in values]

        preds = []
        actual = []

        for i in range(self.lookback, len(values)):
            preds.append(float(np.mean(values[max(0, i - self.lookback):i])))
            actual.append(float(values[i]))

        self.residual_std = float(np.std(np.asarray(actual) - np.asarray(preds))) if preds else 0.0

    def forecast(self, horizon: int) -> List[float]:
        history = list(self.history)
        preds: List[float] = []

        for _ in range(horizon):
            pred = float(np.mean(history[-self.lookback:])) if history else 0.0
            preds.append(pred)
            history.append(pred)

        return preds


class EnterpriseLSTMForecaster:
    def __init__(self, config: Optional[LSTMForecastConfig] = None) -> None:
        self.config = config or LSTMForecastConfig()
        self.sequence_builder = SequenceBuilder(self.config)
        self.scaler = StandardTimeSeriesScaler()

        self.model: Any = None
        self.fallback: Optional[MovingAverageFallback] = None
        self.device: str = "cpu"

        self.feature_names: List[str] = []
        self.training_points: List[TimeSeriesPoint] = []
        self.residual_std: float = 0.0
        self.is_trained: bool = False
        self.training_result: Optional[LSTMTrainingResult] = None

    def train(
        self,
        points: Sequence[TimeSeriesPoint],
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LSTMTrainingResult:
        run_id = f"lstm-train-{uuid.uuid4().hex[:8]}"

        sorted_points = sorted(points, key=lambda p: p.timestamp)
        self.training_points = list(sorted_points)

        matrix, feature_names = self.sequence_builder.points_to_matrix(sorted_points)
        self.feature_names = feature_names

        if torch is None:
            return self._train_fallback(run_id, matrix[:, 0], metadata)

        self._set_seed()
        self.device = self._resolve_device()

        scaled = self.scaler.fit(matrix).transform(matrix)
        x, y_scaled = self.sequence_builder.build_sequences(scaled)

        train_x, val_x, train_y, val_y = self._train_validation_split(x, y_scaled)

        train_dataset = TensorDataset(
            torch.tensor(train_x, dtype=torch.float32),
            torch.tensor(train_y, dtype=torch.float32),
        )

        train_loader = DataLoader(
            train_dataset,
            batch_size=self.config.batch_size,
            shuffle=True,
        )

        self.model = LSTMForecasterNet(
            input_size=x.shape[-1],
            hidden_size=self.config.hidden_size,
            num_layers=self.config.num_layers,
            dropout=self.config.dropout,
            horizon=self.config.horizon,
        ).to(self.device)

        optimizer = torch.optim.AdamW(
            self.model.parameters(),
            lr=self.config.learning_rate,
            weight_decay=self.config.weight_decay,
        )

        loss_fn = nn.MSELoss()

        best_val = float("inf")
        best_state = None
        patience = 0
        last_train_loss = float("inf")
        last_val_loss: Optional[float] = None

        for _epoch in range(self.config.epochs):
            self.model.train()
            losses: List[float] = []

            for xb, yb in train_loader:
                xb = xb.to(self.device)
                yb = yb.to(self.device)

                optimizer.zero_grad()
                pred = self.model(xb)
                loss = loss_fn(pred, yb)
                loss.backward()

                if self.config.gradient_clip_norm > 0:
                    torch.nn.utils.clip_grad_norm_(
                        self.model.parameters(),
                        self.config.gradient_clip_norm,
                    )

                optimizer.step()
                losses.append(float(loss.detach().cpu().item()))

            last_train_loss = float(np.mean(losses)) if losses else float("inf")

            if val_x is not None and len(val_x):
                last_val_loss = self._evaluate_loss(val_x, val_y)

                if last_val_loss < best_val:
                    best_val = last_val_loss
                    best_state = {
                        k: v.detach().cpu().clone()
                        for k, v in self.model.state_dict().items()
                    }
                    patience = 0
                else:
                    patience += 1

                if patience >= self.config.early_stopping_patience:
                    break

        if best_state is not None:
            self.model.load_state_dict(best_state)

        residuals = self._compute_residuals(x, y_scaled)
        self.residual_std = float(np.std(residuals)) if len(residuals) else 0.0

        metrics = {
            "train_loss": float(last_train_loss),
            "validation_loss": float(last_val_loss) if last_val_loss is not None else 0.0,
            "residual_std": self.residual_std,
        }

        self.is_trained = True

        self.training_result = LSTMTrainingResult(
            run_id=run_id,
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            trained_at=datetime.now(timezone.utc).isoformat(),
            samples=len(points),
            train_loss=float(last_train_loss),
            validation_loss=last_val_loss,
            residual_std=self.residual_std,
            feature_names=self.feature_names,
            metrics=metrics,
            metadata={
                "backend": "pytorch",
                "device": self.device,
                **(metadata or {}),
            },
        )

        return self.training_result

    def forecast(
        self,
        *,
        horizon: Optional[int] = None,
        future_features: Optional[Sequence[Mapping[str, float]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LSTMForecastReport:
        if not self.is_trained:
            raise LSTMForecastError("Modelo ainda não treinado.")

        horizon = horizon or self.config.horizon

        if self.fallback is not None:
            predictions = self.fallback.forecast(horizon)
            residual_std = self.fallback.residual_std
        else:
            predictions = self._forecast_torch(horizon, future_features)
            residual_std = self.residual_std

        last_timestamp = self.training_points[-1].timestamp
        timestamps = self._future_timestamps(last_timestamp, horizon)

        points: List[ForecastPoint] = []

        for i, pred in enumerate(predictions):
            lower = pred - self.config.confidence_z * residual_std
            upper = pred + self.config.confidence_z * residual_std

            points.append(
                ForecastPoint(
                    timestamp=timestamps[i],
                    step=i + 1,
                    prediction=float(pred),
                    lower_bound=float(lower),
                    upper_bound=float(upper),
                )
            )

        return LSTMForecastReport(
            forecast_id=str(uuid.uuid4()),
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            generated_at=datetime.now(timezone.utc).isoformat(),
            horizon=horizon,
            points=points,
            metadata=metadata or {},
        )

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "config": asdict(self.config),
            "scaler": self.scaler,
            "feature_names": self.feature_names,
            "training_points": self.training_points,
            "residual_std": self.residual_std,
            "is_trained": self.is_trained,
            "training_result": self.training_result,
            "fallback": self.fallback,
            "device": self.device,
        }

        if torch is not None and self.model is not None:
            payload["model_state_dict"] = {
                k: v.detach().cpu()
                for k, v in self.model.state_dict().items()
            }
            payload["input_size"] = len(self.feature_names)
        else:
            payload["model_state_dict"] = None
            payload["input_size"] = None

        with target.open("wb") as file:
            pickle.dump(payload, file)

        return target

    @classmethod
    def load(cls, path: str | Path) -> "EnterpriseLSTMForecaster":
        source = Path(path)

        if not source.exists():
            raise LSTMForecastError(f"Arquivo não encontrado: {source}")

        with source.open("rb") as file:
            payload = pickle.load(file)

        config_raw = payload["config"]
        config = LSTMForecastConfig(
            **{
                **config_raw,
                "mode": ForecastMode(config_raw["mode"]),
                "device": ForecastDevice(config_raw["device"]),
            }
        )

        obj = cls(config)
        obj.scaler = payload["scaler"]
        obj.feature_names = payload["feature_names"]
        obj.training_points = payload["training_points"]
        obj.residual_std = payload["residual_std"]
        obj.is_trained = payload["is_trained"]
        obj.training_result = payload["training_result"]
        obj.fallback = payload["fallback"]
        obj.device = obj._resolve_device()

        if torch is not None and payload.get("model_state_dict") is not None:
            obj.model = LSTMForecasterNet(
                input_size=payload["input_size"],
                hidden_size=config.hidden_size,
                num_layers=config.num_layers,
                dropout=config.dropout,
                horizon=config.horizon,
            ).to(obj.device)

            obj.model.load_state_dict(payload["model_state_dict"])
            obj.model.eval()

        return obj

    def _train_fallback(
        self,
        run_id: str,
        values: Sequence[float],
        metadata: Optional[Dict[str, Any]],
    ) -> LSTMTrainingResult:
        self.fallback = MovingAverageFallback(
            lookback=self.config.lookback,
            horizon=self.config.horizon,
        )
        self.fallback.fit(values)

        self.residual_std = self.fallback.residual_std
        self.is_trained = True

        result = LSTMTrainingResult(
            run_id=run_id,
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            trained_at=datetime.now(timezone.utc).isoformat(),
            samples=len(values),
            train_loss=0.0,
            validation_loss=None,
            residual_std=self.residual_std,
            feature_names=self.feature_names,
            metrics={
                "residual_std": self.residual_std,
                "backend": 0.0,
            },
            metadata={
                "backend": "moving_average_fallback",
                **(metadata or {}),
            },
        )

        self.training_result = result
        return result

    def _forecast_torch(
        self,
        horizon: int,
        future_features: Optional[Sequence[Mapping[str, float]]],
    ) -> List[float]:
        if torch is None or self.model is None:
            raise LSTMForecastError("Backend PyTorch indisponível.")

        self.model.eval()

        matrix, _ = self.sequence_builder.points_to_matrix(self.training_points)
        scaled_history = self.scaler.transform(matrix).tolist()

        predictions: List[float] = []

        while len(predictions) < horizon:
            window = np.asarray(scaled_history[-self.config.lookback:], dtype=np.float32)
            xb = torch.tensor(window[None, :, :], dtype=torch.float32).to(self.device)

            with torch.no_grad():
                pred_scaled = self.model(xb).detach().cpu().numpy()[0]

            pred_values = self.scaler.inverse_target(pred_scaled)

            for value in pred_values:
                if len(predictions) >= horizon:
                    break

                predictions.append(float(value))

                next_row = self._build_next_scaled_row(
                    target_value=float(value),
                    step=len(predictions),
                    future_features=future_features,
                )
                scaled_history.append(next_row)

        return predictions

    def _build_next_scaled_row(
        self,
        target_value: float,
        step: int,
        future_features: Optional[Sequence[Mapping[str, float]]],
    ) -> List[float]:
        raw = [target_value]

        if self.config.mode == ForecastMode.MULTIVARIATE:
            feature_map = {}
            if future_features and step - 1 < len(future_features):
                feature_map = dict(future_features[step - 1])

            for name in self.feature_names[1:]:
                raw.append(float(feature_map.get(name, 0.0)))

        scaled = self.scaler.transform(np.asarray([raw], dtype=np.float32))[0]
        return [float(v) for v in scaled]

    def _evaluate_loss(self, val_x: np.ndarray, val_y: np.ndarray) -> float:
        self.model.eval()
        loss_fn = nn.MSELoss()

        with torch.no_grad():
            xb = torch.tensor(val_x, dtype=torch.float32).to(self.device)
            yb = torch.tensor(val_y, dtype=torch.float32).to(self.device)
            pred = self.model(xb)
            loss = loss_fn(pred, yb)

        return float(loss.detach().cpu().item())

    def _compute_residuals(self, x: np.ndarray, y_scaled: np.ndarray) -> np.ndarray:
        self.model.eval()

        with torch.no_grad():
            xb = torch.tensor(x, dtype=torch.float32).to(self.device)
            pred_scaled = self.model(xb).detach().cpu().numpy()

        pred = self.scaler.inverse_target(pred_scaled.reshape(-1))
        actual = self.scaler.inverse_target(y_scaled.reshape(-1))

        return actual - pred

    def _train_validation_split(
        self,
        x: np.ndarray,
        y: np.ndarray,
    ) -> Tuple[np.ndarray, Optional[np.ndarray], np.ndarray, Optional[np.ndarray]]:
        n = len(x)
        val_size = int(n * self.config.validation_ratio)

        if val_size <= 0:
            return x, None, y, None

        train_end = n - val_size
        return x[:train_end], x[train_end:], y[:train_end], y[train_end:]

    def _resolve_device(self) -> str:
        if torch is None:
            return "cpu"

        if self.config.device == ForecastDevice.CPU:
            return "cpu"

        if self.config.device == ForecastDevice.CUDA:
            if not torch.cuda.is_available():
                raise LSTMForecastError("CUDA solicitado, mas não disponível.")
            return "cuda"

        return "cuda" if torch.cuda.is_available() else "cpu"

    def _set_seed(self) -> None:
        np.random.seed(self.config.random_state)

        if torch is not None:
            torch.manual_seed(self.config.random_state)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(self.config.random_state)

    @staticmethod
    def _future_timestamps(last_timestamp: str, horizon: int) -> List[str]:
        base = datetime.fromisoformat(last_timestamp.replace("Z", "+00:00"))
        return [
            (base + np.timedelta64(i, "D")).astype("datetime64[D]").astype(str)
            if False
            else (base.replace(tzinfo=None) + __import__("datetime").timedelta(days=i)).date().isoformat()
            for i in range(1, horizon + 1)
        ]


def generate_synthetic_series(
    days: int = 365,
    seed: int = 42,
    start_date: str = "2025-01-01",
) -> List[TimeSeriesPoint]:
    rng = np.random.default_rng(seed)
    start = datetime.fromisoformat(start_date)

    points: List[TimeSeriesPoint] = []

    for i in range(days):
        ts = start + __import__("datetime").timedelta(days=i)

        weekly = 15 * math.sin(2 * math.pi * i / 7)
        yearly = 25 * math.sin(2 * math.pi * i / 365)
        trend = i * 0.08
        noise = rng.normal(0, 5)

        target = 100 + weekly + yearly + trend + noise

        points.append(
            TimeSeriesPoint(
                timestamp=ts.date().isoformat(),
                target=float(target),
                features={
                    "weekday": float(ts.weekday()),
                    "month": float(ts.month),
                    "is_weekend": 1.0 if ts.weekday() >= 5 else 0.0,
                },
            )
        )

    return points


if __name__ == "__main__":
    series = generate_synthetic_series(days=420)

    forecaster = EnterpriseLSTMForecaster(
        LSTMForecastConfig(
            mode=ForecastMode.MULTIVARIATE,
            lookback=30,
            horizon=14,
            epochs=20,
            hidden_size=64,
            num_layers=2,
        )
    )

    result = forecaster.train(series, metadata={"dataset": "synthetic"})
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False, default=str))

    forecast = forecaster.forecast(horizon=14)
    print(forecast.to_json())
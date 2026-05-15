# ml/ueba/model.py
from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest


class UEBAModel:
    def __init__(
        self,
        contamination: float = 0.05,
        random_state: int = 42,
        n_estimators: int = 100,
        **config: Any,
    ) -> None:
        self.contamination = contamination
        self.random_state = random_state
        self.n_estimators = n_estimators
        self.config = dict(config)

        self.is_trained: bool = False
        self.feature_names: list[str] = []

        self.model = IsolationForest(
            contamination=self.contamination,
            random_state=self.random_state,
            n_estimators=self.n_estimators,
        )

    def _to_numpy(self, data: Any) -> np.ndarray:
        if isinstance(data, pd.DataFrame):
            if not self.feature_names:
                self.feature_names = list(data.columns)
            values = data.to_numpy(dtype=float)
        elif isinstance(data, pd.Series):
            values = data.to_numpy(dtype=float).reshape(1, -1)
        else:
            values = np.asarray(data, dtype=float)

        if values.ndim == 1:
            values = values.reshape(1, -1)

        if values.size == 0 or values.shape[0] == 0:
            raise ValueError("UEBA input data cannot be empty")

        if not np.isfinite(values).all():
            raise ValueError("UEBA input data contains NaN or infinite values")

        return values

    def train(self, data: Any, *_args: Any, **_kwargs: Any) -> "UEBAModel":
        values = self._to_numpy(data)

        if isinstance(data, pd.DataFrame):
            self.feature_names = list(data.columns)
        elif not self.feature_names:
            self.feature_names = [
                f"feature_{index}"
                for index in range(values.shape[1])
            ]

        self.model.fit(values)
        self.is_trained = True

        return self

    def fit(self, data: Any, *args: Any, **kwargs: Any) -> "UEBAModel":
        return self.train(data, *args, **kwargs)

    def _ensure_trained(self) -> None:
        if not self.is_trained:
            raise RuntimeError("UEBA model must be trained before prediction")

    def predict(self, data: Any, *_args: Any, **_kwargs: Any) -> np.ndarray:
        self._ensure_trained()

        values = self._to_numpy(data)
        raw_predictions = self.model.predict(values)

        return np.where(raw_predictions == -1, 1, 0).astype(int)

    def predict_proba(self, data: Any, *_args: Any, **_kwargs: Any) -> np.ndarray:
        self._ensure_trained()

        values = self._to_numpy(data)

        if hasattr(self.model, "decision_function"):
            scores = -self.model.decision_function(values)
        else:
            predictions = self.model.predict(values)
            scores = np.asarray(predictions, dtype=float)

        min_score = float(np.min(scores))
        max_score = float(np.max(scores))

        if max_score == min_score:
            probabilities = np.full(scores.shape, 0.5, dtype=float)
        else:
            probabilities = (scores - min_score) / (max_score - min_score)

        return np.clip(probabilities, 0.0, 1.0)

    def score(self, data: Any, *_args: Any, **_kwargs: Any) -> float:
        probabilities = self.predict_proba(data)

        return float(np.mean(probabilities))

    def save(self, path: str | Path) -> Path:
        self._ensure_trained()

        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "model": self.model,
            "is_trained": self.is_trained,
            "feature_names": self.feature_names,
            "contamination": self.contamination,
            "random_state": self.random_state,
            "n_estimators": self.n_estimators,
            "config": self.config,
        }

        joblib.dump(payload, output_path)

        return output_path

    def load(self, path: str | Path) -> "UEBAModel":
        input_path = Path(path)

        if not input_path.exists():
            raise FileNotFoundError(
                f"UEBA model artifact not found: {input_path}"
            )

        payload = joblib.load(input_path)

        self.model = payload["model"]
        self.is_trained = bool(payload.get("is_trained", True))
        self.feature_names = list(payload.get("feature_names", []))
        self.contamination = float(payload.get("contamination", self.contamination))
        self.random_state = int(payload.get("random_state", self.random_state))
        self.n_estimators = int(payload.get("n_estimators", self.n_estimators))
        self.config = dict(payload.get("config", self.config))

        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": "UEBAModel",
            "is_trained": self.is_trained,
            "feature_names": self.feature_names,
            "contamination": self.contamination,
            "random_state": self.random_state,
            "n_estimators": self.n_estimators,
            "config": self.config,
        }


__all__ = ["UEBAModel"]
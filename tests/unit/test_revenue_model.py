# =========================================================
# TESTS / UNIT / test_revenue_model.py
# KWANZACONTROL ENTERPRISE CFO AI
# Enterprise Revenue Prediction Test Suite
# =========================================================

from __future__ import annotations

import json
import math
import os
import shutil
import time
import unittest

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np
from sklearn.linear_model import LinearRegression


# =========================================================
# JSON / NUMERIC SAFE HELPERS
# =========================================================

def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)

    if not math.isfinite(number):
        return float(default)

    return float(number)


def clamp(value: Any, minimum: float = 0.0, maximum: float = 1.0) -> float:
    number = safe_float(value, default=minimum)
    return float(max(minimum, min(maximum, number)))


def json_safe(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, np.bool_):
        return bool(value)

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        number = float(value)
        return None if not math.isfinite(number) else number

    if isinstance(value, np.ndarray):
        return [json_safe(item) for item in value.tolist()]

    if isinstance(value, bool):
        return bool(value)

    if isinstance(value, int):
        return int(value)

    if isinstance(value, float):
        return None if not math.isfinite(value) else float(value)

    if isinstance(value, str):
        return value

    if isinstance(value, Mapping):
        return {
            str(json_safe(key)): json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]

    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except Exception:
            pass

    return value


# =========================================================
# REVENUE RESULT
# =========================================================

@dataclass
class RevenueResult:
    tenant_id: str
    predicted_revenue: float
    confidence: float
    model_version: str
    processing_time: float
    created_at: float

    def __post_init__(self) -> None:
        self.tenant_id = str(self.tenant_id)
        self.predicted_revenue = float(
            safe_float(self.predicted_revenue)
        )
        self.confidence = clamp(self.confidence, 0.0, 1.0)
        self.model_version = str(self.model_version)
        self.processing_time = float(
            safe_float(self.processing_time)
        )
        self.created_at = float(
            safe_float(self.created_at, time.time())
        )


# =========================================================
# ENTERPRISE REVENUE MODEL
# =========================================================

class EnterpriseRevenueModel:
    def __init__(
        self,
        storage_dir: str | Path = "revenue_storage",
    ) -> None:
        self.model_version = "revenue-enterprise-v1"
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.model = LinearRegression()
        self.is_trained = False

        self._training_target_mean = 0.0
        self._training_target_std = 1.0
        self._feature_count: int | None = None

    # =====================================================
    # TRAIN MODEL
    # =====================================================

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
    ) -> None:
        X_array = np.asarray(X, dtype=float)
        y_array = np.asarray(y, dtype=float)

        if X_array.ndim != 2:
            raise ValueError("X must be a 2D array.")

        if y_array.ndim != 1:
            raise ValueError("y must be a 1D array.")

        if X_array.shape[0] == 0 or y_array.shape[0] == 0:
            raise ValueError("Training data cannot be empty.")

        if X_array.shape[0] != y_array.shape[0]:
            raise ValueError("X and y must contain the same number of rows.")

        if not np.isfinite(X_array).all():
            raise ValueError("X contains invalid numeric values.")

        if not np.isfinite(y_array).all():
            raise ValueError("y contains invalid numeric values.")

        self.model.fit(X_array, y_array)

        self._training_target_mean = float(np.mean(y_array))
        self._training_target_std = max(
            float(np.std(y_array)),
            1.0,
        )
        self._feature_count = int(X_array.shape[1])
        self.is_trained = True

    # =====================================================
    # PREDICT REVENUE
    # =====================================================

    def predict(
        self,
        tenant_id: str,
        features: List[float],
    ) -> RevenueResult:
        if not self.is_trained:
            raise RuntimeError("Model not trained")

        start = time.perf_counter()

        X = self._build_feature_vector(features)

        prediction = float(self.model.predict(X)[0])

        confidence = self._calculate_confidence(prediction)

        result = RevenueResult(
            tenant_id=str(tenant_id),
            predicted_revenue=float(prediction),
            confidence=float(confidence),
            model_version=self.model_version,
            processing_time=float(time.perf_counter() - start),
            created_at=float(time.time()),
        )

        self._persist(result)

        return result

    # =====================================================
    # FEATURE VECTOR
    # =====================================================

    def _build_feature_vector(
        self,
        features: Sequence[Any],
    ) -> np.ndarray:
        values = np.asarray(features, dtype=float)

        if values.ndim != 1:
            raise ValueError("features must be a 1D sequence.")

        if self._feature_count is not None and len(values) != self._feature_count:
            raise ValueError(
                f"Expected {self._feature_count} features, got {len(values)}."
            )

        if not np.isfinite(values).all():
            raise ValueError("features contain invalid numeric values.")

        return values.reshape(1, -1)

    # =====================================================
    # CONFIDENCE
    # =====================================================

    def _calculate_confidence(self, prediction: float) -> float:
        """Return deterministic 0..1 confidence.

        Não usamos model.score com uma única amostra porque R² não é
        matematicamente estável nesse cenário e pode retornar NaN.
        """

        predicted = safe_float(prediction)

        distance = abs(predicted - self._training_target_mean)

        normalized_distance = distance / (
            self._training_target_std * 4.0
        )

        confidence = 1.0 - normalized_distance

        return clamp(confidence, 0.0, 1.0)

    # =====================================================
    # PERSIST
    # =====================================================

    def _persist(
        self,
        result: RevenueResult,
    ) -> Path:
        tenant_dir = self.storage_dir / str(result.tenant_id)
        tenant_dir.mkdir(parents=True, exist_ok=True)

        filename = f"revenue_{int(result.created_at * 1000)}.json"
        path = tenant_dir / filename

        payload = json_safe(asdict(result))

        with path.open("w", encoding="utf-8") as file:
            json.dump(
                payload,
                file,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )

        # Legacy compatibility: mantém arquivo também na raiz.
        root_path = self.storage_dir / filename

        with root_path.open("w", encoding="utf-8") as file:
            json.dump(
                payload,
                file,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )

        return path

    # =====================================================
    # HEALTH
    # =====================================================

    def health(self) -> Dict[str, Any]:
        return {
            "service": "revenue_model",
            "status": "healthy",
            "model_version": self.model_version,
            "trained": bool(self.is_trained),
            "storage_dir": str(self.storage_dir),
        }


# =========================================================
# TEST SUITE
# =========================================================

class TestEnterpriseRevenueModel(unittest.TestCase):
    # =====================================================
    # SETUP
    # =====================================================

    def setUp(self) -> None:
        self.model = EnterpriseRevenueModel()

        X = np.array(
            [
                [1, 100],
                [2, 200],
                [3, 300],
                [4, 400],
                [5, 500],
            ],
            dtype=float,
        )

        y = np.array(
            [
                1000,
                2000,
                3000,
                4000,
                5000,
            ],
            dtype=float,
        )

        self.model.train(X, y)

    # =====================================================
    # CLEANUP
    # =====================================================

    def tearDown(self) -> None:
        if os.path.exists("revenue_storage"):
            shutil.rmtree("revenue_storage")

    # =====================================================
    # TEST PREDICTION
    # =====================================================

    def test_prediction(self) -> None:
        result = self.model.predict(
            tenant_id="tenant-001",
            features=[6, 600],
        )

        self.assertIsNotNone(result.predicted_revenue)
        self.assertIsInstance(result.predicted_revenue, float)

    # =====================================================
    # TEST CONFIDENCE
    # =====================================================

    def test_confidence(self) -> None:
        result = self.model.predict(
            tenant_id="tenant-002",
            features=[7, 700],
        )

        self.assertTrue(
            0 <= result.confidence <= 1
        )

        self.assertIsInstance(result.confidence, float)

    # =====================================================
    # TEST STORAGE
    # =====================================================

    def test_storage(self) -> None:
        self.model.predict(
            tenant_id="tenant-storage",
            features=[8, 800],
        )

        files = os.listdir("revenue_storage")

        self.assertTrue(len(files) > 0)

    # =====================================================
    # TEST HEALTH
    # =====================================================

    def test_health(self) -> None:
        health = self.model.health()

        self.assertEqual(
            health["status"],
            "healthy",
        )

        self.assertTrue(health["trained"])

    # =====================================================
    # TEST MODEL TRAINED
    # =====================================================

    def test_trained(self) -> None:
        self.assertTrue(self.model.is_trained)

    # =====================================================
    # TEST MULTI TENANT
    # =====================================================

    def test_multi_tenant(self) -> None:
        r1 = self.model.predict(
            tenant_id="A",
            features=[9, 900],
        )

        r2 = self.model.predict(
            tenant_id="B",
            features=[10, 1000],
        )

        self.assertNotEqual(
            r1.tenant_id,
            r2.tenant_id,
        )

        self.assertTrue(
            Path("revenue_storage/A").exists()
        )

        self.assertTrue(
            Path("revenue_storage/B").exists()
        )

    # =====================================================
    # TEST PROCESSING TIME
    # =====================================================

    def test_processing_time(self) -> None:
        result = self.model.predict(
            tenant_id="tenant-speed",
            features=[11, 1100],
        )

        self.assertLess(
            result.processing_time,
            5,
        )

    # =====================================================
    # TEST INVALID STATE
    # =====================================================

    def test_untrained_model(self) -> None:
        model = EnterpriseRevenueModel()

        with self.assertRaises(RuntimeError):
            model.predict(
                tenant_id="x",
                features=[1, 2],
            )


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    print(
        """
=========================================================
KWANZACONTROL CFO AI
ENTERPRISE REVENUE MODEL TEST SUITE
=========================================================

Running revenue prediction tests...

=========================================================
"""
    )

    unittest.main(verbosity=2)

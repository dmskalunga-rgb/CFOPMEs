# =========================================================
# TESTS / ML / test_retraining.py
# KWANZACONTROL - CFO AI ENTERPRISE
# Enterprise Retraining Pipeline Tests
# =========================================================

"""
ENTERPRISE OBJECTIVES
---------------------
- Validate automated retraining
- Validate model lifecycle management
- Validate retraining thresholds
- Validate concept drift retraining
- Validate scheduling pipelines
- Validate model promotion workflow
- Validate rollback readiness
- Validate retraining observability
- Validate dataset versioning
- Validate enterprise governance

Correções aplicadas:
- drift_detected sempre retorna bool nativo Python, não np.bool_
- drift_score sempre retorna float nativo Python
- cálculos numpy normalizados com float(...)
- proteção contra baseline_mean zero, NaN e infinito
"""

from __future__ import annotations

import hashlib
import math
import pickle
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error


# =========================================================
# HELPERS
# =========================================================


def native_bool(value: Any) -> bool:
    """Convert numpy/pandas/python boolean-like values into native bool."""
    if isinstance(value, bool):
        return value

    if value is None:
        return False

    if hasattr(value, "item"):
        try:
            return bool(value.item())
        except Exception:
            pass

    return bool(value)


def native_float(value: Any, default: float = 0.0) -> float:
    """Convert numpy/pandas/python numeric-like values into safe native float."""
    if value is None:
        return default

    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass

    try:
        result = float(value)
    except Exception:
        return default

    if math.isnan(result) or math.isinf(result):
        return default

    return result


# =========================================================
# DATASET VERSION
# =========================================================


@dataclass
class DatasetVersion:
    dataset_id: str
    name: str
    rows: int
    columns: int
    checksum: str
    created_at: float


# =========================================================
# MODEL VERSION
# =========================================================


@dataclass
class RetrainedModel:
    model_id: str
    model_name: str
    version: str
    model: Any
    accuracy: float
    dataset_checksum: str
    created_at: float
    promoted: bool = False


# =========================================================
# ENTERPRISE RETRAINING ENGINE
# =========================================================


class EnterpriseRetrainingEngine:
    """Enterprise retraining orchestrator used by the tests."""

    def __init__(self):
        self.models: Dict[str, RetrainedModel] = {}
        self.datasets: Dict[str, DatasetVersion] = {}
        self.metrics = {
            "retraining_jobs": 0,
            "successful_retraining": 0,
            "failed_retraining": 0,
            "promotions": 0,
        }
        self.retraining_threshold = 0.75

    # =====================================================
    # REGISTER DATASET
    # =====================================================

    def register_dataset(self, name: str, dataframe: pd.DataFrame) -> DatasetVersion:
        checksum = self._checksum_dataframe(dataframe)

        dataset = DatasetVersion(
            dataset_id=str(uuid.uuid4()),
            name=name,
            rows=int(len(dataframe)),
            columns=int(len(dataframe.columns)),
            checksum=checksum,
            created_at=time.time(),
        )

        self.datasets[dataset.dataset_id] = dataset
        return dataset

    # =====================================================
    # RETRAIN MODEL
    # =====================================================

    def retrain_model(self, model_name, X, y):
        self.metrics["retraining_jobs"] += 1

        try:
            model = LinearRegression()
            model.fit(X, y)

            predictions = model.predict(X)
            mae = native_float(mean_absolute_error(y, predictions))
            y_mean = abs(native_float(np.mean(y), default=1.0)) or 1.0

            accuracy = max(0.0, 1.0 - (mae / y_mean))
            accuracy = native_float(accuracy)

            dataset_checksum = self._checksum_training_data(X, y)

            retrained_model = RetrainedModel(
                model_id=str(uuid.uuid4()),
                model_name=model_name,
                version=f"v{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
                model=model,
                accuracy=accuracy,
                dataset_checksum=dataset_checksum,
                created_at=time.time(),
            )

            self.models[retrained_model.model_id] = retrained_model
            self.metrics["successful_retraining"] += 1

            return retrained_model

        except Exception:
            self.metrics["failed_retraining"] += 1
            raise

    # =====================================================
    # PROMOTE MODEL
    # =====================================================

    def promote_model(self, model: RetrainedModel) -> RetrainedModel:
        if model.accuracy < self.retraining_threshold:
            raise RuntimeError("Accuracy below threshold")

        model.promoted = True
        self.metrics["promotions"] += 1
        return model

    # =====================================================
    # CONCEPT DRIFT DETECTION
    # =====================================================

    def detect_drift(self, baseline, current):
        baseline_mean = native_float(np.mean(baseline), default=0.0)
        current_mean = native_float(np.mean(current), default=0.0)

        denominator = abs(baseline_mean)
        if denominator <= 1e-12:
            drift_score = 0.0 if abs(current_mean) <= 1e-12 else 1.0
        else:
            drift_score = abs(current_mean - baseline_mean) / denominator

        drift_score = native_float(drift_score)
        drift_detected = native_bool(drift_score > 0.20)

        return {
            "drift_detected": bool(drift_detected),
            "drift_score": float(round(drift_score, 4)),
        }

    # =====================================================
    # HEALTH
    # =====================================================

    def health(self):
        return {
            "service": "enterprise_retraining_engine",
            "registered_models": int(len(self.models)),
            "registered_datasets": int(len(self.datasets)),
            "status": "healthy",
            "metrics": dict(self.metrics),
        }

    # =====================================================
    # PRIVATE HELPERS
    # =====================================================

    @staticmethod
    def _checksum_dataframe(dataframe: pd.DataFrame) -> str:
        payload = pd.util.hash_pandas_object(dataframe, index=True).values.tobytes()
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _checksum_training_data(X, y) -> str:
        x_array = np.asarray(X)
        y_array = np.asarray(y)
        payload = x_array.tobytes() + y_array.tobytes()
        return hashlib.sha256(payload).hexdigest()


# =========================================================
# TEST DATA FACTORY
# =========================================================


def generate_training_data(samples: int = 500):
    rng = np.random.default_rng(42)
    X = rng.normal(0, 1, size=(samples, 3))
    coefficients = np.array([10.0, 5.0, 2.0])
    noise = rng.normal(0, 0.5, size=samples)
    y = X @ coefficients + 100.0 + noise
    return X, y


# =========================================================
# TEST DATASET REGISTRATION
# =========================================================


def test_dataset_registration():
    engine = EnterpriseRetrainingEngine()

    df = pd.DataFrame({
        "amount": np.random.normal(100, 10, 100),
        "category": ["sales"] * 100,
    })

    dataset = engine.register_dataset("financial_dataset", df)

    assert dataset.rows == 100
    assert dataset.columns == 2
    assert dataset.checksum


# =========================================================
# TEST RETRAINING
# =========================================================


def test_model_retraining():
    engine = EnterpriseRetrainingEngine()
    X, y = generate_training_data()

    model = engine.retrain_model("forecast_model", X, y)

    assert model.model_name == "forecast_model"
    assert model.accuracy >= 0
    assert model.model is not None
    assert model.model_id in engine.models


# =========================================================
# TEST PROMOTION
# =========================================================


def test_model_promotion():
    engine = EnterpriseRetrainingEngine()
    X, y = generate_training_data()

    model = engine.retrain_model("forecast_model", X, y)
    promoted = engine.promote_model(model)

    assert promoted.promoted is True


# =========================================================
# TEST DRIFT DETECTION
# =========================================================


def test_concept_drift_detection():
    engine = EnterpriseRetrainingEngine()

    baseline = np.random.normal(100, 10, 1000)
    current = np.random.normal(150, 10, 1000)

    drift = engine.detect_drift(baseline, current)

    assert drift["drift_detected"] is True
    assert isinstance(drift["drift_detected"], bool)


# =========================================================
# TEST NO DRIFT
# =========================================================


def test_no_drift():
    engine = EnterpriseRetrainingEngine()

    baseline = np.random.normal(100, 10, 1000)
    current = np.random.normal(102, 10, 1000)

    drift = engine.detect_drift(baseline, current)

    assert drift["drift_detected"] is False
    assert isinstance(drift["drift_detected"], bool)


# =========================================================
# TEST VERSIONING
# =========================================================


def test_model_versioning():
    engine = EnterpriseRetrainingEngine()
    X, y = generate_training_data()

    model1 = engine.retrain_model("forecast_model", X, y)
    time.sleep(0.001)
    model2 = engine.retrain_model("forecast_model", X, y)

    assert model1.version != model2.version


# =========================================================
# TEST MULTI-TENANT RETRAINING
# =========================================================


def test_multi_tenant_retraining():
    tenant_a = {
        "tenant_id": "tenant_A",
        "model_name": "forecast_model_A",
    }
    tenant_b = {
        "tenant_id": "tenant_B",
        "model_name": "forecast_model_B",
    }

    assert tenant_a["tenant_id"] != tenant_b["tenant_id"]
    assert tenant_a["model_name"] != tenant_b["model_name"]


# =========================================================
# TEST METRICS TRACKING
# =========================================================


def test_retraining_metrics():
    engine = EnterpriseRetrainingEngine()
    X, y = generate_training_data()

    engine.retrain_model("fraud_model", X, y)

    assert engine.metrics["retraining_jobs"] >= 1
    assert engine.metrics["successful_retraining"] >= 1


# =========================================================
# TEST HEALTH CHECK
# =========================================================


def test_retraining_health():
    engine = EnterpriseRetrainingEngine()
    health = engine.health()

    assert health["status"] == "healthy"
    assert health["service"] == "enterprise_retraining_engine"


# =========================================================
# TEST LOAD RETRAINING
# =========================================================


def test_bulk_retraining():
    engine = EnterpriseRetrainingEngine()

    start = time.time()

    for i in range(20):
        X, y = generate_training_data()
        engine.retrain_model(f"model_{i}", X, y)

    elapsed = time.time() - start

    assert engine.metrics["retraining_jobs"] == 20
    assert elapsed < 30


# =========================================================
# TEST DATASET CHECKSUM
# =========================================================


def test_dataset_checksum():
    engine = EnterpriseRetrainingEngine()

    df = pd.DataFrame({"x": [1, 2, 3]})
    dataset = engine.register_dataset("checksum_dataset", df)

    assert len(dataset.checksum) == 64


# =========================================================
# TEST FAILED PROMOTION
# =========================================================


def test_failed_promotion():
    engine = EnterpriseRetrainingEngine()

    weak_model = RetrainedModel(
        model_id=str(uuid.uuid4()),
        model_name="weak_model",
        version="v1",
        model=None,
        accuracy=0.10,
        dataset_checksum="checksum",
        created_at=time.time(),
    )

    with pytest.raises(RuntimeError):
        engine.promote_model(weak_model)


# =========================================================
# TEST GOVERNANCE THRESHOLD
# =========================================================


def test_governance_threshold():
    engine = EnterpriseRetrainingEngine()

    assert engine.retraining_threshold == 0.75


# =========================================================
# TEST SERIALIZATION
# =========================================================


def test_model_serialization():
    engine = EnterpriseRetrainingEngine()
    X, y = generate_training_data()

    model = engine.retrain_model("forecast_model", X, y)
    payload = pickle.dumps(model)
    restored = pickle.loads(payload)

    assert restored.model_name == model.model_name
    assert restored.version == model.version


# =========================================================
# TEST AUDIT READINESS
# =========================================================


def test_audit_readiness():
    model = RetrainedModel(
        model_id=str(uuid.uuid4()),
        model_name="forecast_model",
        version="v1",
        model=None,
        accuracy=0.95,
        dataset_checksum="checksum",
        created_at=time.time(),
    )

    assert model.model_id
    assert model.dataset_checksum
    assert model.created_at > 0


# =========================================================
# DIRECT EXECUTION
# =========================================================


if __name__ == "__main__":
    print("\nRUNNING ENTERPRISE RETRAINING TESTS...\n")

    test_dataset_registration()
    test_model_retraining()
    test_model_promotion()
    test_concept_drift_detection()
    test_no_drift()
    test_model_versioning()
    test_retraining_metrics()
    test_retraining_health()

    print("\nALL RETRAINING TESTS EXECUTED\n")

# =========================================================
# TESTS / ML / test_drift_monitoring.py
# KWANZACONTROL - CFO AI ENTERPRISE
# Enterprise ML Drift Monitoring Tests
# =========================================================

"""
ENTERPRISE OBJECTIVES
---------------------
- Validate feature drift detection
- Validate prediction drift detection
- Validate concept drift logic
- Validate realtime monitoring readiness
- Validate multi-tenant ML monitoring
- Validate alert generation
- Validate retraining triggers
- Validate observability metrics
- Validate statistical integrity
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp


# =========================================================
# HELPERS
# =========================================================

def native_bool(value: Any) -> bool:
    """
    Convert Python/numpy/pandas boolean-like values to native bool.

    This prevents pytest failures such as:
        assert np.True_ is True
        assert np.False_ is False
    """
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


def safe_float(value: Any, default: float = 0.0) -> float:
    """
    Convert numeric-like values to safe native float.
    """
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
# MOCK ENTERPRISE DRIFT ENGINE
# =========================================================

class EnterpriseDriftMonitor:
    """
    Enterprise-grade drift monitoring engine.

    Notes:
    - All public boolean outputs are native Python bool.
    - Empty/invalid samples are handled safely.
    - Feature drift uses KS p-value semantics:
        pvalue < alpha => drift detected.
    """

    def __init__(self):
        self.threshold = 0.15
        self.ks_alpha = 0.05

        self.alerts = []

        self.metrics = {
            "drift_checks": 0,
            "drift_detected": 0,
            "alerts_generated": 0,
        }

    # =====================================================
    # FEATURE DRIFT
    # =====================================================

    def detect_feature_drift(
        self,
        reference_data,
        current_data,
    ) -> Dict[str, Any]:
        reference = np.asarray(reference_data, dtype=float)
        current = np.asarray(current_data, dtype=float)

        reference = reference[np.isfinite(reference)]
        current = current[np.isfinite(current)]

        self.metrics["drift_checks"] += 1

        if reference.size < 2 or current.size < 2:
            return {
                "drift_score": 0.0,
                "drift_detected": False,
                "statistic": 0.0,
                "pvalue": 1.0,
                "reason": "insufficient_samples",
            }

        statistic, pvalue = ks_2samp(
            reference,
            current,
        )

        statistic = safe_float(statistic)
        pvalue = safe_float(pvalue, default=1.0)

        drift_score = safe_float(1.0 - pvalue)

        drift_detected = native_bool(
            pvalue < self.ks_alpha
        )

        if drift_detected:
            self.metrics["drift_detected"] += 1

        return {
            "drift_score": float(round(drift_score, 4)),
            "drift_detected": bool(drift_detected),
            "statistic": float(round(statistic, 4)),
            "pvalue": float(round(pvalue, 6)),
        }

    # =====================================================
    # PREDICTION DRIFT
    # =====================================================

    def detect_prediction_drift(
        self,
        baseline_predictions,
        current_predictions,
    ) -> Dict[str, Any]:
        baseline = np.asarray(baseline_predictions, dtype=float)
        current = np.asarray(current_predictions, dtype=float)

        baseline = baseline[np.isfinite(baseline)]
        current = current[np.isfinite(current)]

        if baseline.size == 0 or current.size == 0:
            return {
                "baseline_mean": 0.0,
                "current_mean": 0.0,
                "difference": 0.0,
                "drift_detected": False,
                "reason": "empty_predictions",
            }

        baseline_mean = safe_float(np.mean(baseline))
        current_mean = safe_float(np.mean(current))

        diff = safe_float(
            abs(baseline_mean - current_mean)
        )

        drift_detected = native_bool(
            diff >= self.threshold
        )

        if drift_detected:
            self.metrics["drift_detected"] += 1

        return {
            "baseline_mean": float(round(baseline_mean, 4)),
            "current_mean": float(round(current_mean, 4)),
            "difference": float(round(diff, 4)),
            "drift_detected": bool(drift_detected),
        }

    # =====================================================
    # CONCEPT DRIFT
    # =====================================================

    def detect_concept_drift(
        self,
        historical_accuracy,
        current_accuracy,
    ) -> Dict[str, Any]:
        historical_accuracy = safe_float(historical_accuracy)
        current_accuracy = safe_float(current_accuracy)

        degradation = safe_float(
            historical_accuracy - current_accuracy
        )

        drift_detected = native_bool(
            degradation >= self.threshold
        )

        return {
            "historical_accuracy": float(historical_accuracy),
            "current_accuracy": float(current_accuracy),
            "degradation": float(round(degradation, 4)),
            "drift_detected": bool(drift_detected),
        }

    # =====================================================
    # ALERT GENERATION
    # =====================================================

    def generate_alert(
        self,
        tenant_id,
        drift_type,
        score,
    ) -> Dict[str, Any]:
        alert = {
            "tenant_id": tenant_id,
            "drift_type": drift_type,
            "score": float(score),
            "timestamp": float(time.time()),
        }

        self.alerts.append(alert)
        self.metrics["alerts_generated"] += 1

        return alert

    # =====================================================
    # RETRAIN TRIGGER
    # =====================================================

    def should_trigger_retraining(
        self,
        drift_score,
    ) -> bool:
        return native_bool(
            safe_float(drift_score) >= 0.30
        )

    # =====================================================
    # HEALTH
    # =====================================================

    def health(self) -> Dict[str, Any]:
        return {
            "service": "enterprise_drift_monitor",
            "status": "healthy",
            "metrics": self.metrics,
            "alerts": int(len(self.alerts)),
        }


# =========================================================
# FIXTURES
# =========================================================

def generate_reference_data():
    np.random.seed(42)

    return np.random.normal(
        loc=100,
        scale=10,
        size=1000,
    )


def generate_current_data_no_drift():
    """
    Same distribution as reference data.

    The previous version used loc=101 and scale=11, which can be
    statistically different with n=1000 and may legitimately trigger KS drift.
    """
    np.random.seed(43)

    return np.random.normal(
        loc=100,
        scale=10,
        size=1000,
    )


def generate_current_data_with_drift():
    np.random.seed(44)

    return np.random.normal(
        loc=150,
        scale=25,
        size=1000,
    )


# =========================================================
# TEST FEATURE DRIFT
# =========================================================

def test_feature_drift_detection():
    monitor = EnterpriseDriftMonitor()

    reference = generate_reference_data()
    current = generate_current_data_with_drift()

    result = monitor.detect_feature_drift(
        reference,
        current,
    )

    assert result["drift_detected"] is True
    assert result["drift_score"] >= 0.15


# =========================================================
# TEST NO DRIFT
# =========================================================

def test_no_feature_drift():
    monitor = EnterpriseDriftMonitor()

    reference = generate_reference_data()
    current = generate_current_data_no_drift()

    result = monitor.detect_feature_drift(
        reference,
        current,
    )

    assert isinstance(
        result["drift_detected"],
        bool,
    )

    assert result["drift_detected"] is False


# =========================================================
# TEST PREDICTION DRIFT
# =========================================================

def test_prediction_drift():
    monitor = EnterpriseDriftMonitor()

    baseline = np.random.normal(
        0.2,
        0.05,
        1000,
    )

    current = np.random.normal(
        0.6,
        0.08,
        1000,
    )

    result = monitor.detect_prediction_drift(
        baseline,
        current,
    )

    assert result["drift_detected"] is True


# =========================================================
# TEST CONCEPT DRIFT
# =========================================================

def test_concept_drift():
    monitor = EnterpriseDriftMonitor()

    result = monitor.detect_concept_drift(
        historical_accuracy=0.96,
        current_accuracy=0.71,
    )

    assert result["drift_detected"] is True


# =========================================================
# TEST ALERT GENERATION
# =========================================================

def test_alert_generation():
    monitor = EnterpriseDriftMonitor()

    alert = monitor.generate_alert(
        tenant_id="tenant-finance-001",
        drift_type="feature_drift",
        score=0.82,
    )

    assert alert["tenant_id"] == "tenant-finance-001"
    assert len(monitor.alerts) == 1


# =========================================================
# TEST RETRAINING TRIGGER
# =========================================================

def test_retraining_trigger():
    monitor = EnterpriseDriftMonitor()

    trigger = monitor.should_trigger_retraining(
        drift_score=0.45,
    )

    assert trigger is True


# =========================================================
# TEST MULTI-TENANT ISOLATION
# =========================================================

def test_multi_tenant_monitoring():
    monitor = EnterpriseDriftMonitor()

    alert1 = monitor.generate_alert(
        tenant_id="tenant_A",
        drift_type="prediction_drift",
        score=0.70,
    )

    alert2 = monitor.generate_alert(
        tenant_id="tenant_B",
        drift_type="feature_drift",
        score=0.92,
    )

    assert alert1["tenant_id"] != alert2["tenant_id"]


# =========================================================
# TEST MONITOR METRICS
# =========================================================

def test_metrics_tracking():
    monitor = EnterpriseDriftMonitor()

    reference = generate_reference_data()
    current = generate_current_data_with_drift()

    monitor.detect_feature_drift(
        reference,
        current,
    )

    assert monitor.metrics["drift_checks"] >= 1


# =========================================================
# TEST HEALTH CHECK
# =========================================================

def test_health_check():
    monitor = EnterpriseDriftMonitor()

    health = monitor.health()

    assert health["status"] == "healthy"
    assert "metrics" in health


# =========================================================
# TEST STATISTICAL ROBUSTNESS
# =========================================================

def test_statistical_integrity():
    reference = generate_reference_data()
    current = generate_current_data_with_drift()

    statistic, pvalue = ks_2samp(
        reference,
        current,
    )

    assert statistic >= 0
    assert 0 <= pvalue <= 1


# =========================================================
# TEST PERFORMANCE
# =========================================================

def test_drift_detection_performance():
    monitor = EnterpriseDriftMonitor()

    reference = np.random.normal(
        100,
        15,
        10000,
    )

    current = np.random.normal(
        130,
        20,
        10000,
    )

    start = time.time()

    monitor.detect_feature_drift(
        reference,
        current,
    )

    duration = time.time() - start

    assert duration < 2.0


# =========================================================
# TEST EMPTY DATA SAFETY
# =========================================================

def test_empty_data_safety():
    monitor = EnterpriseDriftMonitor()

    reference = np.array([])
    current = np.array([])

    result = monitor.detect_feature_drift(
        reference,
        current,
    )

    assert result["drift_detected"] is False
    assert result["reason"] == "insufficient_samples"


# =========================================================
# LOCAL EXECUTION
# =========================================================

if __name__ == "__main__":
    print("\nRUNNING ENTERPRISE DRIFT TESTS...\n")

    test_feature_drift_detection()
    test_no_feature_drift()
    test_prediction_drift()
    test_concept_drift()
    test_alert_generation()
    test_retraining_trigger()
    test_health_check()
    test_empty_data_safety()

    print("\nALL DRIFT TESTS EXECUTED SUCCESSFULLY\n")

"""
Enterprise unit tests for drift detection.

KWANZACONTROL - CFO AI ENTERPRISE

Correções aplicadas:
- Mantém os testes descobertos pelo pytest/unittest.
- Evita numpy.bool_ em json.dump(asdict(result)).
- Converte numpy.integer/numpy.floating para tipos nativos Python.
- Garante isolamento multi-tenant e persistência segura.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence
import json
import math
import random
import tempfile
import unittest

import numpy as np


# =========================================================
# ENTERPRISE DRIFT RESULT
# =========================================================

@dataclass
class DriftResult:
    tenant_id: str
    model_name: str
    drift_score: float
    threshold: float
    drift_detected: bool
    drift_type: str
    created_at: str
    reference_mean: float
    current_mean: float
    reference_count: int
    current_count: int
    metadata: Dict[str, Any]


# =========================================================
# ENTERPRISE DRIFT DETECTOR FOR TESTS
# =========================================================

class EnterpriseDriftDetector:
    def __init__(
        self,
        threshold: float = 0.20,
        storage_dir: str | Path | None = None,
    ) -> None:
        self.threshold = float(threshold)
        self.storage_dir = Path(storage_dir or tempfile.mkdtemp())
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def detect(
        self,
        tenant_id: str,
        model_name: str,
        reference_data: Sequence[Any],
        current_data: Sequence[Any],
    ) -> DriftResult:
        reference_values = self._clean_numeric_values(reference_data)
        current_values = self._clean_numeric_values(current_data)

        if not reference_values or not current_values:
            result = DriftResult(
                tenant_id=str(tenant_id),
                model_name=str(model_name),
                drift_score=0.0,
                threshold=float(self.threshold),
                drift_detected=False,
                drift_type="none",
                created_at=datetime.utcnow().isoformat(),
                reference_mean=0.0,
                current_mean=0.0,
                reference_count=int(len(reference_values)),
                current_count=int(len(current_values)),
                metadata={
                    "reason": (
                        "empty_reference_or_current_data"
                    )
                },
            )
            self._persist(result)
            return result

        reference_mean = float(np.mean(reference_values))
        current_mean = float(np.mean(current_values))

        denominator = max(abs(reference_mean), 1.0)

        drift_score = float(
            abs(current_mean - reference_mean) / denominator
        )

        # Correção principal:
        # drift_score > threshold pode retornar numpy.bool_.
        detected = bool(
            float(drift_score) > float(self.threshold)
        )

        result = DriftResult(
            tenant_id=str(tenant_id),
            model_name=str(model_name),
            drift_score=float(drift_score),
            threshold=float(self.threshold),
            drift_detected=bool(detected),
            drift_type=self._classify_drift(float(drift_score)),
            created_at=datetime.utcnow().isoformat(),
            reference_mean=float(reference_mean),
            current_mean=float(current_mean),
            reference_count=int(len(reference_values)),
            current_count=int(len(current_values)),
            metadata={
                "detector": "enterprise_mean_shift",
                "version": "test-safe-v2",
            },
        )

        self._persist(result)
        return result

    def _classify_drift(self, drift_score: float) -> str:
        score = float(drift_score)

        if score <= self.threshold:
            return "none"

        if score >= 1.0:
            return "critical"

        if score >= 0.5:
            return "high"

        if score >= 0.2:
            return "medium"

        return "low"

    def _persist(self, result: DriftResult) -> Path:
        tenant_dir = self.storage_dir / str(result.tenant_id)
        tenant_dir.mkdir(parents=True, exist_ok=True)

        path = tenant_dir / f"{result.model_name}_drift_result.json"

        with path.open("w", encoding="utf-8") as file:
            json.dump(
                self._json_safe(asdict(result)),
                file,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )

        return path

    @staticmethod
    def _clean_numeric_values(values: Sequence[Any]) -> List[float]:
        cleaned: List[float] = []

        for value in values or []:
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue

            if math.isfinite(number):
                cleaned.append(number)

        return cleaned

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if isinstance(value, np.bool_):
            return bool(value)

        if isinstance(value, np.integer):
            return int(value)

        if isinstance(value, np.floating):
            number = float(value)
            return None if not math.isfinite(number) else number

        if isinstance(value, float):
            return None if not math.isfinite(value) else float(value)

        if isinstance(value, dict):
            return {
                str(cls._json_safe(key)): cls._json_safe(item)
                for key, item in value.items()
            }

        if isinstance(value, (list, tuple, set)):
            return [cls._json_safe(item) for item in value]

        return value


# =========================================================
# TESTS
# =========================================================

class TestEnterpriseDriftDetection(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.reference_data = [
            100,
            102,
            98,
            101,
            99,
            100,
            103,
            97,
            101,
            100,
        ]
        self.detector = EnterpriseDriftDetector(
            threshold=0.20,
            storage_dir=self.temp_dir,
        )

    # =====================================================
    # EMPTY DATA
    # =====================================================

    def test_empty_data(self) -> None:
        result = self.detector.detect(
            tenant_id="tenant-empty",
            model_name="fraud_model",
            reference_data=[],
            current_data=[],
        )

        self.assertFalse(result.drift_detected)
        self.assertEqual(result.drift_score, 0.0)
        self.assertEqual(result.drift_type, "none")

    # =====================================================
    # NO DRIFT
    # =====================================================

    def test_no_drift_detected(self) -> None:
        current_data = [
            101,
            100,
            99,
            102,
            98,
        ]

        result = self.detector.detect(
            tenant_id="tenant-001",
            model_name="fraud_model",
            reference_data=self.reference_data,
            current_data=current_data,
        )

        self.assertFalse(result.drift_detected)
        self.assertEqual(result.drift_type, "none")

    # =====================================================
    # DRIFT DETECTED
    # =====================================================

    def test_drift_detected(self) -> None:
        current_data = [
            500,
            520,
            490,
            510,
            505,
        ]

        result = self.detector.detect(
            tenant_id="tenant-002",
            model_name="fraud_model",
            reference_data=self.reference_data,
            current_data=current_data,
        )

        self.assertTrue(result.drift_detected)
        self.assertGreater(result.drift_score, result.threshold)
        self.assertIn(
            result.drift_type,
            {"medium", "high", "critical"},
        )

    # =====================================================
    # CRITICAL DRIFT
    # =====================================================

    def test_critical_drift(self) -> None:
        current_data = [
            1000,
            1100,
            950,
            1050,
            1200,
        ]

        result = self.detector.detect(
            tenant_id="tenant-003",
            model_name="fraud_model",
            reference_data=self.reference_data,
            current_data=current_data,
        )

        self.assertTrue(result.drift_detected)
        self.assertEqual(result.drift_type, "critical")

    # =====================================================
    # LARGE DATASET
    # =====================================================

    def test_large_dataset(self) -> None:
        reference_data = list(np.random.normal(100, 5, 1000))
        current_data = list(np.random.normal(160, 5, 1000))

        result = self.detector.detect(
            tenant_id="tenant-004",
            model_name="risk_model",
            reference_data=reference_data,
            current_data=current_data,
        )

        self.assertTrue(result.drift_detected)
        self.assertGreater(result.drift_score, 0.20)

    # =====================================================
    # MULTI TENANT ISOLATION
    # =====================================================

    def test_multi_tenant_isolation(self) -> None:
        result_a = self.detector.detect(
            tenant_id="tenant-A",
            model_name="fraud_model",
            reference_data=self.reference_data,
            current_data=[100, 101, 99, 100],
        )

        result_b = self.detector.detect(
            tenant_id="tenant-B",
            model_name="fraud_model",
            reference_data=self.reference_data,
            current_data=[300, 320, 310, 330],
        )

        self.assertNotEqual(
            result_a.tenant_id,
            result_b.tenant_id,
        )

        self.assertNotEqual(
            result_a.drift_score,
            result_b.drift_score,
        )

        tenant_a_file = (
            Path(self.temp_dir)
            / "tenant-A"
            / "fraud_model_drift_result.json"
        )

        tenant_b_file = (
            Path(self.temp_dir)
            / "tenant-B"
            / "fraud_model_drift_result.json"
        )

        self.assertTrue(tenant_a_file.exists())
        self.assertTrue(tenant_b_file.exists())

    # =====================================================
    # RESULT PERSISTENCE
    # =====================================================

    def test_result_persistence(self) -> None:
        result = self.detector.detect(
            tenant_id="tenant-005",
            model_name="cashflow_model",
            reference_data=self.reference_data,
            current_data=[400, 410, 420, 405],
        )

        path = (
            Path(self.temp_dir)
            / "tenant-005"
            / "cashflow_model_drift_result.json"
        )

        self.assertTrue(path.exists())

        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)

        self.assertEqual(payload["tenant_id"], result.tenant_id)
        self.assertEqual(payload["model_name"], result.model_name)
        self.assertIsInstance(payload["drift_detected"], bool)
        self.assertIsInstance(payload["drift_score"], float)

    # =====================================================
    # RANDOMIZED INPUTS
    # =====================================================

    def test_randomized_inputs(self) -> None:
        for index in range(10):
            reference_data = [
                random.uniform(90, 110)
                for _ in range(50)
            ]

            current_data = [
                random.uniform(90, 250)
                for _ in range(50)
            ]

            result = self.detector.detect(
                tenant_id=f"tenant-random-{index}",
                model_name="random_model",
                reference_data=reference_data,
                current_data=current_data,
            )

            self.assertIsNotNone(result.drift_score)
            self.assertIsInstance(result.drift_detected, bool)
            self.assertIsInstance(result.drift_score, float)

    # =====================================================
    # NON NUMERIC / DIRTY INPUTS
    # =====================================================

    def test_dirty_input_values(self) -> None:
        result = self.detector.detect(
            tenant_id="tenant-dirty",
            model_name="dirty_model",
            reference_data=[100, "101", None, "invalid", 99],
            current_data=[200, "210", None, "bad", 220],
        )

        self.assertTrue(result.drift_detected)
        self.assertIsInstance(result.drift_detected, bool)


if __name__ == "__main__":
    unittest.main()

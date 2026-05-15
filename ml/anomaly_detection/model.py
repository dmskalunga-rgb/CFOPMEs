# ml/anomaly_detection/model.py
"""
Enterprise Anomaly Detection Model.

Recursos:
- Detecção não supervisionada e semi-supervisionada
- Isolation Forest, Local Outlier Factor, One-Class SVM e fallback estatístico
- Ensemble ponderado
- Normalização de scores
- Threshold automático por contaminação/percentil
- Explicações simples por contribuição de features
- Predição realtime e batch
- Persistência com pickle
- Interface enterprise para pipelines, APIs e monitoramento
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
    from sklearn.ensemble import IsolationForest
    from sklearn.neighbors import LocalOutlierFactor
    from sklearn.preprocessing import RobustScaler, StandardScaler
    from sklearn.svm import OneClassSVM
except Exception:  # pragma: no cover
    IsolationForest = None
    LocalOutlierFactor = None
    OneClassSVM = None
    RobustScaler = None
    StandardScaler = None


class AnomalyModelError(RuntimeError):
    pass


class DetectorType(str, Enum):
    ISOLATION_FOREST = "isolation_forest"
    LOCAL_OUTLIER_FACTOR = "local_outlier_factor"
    ONE_CLASS_SVM = "one_class_svm"
    ROBUST_ZSCORE = "robust_zscore"
    ENSEMBLE = "ensemble"


class ScalingType(str, Enum):
    NONE = "none"
    STANDARD = "standard"
    ROBUST = "robust"


class AnomalyDecision(str, Enum):
    NORMAL = "normal"
    REVIEW = "review"
    ANOMALY = "anomaly"


class AnomalySeverity(str, Enum):
    NORMAL = "normal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class AnomalyModelConfig:
    model_name: str = "enterprise_anomaly_detector"
    model_version: str = "1.0.0"

    detector_type: DetectorType = DetectorType.ENSEMBLE
    scaling: ScalingType = ScalingType.ROBUST

    contamination: float = 0.05
    review_threshold: float = 0.65
    anomaly_threshold: float = 0.85
    critical_threshold: float = 0.95

    isolation_forest_weight: float = 0.45
    lof_weight: float = 0.30
    svm_weight: float = 0.15
    robust_zscore_weight: float = 0.10

    random_state: int = 42
    n_estimators: int = 300
    max_samples: str | int | float = "auto"

    lof_neighbors: int = 35
    svm_nu: float = 0.05
    svm_gamma: str | float = "scale"

    min_training_samples: int = 50
    explanation_top_k: int = 10


@dataclass(frozen=True)
class AnomalyPrediction:
    prediction_id: str
    model_name: str
    model_version: str
    record_id: Optional[str]
    anomaly_score: float
    decision: AnomalyDecision
    severity: AnomalySeverity
    threshold: float
    detector_scores: Dict[str, float]
    feature_contributions: Dict[str, float]
    generated_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class AnomalyTrainingResult:
    run_id: str
    model_name: str
    model_version: str
    trained_at: str
    samples: int
    features: int
    feature_names: List[str]
    detector_type: DetectorType
    threshold: float
    metrics: Dict[str, float]
    metadata: Dict[str, Any] = field(default_factory=dict)


class ScoreNormalizer:
    def __init__(self) -> None:
        self.min_: float = 0.0
        self.max_: float = 1.0
        self.p05_: float = 0.0
        self.p95_: float = 1.0

    def fit(self, scores: Sequence[float]) -> "ScoreNormalizer":
        arr = np.asarray(scores, dtype=float)

        if len(arr) == 0:
            return self

        self.min_ = float(np.min(arr))
        self.max_ = float(np.max(arr))
        self.p05_ = float(np.percentile(arr, 5))
        self.p95_ = float(np.percentile(arr, 95))

        if abs(self.p95_ - self.p05_) < 1e-12:
            self.p05_ = self.min_
            self.p95_ = self.max_

        return self

    def transform(self, scores: Sequence[float]) -> np.ndarray:
        arr = np.asarray(scores, dtype=float)
        denom = max(self.p95_ - self.p05_, 1e-12)
        normalized = (arr - self.p05_) / denom
        return np.clip(normalized, 0.0, 1.0)


class RobustZScoreDetector:
    def __init__(self) -> None:
        self.median_: Optional[np.ndarray] = None
        self.mad_: Optional[np.ndarray] = None

    def fit(self, x: np.ndarray) -> "RobustZScoreDetector":
        self.median_ = np.median(x, axis=0)
        mad = np.median(np.abs(x - self.median_), axis=0)
        self.mad_ = np.where(mad < 1e-12, 1.0, mad)
        return self

    def score_samples(self, x: np.ndarray) -> np.ndarray:
        if self.median_ is None or self.mad_ is None:
            raise AnomalyModelError("RobustZScoreDetector não treinado.")

        z = 0.6745 * (x - self.median_) / self.mad_
        return np.max(np.abs(z), axis=1)


class EnterpriseAnomalyDetector:
    def __init__(self, config: Optional[AnomalyModelConfig] = None) -> None:
        self.config = config or AnomalyModelConfig()

        self.scaler: Any = None
        self.isolation_forest: Any = None
        self.lof: Any = None
        self.svm: Any = None
        self.robust_z: Optional[RobustZScoreDetector] = None

        self.normalizers: Dict[str, ScoreNormalizer] = {}
        self.feature_names: List[str] = []
        self.threshold: float = self.config.anomaly_threshold
        self.training_matrix: Optional[np.ndarray] = None
        self.training_scores: Optional[np.ndarray] = None
        self.is_trained = False
        self.training_result: Optional[AnomalyTrainingResult] = None

    def train(
        self,
        x: Sequence[Sequence[float]],
        *,
        feature_names: Optional[Sequence[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AnomalyTrainingResult:
        matrix = np.asarray(x, dtype=float)

        if matrix.ndim != 2:
            raise AnomalyModelError("x precisa ser uma matriz 2D.")

        if len(matrix) < self.config.min_training_samples:
            raise AnomalyModelError(
                f"Amostras insuficientes: {len(matrix)} < {self.config.min_training_samples}"
            )

        self.feature_names = (
            list(feature_names)
            if feature_names
            else [f"feature_{i}" for i in range(matrix.shape[1])]
        )

        if len(self.feature_names) != matrix.shape[1]:
            raise AnomalyModelError("feature_names precisa ter o mesmo número de colunas de x.")

        scaled = self._fit_scale(matrix)
        self.training_matrix = scaled

        detector_scores = self._fit_detectors(scaled)
        ensemble_scores = self._combine_scores(detector_scores)

        self.training_scores = ensemble_scores
        self.threshold = float(np.percentile(ensemble_scores, 100 * (1.0 - self.config.contamination)))

        metrics = {
            "training_score_mean": float(np.mean(ensemble_scores)),
            "training_score_std": float(np.std(ensemble_scores)),
            "training_score_p50": float(np.percentile(ensemble_scores, 50)),
            "training_score_p95": float(np.percentile(ensemble_scores, 95)),
            "training_score_p99": float(np.percentile(ensemble_scores, 99)),
            "threshold": self.threshold,
            "estimated_anomaly_rate": float(np.mean(ensemble_scores >= self.threshold)),
        }

        self.is_trained = True

        self.training_result = AnomalyTrainingResult(
            run_id=f"anomaly-train-{uuid.uuid4().hex[:8]}",
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            trained_at=datetime.now(timezone.utc).isoformat(),
            samples=matrix.shape[0],
            features=matrix.shape[1],
            feature_names=self.feature_names,
            detector_type=self.config.detector_type,
            threshold=self.threshold,
            metrics=metrics,
            metadata=metadata or {},
        )

        return self.training_result

    def predict_one(
        self,
        row: Sequence[float],
        *,
        record_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AnomalyPrediction:
        matrix = np.asarray([row], dtype=float)
        return self.predict_batch(matrix, record_ids=[record_id], metadata=metadata)[0]

    def predict_batch(
        self,
        x: Sequence[Sequence[float]],
        *,
        record_ids: Optional[Sequence[Optional[str]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[AnomalyPrediction]:
        if not self.is_trained:
            raise AnomalyModelError("Modelo ainda não treinado.")

        matrix = np.asarray(x, dtype=float)

        if matrix.ndim != 2:
            raise AnomalyModelError("x precisa ser uma matriz 2D.")

        if len(matrix) == 0:
            return []

        if matrix.shape[1] != len(self.feature_names):
            raise AnomalyModelError(
                f"Número de features inválido: {matrix.shape[1]} != {len(self.feature_names)}"
            )

        scaled = self._transform_scale(matrix)
        detector_scores = self._score_detectors(scaled)
        scores = self._combine_scores(detector_scores)

        ids = list(record_ids or [None] * len(matrix))

        predictions: List[AnomalyPrediction] = []

        for i, score in enumerate(scores):
            contributions = self._explain_row(scaled[i])

            predictions.append(
                AnomalyPrediction(
                    prediction_id=str(uuid.uuid4()),
                    model_name=self.config.model_name,
                    model_version=self.config.model_version,
                    record_id=ids[i] if i < len(ids) else None,
                    anomaly_score=float(score),
                    decision=self._decision(float(score)),
                    severity=self._severity(float(score)),
                    threshold=self.threshold,
                    detector_scores={
                        name: float(values[i])
                        for name, values in detector_scores.items()
                    },
                    feature_contributions=contributions,
                    generated_at=datetime.now(timezone.utc).isoformat(),
                    metadata=metadata or {},
                )
            )

        return predictions

    def score_batch(self, x: Sequence[Sequence[float]]) -> np.ndarray:
        if not self.is_trained:
            raise AnomalyModelError("Modelo ainda não treinado.")

        matrix = np.asarray(x, dtype=float)
        scaled = self._transform_scale(matrix)
        return self._combine_scores(self._score_detectors(scaled))

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "config": self.config,
            "scaler": self.scaler,
            "isolation_forest": self.isolation_forest,
            "lof": self.lof,
            "svm": self.svm,
            "robust_z": self.robust_z,
            "normalizers": self.normalizers,
            "feature_names": self.feature_names,
            "threshold": self.threshold,
            "training_matrix": self.training_matrix,
            "training_scores": self.training_scores,
            "is_trained": self.is_trained,
            "training_result": self.training_result,
        }

        with target.open("wb") as file:
            pickle.dump(payload, file)

        return target

    @classmethod
    def load(cls, path: str | Path) -> "EnterpriseAnomalyDetector":
        source = Path(path)

        if not source.exists():
            raise AnomalyModelError(f"Modelo não encontrado: {source}")

        with source.open("rb") as file:
            payload = pickle.load(file)

        obj = cls(payload["config"])
        obj.scaler = payload["scaler"]
        obj.isolation_forest = payload["isolation_forest"]
        obj.lof = payload["lof"]
        obj.svm = payload["svm"]
        obj.robust_z = payload["robust_z"]
        obj.normalizers = payload["normalizers"]
        obj.feature_names = payload["feature_names"]
        obj.threshold = payload["threshold"]
        obj.training_matrix = payload["training_matrix"]
        obj.training_scores = payload["training_scores"]
        obj.is_trained = payload["is_trained"]
        obj.training_result = payload["training_result"]

        return obj

    def _fit_scale(self, x: np.ndarray) -> np.ndarray:
        if self.config.scaling == ScalingType.NONE:
            self.scaler = None
            return x

        if self.config.scaling == ScalingType.STANDARD:
            if StandardScaler is None:
                return x
            self.scaler = StandardScaler()
            return self.scaler.fit_transform(x)

        if RobustScaler is None:
            return x

        self.scaler = RobustScaler()
        return self.scaler.fit_transform(x)

    def _transform_scale(self, x: np.ndarray) -> np.ndarray:
        if self.scaler is None:
            return x
        return self.scaler.transform(x)

    def _fit_detectors(self, x: np.ndarray) -> Dict[str, np.ndarray]:
        scores: Dict[str, np.ndarray] = {}

        if self.config.detector_type in {DetectorType.ISOLATION_FOREST, DetectorType.ENSEMBLE}:
            if IsolationForest is not None:
                self.isolation_forest = IsolationForest(
                    n_estimators=self.config.n_estimators,
                    contamination=self.config.contamination,
                    max_samples=self.config.max_samples,
                    random_state=self.config.random_state,
                    n_jobs=-1,
                )
                self.isolation_forest.fit(x)
                raw = -self.isolation_forest.score_samples(x)
                self.normalizers["isolation_forest"] = ScoreNormalizer().fit(raw)
                scores["isolation_forest"] = self.normalizers["isolation_forest"].transform(raw)

        if self.config.detector_type in {DetectorType.LOCAL_OUTLIER_FACTOR, DetectorType.ENSEMBLE}:
            if LocalOutlierFactor is not None:
                neighbors = min(self.config.lof_neighbors, max(len(x) - 1, 2))
                self.lof = LocalOutlierFactor(
                    n_neighbors=neighbors,
                    novelty=True,
                    contamination=self.config.contamination,
                )
                self.lof.fit(x)
                raw = -self.lof.score_samples(x)
                self.normalizers["local_outlier_factor"] = ScoreNormalizer().fit(raw)
                scores["local_outlier_factor"] = self.normalizers["local_outlier_factor"].transform(raw)

        if self.config.detector_type in {DetectorType.ONE_CLASS_SVM, DetectorType.ENSEMBLE}:
            if OneClassSVM is not None:
                self.svm = OneClassSVM(
                    nu=self.config.svm_nu,
                    gamma=self.config.svm_gamma,
                )
                self.svm.fit(x)
                raw = -self.svm.score_samples(x)
                self.normalizers["one_class_svm"] = ScoreNormalizer().fit(raw)
                scores["one_class_svm"] = self.normalizers["one_class_svm"].transform(raw)

        if self.config.detector_type in {DetectorType.ROBUST_ZSCORE, DetectorType.ENSEMBLE} or not scores:
            self.robust_z = RobustZScoreDetector().fit(x)
            raw = self.robust_z.score_samples(x)
            self.normalizers["robust_zscore"] = ScoreNormalizer().fit(raw)
            scores["robust_zscore"] = self.normalizers["robust_zscore"].transform(raw)

        return scores

    def _score_detectors(self, x: np.ndarray) -> Dict[str, np.ndarray]:
        scores: Dict[str, np.ndarray] = {}

        if self.isolation_forest is not None:
            raw = -self.isolation_forest.score_samples(x)
            scores["isolation_forest"] = self.normalizers["isolation_forest"].transform(raw)

        if self.lof is not None:
            raw = -self.lof.score_samples(x)
            scores["local_outlier_factor"] = self.normalizers["local_outlier_factor"].transform(raw)

        if self.svm is not None:
            raw = -self.svm.score_samples(x)
            scores["one_class_svm"] = self.normalizers["one_class_svm"].transform(raw)

        if self.robust_z is not None:
            raw = self.robust_z.score_samples(x)
            scores["robust_zscore"] = self.normalizers["robust_zscore"].transform(raw)

        if not scores:
            raise AnomalyModelError("Nenhum detector disponível.")

        return scores

    def _combine_scores(self, detector_scores: Mapping[str, np.ndarray]) -> np.ndarray:
        if not detector_scores:
            raise AnomalyModelError("Sem scores para combinar.")

        weights = {
            "isolation_forest": self.config.isolation_forest_weight,
            "local_outlier_factor": self.config.lof_weight,
            "one_class_svm": self.config.svm_weight,
            "robust_zscore": self.config.robust_zscore_weight,
        }

        total_weight = sum(weights.get(name, 0.0) for name in detector_scores)

        if total_weight <= 0:
            total_weight = float(len(detector_scores))
            weights = {name: 1.0 for name in detector_scores}

        combined = np.zeros(len(next(iter(detector_scores.values()))), dtype=float)

        for name, values in detector_scores.items():
            combined += np.asarray(values, dtype=float) * (weights.get(name, 0.0) / total_weight)

        return np.clip(combined, 0.0, 1.0)

    def _decision(self, score: float) -> AnomalyDecision:
        if score >= self.config.anomaly_threshold or score >= self.threshold:
            return AnomalyDecision.ANOMALY

        if score >= self.config.review_threshold:
            return AnomalyDecision.REVIEW

        return AnomalyDecision.NORMAL

    def _severity(self, score: float) -> AnomalySeverity:
        if score >= self.config.critical_threshold:
            return AnomalySeverity.CRITICAL
        if score >= self.config.anomaly_threshold:
            return AnomalySeverity.HIGH
        if score >= self.config.review_threshold:
            return AnomalySeverity.MEDIUM
        if score >= 0.45:
            return AnomalySeverity.LOW
        return AnomalySeverity.NORMAL

    def _explain_row(self, row: np.ndarray) -> Dict[str, float]:
        if self.training_matrix is None:
            return {}

        center = np.median(self.training_matrix, axis=0)
        spread = np.median(np.abs(self.training_matrix - center), axis=0)
        spread = np.where(spread < 1e-12, 1.0, spread)

        contributions = np.abs((row - center) / spread)
        total = float(np.sum(contributions))

        if total <= 1e-12:
            normalized = np.zeros_like(contributions)
        else:
            normalized = contributions / total

        pairs = sorted(
            zip(self.feature_names, normalized),
            key=lambda item: abs(item[1]),
            reverse=True,
        )[: self.config.explanation_top_k]

        return {name: float(value) for name, value in pairs}


def train_anomaly_detector_from_feature_result(
    feature_result: Any,
    *,
    config: Optional[AnomalyModelConfig] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[EnterpriseAnomalyDetector, AnomalyTrainingResult]:
    matrix = feature_result.to_matrix()
    feature_names = list(feature_result.feature_names)

    model = EnterpriseAnomalyDetector(config)
    result = model.train(
        matrix,
        feature_names=feature_names,
        metadata=metadata or getattr(feature_result, "metadata", {}),
    )

    return model, result


def generate_synthetic_anomaly_matrix(
    samples: int = 1000,
    features: int = 8,
    anomaly_rate: float = 0.05,
    seed: int = 42,
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    rng = np.random.default_rng(seed)

    x = rng.normal(0, 1, size=(samples, features))
    y = rng.choice([0, 1], size=samples, p=[1 - anomaly_rate, anomaly_rate])

    anomaly_idx = np.where(y == 1)[0]

    if len(anomaly_idx):
        x[anomaly_idx] += rng.normal(4, 1.5, size=(len(anomaly_idx), features))

    feature_names = [f"feature_{i}" for i in range(features)]

    return x, y, feature_names


if __name__ == "__main__":
    x, y, feature_names = generate_synthetic_anomaly_matrix()

    model = EnterpriseAnomalyDetector(
        AnomalyModelConfig(
            detector_type=DetectorType.ENSEMBLE,
            contamination=0.05,
            review_threshold=0.60,
            anomaly_threshold=0.85,
        )
    )

    training = model.train(
        x,
        feature_names=feature_names,
        metadata={"dataset": "synthetic_demo"},
    )

    print(json.dumps(asdict(training), indent=2, ensure_ascii=False, default=str))

    predictions = model.predict_batch(x[:5], record_ids=[f"rec-{i}" for i in range(5)])

    for prediction in predictions:
        print(prediction.to_json())
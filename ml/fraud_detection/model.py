# ml/fraud_detection/model.py
"""
Enterprise Fraud Detection Model.

Recursos:
- Modelo híbrido: ML supervisionado + regras heurísticas
- Feature engineering transacional
- Risk score calibrado
- Explicações por reason codes
- Predição batch e realtime
- Thresholds configuráveis por severidade
- Serialização do modelo
- Interface limpa para pipelines enterprise
"""

from __future__ import annotations

import json
import math
import pickle
import statistics
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

import numpy as np


try:
    from sklearn.ensemble import GradientBoostingClassifier, IsolationForest, RandomForestClassifier
    from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score, roc_auc_score
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import StandardScaler
except Exception:  # pragma: no cover
    GradientBoostingClassifier = None
    IsolationForest = None
    RandomForestClassifier = None
    StandardScaler = None
    train_test_split = None
    roc_auc_score = None
    average_precision_score = None
    precision_score = None
    recall_score = None
    f1_score = None


class FraudModelError(RuntimeError):
    pass


class FraudRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FraudDecision(str, Enum):
    APPROVE = "approve"
    REVIEW = "review"
    BLOCK = "block"


class FraudSignalType(str, Enum):
    AMOUNT_ANOMALY = "amount_anomaly"
    VELOCITY = "velocity"
    GEO_ANOMALY = "geo_anomaly"
    DEVICE_RISK = "device_risk"
    MERCHANT_RISK = "merchant_risk"
    USER_HISTORY = "user_history"
    ML_SCORE = "ml_score"
    RULE = "rule"


@dataclass(frozen=True)
class FraudModelConfig:
    model_name: str = "enterprise_fraud_detector"
    model_version: str = "1.0.0"

    review_threshold: float = 0.55
    block_threshold: float = 0.82
    critical_threshold: float = 0.92

    high_amount_threshold: float = 5_000.0
    extreme_amount_threshold: float = 20_000.0
    velocity_count_threshold: int = 8
    velocity_amount_threshold: float = 30_000.0

    suspicious_country_risk: float = 0.20
    unknown_device_risk: float = 0.15
    risky_merchant_risk: float = 0.20

    heuristic_weight: float = 0.35
    ml_weight: float = 0.65

    random_state: int = 42


@dataclass(frozen=True)
class Transaction:
    transaction_id: str
    user_id: str
    amount: float
    currency: str
    merchant_id: Optional[str] = None
    merchant_category: Optional[str] = None
    country: Optional[str] = None
    device_id: Optional[str] = None
    ip_address: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UserProfile:
    user_id: str
    avg_amount: float = 0.0
    std_amount: float = 0.0
    transaction_count_24h: int = 0
    transaction_amount_24h: float = 0.0
    known_devices: Sequence[str] = field(default_factory=list)
    known_countries: Sequence[str] = field(default_factory=list)
    chargeback_count: int = 0
    account_age_days: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FraudSignal:
    signal_type: FraudSignalType
    score: float
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FraudPrediction:
    prediction_id: str
    transaction_id: str
    model_name: str
    model_version: str
    risk_score: float
    risk_level: FraudRiskLevel
    decision: FraudDecision
    ml_score: Optional[float]
    heuristic_score: float
    signals: List[FraudSignal]
    generated_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)


@dataclass(frozen=True)
class FraudTrainingResult:
    model_name: str
    model_version: str
    trained_at: str
    samples: int
    metrics: Dict[str, float]
    feature_names: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)


class FraudFeatureExtractor:
    DEFAULT_FEATURES = [
        "amount",
        "log_amount",
        "amount_zscore",
        "amount_to_avg_ratio",
        "transaction_count_24h",
        "transaction_amount_24h",
        "velocity_amount_ratio",
        "unknown_device",
        "unknown_country",
        "chargeback_count",
        "account_age_days",
        "merchant_risk",
        "country_risk",
        "device_risk",
    ]

    def __init__(
        self,
        risky_merchants: Optional[Mapping[str, float]] = None,
        risky_countries: Optional[Mapping[str, float]] = None,
    ) -> None:
        self.risky_merchants = dict(risky_merchants or {})
        self.risky_countries = dict(risky_countries or {})

    @property
    def feature_names(self) -> List[str]:
        return list(self.DEFAULT_FEATURES)

    def extract(
        self,
        transaction: Transaction,
        profile: Optional[UserProfile] = None,
    ) -> Dict[str, float]:
        profile = profile or UserProfile(user_id=transaction.user_id)

        amount = max(float(transaction.amount), 0.0)
        avg = max(float(profile.avg_amount), 0.0)
        std = max(float(profile.std_amount), 1e-6)

        amount_zscore = (amount - avg) / std if avg > 0 else 0.0
        amount_to_avg_ratio = amount / max(avg, 1e-6) if avg > 0 else 1.0

        unknown_device = (
            1.0
            if transaction.device_id and transaction.device_id not in set(profile.known_devices)
            else 0.0
        )

        unknown_country = (
            1.0
            if transaction.country and transaction.country not in set(profile.known_countries)
            else 0.0
        )

        merchant_risk = self.risky_merchants.get(str(transaction.merchant_id), 0.0)
        country_risk = self.risky_countries.get(str(transaction.country), 0.0)
        device_risk = float(transaction.metadata.get("device_risk", 0.0) or 0.0)

        velocity_amount_ratio = (
            profile.transaction_amount_24h / max(profile.avg_amount, 1e-6)
            if profile.avg_amount > 0
            else 0.0
        )

        return {
            "amount": amount,
            "log_amount": math.log1p(amount),
            "amount_zscore": amount_zscore,
            "amount_to_avg_ratio": amount_to_avg_ratio,
            "transaction_count_24h": float(profile.transaction_count_24h),
            "transaction_amount_24h": float(profile.transaction_amount_24h),
            "velocity_amount_ratio": float(velocity_amount_ratio),
            "unknown_device": unknown_device,
            "unknown_country": unknown_country,
            "chargeback_count": float(profile.chargeback_count),
            "account_age_days": float(profile.account_age_days),
            "merchant_risk": float(merchant_risk),
            "country_risk": float(country_risk),
            "device_risk": float(device_risk),
        }

    def vectorize(
        self,
        transactions: Sequence[Transaction],
        profiles: Optional[Mapping[str, UserProfile]] = None,
    ) -> np.ndarray:
        rows = []

        for tx in transactions:
            profile = profiles.get(tx.user_id) if profiles else None
            features = self.extract(tx, profile)
            rows.append([features[name] for name in self.feature_names])

        return np.asarray(rows, dtype=float)


class FraudHeuristicEngine:
    def __init__(self, config: FraudModelConfig) -> None:
        self.config = config

    def evaluate(
        self,
        transaction: Transaction,
        profile: Optional[UserProfile] = None,
        features: Optional[Mapping[str, float]] = None,
    ) -> Tuple[float, List[FraudSignal]]:
        profile = profile or UserProfile(user_id=transaction.user_id)
        features = features or {}

        signals: List[FraudSignal] = []
        score = 0.0

        amount = float(transaction.amount)

        if amount >= self.config.extreme_amount_threshold:
            signal_score = 0.35
            score += signal_score
            signals.append(
                FraudSignal(
                    FraudSignalType.AMOUNT_ANOMALY,
                    signal_score,
                    "Valor extremamente alto para a política de risco.",
                    {"amount": amount, "threshold": self.config.extreme_amount_threshold},
                )
            )
        elif amount >= self.config.high_amount_threshold:
            signal_score = 0.18
            score += signal_score
            signals.append(
                FraudSignal(
                    FraudSignalType.AMOUNT_ANOMALY,
                    signal_score,
                    "Valor acima do limite de atenção.",
                    {"amount": amount, "threshold": self.config.high_amount_threshold},
                )
            )

        amount_zscore = float(features.get("amount_zscore", 0.0))
        if amount_zscore >= 4.0:
            signal_score = min(0.25, amount_zscore / 20)
            score += signal_score
            signals.append(
                FraudSignal(
                    FraudSignalType.AMOUNT_ANOMALY,
                    signal_score,
                    "Valor fora do padrão histórico do usuário.",
                    {"amount_zscore": amount_zscore},
                )
            )

        if profile.transaction_count_24h >= self.config.velocity_count_threshold:
            signal_score = 0.20
            score += signal_score
            signals.append(
                FraudSignal(
                    FraudSignalType.VELOCITY,
                    signal_score,
                    "Alta quantidade de transações nas últimas 24h.",
                    {
                        "transaction_count_24h": profile.transaction_count_24h,
                        "threshold": self.config.velocity_count_threshold,
                    },
                )
            )

        if profile.transaction_amount_24h >= self.config.velocity_amount_threshold:
            signal_score = 0.22
            score += signal_score
            signals.append(
                FraudSignal(
                    FraudSignalType.VELOCITY,
                    signal_score,
                    "Alto volume financeiro nas últimas 24h.",
                    {
                        "transaction_amount_24h": profile.transaction_amount_24h,
                        "threshold": self.config.velocity_amount_threshold,
                    },
                )
            )

        if float(features.get("unknown_device", 0.0)) >= 1.0:
            score += self.config.unknown_device_risk
            signals.append(
                FraudSignal(
                    FraudSignalType.DEVICE_RISK,
                    self.config.unknown_device_risk,
                    "Dispositivo desconhecido para o usuário.",
                    {"device_id": transaction.device_id},
                )
            )

        if float(features.get("unknown_country", 0.0)) >= 1.0:
            score += self.config.suspicious_country_risk
            signals.append(
                FraudSignal(
                    FraudSignalType.GEO_ANOMALY,
                    self.config.suspicious_country_risk,
                    "País incomum para o histórico do usuário.",
                    {"country": transaction.country},
                )
            )

        merchant_risk = float(features.get("merchant_risk", 0.0))
        if merchant_risk > 0:
            score += min(0.25, merchant_risk)
            signals.append(
                FraudSignal(
                    FraudSignalType.MERCHANT_RISK,
                    min(0.25, merchant_risk),
                    "Estabelecimento com risco histórico elevado.",
                    {"merchant_id": transaction.merchant_id, "merchant_risk": merchant_risk},
                )
            )

        if profile.chargeback_count > 0:
            signal_score = min(0.25, 0.05 * profile.chargeback_count)
            score += signal_score
            signals.append(
                FraudSignal(
                    FraudSignalType.USER_HISTORY,
                    signal_score,
                    "Usuário possui histórico de chargeback.",
                    {"chargeback_count": profile.chargeback_count},
                )
            )

        return min(score, 1.0), signals


class EnterpriseFraudDetectionModel:
    def __init__(
        self,
        config: Optional[FraudModelConfig] = None,
        feature_extractor: Optional[FraudFeatureExtractor] = None,
    ) -> None:
        self.config = config or FraudModelConfig()
        self.feature_extractor = feature_extractor or FraudFeatureExtractor()
        self.heuristics = FraudHeuristicEngine(self.config)
        self.scaler: Any = None
        self.classifier: Any = None
        self.anomaly_model: Any = None
        self.is_trained = False
        self.training_result: Optional[FraudTrainingResult] = None

    def train(
        self,
        transactions: Sequence[Transaction],
        labels: Sequence[int],
        *,
        profiles: Optional[Mapping[str, UserProfile]] = None,
        validation_size: float = 0.2,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FraudTrainingResult:
        self._ensure_sklearn()

        if len(transactions) == 0:
            raise FraudModelError("Nenhuma transação para treino.")

        if len(transactions) != len(labels):
            raise FraudModelError("transactions e labels precisam ter o mesmo tamanho.")

        x = self.feature_extractor.vectorize(transactions, profiles)
        y = np.asarray(labels, dtype=int)

        self.scaler = StandardScaler()
        x_scaled = self.scaler.fit_transform(x)

        if len(set(y.tolist())) < 2:
            raise FraudModelError("É necessário ter pelo menos duas classes para treino supervisionado.")

        x_train, x_val, y_train, y_val = train_test_split(
            x_scaled,
            y,
            test_size=validation_size,
            random_state=self.config.random_state,
            stratify=y,
        )

        self.classifier = GradientBoostingClassifier(random_state=self.config.random_state)
        self.classifier.fit(x_train, y_train)

        self.anomaly_model = IsolationForest(
            contamination=min(max(float(np.mean(y)), 0.01), 0.30),
            random_state=self.config.random_state,
        )
        self.anomaly_model.fit(x_train)

        proba = self.classifier.predict_proba(x_val)[:, 1]
        pred = (proba >= self.config.review_threshold).astype(int)

        metrics = self._compute_metrics(y_val, pred, proba)

        self.is_trained = True

        self.training_result = FraudTrainingResult(
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            trained_at=datetime.now(timezone.utc).isoformat(),
            samples=len(transactions),
            metrics=metrics,
            feature_names=self.feature_extractor.feature_names,
            metadata=metadata or {},
        )

        return self.training_result

    def predict_one(
        self,
        transaction: Transaction,
        profile: Optional[UserProfile] = None,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FraudPrediction:
        features = self.feature_extractor.extract(transaction, profile)
        heuristic_score, signals = self.heuristics.evaluate(transaction, profile, features)

        ml_score = self._predict_ml_score(transaction, profile)
        anomaly_score = self._predict_anomaly_score(transaction, profile)

        if anomaly_score is not None and anomaly_score > 0:
            signals.append(
                FraudSignal(
                    FraudSignalType.ML_SCORE,
                    min(0.15, anomaly_score),
                    "Modelo de anomalia detectou comportamento incomum.",
                    {"anomaly_score": anomaly_score},
                )
            )

        if ml_score is None:
            risk_score = heuristic_score
        else:
            risk_score = (
                self.config.ml_weight * ml_score
                + self.config.heuristic_weight * heuristic_score
            )

        if anomaly_score is not None:
            risk_score = min(1.0, risk_score + min(0.10, anomaly_score * 0.10))

        risk_score = float(min(max(risk_score, 0.0), 1.0))
        risk_level = self._risk_level(risk_score)
        decision = self._decision(risk_score)

        if ml_score is not None:
            signals.append(
                FraudSignal(
                    FraudSignalType.ML_SCORE,
                    ml_score,
                    "Score supervisionado de fraude.",
                    {"ml_score": ml_score},
                )
            )

        return FraudPrediction(
            prediction_id=str(uuid.uuid4()),
            transaction_id=transaction.transaction_id,
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            risk_score=risk_score,
            risk_level=risk_level,
            decision=decision,
            ml_score=ml_score,
            heuristic_score=heuristic_score,
            signals=sorted(signals, key=lambda s: s.score, reverse=True),
            generated_at=datetime.now(timezone.utc).isoformat(),
            metadata=metadata or {},
        )

    def predict_batch(
        self,
        transactions: Sequence[Transaction],
        profiles: Optional[Mapping[str, UserProfile]] = None,
    ) -> List[FraudPrediction]:
        return [
            self.predict_one(
                tx,
                profiles.get(tx.user_id) if profiles else None,
            )
            for tx in transactions
        ]

    def feature_importance(self) -> Dict[str, float]:
        if not self.is_trained or self.classifier is None:
            return {}

        if not hasattr(self.classifier, "feature_importances_"):
            return {}

        values = self.classifier.feature_importances_
        return {
            name: float(value)
            for name, value in zip(self.feature_extractor.feature_names, values)
        }

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "config": asdict(self.config),
            "feature_extractor": self.feature_extractor,
            "scaler": self.scaler,
            "classifier": self.classifier,
            "anomaly_model": self.anomaly_model,
            "is_trained": self.is_trained,
            "training_result": self.training_result,
        }

        with target.open("wb") as file:
            pickle.dump(payload, file)

        return target

    @classmethod
    def load(cls, path: str | Path) -> "EnterpriseFraudDetectionModel":
        source = Path(path)

        if not source.exists():
            raise FraudModelError(f"Modelo não encontrado: {source}")

        with source.open("rb") as file:
            payload = pickle.load(file)

        model = cls(
            config=FraudModelConfig(**payload["config"]),
            feature_extractor=payload["feature_extractor"],
        )

        model.scaler = payload["scaler"]
        model.classifier = payload["classifier"]
        model.anomaly_model = payload["anomaly_model"]
        model.is_trained = payload["is_trained"]
        model.training_result = payload["training_result"]

        return model

    def _predict_ml_score(
        self,
        transaction: Transaction,
        profile: Optional[UserProfile],
    ) -> Optional[float]:
        if not self.is_trained or self.classifier is None or self.scaler is None:
            return None

        x = self.feature_extractor.vectorize([transaction], {transaction.user_id: profile} if profile else None)
        x_scaled = self.scaler.transform(x)

        if hasattr(self.classifier, "predict_proba"):
            return float(self.classifier.predict_proba(x_scaled)[0, 1])

        return float(self.classifier.predict(x_scaled)[0])

    def _predict_anomaly_score(
        self,
        transaction: Transaction,
        profile: Optional[UserProfile],
    ) -> Optional[float]:
        if not self.is_trained or self.anomaly_model is None or self.scaler is None:
            return None

        x = self.feature_extractor.vectorize([transaction], {transaction.user_id: profile} if profile else None)
        x_scaled = self.scaler.transform(x)

        raw = float(self.anomaly_model.decision_function(x_scaled)[0])
        return float(max(0.0, min(1.0, -raw + 0.5)))

    def _compute_metrics(
        self,
        y_true: np.ndarray,
        y_pred: np.ndarray,
        y_score: np.ndarray,
    ) -> Dict[str, float]:
        metrics: Dict[str, float] = {}

        if roc_auc_score is not None:
            metrics["roc_auc"] = float(roc_auc_score(y_true, y_score))

        if average_precision_score is not None:
            metrics["average_precision"] = float(average_precision_score(y_true, y_score))

        if precision_score is not None:
            metrics["precision"] = float(precision_score(y_true, y_pred, zero_division=0))

        if recall_score is not None:
            metrics["recall"] = float(recall_score(y_true, y_pred, zero_division=0))

        if f1_score is not None:
            metrics["f1"] = float(f1_score(y_true, y_pred, zero_division=0))

        return metrics

    def _risk_level(self, score: float) -> FraudRiskLevel:
        if score >= self.config.critical_threshold:
            return FraudRiskLevel.CRITICAL
        if score >= self.config.block_threshold:
            return FraudRiskLevel.HIGH
        if score >= self.config.review_threshold:
            return FraudRiskLevel.MEDIUM
        return FraudRiskLevel.LOW

    def _decision(self, score: float) -> FraudDecision:
        if score >= self.config.block_threshold:
            return FraudDecision.BLOCK
        if score >= self.config.review_threshold:
            return FraudDecision.REVIEW
        return FraudDecision.APPROVE

    @staticmethod
    def _ensure_sklearn() -> None:
        if GradientBoostingClassifier is None or StandardScaler is None:
            raise FraudModelError("scikit-learn não está instalado.")


def build_profiles_from_history(
    transactions: Sequence[Transaction],
    chargebacks: Optional[Mapping[str, int]] = None,
) -> Dict[str, UserProfile]:
    grouped: Dict[str, List[Transaction]] = {}

    for tx in transactions:
        grouped.setdefault(tx.user_id, []).append(tx)

    profiles: Dict[str, UserProfile] = {}

    for user_id, txs in grouped.items():
        amounts = [float(tx.amount) for tx in txs]

        profiles[user_id] = UserProfile(
            user_id=user_id,
            avg_amount=float(statistics.mean(amounts)) if amounts else 0.0,
            std_amount=float(statistics.pstdev(amounts)) if len(amounts) > 1 else 0.0,
            transaction_count_24h=len(txs[-24:]),
            transaction_amount_24h=float(sum(tx.amount for tx in txs[-24:])),
            known_devices=sorted({tx.device_id for tx in txs if tx.device_id}),
            known_countries=sorted({tx.country for tx in txs if tx.country}),
            chargeback_count=int((chargebacks or {}).get(user_id, 0)),
            account_age_days=int(txs[-1].metadata.get("account_age_days", 0) if txs else 0),
        )

    return profiles


if __name__ == "__main__":
    rng = np.random.default_rng(42)

    txs: List[Transaction] = []
    labels: List[int] = []

    for i in range(500):
        fraud = rng.random() < 0.12
        amount = float(rng.normal(9000, 4000) if fraud else rng.normal(300, 120))
        amount = max(amount, 5.0)

        txs.append(
            Transaction(
                transaction_id=f"tx-{i}",
                user_id=f"user-{i % 50}",
                amount=amount,
                currency="BRL",
                merchant_id="risky" if fraud else "normal",
                country="XX" if fraud else "BR",
                device_id=f"device-{i % 80}",
                metadata={"account_age_days": int(rng.integers(5, 1000))},
            )
        )
        labels.append(1 if fraud else 0)

    profiles = build_profiles_from_history(txs)

    extractor = FraudFeatureExtractor(
        risky_merchants={"risky": 0.25},
        risky_countries={"XX": 0.25},
    )

    model = EnterpriseFraudDetectionModel(feature_extractor=extractor)
    result = model.train(txs, labels, profiles=profiles)

    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))

    prediction = model.predict_one(
        Transaction(
            transaction_id="tx-live-001",
            user_id="user-1",
            amount=15000,
            currency="BRL",
            merchant_id="risky",
            country="XX",
            device_id="new-device",
        ),
        profiles.get("user-1"),
    )

    print(prediction.to_json())
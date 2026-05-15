"""
data/analytics/predictive_analytics.py

Enterprise Predictive Analytics Engine.

Recursos:
- Modelos preditivos para classificação, regressão e scoring
- Feature pipeline simples e extensível
- Registro/versionamento de modelos
- Scoring online e batch
- Avaliação de modelos
- Métricas: accuracy, precision, recall, F1, MAE, RMSE, R2
- Drift básico de features e predições
- Multi-tenant
- Auditoria e observabilidade
- Exportação JSON
- Sem dependências externas obrigatórias
"""

from __future__ import annotations

import json
import logging
import math
import statistics
import uuid
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Tuple


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class PredictiveTaskType(str, Enum):
    CLASSIFICATION = "classification"
    REGRESSION = "regression"
    RANKING = "ranking"
    SCORING = "scoring"


class ModelStatus(str, Enum):
    DRAFT = "draft"
    STAGING = "staging"
    PRODUCTION = "production"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class PredictionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    VALIDATION_ERROR = "validation_error"


class FeatureType(str, Enum):
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    BOOLEAN = "boolean"
    TEXT = "text"
    DATETIME = "datetime"


class DriftStatus(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


# =============================================================================
# Exceptions
# =============================================================================

class PredictiveAnalyticsError(Exception):
    """Erro base de predictive analytics."""


class ModelNotFound(PredictiveAnalyticsError):
    """Modelo não encontrado."""


class ModelValidationError(PredictiveAnalyticsError):
    """Modelo inválido."""


class PredictionExecutionError(PredictiveAnalyticsError):
    """Erro durante predição."""


class FeatureValidationError(PredictiveAnalyticsError):
    """Erro de validação de feature."""


# =============================================================================
# Protocols
# =============================================================================

class PredictiveModel(Protocol):
    def predict(self, features: Dict[str, Any]) -> Any:
        ...

    def predict_proba(self, features: Dict[str, Any]) -> Optional[Dict[str, float]]:
        ...


class AuditBackend(Protocol):
    def write_event(self, event: Dict[str, Any]) -> None:
        ...


class MetricsBackend(Protocol):
    def increment(
        self,
        metric_name: str,
        value: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        ...

    def gauge(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        ...


# =============================================================================
# Backends
# =============================================================================

class LoggingAuditBackend:
    def write_event(self, event: Dict[str, Any]) -> None:
        logger.info("predictive_audit=%s", json.dumps(event, ensure_ascii=False, default=str))


class LoggingMetricsBackend:
    def increment(
        self,
        metric_name: str,
        value: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("metric=%s value=%s tags=%s", metric_name, value, tags or {})

    def gauge(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("gauge=%s value=%s tags=%s", metric_name, value, tags or {})


# =============================================================================
# Simple Built-in Models
# =============================================================================

class RuleBasedModel:
    """
    Modelo simples baseado em função.

    Útil para:
    - MVP
    - fallback
    - regras de negócio
    - testes unitários
    """

    def __init__(
        self,
        predict_fn: Callable[[Dict[str, Any]], Any],
        proba_fn: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, float]]]] = None,
    ) -> None:
        self.predict_fn = predict_fn
        self.proba_fn = proba_fn

    def predict(self, features: Dict[str, Any]) -> Any:
        return self.predict_fn(features)

    def predict_proba(self, features: Dict[str, Any]) -> Optional[Dict[str, float]]:
        if self.proba_fn:
            return self.proba_fn(features)
        return None


class LinearRegressionModel:
    """
    Regressão linear simples com pesos fornecidos.

    prediction = intercept + sum(feature * weight)
    """

    def __init__(
        self,
        weights: Dict[str, float],
        intercept: float = 0.0,
    ) -> None:
        self.weights = weights
        self.intercept = intercept

    def predict(self, features: Dict[str, Any]) -> float:
        total = self.intercept

        for name, weight in self.weights.items():
            try:
                total += float(features.get(name, 0.0)) * weight
            except Exception:
                continue

        return total

    def predict_proba(self, features: Dict[str, Any]) -> Optional[Dict[str, float]]:
        return None


class LogisticRuleModel:
    """
    Modelo logístico simples com pesos fornecidos.

    Retorna classe positiva quando probabilidade >= threshold.
    """

    def __init__(
        self,
        weights: Dict[str, float],
        intercept: float = 0.0,
        threshold: float = 0.5,
        positive_label: str = "positive",
        negative_label: str = "negative",
    ) -> None:
        self.weights = weights
        self.intercept = intercept
        self.threshold = threshold
        self.positive_label = positive_label
        self.negative_label = negative_label

    def predict(self, features: Dict[str, Any]) -> str:
        probability = self._probability(features)
        return self.positive_label if probability >= self.threshold else self.negative_label

    def predict_proba(self, features: Dict[str, Any]) -> Dict[str, float]:
        probability = self._probability(features)
        return {
            self.positive_label: probability,
            self.negative_label: 1 - probability,
        }

    def _probability(self, features: Dict[str, Any]) -> float:
        score = self.intercept

        for name, weight in self.weights.items():
            try:
                score += float(features.get(name, 0.0)) * weight
            except Exception:
                continue

        return 1 / (1 + math.exp(-score))


# =============================================================================
# Models
# =============================================================================

@dataclass(frozen=True)
class PredictiveContext:
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    user_id: Optional[str] = None
    correlation_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    feature_type: FeatureType
    required: bool = False
    default_value: Any = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    allowed_values: Optional[List[Any]] = None
    description: str = ""
    tags: Dict[str, str] = field(default_factory=dict)

    def validate_value(self, value: Any) -> Any:
        if value is None:
            if self.required and self.default_value is None:
                raise FeatureValidationError(f"Feature obrigatória ausente: {self.name}")
            return self.default_value

        if self.feature_type == FeatureType.NUMERIC:
            try:
                numeric = float(value)
            except Exception as exc:
                raise FeatureValidationError(f"Feature {self.name} precisa ser numérica") from exc

            if self.min_value is not None and numeric < self.min_value:
                raise FeatureValidationError(f"Feature {self.name} abaixo do mínimo")

            if self.max_value is not None and numeric > self.max_value:
                raise FeatureValidationError(f"Feature {self.name} acima do máximo")

            return numeric

        if self.feature_type == FeatureType.BOOLEAN:
            return bool(value)

        if self.feature_type == FeatureType.CATEGORICAL:
            if self.allowed_values is not None and value not in self.allowed_values:
                raise FeatureValidationError(
                    f"Feature {self.name} fora dos valores permitidos"
                )
            return value

        if self.feature_type == FeatureType.TEXT:
            return str(value)

        return value


@dataclass(frozen=True)
class ModelMetadata:
    model_id: str
    name: str
    version: str
    task_type: PredictiveTaskType
    status: ModelStatus
    owner: str
    feature_definitions: List[FeatureDefinition]
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    description: str = ""
    tags: Dict[str, str] = field(default_factory=dict)
    training_dataset: Optional[str] = None
    target_name: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = None

    def validate(self) -> None:
        if not self.model_id:
            raise ModelValidationError("model_id é obrigatório")

        if not self.name:
            raise ModelValidationError("name é obrigatório")

        if not self.version:
            raise ModelValidationError("version é obrigatório")

        if not self.feature_definitions:
            raise ModelValidationError("feature_definitions é obrigatório")


@dataclass
class RegisteredModel:
    metadata: ModelMetadata
    model: PredictiveModel


@dataclass(frozen=True)
class PredictionRequest:
    features: Dict[str, Any]
    entity_id: Optional[str] = None
    tenant_id: Optional[str] = None
    explain: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PredictionResult:
    prediction_id: str
    model_id: str
    model_version: str
    status: PredictionStatus
    prediction: Any
    probabilities: Optional[Dict[str, float]]
    scored_at: datetime
    entity_id: Optional[str] = None
    error: Optional[str] = None
    explanation: Dict[str, Any] = field(default_factory=dict)
    context: Optional[PredictiveContext] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationResult:
    evaluation_id: str
    model_id: str
    model_version: str
    task_type: PredictiveTaskType
    metrics: Dict[str, float]
    sample_count: int
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DriftReport:
    drift_id: str
    model_id: str
    status: DriftStatus
    feature_drift_scores: Dict[str, float]
    prediction_drift_score: Optional[float]
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    details: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Registry
# =============================================================================

class PredictiveModelRegistry:
    def __init__(self) -> None:
        self._models: Dict[str, RegisteredModel] = {}

    def register(self, registered_model: RegisteredModel) -> None:
        registered_model.metadata.validate()
        self._models[registered_model.metadata.model_id] = registered_model

    def get(self, model_id: str) -> RegisteredModel:
        model = self._models.get(model_id)
        if not model:
            raise ModelNotFound(model_id)
        return model

    def list_all(
        self,
        status: Optional[ModelStatus] = None,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> List[RegisteredModel]:
        models = list(self._models.values())

        if status is not None:
            models = [item for item in models if item.metadata.status == status]

        if tenant_id is not None:
            models = [
                item for item in models
                if item.metadata.tenant_id is None or item.metadata.tenant_id == tenant_id
            ]

        if domain is not None:
            models = [
                item for item in models
                if item.metadata.domain is None or item.metadata.domain == domain
            ]

        return models


# =============================================================================
# Evaluation
# =============================================================================

class PredictionEvaluator:
    @staticmethod
    def evaluate_classification(
        actual: List[Any],
        predicted: List[Any],
        positive_label: Optional[Any] = None,
    ) -> Dict[str, float]:
        if len(actual) != len(predicted):
            raise ModelValidationError("actual e predicted precisam ter o mesmo tamanho")

        if not actual:
            return {}

        correct = sum(1 for a, p in zip(actual, predicted) if a == p)
        accuracy = correct / len(actual)

        labels = sorted(set(actual) | set(predicted), key=str)

        if positive_label is None:
            positive_label = labels[0] if labels else None

        tp = sum(1 for a, p in zip(actual, predicted) if a == positive_label and p == positive_label)
        fp = sum(1 for a, p in zip(actual, predicted) if a != positive_label and p == positive_label)
        fn = sum(1 for a, p in zip(actual, predicted) if a == positive_label and p != positive_label)

        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )

        return {
            "accuracy": accuracy,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }

    @staticmethod
    def evaluate_regression(actual: List[float], predicted: List[float]) -> Dict[str, float]:
        if len(actual) != len(predicted):
            raise ModelValidationError("actual e predicted precisam ter o mesmo tamanho")

        if not actual:
            return {}

        errors = [a - p for a, p in zip(actual, predicted)]
        abs_errors = [abs(error) for error in errors]
        sq_errors = [error ** 2 for error in errors]

        mae = sum(abs_errors) / len(abs_errors)
        rmse = math.sqrt(sum(sq_errors) / len(sq_errors))

        mean_actual = sum(actual) / len(actual)
        ss_total = sum((a - mean_actual) ** 2 for a in actual)
        ss_res = sum((a - p) ** 2 for a, p in zip(actual, predicted))

        r2 = 1 - ss_res / ss_total if ss_total else 0.0

        return {
            "mae": mae,
            "rmse": rmse,
            "r2": r2,
            "bias": sum(errors) / len(errors),
        }


# =============================================================================
# Drift
# =============================================================================

class BasicDriftDetector:
    @staticmethod
    def compare_numeric_distribution(
        baseline: List[float],
        current: List[float],
    ) -> float:
        if not baseline or not current:
            return 0.0

        base_mean = statistics.mean(baseline)
        curr_mean = statistics.mean(current)

        base_std = statistics.stdev(baseline) if len(baseline) > 1 else 0.0

        if base_std == 0:
            return abs(curr_mean - base_mean)

        return abs(curr_mean - base_mean) / base_std

    @staticmethod
    def compare_categorical_distribution(
        baseline: List[Any],
        current: List[Any],
    ) -> float:
        if not baseline or not current:
            return 0.0

        base_counter = Counter(baseline)
        curr_counter = Counter(current)

        labels = set(base_counter) | set(curr_counter)

        base_total = sum(base_counter.values())
        curr_total = sum(curr_counter.values())

        distance = 0.0

        for label in labels:
            base_ratio = base_counter[label] / base_total if base_total else 0.0
            curr_ratio = curr_counter[label] / curr_total if curr_total else 0.0
            distance += abs(base_ratio - curr_ratio)

        return distance / 2

    @staticmethod
    def status_from_scores(scores: Dict[str, float]) -> DriftStatus:
        if not scores:
            return DriftStatus.UNKNOWN

        max_score = max(scores.values())

        if max_score >= 3.0:
            return DriftStatus.CRITICAL

        if max_score >= 1.5:
            return DriftStatus.WARNING

        return DriftStatus.NORMAL


# =============================================================================
# Engine
# =============================================================================

class PredictiveAnalyticsEngine:
    def __init__(
        self,
        registry: PredictiveModelRegistry,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
    ) -> None:
        self.registry = registry
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()

    def predict(
        self,
        model_id: str,
        request: PredictionRequest,
        context: Optional[PredictiveContext] = None,
    ) -> PredictionResult:
        context = context or PredictiveContext(
            tenant_id=request.tenant_id,
        )

        registered = self.registry.get(model_id)
        metadata = registered.metadata

        try:
            self._validate_model_access(metadata, context)
            validated_features = self._validate_features(
                metadata.feature_definitions,
                request.features,
            )

            prediction = registered.model.predict(validated_features)
            probabilities = registered.model.predict_proba(validated_features)

            explanation = (
                self._explain_prediction(metadata, validated_features, prediction)
                if request.explain
                else {}
            )

            result = PredictionResult(
                prediction_id=str(uuid.uuid4()),
                model_id=metadata.model_id,
                model_version=metadata.version,
                status=PredictionStatus.SUCCESS,
                prediction=prediction,
                probabilities=probabilities,
                scored_at=datetime.now(timezone.utc),
                entity_id=request.entity_id,
                explanation=explanation,
                context=context,
                metadata={
                    "task_type": metadata.task_type.value,
                    "domain": metadata.domain,
                    "request_metadata": request.metadata,
                },
            )

            self._audit("predictive.prediction.success", result)
            self._emit_prediction_metrics(metadata, result)

            return result

        except FeatureValidationError as exc:
            result = self._failed_result(
                metadata=metadata,
                request=request,
                context=context,
                status=PredictionStatus.VALIDATION_ERROR,
                error=str(exc),
            )
            self._audit("predictive.prediction.validation_error", result)
            return result

        except Exception as exc:
            logger.exception("Erro ao executar predição")
            result = self._failed_result(
                metadata=metadata,
                request=request,
                context=context,
                status=PredictionStatus.FAILED,
                error=str(exc),
            )
            self._audit("predictive.prediction.failed", result)
            return result

    def predict_batch(
        self,
        model_id: str,
        requests: Iterable[PredictionRequest],
        context: Optional[PredictiveContext] = None,
    ) -> List[PredictionResult]:
        return [
            self.predict(model_id, request, context=context)
            for request in requests
        ]

    def evaluate(
        self,
        model_id: str,
        dataset: List[Dict[str, Any]],
        target_field: str,
        context: Optional[PredictiveContext] = None,
        positive_label: Optional[Any] = None,
    ) -> EvaluationResult:
        registered = self.registry.get(model_id)
        metadata = registered.metadata
        context = context or PredictiveContext()

        self._validate_model_access(metadata, context)

        actual: List[Any] = []
        predicted: List[Any] = []

        for row in dataset:
            if target_field not in row:
                continue

            features = {
                key: value
                for key, value in row.items()
                if key != target_field
            }

            result = self.predict(
                model_id=model_id,
                request=PredictionRequest(features=features),
                context=context,
            )

            if result.status == PredictionStatus.SUCCESS:
                actual.append(row[target_field])
                predicted.append(result.prediction)

        if metadata.task_type == PredictiveTaskType.CLASSIFICATION:
            metrics = PredictionEvaluator.evaluate_classification(
                actual,
                predicted,
                positive_label=positive_label,
            )
        elif metadata.task_type == PredictiveTaskType.REGRESSION:
            metrics = PredictionEvaluator.evaluate_regression(
                [float(v) for v in actual],
                [float(v) for v in predicted],
            )
        else:
            metrics = {"sample_count": float(len(actual))}

        evaluation = EvaluationResult(
            evaluation_id=str(uuid.uuid4()),
            model_id=metadata.model_id,
            model_version=metadata.version,
            task_type=metadata.task_type,
            metrics=metrics,
            sample_count=len(actual),
            metadata={
                "target_field": target_field,
                "domain": metadata.domain,
            },
        )

        self._audit_evaluation(evaluation, context)

        for metric_name, value in metrics.items():
            self.metrics_backend.gauge(
                f"predictive.evaluation.{metric_name}",
                float(value),
                tags={"model_id": metadata.model_id},
            )

        return evaluation

    def detect_drift(
        self,
        model_id: str,
        baseline_rows: List[Dict[str, Any]],
        current_rows: List[Dict[str, Any]],
    ) -> DriftReport:
        registered = self.registry.get(model_id)
        metadata = registered.metadata

        scores: Dict[str, float] = {}

        for feature in metadata.feature_definitions:
            baseline_values = [
                row.get(feature.name)
                for row in baseline_rows
                if row.get(feature.name) is not None
            ]
            current_values = [
                row.get(feature.name)
                for row in current_rows
                if row.get(feature.name) is not None
            ]

            if feature.feature_type == FeatureType.NUMERIC:
                scores[feature.name] = BasicDriftDetector.compare_numeric_distribution(
                    [float(value) for value in baseline_values],
                    [float(value) for value in current_values],
                )
            elif feature.feature_type in {FeatureType.CATEGORICAL, FeatureType.BOOLEAN}:
                scores[feature.name] = BasicDriftDetector.compare_categorical_distribution(
                    baseline_values,
                    current_values,
                )

        status = BasicDriftDetector.status_from_scores(scores)

        report = DriftReport(
            drift_id=str(uuid.uuid4()),
            model_id=model_id,
            status=status,
            feature_drift_scores=scores,
            prediction_drift_score=None,
            details={
                "baseline_rows": len(baseline_rows),
                "current_rows": len(current_rows),
                "model_version": metadata.version,
            },
        )

        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": "predictive.drift.checked",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "model_id": model_id,
                "status": status.value,
                "scores": scores,
            }
        )

        self.metrics_backend.increment(
            "predictive.drift.checked.total",
            tags={"model_id": model_id, "status": status.value},
        )

        return report

    def export_prediction_json(self, result: PredictionResult) -> str:
        return json.dumps(
            self._prediction_to_dict(result),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def export_evaluation_json(self, result: EvaluationResult) -> str:
        data = asdict(result)
        data["task_type"] = result.task_type.value
        data["evaluated_at"] = result.evaluated_at.isoformat()
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)

    def export_drift_json(self, result: DriftReport) -> str:
        data = asdict(result)
        data["status"] = result.status.value
        data["checked_at"] = result.checked_at.isoformat()
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)

    @staticmethod
    def _validate_model_access(
        metadata: ModelMetadata,
        context: PredictiveContext,
    ) -> None:
        if metadata.status not in {ModelStatus.PRODUCTION, ModelStatus.STAGING}:
            raise ModelValidationError(
                f"Modelo não está disponível para scoring: {metadata.status.value}"
            )

        if metadata.tenant_id and context.tenant_id and metadata.tenant_id != context.tenant_id:
            raise ModelValidationError("Tenant inválido para o modelo")

        if metadata.domain and context.domain and metadata.domain != context.domain:
            raise ModelValidationError("Domínio inválido para o modelo")

    @staticmethod
    def _validate_features(
        definitions: List[FeatureDefinition],
        features: Dict[str, Any],
    ) -> Dict[str, Any]:
        output: Dict[str, Any] = {}

        for definition in definitions:
            output[definition.name] = definition.validate_value(
                features.get(definition.name)
            )

        return output

    @staticmethod
    def _explain_prediction(
        metadata: ModelMetadata,
        features: Dict[str, Any],
        prediction: Any,
    ) -> Dict[str, Any]:
        return {
            "method": "feature_echo",
            "message": "Explicação simplificada baseada nos valores de entrada.",
            "prediction": prediction,
            "features_used": list(features.keys()),
            "feature_values": features,
            "model_version": metadata.version,
        }

    @staticmethod
    def _failed_result(
        metadata: ModelMetadata,
        request: PredictionRequest,
        context: PredictiveContext,
        status: PredictionStatus,
        error: str,
    ) -> PredictionResult:
        return PredictionResult(
            prediction_id=str(uuid.uuid4()),
            model_id=metadata.model_id,
            model_version=metadata.version,
            status=status,
            prediction=None,
            probabilities=None,
            scored_at=datetime.now(timezone.utc),
            entity_id=request.entity_id,
            error=error,
            context=context,
        )

    def _audit(self, event_type: str, result: PredictionResult) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "prediction_id": result.prediction_id,
                "model_id": result.model_id,
                "model_version": result.model_version,
                "status": result.status.value,
                "entity_id": result.entity_id,
                "tenant_id": result.context.tenant_id if result.context else None,
                "domain": result.context.domain if result.context else None,
                "correlation_id": result.context.correlation_id if result.context else None,
                "error": result.error,
            }
        )

    def _audit_evaluation(
        self,
        result: EvaluationResult,
        context: PredictiveContext,
    ) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": "predictive.model.evaluated",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "evaluation_id": result.evaluation_id,
                "model_id": result.model_id,
                "model_version": result.model_version,
                "task_type": result.task_type.value,
                "sample_count": result.sample_count,
                "metrics": result.metrics,
                "tenant_id": context.tenant_id,
                "domain": context.domain,
                "correlation_id": context.correlation_id,
            }
        )

    def _emit_prediction_metrics(
        self,
        metadata: ModelMetadata,
        result: PredictionResult,
    ) -> None:
        self.metrics_backend.increment(
            "predictive.prediction.total",
            tags={
                "model_id": metadata.model_id,
                "version": metadata.version,
                "task_type": metadata.task_type.value,
                "status": result.status.value,
            },
        )

    @staticmethod
    def _prediction_to_dict(result: PredictionResult) -> Dict[str, Any]:
        data = asdict(result)
        data["status"] = result.status.value
        data["scored_at"] = result.scored_at.isoformat()

        if result.context:
            data["context"] = asdict(result.context)

        return data


# =============================================================================
# Default Models
# =============================================================================

def build_default_predictive_registry() -> PredictiveModelRegistry:
    registry = PredictiveModelRegistry()

    churn_model = LogisticRuleModel(
        weights={
            "days_since_last_purchase": 0.08,
            "complaints_count": 0.4,
            "sessions_last_30d": -0.06,
            "orders_last_90d": -0.12,
        },
        intercept=-1.5,
        threshold=0.5,
        positive_label="churn_risk",
        negative_label="active",
    )

    registry.register(
        RegisteredModel(
            metadata=ModelMetadata(
                model_id="customer_churn_risk_v1",
                name="Customer Churn Risk",
                version="1.0.0",
                task_type=PredictiveTaskType.CLASSIFICATION,
                status=ModelStatus.PRODUCTION,
                owner="analytics-team",
                domain="customer",
                target_name="churn_label",
                feature_definitions=[
                    FeatureDefinition(
                        name="days_since_last_purchase",
                        feature_type=FeatureType.NUMERIC,
                        required=True,
                        min_value=0,
                    ),
                    FeatureDefinition(
                        name="complaints_count",
                        feature_type=FeatureType.NUMERIC,
                        required=False,
                        default_value=0,
                        min_value=0,
                    ),
                    FeatureDefinition(
                        name="sessions_last_30d",
                        feature_type=FeatureType.NUMERIC,
                        required=False,
                        default_value=0,
                        min_value=0,
                    ),
                    FeatureDefinition(
                        name="orders_last_90d",
                        feature_type=FeatureType.NUMERIC,
                        required=False,
                        default_value=0,
                        min_value=0,
                    ),
                ],
                tags={"use_case": "retention", "risk": "true"},
            ),
            model=churn_model,
        )
    )

    demand_model = LinearRegressionModel(
        weights={
            "sales_last_7d": 0.6,
            "sales_last_30d_avg": 0.3,
            "promotion_flag": 25.0,
            "stock_available": 0.05,
        },
        intercept=10.0,
    )

    registry.register(
        RegisteredModel(
            metadata=ModelMetadata(
                model_id="product_demand_forecast_v1",
                name="Product Demand Forecast",
                version="1.0.0",
                task_type=PredictiveTaskType.REGRESSION,
                status=ModelStatus.PRODUCTION,
                owner="analytics-team",
                domain="operations",
                target_name="expected_demand",
                feature_definitions=[
                    FeatureDefinition(
                        name="sales_last_7d",
                        feature_type=FeatureType.NUMERIC,
                        required=True,
                        min_value=0,
                    ),
                    FeatureDefinition(
                        name="sales_last_30d_avg",
                        feature_type=FeatureType.NUMERIC,
                        required=True,
                        min_value=0,
                    ),
                    FeatureDefinition(
                        name="promotion_flag",
                        feature_type=FeatureType.BOOLEAN,
                        required=False,
                        default_value=False,
                    ),
                    FeatureDefinition(
                        name="stock_available",
                        feature_type=FeatureType.NUMERIC,
                        required=False,
                        default_value=0,
                        min_value=0,
                    ),
                ],
                tags={"use_case": "demand", "forecast": "true"},
            ),
            model=demand_model,
        )
    )

    return registry


def create_default_predictive_engine() -> PredictiveAnalyticsEngine:
    return PredictiveAnalyticsEngine(
        registry=build_default_predictive_registry()
    )


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    engine = create_default_predictive_engine()

    context = PredictiveContext(
        tenant_id="tenant-default",
        domain="customer",
        user_id="analytics-admin",
        correlation_id="corr-predictive-001",
    )

    result = engine.predict(
        model_id="customer_churn_risk_v1",
        request=PredictionRequest(
            entity_id="customer-001",
            tenant_id="tenant-default",
            explain=True,
            features={
                "days_since_last_purchase": 45,
                "complaints_count": 2,
                "sessions_last_30d": 1,
                "orders_last_90d": 1,
            },
        ),
        context=context,
    )

    print(engine.export_prediction_json(result))

    evaluation = engine.evaluate(
        model_id="customer_churn_risk_v1",
        target_field="label",
        context=context,
        positive_label="churn_risk",
        dataset=[
            {
                "days_since_last_purchase": 60,
                "complaints_count": 2,
                "sessions_last_30d": 1,
                "orders_last_90d": 0,
                "label": "churn_risk",
            },
            {
                "days_since_last_purchase": 5,
                "complaints_count": 0,
                "sessions_last_30d": 12,
                "orders_last_90d": 5,
                "label": "active",
            },
        ],
    )

    print(engine.export_evaluation_json(evaluation))


if __name__ == "__main__":
    example_usage()
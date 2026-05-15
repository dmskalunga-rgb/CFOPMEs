# ml/governance/explainability.py
"""
Enterprise ML Explainability Engine.

Recursos:
- explicabilidade local e global
- permutation feature importance
- surrogate linear model
- suporte opcional a SHAP
- suporte opcional a LIME
- explicações para embeddings/texto
- reason codes
- relatórios JSON/Markdown
- camada de governança e auditoria
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

import numpy as np


try:
    import shap  # type: ignore
except Exception:  # pragma: no cover
    shap = None


try:
    from lime.lime_tabular import LimeTabularExplainer  # type: ignore
except Exception:  # pragma: no cover
    LimeTabularExplainer = None


try:
    from sklearn.inspection import permutation_importance  # type: ignore
    from sklearn.linear_model import Ridge  # type: ignore
    from sklearn.metrics import r2_score  # type: ignore
except Exception:  # pragma: no cover
    permutation_importance = None
    Ridge = None
    r2_score = None


class ExplanationScope(str, Enum):
    LOCAL = "local"
    GLOBAL = "global"


class ExplanationMethod(str, Enum):
    PERMUTATION_IMPORTANCE = "permutation_importance"
    SHAP = "shap"
    LIME = "lime"
    SURROGATE_LINEAR = "surrogate_linear"
    FEATURE_CONTRIBUTION = "feature_contribution"
    EMBEDDING_SIMILARITY = "embedding_similarity"
    TEXT_TOKEN_IMPORTANCE = "text_token_importance"
    RULE_BASED = "rule_based"


class ExplanationRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class FeatureAttribution:
    feature: str
    value: Any
    contribution: float
    direction: str
    rank: int
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReasonCode:
    code: str
    title: str
    description: str
    severity: ExplanationRisk = ExplanationRisk.LOW
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExplanationContext:
    model_id: str
    model_version: str
    environment: str = "production"
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    correlation_id: Optional[str] = None
    request_id: Optional[str] = None


@dataclass(frozen=True)
class ExplanationResult:
    explanation_id: str
    generated_at: str
    scope: ExplanationScope
    method: ExplanationMethod
    prediction: Any
    confidence: Optional[float]
    context: ExplanationContext
    attributions: List[FeatureAttribution]
    reason_codes: List[ReasonCode] = field(default_factory=list)
    baseline: Optional[Any] = None
    fidelity_score: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)


@dataclass(frozen=True)
class GlobalExplanationResult:
    explanation_id: str
    generated_at: str
    scope: ExplanationScope
    method: ExplanationMethod
    context: ExplanationContext
    feature_importance: List[FeatureAttribution]
    fidelity_score: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)


class PredictFunction(Protocol):
    def __call__(self, rows: Sequence[Mapping[str, Any]]) -> Sequence[Any]:
        ...


class PredictProbaFunction(Protocol):
    def __call__(self, rows: Sequence[Mapping[str, Any]]) -> Sequence[Mapping[str, float]]:
        ...


class ExplainabilityError(RuntimeError):
    pass


class FeatureEncoder:
    def __init__(self) -> None:
        self.feature_names_: List[str] = []
        self.categorical_maps_: Dict[str, List[str]] = {}
        self.numeric_features_: List[str] = []

    def fit(self, records: Sequence[Mapping[str, Any]]) -> "FeatureEncoder":
        if not records:
            raise ExplainabilityError("records vazio.")

        features = sorted(set().union(*(r.keys() for r in records)))

        for feature in features:
            values = [r.get(feature) for r in records if r.get(feature) is not None]
            if self._is_numeric(values):
                self.numeric_features_.append(feature)
                self.feature_names_.append(feature)
            else:
                categories = sorted(set(str(v) for v in values))
                self.categorical_maps_[feature] = categories
                for category in categories:
                    self.feature_names_.append(f"{feature}={category}")

        return self

    def transform(self, records: Sequence[Mapping[str, Any]]) -> np.ndarray:
        rows: List[List[float]] = []

        for record in records:
            row: List[float] = []

            for feature in self.numeric_features_:
                value = record.get(feature)
                row.append(float(value) if value is not None else 0.0)

            for feature, categories in self.categorical_maps_.items():
                value = str(record.get(feature))
                for category in categories:
                    row.append(1.0 if value == category else 0.0)

            rows.append(row)

        return np.asarray(rows, dtype=float)

    def fit_transform(self, records: Sequence[Mapping[str, Any]]) -> np.ndarray:
        self.fit(records)
        return self.transform(records)

    @staticmethod
    def _is_numeric(values: Sequence[Any]) -> bool:
        if not values:
            return False
        try:
            [float(v) for v in values]
            return True
        except Exception:
            return False


class ExplanationUtils:
    @staticmethod
    def direction(contribution: float) -> str:
        if contribution > 0:
            return "positive"
        if contribution < 0:
            return "negative"
        return "neutral"

    @staticmethod
    def normalize_importance(values: Mapping[str, float]) -> Dict[str, float]:
        total = sum(abs(v) for v in values.values())
        if total <= 1e-12:
            return {k: 0.0 for k in values}
        return {k: float(v / total) for k, v in values.items()}

    @staticmethod
    def top_attributions(
        values: Mapping[str, float],
        sample: Optional[Mapping[str, Any]] = None,
        top_k: int = 10,
    ) -> List[FeatureAttribution]:
        sorted_items = sorted(values.items(), key=lambda x: abs(x[1]), reverse=True)[:top_k]

        return [
            FeatureAttribution(
                feature=feature,
                value=None if sample is None else sample.get(feature),
                contribution=float(score),
                direction=ExplanationUtils.direction(score),
                rank=i + 1,
            )
            for i, (feature, score) in enumerate(sorted_items)
        ]


class RuleBasedReasonCodeGenerator:
    def __init__(self, thresholds: Optional[Mapping[str, float]] = None) -> None:
        self.thresholds = dict(thresholds or {})

    def generate(
        self,
        attributions: Sequence[FeatureAttribution],
        prediction: Any,
        confidence: Optional[float],
    ) -> List[ReasonCode]:
        codes: List[ReasonCode] = []

        for attr in attributions[:5]:
            abs_score = abs(attr.contribution)
            risk = ExplanationRisk.LOW

            if abs_score >= self.thresholds.get("high_contribution", 0.30):
                risk = ExplanationRisk.HIGH
            elif abs_score >= self.thresholds.get("medium_contribution", 0.15):
                risk = ExplanationRisk.MEDIUM

            codes.append(
                ReasonCode(
                    code=f"TOP_FACTOR_{attr.rank}",
                    title=f"Fator relevante: {attr.feature}",
                    description=(
                        f"A variável '{attr.feature}' teve contribuição "
                        f"{attr.direction} para a decisão '{prediction}'."
                    ),
                    severity=risk,
                    evidence={
                        "feature": attr.feature,
                        "value": attr.value,
                        "contribution": attr.contribution,
                        "direction": attr.direction,
                    },
                )
            )

        if confidence is not None and confidence < 0.60:
            codes.append(
                ReasonCode(
                    code="LOW_CONFIDENCE",
                    title="Baixa confiança",
                    description="A previsão foi gerada com confiança relativamente baixa.",
                    severity=ExplanationRisk.MEDIUM,
                    evidence={"confidence": confidence},
                )
            )

        return codes


class PermutationImportanceExplainer:
    def explain_global(
        self,
        *,
        model: Any,
        records: Sequence[Mapping[str, Any]],
        target: Sequence[Any],
        context: ExplanationContext,
        scoring: Optional[str] = None,
        n_repeats: int = 10,
        top_k: int = 20,
    ) -> GlobalExplanationResult:
        if permutation_importance is None:
            raise ExplainabilityError("sklearn não disponível para permutation_importance.")

        encoder = FeatureEncoder()
        x = encoder.fit_transform(records)

        result = permutation_importance(
            model,
            x,
            target,
            scoring=scoring,
            n_repeats=n_repeats,
            random_state=42,
        )

        values = {
            feature: float(score)
            for feature, score in zip(encoder.feature_names_, result.importances_mean)
        }

        attributions = ExplanationUtils.top_attributions(values, top_k=top_k)

        return GlobalExplanationResult(
            explanation_id=str(uuid.uuid4()),
            generated_at=datetime.now(timezone.utc).isoformat(),
            scope=ExplanationScope.GLOBAL,
            method=ExplanationMethod.PERMUTATION_IMPORTANCE,
            context=context,
            feature_importance=attributions,
            metadata={
                "n_repeats": n_repeats,
                "scoring": scoring,
            },
        )


class SurrogateLinearExplainer:
    def explain_global(
        self,
        *,
        predict_numeric: Callable[[np.ndarray], Sequence[float]],
        records: Sequence[Mapping[str, Any]],
        context: ExplanationContext,
        top_k: int = 20,
    ) -> GlobalExplanationResult:
        if Ridge is None:
            raise ExplainabilityError("sklearn não disponível para Ridge surrogate.")

        encoder = FeatureEncoder()
        x = encoder.fit_transform(records)
        y = np.asarray(predict_numeric(x), dtype=float)

        surrogate = Ridge(alpha=1.0, random_state=42)
        surrogate.fit(x, y)

        y_hat = surrogate.predict(x)
        fidelity = float(r2_score(y, y_hat)) if r2_score is not None else None

        values = {
            feature: float(coef)
            for feature, coef in zip(encoder.feature_names_, surrogate.coef_)
        }

        attributions = ExplanationUtils.top_attributions(values, top_k=top_k)

        return GlobalExplanationResult(
            explanation_id=str(uuid.uuid4()),
            generated_at=datetime.now(timezone.utc).isoformat(),
            scope=ExplanationScope.GLOBAL,
            method=ExplanationMethod.SURROGATE_LINEAR,
            context=context,
            feature_importance=attributions,
            fidelity_score=fidelity,
            metadata={
                "surrogate_model": "ridge",
                "alpha": 1.0,
            },
        )

    def explain_local(
        self,
        *,
        predict_numeric: Callable[[np.ndarray], Sequence[float]],
        background_records: Sequence[Mapping[str, Any]],
        sample: Mapping[str, Any],
        prediction: Any,
        confidence: Optional[float],
        context: ExplanationContext,
        top_k: int = 10,
    ) -> ExplanationResult:
        global_result = self.explain_global(
            predict_numeric=predict_numeric,
            records=background_records,
            context=context,
            top_k=top_k,
        )

        values = {
            attr.feature: attr.contribution * self._encoded_feature_value(attr.feature, sample)
            for attr in global_result.feature_importance
        }

        attributions = ExplanationUtils.top_attributions(values, sample=sample, top_k=top_k)
        reasons = RuleBasedReasonCodeGenerator().generate(attributions, prediction, confidence)

        return ExplanationResult(
            explanation_id=str(uuid.uuid4()),
            generated_at=datetime.now(timezone.utc).isoformat(),
            scope=ExplanationScope.LOCAL,
            method=ExplanationMethod.SURROGATE_LINEAR,
            prediction=prediction,
            confidence=confidence,
            context=context,
            attributions=attributions,
            reason_codes=reasons,
            fidelity_score=global_result.fidelity_score,
            metadata={"source_global_explanation_id": global_result.explanation_id},
        )

    @staticmethod
    def _encoded_feature_value(feature: str, sample: Mapping[str, Any]) -> float:
        if "=" in feature:
            raw_feature, category = feature.split("=", 1)
            return 1.0 if str(sample.get(raw_feature)) == category else 0.0

        value = sample.get(feature)
        try:
            return float(value)
        except Exception:
            return 0.0


class ShapExplainer:
    def explain_local(
        self,
        *,
        model: Any,
        background: Sequence[Mapping[str, Any]],
        sample: Mapping[str, Any],
        prediction: Any,
        confidence: Optional[float],
        context: ExplanationContext,
        top_k: int = 10,
    ) -> ExplanationResult:
        if shap is None:
            raise ExplainabilityError("SHAP não instalado.")

        encoder = FeatureEncoder()
        x_background = encoder.fit_transform(background)
        x_sample = encoder.transform([sample])

        explainer = shap.Explainer(model, x_background)
        shap_values = explainer(x_sample)

        values_array = np.asarray(shap_values.values)
        values = values_array[0]

        if values.ndim > 1:
            values = values[:, 0]

        scores = {
            feature: float(score)
            for feature, score in zip(encoder.feature_names_, values)
        }

        attributions = ExplanationUtils.top_attributions(scores, sample=sample, top_k=top_k)
        reasons = RuleBasedReasonCodeGenerator().generate(attributions, prediction, confidence)

        return ExplanationResult(
            explanation_id=str(uuid.uuid4()),
            generated_at=datetime.now(timezone.utc).isoformat(),
            scope=ExplanationScope.LOCAL,
            method=ExplanationMethod.SHAP,
            prediction=prediction,
            confidence=confidence,
            context=context,
            attributions=attributions,
            reason_codes=reasons,
            baseline=float(np.asarray(shap_values.base_values).flatten()[0]),
            metadata={"feature_names": encoder.feature_names_},
        )


class LimeExplainer:
    def explain_local(
        self,
        *,
        predict_proba_array: Callable[[np.ndarray], np.ndarray],
        background: Sequence[Mapping[str, Any]],
        sample: Mapping[str, Any],
        prediction: Any,
        confidence: Optional[float],
        context: ExplanationContext,
        class_names: Sequence[str],
        top_k: int = 10,
    ) -> ExplanationResult:
        if LimeTabularExplainer is None:
            raise ExplainabilityError("LIME não instalado.")

        encoder = FeatureEncoder()
        x_background = encoder.fit_transform(background)
        x_sample = encoder.transform([sample])[0]

        explainer = LimeTabularExplainer(
            training_data=x_background,
            feature_names=encoder.feature_names_,
            class_names=list(class_names),
            mode="classification",
            discretize_continuous=True,
        )

        explanation = explainer.explain_instance(
            x_sample,
            predict_proba_array,
            num_features=top_k,
        )

        scores = dict(explanation.as_list())

        attributions = [
            FeatureAttribution(
                feature=str(feature),
                value=None,
                contribution=float(score),
                direction=ExplanationUtils.direction(float(score)),
                rank=i + 1,
            )
            for i, (feature, score) in enumerate(scores.items())
        ]

        reasons = RuleBasedReasonCodeGenerator().generate(attributions, prediction, confidence)

        return ExplanationResult(
            explanation_id=str(uuid.uuid4()),
            generated_at=datetime.now(timezone.utc).isoformat(),
            scope=ExplanationScope.LOCAL,
            method=ExplanationMethod.LIME,
            prediction=prediction,
            confidence=confidence,
            context=context,
            attributions=attributions,
            reason_codes=reasons,
            metadata={"class_names": list(class_names)},
        )


class TextExplainability:
    def token_importance_by_occlusion(
        self,
        *,
        text: str,
        predict_score: Callable[[str], float],
        prediction: Any,
        confidence: Optional[float],
        context: ExplanationContext,
        top_k: int = 10,
    ) -> ExplanationResult:
        tokens = text.split()
        if not tokens:
            raise ExplainabilityError("Texto vazio.")

        baseline = float(predict_score(text))
        scores: Dict[str, float] = {}

        for i, token in enumerate(tokens):
            occluded = " ".join(tokens[:i] + tokens[i + 1 :])
            score = float(predict_score(occluded))
            contribution = baseline - score
            key = f"token:{i}:{token}"
            scores[key] = contribution

        attributions = ExplanationUtils.top_attributions(scores, top_k=top_k)
        reasons = RuleBasedReasonCodeGenerator().generate(attributions, prediction, confidence)

        return ExplanationResult(
            explanation_id=str(uuid.uuid4()),
            generated_at=datetime.now(timezone.utc).isoformat(),
            scope=ExplanationScope.LOCAL,
            method=ExplanationMethod.TEXT_TOKEN_IMPORTANCE,
            prediction=prediction,
            confidence=confidence,
            context=context,
            attributions=attributions,
            reason_codes=reasons,
            baseline=baseline,
            metadata={"token_count": len(tokens)},
        )


class EmbeddingExplainability:
    def nearest_reference_explanation(
        self,
        *,
        embedding: Sequence[float],
        reference_embeddings: Sequence[Sequence[float]],
        reference_labels: Sequence[Any],
        prediction: Any,
        confidence: Optional[float],
        context: ExplanationContext,
        top_k: int = 5,
    ) -> ExplanationResult:
        emb = np.asarray(embedding, dtype=float)
        refs = np.asarray(reference_embeddings, dtype=float)

        if refs.ndim != 2:
            raise ExplainabilityError("reference_embeddings precisa ser matriz 2D.")

        if refs.shape[1] != emb.shape[0]:
            raise ExplainabilityError("Dimensão do embedding incompatível.")

        sims = refs @ emb / (
            np.clip(np.linalg.norm(refs, axis=1), 1e-12, None)
            * max(float(np.linalg.norm(emb)), 1e-12)
        )

        order = np.argsort(-sims)[:top_k]

        attributions = [
            FeatureAttribution(
                feature=f"nearest_neighbor_{rank}",
                value=str(reference_labels[idx]),
                contribution=float(sims[idx]),
                direction="positive",
                rank=rank,
                metadata={"reference_index": int(idx)},
            )
            for rank, idx in enumerate(order, start=1)
        ]

        reason_codes = [
            ReasonCode(
                code="SIMILAR_REFERENCE_CASES",
                title="Casos similares encontrados",
                description="A decisão foi explicada por similaridade com exemplos de referência.",
                severity=ExplanationRisk.LOW,
                evidence={
                    "top_similarity": attributions[0].contribution if attributions else None,
                    "neighbors": [asdict(a) for a in attributions],
                },
            )
        ]

        return ExplanationResult(
            explanation_id=str(uuid.uuid4()),
            generated_at=datetime.now(timezone.utc).isoformat(),
            scope=ExplanationScope.LOCAL,
            method=ExplanationMethod.EMBEDDING_SIMILARITY,
            prediction=prediction,
            confidence=confidence,
            context=context,
            attributions=attributions,
            reason_codes=reason_codes,
        )


class ExplanationReportWriter:
    def __init__(self, output_dir: str | Path = "artifacts/explainability") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_json(self, result: ExplanationResult | GlobalExplanationResult) -> Path:
        path = self.output_dir / f"{result.explanation_id}.json"
        path.write_text(result.to_json(), encoding="utf-8")
        return path

    def write_markdown(self, result: ExplanationResult | GlobalExplanationResult) -> Path:
        path = self.output_dir / f"{result.explanation_id}.md"

        lines = [
            f"# Explainability Report - {result.explanation_id}",
            "",
            f"- Generated at: `{result.generated_at}`",
            f"- Scope: `{result.scope.value}`",
            f"- Method: `{result.method.value}`",
            f"- Model: `{result.context.model_id}:{result.context.model_version}`",
            "",
        ]

        if isinstance(result, ExplanationResult):
            lines.extend(
                [
                    f"- Prediction: `{result.prediction}`",
                    f"- Confidence: `{result.confidence}`",
                    "",
                    "## Feature Attributions",
                    "",
                ]
            )
            attrs = result.attributions
        else:
            lines.extend(["## Global Feature Importance", ""])
            attrs = result.feature_importance

        lines.extend(
            [
                "| Rank | Feature | Value | Contribution | Direction |",
                "|---:|---|---|---:|---|",
            ]
        )

        for attr in attrs:
            lines.append(
                f"| {attr.rank} | {attr.feature} | {attr.value} | "
                f"{attr.contribution:.6f} | {attr.direction} |"
            )

        if isinstance(result, ExplanationResult) and result.reason_codes:
            lines.extend(["", "## Reason Codes", ""])
            for reason in result.reason_codes:
                lines.append(f"- **{reason.code}**: {reason.description}")

        path.write_text("\n".join(lines), encoding="utf-8")
        return path


class ExplainabilityService:
    def __init__(self) -> None:
        self.permutation = PermutationImportanceExplainer()
        self.surrogate = SurrogateLinearExplainer()
        self.shap = ShapExplainer()
        self.lime = LimeExplainer()
        self.text = TextExplainability()
        self.embedding = EmbeddingExplainability()
        self.writer = ExplanationReportWriter()

    def write_report(
        self,
        result: ExplanationResult | GlobalExplanationResult,
    ) -> Dict[str, str]:
        return {
            "json": str(self.writer.write_json(result)),
            "markdown": str(self.writer.write_markdown(result)),
        }


if __name__ == "__main__":
    context = ExplanationContext(
        model_id="document-router",
        model_version="1.0.0",
        environment="development",
        tenant_id="digital-meta",
        correlation_id="corr-001",
    )

    background = [
        {"amount": 100.0, "vendor": "A", "days_late": 0},
        {"amount": 900.0, "vendor": "B", "days_late": 3},
        {"amount": 300.0, "vendor": "A", "days_late": 1},
        {"amount": 1200.0, "vendor": "C", "days_late": 7},
    ]

    sample = {"amount": 950.0, "vendor": "B", "days_late": 4}

    def predict_score(text: str) -> float:
        return min(1.0, len(text) / 100)

    service = ExplainabilityService()

    text_result = service.text.token_importance_by_occlusion(
        text="invoice overdue high amount supplier risk",
        predict_score=predict_score,
        prediction="finance_risk",
        confidence=0.78,
        context=context,
    )

    print(text_result.to_json())
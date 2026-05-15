"""
data/analytics/statistical_engine.py

Enterprise Statistical Engine.

Recursos:
- Estatística descritiva
- Distribuições e percentis
- Correlação Pearson/Spearman
- Regressão linear simples
- Testes de hipótese básicos
- Intervalos de confiança
- Detecção de outliers por Z-Score, Modified Z-Score e IQR
- Comparação entre amostras
- Multi-tenant
- Auditoria e métricas plugáveis
- Exportação JSON
- Sem dependências externas obrigatórias
"""

from __future__ import annotations

import json
import logging
import math
import statistics
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class StatisticalMethod(str, Enum):
    DESCRIPTIVE = "descriptive"
    CORRELATION = "correlation"
    REGRESSION = "regression"
    HYPOTHESIS_TEST = "hypothesis_test"
    CONFIDENCE_INTERVAL = "confidence_interval"
    OUTLIER_DETECTION = "outlier_detection"
    DISTRIBUTION = "distribution"


class CorrelationMethod(str, Enum):
    PEARSON = "pearson"
    SPEARMAN = "spearman"


class OutlierMethod(str, Enum):
    Z_SCORE = "z_score"
    MODIFIED_Z_SCORE = "modified_z_score"
    IQR = "iqr"


class HypothesisTestType(str, Enum):
    ONE_SAMPLE_Z = "one_sample_z"
    TWO_SAMPLE_Z = "two_sample_z"
    ONE_SAMPLE_T_APPROX = "one_sample_t_approx"
    TWO_SAMPLE_T_APPROX = "two_sample_t_approx"
    PROPORTION_Z = "proportion_z"


class AlternativeHypothesis(str, Enum):
    TWO_SIDED = "two_sided"
    GREATER = "greater"
    LESS = "less"


class StatisticalStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    INSUFFICIENT_DATA = "insufficient_data"


# =============================================================================
# Exceptions
# =============================================================================

class StatisticalEngineError(Exception):
    """Erro base do statistical engine."""


class StatisticalValidationError(StatisticalEngineError):
    """Erro de validação estatística."""


class StatisticalComputationError(StatisticalEngineError):
    """Erro de cálculo estatístico."""


# =============================================================================
# Protocols
# =============================================================================

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
        logger.info(
            "statistical_engine_audit=%s",
            json.dumps(event, ensure_ascii=False, default=str),
        )


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
# Models
# =============================================================================

@dataclass(frozen=True)
class StatisticalContext:
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    user_id: Optional[str] = None
    correlation_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StatisticalSample:
    name: str
    values: List[float]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def clean(self) -> List[float]:
        return [
            float(value)
            for value in self.values
            if isinstance(value, (int, float)) and math.isfinite(float(value))
        ]

    def validate(self, min_points: int = 1) -> None:
        if not self.name:
            raise StatisticalValidationError("Nome da amostra é obrigatório")

        if len(self.clean()) < min_points:
            raise StatisticalValidationError(
                f"Amostra {self.name} precisa de pelo menos {min_points} pontos válidos"
            )


@dataclass
class DescriptiveStats:
    sample_name: str
    count: int
    mean: Optional[float]
    median: Optional[float]
    mode: Optional[float]
    min_value: Optional[float]
    max_value: Optional[float]
    range_value: Optional[float]
    variance: Optional[float]
    std_dev: Optional[float]
    coefficient_of_variation: Optional[float]
    skewness: Optional[float]
    kurtosis: Optional[float]
    q1: Optional[float]
    q3: Optional[float]
    iqr: Optional[float]
    percentiles: Dict[str, float] = field(default_factory=dict)


@dataclass
class CorrelationResult:
    method: CorrelationMethod
    x_name: str
    y_name: str
    coefficient: Optional[float]
    strength: str
    direction: str
    sample_size: int


@dataclass
class RegressionResult:
    x_name: str
    y_name: str
    slope: float
    intercept: float
    r_squared: float
    correlation: float
    predictions: List[float]
    residuals: List[float]
    mae: float
    rmse: float


@dataclass
class ConfidenceIntervalResult:
    sample_name: str
    confidence_level: float
    mean: float
    standard_error: float
    margin_of_error: float
    lower_bound: float
    upper_bound: float
    sample_size: int


@dataclass
class HypothesisTestResult:
    test_type: HypothesisTestType
    alternative: AlternativeHypothesis
    statistic: float
    p_value_approx: float
    alpha: float
    reject_null: bool
    conclusion: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OutlierResult:
    sample_name: str
    method: OutlierMethod
    outlier_indices: List[int]
    outlier_values: List[float]
    scores: Dict[int, float]
    lower_bound: Optional[float]
    upper_bound: Optional[float]


@dataclass
class DistributionSummary:
    sample_name: str
    bins: List[Tuple[float, float]]
    frequencies: List[int]
    relative_frequencies: List[float]
    cumulative_frequencies: List[int]
    cumulative_relative_frequencies: List[float]


@dataclass
class StatisticalExecutionResult:
    execution_id: str
    method: StatisticalMethod
    status: StatisticalStatus
    computed_at: datetime
    result: Any = None
    error: Optional[str] = None
    context: Optional[StatisticalContext] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Math Helpers
# =============================================================================

class StatisticalMath:
    @staticmethod
    def percentile(values: List[float], percentile: float) -> float:
        if not values:
            raise StatisticalValidationError("Lista vazia")

        if percentile < 0 or percentile > 100:
            raise StatisticalValidationError("Percentil deve estar entre 0 e 100")

        ordered = sorted(values)
        k = (len(ordered) - 1) * percentile / 100
        floor = math.floor(k)
        ceil = math.ceil(k)

        if floor == ceil:
            return ordered[int(k)]

        return ordered[floor] * (ceil - k) + ordered[ceil] * (k - floor)

    @staticmethod
    def normal_cdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    @staticmethod
    def normal_p_value(
        statistic: float,
        alternative: AlternativeHypothesis,
    ) -> float:
        if alternative == AlternativeHypothesis.TWO_SIDED:
            return 2 * (1 - StatisticalMath.normal_cdf(abs(statistic)))

        if alternative == AlternativeHypothesis.GREATER:
            return 1 - StatisticalMath.normal_cdf(statistic)

        if alternative == AlternativeHypothesis.LESS:
            return StatisticalMath.normal_cdf(statistic)

        return 1.0

    @staticmethod
    def z_for_confidence(confidence_level: float) -> float:
        if confidence_level >= 0.995:
            return 2.807
        if confidence_level >= 0.99:
            return 2.576
        if confidence_level >= 0.98:
            return 2.326
        if confidence_level >= 0.95:
            return 1.96
        if confidence_level >= 0.90:
            return 1.645
        if confidence_level >= 0.80:
            return 1.282
        return 1.96

    @staticmethod
    def ranks(values: List[float]) -> List[float]:
        indexed = sorted(enumerate(values), key=lambda item: item[1])
        ranks = [0.0] * len(values)

        i = 0
        while i < len(indexed):
            j = i

            while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
                j += 1

            avg_rank = (i + j + 2) / 2

            for k in range(i, j + 1):
                original_index = indexed[k][0]
                ranks[original_index] = avg_rank

            i = j + 1

        return ranks

    @staticmethod
    def safe_divide(numerator: float, denominator: float) -> Optional[float]:
        if denominator == 0:
            return None
        return numerator / denominator


# =============================================================================
# Calculator
# =============================================================================

class StatisticalCalculator:
    @staticmethod
    def descriptive(
        sample: StatisticalSample,
        percentiles: Optional[List[float]] = None,
    ) -> DescriptiveStats:
        values = sample.clean()
        sample.validate(min_points=1)

        count = len(values)
        mean = statistics.mean(values)
        median = statistics.median(values)

        try:
            mode = statistics.mode(values)
        except statistics.StatisticsError:
            mode = None

        min_value = min(values)
        max_value = max(values)
        range_value = max_value - min_value

        variance = statistics.variance(values) if count > 1 else 0.0
        std_dev = statistics.stdev(values) if count > 1 else 0.0

        coefficient_of_variation = (
            std_dev / mean
            if mean != 0
            else None
        )

        q1 = StatisticalMath.percentile(values, 25)
        q3 = StatisticalMath.percentile(values, 75)
        iqr = q3 - q1

        skewness = StatisticalCalculator._skewness(values, mean, std_dev)
        kurtosis = StatisticalCalculator._kurtosis(values, mean, std_dev)

        percentile_values = {
            f"p{int(p)}": StatisticalMath.percentile(values, p)
            for p in (percentiles or [5, 10, 25, 50, 75, 90, 95, 99])
        }

        return DescriptiveStats(
            sample_name=sample.name,
            count=count,
            mean=mean,
            median=median,
            mode=mode,
            min_value=min_value,
            max_value=max_value,
            range_value=range_value,
            variance=variance,
            std_dev=std_dev,
            coefficient_of_variation=coefficient_of_variation,
            skewness=skewness,
            kurtosis=kurtosis,
            q1=q1,
            q3=q3,
            iqr=iqr,
            percentiles=percentile_values,
        )

    @staticmethod
    def correlation(
        x: StatisticalSample,
        y: StatisticalSample,
        method: CorrelationMethod = CorrelationMethod.PEARSON,
    ) -> CorrelationResult:
        x_values = x.clean()
        y_values = y.clean()

        if len(x_values) != len(y_values):
            raise StatisticalValidationError("Amostras precisam ter o mesmo tamanho")

        if len(x_values) < 2:
            raise StatisticalValidationError("Correlação exige pelo menos 2 pontos")

        if method == CorrelationMethod.SPEARMAN:
            x_values = StatisticalMath.ranks(x_values)
            y_values = StatisticalMath.ranks(y_values)

        coefficient = StatisticalCalculator._pearson(x_values, y_values)

        return CorrelationResult(
            method=method,
            x_name=x.name,
            y_name=y.name,
            coefficient=coefficient,
            strength=StatisticalCalculator._correlation_strength(coefficient),
            direction=StatisticalCalculator._correlation_direction(coefficient),
            sample_size=len(x_values),
        )

    @staticmethod
    def regression(
        x: StatisticalSample,
        y: StatisticalSample,
    ) -> RegressionResult:
        x_values = x.clean()
        y_values = y.clean()

        if len(x_values) != len(y_values):
            raise StatisticalValidationError("Amostras precisam ter o mesmo tamanho")

        if len(x_values) < 2:
            raise StatisticalValidationError("Regressão exige pelo menos 2 pontos")

        x_mean = statistics.mean(x_values)
        y_mean = statistics.mean(y_values)

        numerator = sum(
            (xi - x_mean) * (yi - y_mean)
            for xi, yi in zip(x_values, y_values)
        )
        denominator = sum((xi - x_mean) ** 2 for xi in x_values)

        if denominator == 0:
            raise StatisticalComputationError("Variância de X igual a zero")

        slope = numerator / denominator
        intercept = y_mean - slope * x_mean

        predictions = [intercept + slope * xi for xi in x_values]
        residuals = [yi - pred for yi, pred in zip(y_values, predictions)]

        correlation = StatisticalCalculator._pearson(x_values, y_values)
        r_squared = correlation ** 2 if correlation is not None else 0.0

        mae = sum(abs(error) for error in residuals) / len(residuals)
        rmse = math.sqrt(sum(error ** 2 for error in residuals) / len(residuals))

        return RegressionResult(
            x_name=x.name,
            y_name=y.name,
            slope=slope,
            intercept=intercept,
            r_squared=r_squared,
            correlation=correlation,
            predictions=predictions,
            residuals=residuals,
            mae=mae,
            rmse=rmse,
        )

    @staticmethod
    def confidence_interval_mean(
        sample: StatisticalSample,
        confidence_level: float = 0.95,
    ) -> ConfidenceIntervalResult:
        values = sample.clean()
        sample.validate(min_points=2)

        mean = statistics.mean(values)
        std_dev = statistics.stdev(values)
        standard_error = std_dev / math.sqrt(len(values))
        z_value = StatisticalMath.z_for_confidence(confidence_level)
        margin = z_value * standard_error

        return ConfidenceIntervalResult(
            sample_name=sample.name,
            confidence_level=confidence_level,
            mean=mean,
            standard_error=standard_error,
            margin_of_error=margin,
            lower_bound=mean - margin,
            upper_bound=mean + margin,
            sample_size=len(values),
        )

    @staticmethod
    def hypothesis_test(
        test_type: HypothesisTestType,
        sample_a: StatisticalSample,
        sample_b: Optional[StatisticalSample] = None,
        null_value: float = 0.0,
        alpha: float = 0.05,
        alternative: AlternativeHypothesis = AlternativeHypothesis.TWO_SIDED,
    ) -> HypothesisTestResult:
        if test_type in {
            HypothesisTestType.ONE_SAMPLE_Z,
            HypothesisTestType.ONE_SAMPLE_T_APPROX,
        }:
            return StatisticalCalculator._one_sample_test(
                test_type=test_type,
                sample=sample_a,
                null_value=null_value,
                alpha=alpha,
                alternative=alternative,
            )

        if test_type in {
            HypothesisTestType.TWO_SAMPLE_Z,
            HypothesisTestType.TWO_SAMPLE_T_APPROX,
        }:
            if sample_b is None:
                raise StatisticalValidationError("Teste de duas amostras exige sample_b")

            return StatisticalCalculator._two_sample_test(
                test_type=test_type,
                sample_a=sample_a,
                sample_b=sample_b,
                null_value=null_value,
                alpha=alpha,
                alternative=alternative,
            )

        if test_type == HypothesisTestType.PROPORTION_Z:
            return StatisticalCalculator._proportion_z_test(
                sample=sample_a,
                null_value=null_value,
                alpha=alpha,
                alternative=alternative,
            )

        raise StatisticalValidationError(f"Teste não suportado: {test_type}")

    @staticmethod
    def outliers(
        sample: StatisticalSample,
        method: OutlierMethod = OutlierMethod.IQR,
        threshold: float = 3.0,
    ) -> OutlierResult:
        values = sample.clean()
        sample.validate(min_points=3)

        if method == OutlierMethod.Z_SCORE:
            return StatisticalCalculator._outliers_z_score(sample.name, values, threshold)

        if method == OutlierMethod.MODIFIED_Z_SCORE:
            return StatisticalCalculator._outliers_modified_z_score(sample.name, values, threshold)

        if method == OutlierMethod.IQR:
            return StatisticalCalculator._outliers_iqr(sample.name, values)

        raise StatisticalValidationError(f"Método de outlier não suportado: {method}")

    @staticmethod
    def distribution(
        sample: StatisticalSample,
        bins: int = 10,
    ) -> DistributionSummary:
        values = sample.clean()
        sample.validate(min_points=1)

        if bins <= 0:
            raise StatisticalValidationError("bins precisa ser maior que zero")

        min_value = min(values)
        max_value = max(values)

        if min_value == max_value:
            return DistributionSummary(
                sample_name=sample.name,
                bins=[(min_value, max_value)],
                frequencies=[len(values)],
                relative_frequencies=[1.0],
                cumulative_frequencies=[len(values)],
                cumulative_relative_frequencies=[1.0],
            )

        width = (max_value - min_value) / bins
        ranges: List[Tuple[float, float]] = []
        frequencies = [0] * bins

        for index in range(bins):
            start = min_value + index * width
            end = start + width
            ranges.append((start, end))

        for value in values:
            index = min(int((value - min_value) / width), bins - 1)
            frequencies[index] += 1

        total = len(values)
        relative = [freq / total for freq in frequencies]

        cumulative: List[int] = []
        running = 0

        for freq in frequencies:
            running += freq
            cumulative.append(running)

        cumulative_relative = [value / total for value in cumulative]

        return DistributionSummary(
            sample_name=sample.name,
            bins=ranges,
            frequencies=frequencies,
            relative_frequencies=relative,
            cumulative_frequencies=cumulative,
            cumulative_relative_frequencies=cumulative_relative,
        )

    @staticmethod
    def _one_sample_test(
        test_type: HypothesisTestType,
        sample: StatisticalSample,
        null_value: float,
        alpha: float,
        alternative: AlternativeHypothesis,
    ) -> HypothesisTestResult:
        values = sample.clean()
        sample.validate(min_points=2)

        mean = statistics.mean(values)
        std_dev = statistics.stdev(values)

        if std_dev == 0:
            raise StatisticalComputationError("Desvio padrão zero")

        standard_error = std_dev / math.sqrt(len(values))
        statistic = (mean - null_value) / standard_error
        p_value = StatisticalMath.normal_p_value(statistic, alternative)
        reject = p_value < alpha

        return HypothesisTestResult(
            test_type=test_type,
            alternative=alternative,
            statistic=statistic,
            p_value_approx=p_value,
            alpha=alpha,
            reject_null=reject,
            conclusion=StatisticalCalculator._conclusion(reject),
            metadata={
                "sample": sample.name,
                "mean": mean,
                "null_value": null_value,
                "standard_error": standard_error,
                "approximation": "normal",
            },
        )

    @staticmethod
    def _two_sample_test(
        test_type: HypothesisTestType,
        sample_a: StatisticalSample,
        sample_b: StatisticalSample,
        null_value: float,
        alpha: float,
        alternative: AlternativeHypothesis,
    ) -> HypothesisTestResult:
        a = sample_a.clean()
        b = sample_b.clean()

        sample_a.validate(min_points=2)
        sample_b.validate(min_points=2)

        mean_a = statistics.mean(a)
        mean_b = statistics.mean(b)
        var_a = statistics.variance(a)
        var_b = statistics.variance(b)

        standard_error = math.sqrt(var_a / len(a) + var_b / len(b))

        if standard_error == 0:
            raise StatisticalComputationError("Erro padrão zero")

        statistic = ((mean_a - mean_b) - null_value) / standard_error
        p_value = StatisticalMath.normal_p_value(statistic, alternative)
        reject = p_value < alpha

        return HypothesisTestResult(
            test_type=test_type,
            alternative=alternative,
            statistic=statistic,
            p_value_approx=p_value,
            alpha=alpha,
            reject_null=reject,
            conclusion=StatisticalCalculator._conclusion(reject),
            metadata={
                "sample_a": sample_a.name,
                "sample_b": sample_b.name,
                "mean_a": mean_a,
                "mean_b": mean_b,
                "null_value": null_value,
                "standard_error": standard_error,
                "approximation": "normal",
            },
        )

    @staticmethod
    def _proportion_z_test(
        sample: StatisticalSample,
        null_value: float,
        alpha: float,
        alternative: AlternativeHypothesis,
    ) -> HypothesisTestResult:
        values = sample.clean()
        sample.validate(min_points=2)

        successes = sum(1 for value in values if value == 1)
        n = len(values)
        observed = successes / n

        if not 0 < null_value < 1:
            raise StatisticalValidationError("null_value deve estar entre 0 e 1 para proporção")

        standard_error = math.sqrt(null_value * (1 - null_value) / n)

        if standard_error == 0:
            raise StatisticalComputationError("Erro padrão zero")

        statistic = (observed - null_value) / standard_error
        p_value = StatisticalMath.normal_p_value(statistic, alternative)
        reject = p_value < alpha

        return HypothesisTestResult(
            test_type=HypothesisTestType.PROPORTION_Z,
            alternative=alternative,
            statistic=statistic,
            p_value_approx=p_value,
            alpha=alpha,
            reject_null=reject,
            conclusion=StatisticalCalculator._conclusion(reject),
            metadata={
                "sample": sample.name,
                "successes": successes,
                "n": n,
                "observed_proportion": observed,
                "null_proportion": null_value,
            },
        )

    @staticmethod
    def _outliers_z_score(
        sample_name: str,
        values: List[float],
        threshold: float,
    ) -> OutlierResult:
        mean = statistics.mean(values)
        std_dev = statistics.stdev(values)

        if std_dev == 0:
            return OutlierResult(
                sample_name=sample_name,
                method=OutlierMethod.Z_SCORE,
                outlier_indices=[],
                outlier_values=[],
                scores={},
                lower_bound=mean,
                upper_bound=mean,
            )

        scores: Dict[int, float] = {}
        outlier_indices: List[int] = []

        for index, value in enumerate(values):
            score = (value - mean) / std_dev
            scores[index] = score

            if abs(score) >= threshold:
                outlier_indices.append(index)

        return OutlierResult(
            sample_name=sample_name,
            method=OutlierMethod.Z_SCORE,
            outlier_indices=outlier_indices,
            outlier_values=[values[index] for index in outlier_indices],
            scores=scores,
            lower_bound=mean - threshold * std_dev,
            upper_bound=mean + threshold * std_dev,
        )

    @staticmethod
    def _outliers_modified_z_score(
        sample_name: str,
        values: List[float],
        threshold: float,
    ) -> OutlierResult:
        median = statistics.median(values)
        deviations = [abs(value - median) for value in values]
        mad = statistics.median(deviations)

        if mad == 0:
            return OutlierResult(
                sample_name=sample_name,
                method=OutlierMethod.MODIFIED_Z_SCORE,
                outlier_indices=[],
                outlier_values=[],
                scores={},
                lower_bound=median,
                upper_bound=median,
            )

        scores: Dict[int, float] = {}
        outlier_indices: List[int] = []

        for index, value in enumerate(values):
            score = 0.6745 * (value - median) / mad
            scores[index] = score

            if abs(score) >= threshold:
                outlier_indices.append(index)

        lower = median - (threshold * mad / 0.6745)
        upper = median + (threshold * mad / 0.6745)

        return OutlierResult(
            sample_name=sample_name,
            method=OutlierMethod.MODIFIED_Z_SCORE,
            outlier_indices=outlier_indices,
            outlier_values=[values[index] for index in outlier_indices],
            scores=scores,
            lower_bound=lower,
            upper_bound=upper,
        )

    @staticmethod
    def _outliers_iqr(
        sample_name: str,
        values: List[float],
    ) -> OutlierResult:
        q1 = StatisticalMath.percentile(values, 25)
        q3 = StatisticalMath.percentile(values, 75)
        iqr = q3 - q1

        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr

        outlier_indices = [
            index for index, value in enumerate(values)
            if value < lower or value > upper
        ]

        scores = {
            index: values[index]
            for index in outlier_indices
        }

        return OutlierResult(
            sample_name=sample_name,
            method=OutlierMethod.IQR,
            outlier_indices=outlier_indices,
            outlier_values=[values[index] for index in outlier_indices],
            scores=scores,
            lower_bound=lower,
            upper_bound=upper,
        )

    @staticmethod
    def _pearson(x_values: List[float], y_values: List[float]) -> float:
        mean_x = statistics.mean(x_values)
        mean_y = statistics.mean(y_values)

        numerator = sum(
            (x - mean_x) * (y - mean_y)
            for x, y in zip(x_values, y_values)
        )

        denominator_x = math.sqrt(sum((x - mean_x) ** 2 for x in x_values))
        denominator_y = math.sqrt(sum((y - mean_y) ** 2 for y in y_values))

        denominator = denominator_x * denominator_y

        if denominator == 0:
            return 0.0

        return numerator / denominator

    @staticmethod
    def _skewness(values: List[float], mean: float, std_dev: float) -> Optional[float]:
        n = len(values)

        if n < 3 or std_dev == 0:
            return None

        return sum(((value - mean) / std_dev) ** 3 for value in values) / n

    @staticmethod
    def _kurtosis(values: List[float], mean: float, std_dev: float) -> Optional[float]:
        n = len(values)

        if n < 4 or std_dev == 0:
            return None

        return sum(((value - mean) / std_dev) ** 4 for value in values) / n - 3

    @staticmethod
    def _correlation_strength(coefficient: Optional[float]) -> str:
        if coefficient is None:
            return "unknown"

        absolute = abs(coefficient)

        if absolute >= 0.9:
            return "very_strong"
        if absolute >= 0.7:
            return "strong"
        if absolute >= 0.5:
            return "moderate"
        if absolute >= 0.3:
            return "weak"

        return "very_weak"

    @staticmethod
    def _correlation_direction(coefficient: Optional[float]) -> str:
        if coefficient is None:
            return "unknown"

        if coefficient > 0:
            return "positive"

        if coefficient < 0:
            return "negative"

        return "none"

    @staticmethod
    def _conclusion(reject: bool) -> str:
        return (
            "Rejeita-se a hipótese nula no nível de significância informado."
            if reject
            else "Não há evidência suficiente para rejeitar a hipótese nula."
        )


# =============================================================================
# Engine
# =============================================================================

class StatisticalEngine:
    def __init__(
        self,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
    ) -> None:
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()

    def descriptive(
        self,
        sample: StatisticalSample,
        context: Optional[StatisticalContext] = None,
        percentiles: Optional[List[float]] = None,
    ) -> StatisticalExecutionResult:
        return self._execute(
            StatisticalMethod.DESCRIPTIVE,
            context,
            lambda: StatisticalCalculator.descriptive(sample, percentiles),
            metadata={"sample": sample.name},
        )

    def correlation(
        self,
        x: StatisticalSample,
        y: StatisticalSample,
        method: CorrelationMethod = CorrelationMethod.PEARSON,
        context: Optional[StatisticalContext] = None,
    ) -> StatisticalExecutionResult:
        return self._execute(
            StatisticalMethod.CORRELATION,
            context,
            lambda: StatisticalCalculator.correlation(x, y, method),
            metadata={"x": x.name, "y": y.name, "correlation_method": method.value},
        )

    def regression(
        self,
        x: StatisticalSample,
        y: StatisticalSample,
        context: Optional[StatisticalContext] = None,
    ) -> StatisticalExecutionResult:
        return self._execute(
            StatisticalMethod.REGRESSION,
            context,
            lambda: StatisticalCalculator.regression(x, y),
            metadata={"x": x.name, "y": y.name},
        )

    def confidence_interval_mean(
        self,
        sample: StatisticalSample,
        confidence_level: float = 0.95,
        context: Optional[StatisticalContext] = None,
    ) -> StatisticalExecutionResult:
        return self._execute(
            StatisticalMethod.CONFIDENCE_INTERVAL,
            context,
            lambda: StatisticalCalculator.confidence_interval_mean(
                sample,
                confidence_level,
            ),
            metadata={
                "sample": sample.name,
                "confidence_level": confidence_level,
            },
        )

    def hypothesis_test(
        self,
        test_type: HypothesisTestType,
        sample_a: StatisticalSample,
        sample_b: Optional[StatisticalSample] = None,
        null_value: float = 0.0,
        alpha: float = 0.05,
        alternative: AlternativeHypothesis = AlternativeHypothesis.TWO_SIDED,
        context: Optional[StatisticalContext] = None,
    ) -> StatisticalExecutionResult:
        return self._execute(
            StatisticalMethod.HYPOTHESIS_TEST,
            context,
            lambda: StatisticalCalculator.hypothesis_test(
                test_type=test_type,
                sample_a=sample_a,
                sample_b=sample_b,
                null_value=null_value,
                alpha=alpha,
                alternative=alternative,
            ),
            metadata={
                "test_type": test_type.value,
                "sample_a": sample_a.name,
                "sample_b": sample_b.name if sample_b else None,
                "alpha": alpha,
                "alternative": alternative.value,
            },
        )

    def outliers(
        self,
        sample: StatisticalSample,
        method: OutlierMethod = OutlierMethod.IQR,
        threshold: float = 3.0,
        context: Optional[StatisticalContext] = None,
    ) -> StatisticalExecutionResult:
        return self._execute(
            StatisticalMethod.OUTLIER_DETECTION,
            context,
            lambda: StatisticalCalculator.outliers(sample, method, threshold),
            metadata={
                "sample": sample.name,
                "outlier_method": method.value,
                "threshold": threshold,
            },
        )

    def distribution(
        self,
        sample: StatisticalSample,
        bins: int = 10,
        context: Optional[StatisticalContext] = None,
    ) -> StatisticalExecutionResult:
        return self._execute(
            StatisticalMethod.DISTRIBUTION,
            context,
            lambda: StatisticalCalculator.distribution(sample, bins),
            metadata={
                "sample": sample.name,
                "bins": bins,
            },
        )

    def compare_samples(
        self,
        sample_a: StatisticalSample,
        sample_b: StatisticalSample,
        context: Optional[StatisticalContext] = None,
    ) -> Dict[str, StatisticalExecutionResult]:
        return {
            "sample_a_descriptive": self.descriptive(sample_a, context=context),
            "sample_b_descriptive": self.descriptive(sample_b, context=context),
            "two_sample_test": self.hypothesis_test(
                test_type=HypothesisTestType.TWO_SAMPLE_T_APPROX,
                sample_a=sample_a,
                sample_b=sample_b,
                null_value=0.0,
                alternative=AlternativeHypothesis.TWO_SIDED,
                context=context,
            ),
        }

    def export_result_json(self, result: StatisticalExecutionResult) -> str:
        return json.dumps(
            self._execution_result_to_dict(result),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def export_many_json(
        self,
        results: Dict[str, StatisticalExecutionResult],
    ) -> str:
        return json.dumps(
            {
                key: self._execution_result_to_dict(value)
                for key, value in results.items()
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def _execute(
        self,
        method: StatisticalMethod,
        context: Optional[StatisticalContext],
        fn: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StatisticalExecutionResult:
        context = context or StatisticalContext()
        started_at = datetime.now(timezone.utc)

        try:
            result = fn()

            execution = StatisticalExecutionResult(
                execution_id=str(uuid.uuid4()),
                method=method,
                status=StatisticalStatus.SUCCESS,
                computed_at=datetime.now(timezone.utc),
                result=result,
                context=context,
                metadata=metadata or {},
            )

            self._audit("statistical.execution.success", execution)
            self.metrics_backend.increment(
                "statistical.execution.success.total",
                tags={"method": method.value},
            )

            elapsed_ms = (
                datetime.now(timezone.utc) - started_at
            ).total_seconds() * 1000

            self.metrics_backend.gauge(
                "statistical.execution.duration_ms",
                elapsed_ms,
                tags={"method": method.value},
            )

            return execution

        except StatisticalValidationError as exc:
            execution = StatisticalExecutionResult(
                execution_id=str(uuid.uuid4()),
                method=method,
                status=StatisticalStatus.INSUFFICIENT_DATA,
                computed_at=datetime.now(timezone.utc),
                error=str(exc),
                context=context,
                metadata=metadata or {},
            )

            self._audit("statistical.execution.validation_error", execution)
            return execution

        except Exception as exc:
            logger.exception("Erro no statistical engine")

            execution = StatisticalExecutionResult(
                execution_id=str(uuid.uuid4()),
                method=method,
                status=StatisticalStatus.FAILED,
                computed_at=datetime.now(timezone.utc),
                error=str(exc),
                context=context,
                metadata=metadata or {},
            )

            self._audit("statistical.execution.failed", execution)
            self.metrics_backend.increment(
                "statistical.execution.failed.total",
                tags={"method": method.value},
            )

            return execution

    def _audit(
        self,
        event_type: str,
        execution: StatisticalExecutionResult,
    ) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "execution_id": execution.execution_id,
                "method": execution.method.value,
                "status": execution.status.value,
                "tenant_id": execution.context.tenant_id if execution.context else None,
                "domain": execution.context.domain if execution.context else None,
                "user_id": execution.context.user_id if execution.context else None,
                "correlation_id": execution.context.correlation_id if execution.context else None,
                "error": execution.error,
                "metadata": execution.metadata,
            }
        )

    @staticmethod
    def _execution_result_to_dict(
        execution: StatisticalExecutionResult,
    ) -> Dict[str, Any]:
        data = asdict(execution)
        data["method"] = execution.method.value
        data["status"] = execution.status.value
        data["computed_at"] = execution.computed_at.isoformat()

        if execution.context:
            data["context"] = asdict(execution.context)

        return data


# =============================================================================
# Factory
# =============================================================================

def create_default_statistical_engine() -> StatisticalEngine:
    return StatisticalEngine()


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    engine = create_default_statistical_engine()

    context = StatisticalContext(
        tenant_id="tenant-default",
        domain="analytics",
        user_id="analytics-admin",
        correlation_id="corr-statistical-001",
    )

    sales = StatisticalSample(
        name="sales",
        values=[100, 120, 130, 125, 140, 150, 160, 155, 1000],
    )

    customers = StatisticalSample(
        name="customers",
        values=[10, 12, 13, 13, 15, 16, 18, 19, 25],
    )

    descriptive = engine.descriptive(sales, context=context)
    correlation = engine.correlation(sales, customers, context=context)
    regression = engine.regression(customers, sales, context=context)
    outliers = engine.outliers(sales, method=OutlierMethod.IQR, context=context)
    ci = engine.confidence_interval_mean(sales, context=context)

    print(engine.export_result_json(descriptive))
    print(engine.export_result_json(correlation))
    print(engine.export_result_json(regression))
    print(engine.export_result_json(outliers))
    print(engine.export_result_json(ci))


if __name__ == "__main__":
    example_usage()
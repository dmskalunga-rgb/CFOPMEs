"""
data/analytics/business_analytics.py

Enterprise Business Analytics Engine.

Recursos:
- KPIs executivos de negócio
- Scorecards
- Metas e acompanhamento
- Period-over-period analysis
- Crescimento, margem, receita, pedidos e ticket médio
- Health score de negócio
- Insights automáticos
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

class BusinessDomain(str, Enum):
    SALES = "sales"
    FINANCE = "finance"
    MARKETING = "marketing"
    CUSTOMER = "customer"
    OPERATIONS = "operations"
    PRODUCT = "product"
    EXECUTIVE = "executive"


class KPIType(str, Enum):
    REVENUE = "revenue"
    COST = "cost"
    PROFIT = "profit"
    MARGIN = "margin"
    ORDERS = "orders"
    CUSTOMERS = "customers"
    CONVERSION = "conversion"
    RETENTION = "retention"
    CHURN = "churn"
    INVENTORY = "inventory"
    PRODUCTIVITY = "productivity"
    CUSTOM = "custom"


class TrendDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"
    UNKNOWN = "unknown"


class PerformanceStatus(str, Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class ComparisonMode(str, Enum):
    ABSOLUTE = "absolute"
    PERCENTAGE = "percentage"
    BOTH = "both"


class InsightSeverity(str, Enum):
    INFO = "info"
    POSITIVE = "positive"
    WARNING = "warning"
    CRITICAL = "critical"


# =============================================================================
# Exceptions
# =============================================================================

class BusinessAnalyticsError(Exception):
    """Erro base de business analytics."""


class KPIValidationError(BusinessAnalyticsError):
    """Erro de validação de KPI."""


class BusinessAnalyticsExecutionError(BusinessAnalyticsError):
    """Erro de execução de business analytics."""


class BusinessDatasetError(BusinessAnalyticsError):
    """Erro no dataset de negócio."""


# =============================================================================
# Protocols
# =============================================================================

class BusinessDataProvider(Protocol):
    def fetch(
        self,
        dataset: str,
        filters: Optional[Dict[str, Any]] = None,
        context: Optional["BusinessAnalyticsContext"] = None,
    ) -> List[Dict[str, Any]]:
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

class InMemoryBusinessDataProvider:
    def __init__(self, datasets: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> None:
        self.datasets = datasets or {}

    def fetch(
        self,
        dataset: str,
        filters: Optional[Dict[str, Any]] = None,
        context: Optional["BusinessAnalyticsContext"] = None,
    ) -> List[Dict[str, Any]]:
        rows = list(self.datasets.get(dataset, []))

        filters = filters or {}

        if context and context.tenant_id:
            rows = [
                row for row in rows
                if row.get("tenant_id") in {None, context.tenant_id}
            ]

        for key, value in filters.items():
            if value is None:
                continue

            if isinstance(value, tuple) and len(value) == 2:
                start, end = value
                rows = [
                    row for row in rows
                    if row.get(key) is not None and start <= row.get(key) <= end
                ]
            elif isinstance(value, list):
                rows = [row for row in rows if row.get(key) in value]
            else:
                rows = [row for row in rows if row.get(key) == value]

        return rows


class LoggingAuditBackend:
    def write_event(self, event: Dict[str, Any]) -> None:
        logger.info("business_analytics_audit=%s", json.dumps(event, ensure_ascii=False, default=str))


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
class BusinessAnalyticsContext:
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    user_id: Optional[str] = None
    correlation_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BusinessPeriod:
    start: datetime
    end: datetime
    label: str = "current"

    def validate(self) -> None:
        if self.start >= self.end:
            raise BusinessAnalyticsExecutionError("Período inválido: start deve ser menor que end")


@dataclass(frozen=True)
class KPIDefinition:
    kpi_id: str
    name: str
    kpi_type: KPIType
    domain: BusinessDomain
    dataset: str
    measure: Optional[str] = None
    aggregation: str = "sum"
    target: Optional[float] = None
    higher_is_better: bool = True
    weight: float = 1.0
    filters: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    unit: str = ""
    tags: Dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.kpi_id:
            raise KPIValidationError("kpi_id é obrigatório")

        if not self.name:
            raise KPIValidationError("name é obrigatório")

        if not self.dataset:
            raise KPIValidationError("dataset é obrigatório")

        if self.aggregation != "count" and not self.measure:
            raise KPIValidationError("measure é obrigatório quando aggregation != count")

        if self.weight < 0:
            raise KPIValidationError("weight não pode ser negativo")


@dataclass
class KPIResult:
    kpi_id: str
    name: str
    value: Optional[float]
    target: Optional[float]
    target_achievement: Optional[float]
    status: PerformanceStatus
    trend: TrendDirection
    delta_absolute: Optional[float] = None
    delta_percentage: Optional[float] = None
    unit: str = ""
    period: Optional[BusinessPeriod] = None
    comparison_period: Optional[BusinessPeriod] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BusinessInsight:
    insight_id: str
    title: str
    message: str
    severity: InsightSeverity
    domain: BusinessDomain
    kpi_id: Optional[str] = None
    value: Optional[float] = None
    recommendation: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BusinessScorecard:
    scorecard_id: str
    name: str
    domain: BusinessDomain
    kpis: List[KPIResult]
    health_score: float
    status: PerformanceStatus
    insights: List[BusinessInsight]
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BusinessAnalyticsReport:
    report_id: str
    title: str
    context: BusinessAnalyticsContext
    period: BusinessPeriod
    comparison_period: Optional[BusinessPeriod]
    scorecards: List[BusinessScorecard]
    executive_summary: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# KPI Repository
# =============================================================================

class KPIRepository:
    def __init__(self, kpis: Optional[List[KPIDefinition]] = None) -> None:
        self._kpis: Dict[str, KPIDefinition] = {}

        for kpi in kpis or []:
            self.save(kpi)

    def save(self, kpi: KPIDefinition) -> None:
        kpi.validate()
        self._kpis[kpi.kpi_id] = kpi

    def get(self, kpi_id: str) -> KPIDefinition:
        if kpi_id not in self._kpis:
            raise KPIValidationError(f"KPI não encontrado: {kpi_id}")
        return self._kpis[kpi_id]

    def list_all(
        self,
        domain: Optional[BusinessDomain] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> List[KPIDefinition]:
        items = list(self._kpis.values())

        if domain is not None:
            items = [item for item in items if item.domain == domain]

        if tags:
            items = [
                item for item in items
                if all(item.tags.get(k) == v for k, v in tags.items())
            ]

        return items


# =============================================================================
# Calculators
# =============================================================================

class BusinessKPICalculator:
    @staticmethod
    def calculate(
        kpi: KPIDefinition,
        rows: List[Dict[str, Any]],
    ) -> Optional[float]:
        if not rows:
            return 0.0

        if kpi.aggregation == "count":
            return float(len(rows))

        values = [
            BusinessKPICalculator._to_float(row.get(kpi.measure))
            for row in rows
            if row.get(kpi.measure) is not None
        ]

        values = [value for value in values if value is not None]

        if not values:
            return 0.0

        if kpi.aggregation == "sum":
            return float(sum(values))

        if kpi.aggregation == "avg":
            return float(sum(values) / len(values))

        if kpi.aggregation == "min":
            return float(min(values))

        if kpi.aggregation == "max":
            return float(max(values))

        if kpi.aggregation == "median":
            return float(statistics.median(values))

        if kpi.aggregation == "distinct_count":
            return float(len(set(values)))

        raise KPIValidationError(f"Agregação não suportada: {kpi.aggregation}")

    @staticmethod
    def compare(
        current: Optional[float],
        previous: Optional[float],
    ) -> Tuple[Optional[float], Optional[float], TrendDirection]:
        if current is None or previous is None:
            return None, None, TrendDirection.UNKNOWN

        delta_absolute = current - previous

        if previous == 0:
            delta_percentage = None
        else:
            delta_percentage = (delta_absolute / abs(previous)) * 100

        if math.isclose(delta_absolute, 0.0, abs_tol=1e-9):
            trend = TrendDirection.FLAT
        elif delta_absolute > 0:
            trend = TrendDirection.UP
        else:
            trend = TrendDirection.DOWN

        return delta_absolute, delta_percentage, trend

    @staticmethod
    def target_achievement(
        value: Optional[float],
        target: Optional[float],
        higher_is_better: bool,
    ) -> Optional[float]:
        if value is None or target is None or target == 0:
            return None

        if higher_is_better:
            return (value / target) * 100

        return (target / value) * 100 if value != 0 else None

    @staticmethod
    def status_from_achievement(
        achievement: Optional[float],
        higher_is_better: bool = True,
    ) -> PerformanceStatus:
        if achievement is None:
            return PerformanceStatus.UNKNOWN

        if achievement >= 110:
            return PerformanceStatus.EXCELLENT

        if achievement >= 95:
            return PerformanceStatus.GOOD

        if achievement >= 75:
            return PerformanceStatus.WARNING

        return PerformanceStatus.CRITICAL

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            return float(value)
        except Exception:
            return None


# =============================================================================
# Engine
# =============================================================================

class BusinessAnalyticsEngine:
    def __init__(
        self,
        kpi_repository: KPIRepository,
        data_provider: Optional[BusinessDataProvider] = None,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
    ) -> None:
        self.kpi_repository = kpi_repository
        self.data_provider = data_provider or InMemoryBusinessDataProvider()
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()

    def compute_kpi(
        self,
        kpi_id: str,
        period: BusinessPeriod,
        context: Optional[BusinessAnalyticsContext] = None,
        comparison_period: Optional[BusinessPeriod] = None,
    ) -> KPIResult:
        context = context or BusinessAnalyticsContext()
        period.validate()

        if comparison_period:
            comparison_period.validate()

        kpi = self.kpi_repository.get(kpi_id)

        current_rows = self.data_provider.fetch(
            dataset=kpi.dataset,
            filters={
                **kpi.filters,
                "timestamp": (period.start, period.end),
            },
            context=context,
        )

        current_value = BusinessKPICalculator.calculate(kpi, current_rows)

        previous_value: Optional[float] = None

        if comparison_period:
            previous_rows = self.data_provider.fetch(
                dataset=kpi.dataset,
                filters={
                    **kpi.filters,
                    "timestamp": (comparison_period.start, comparison_period.end),
                },
                context=context,
            )
            previous_value = BusinessKPICalculator.calculate(kpi, previous_rows)

        delta_absolute, delta_percentage, trend = BusinessKPICalculator.compare(
            current_value,
            previous_value,
        )

        achievement = BusinessKPICalculator.target_achievement(
            current_value,
            kpi.target,
            kpi.higher_is_better,
        )

        status = BusinessKPICalculator.status_from_achievement(
            achievement,
            kpi.higher_is_better,
        )

        result = KPIResult(
            kpi_id=kpi.kpi_id,
            name=kpi.name,
            value=current_value,
            target=kpi.target,
            target_achievement=achievement,
            status=status,
            trend=trend,
            delta_absolute=delta_absolute,
            delta_percentage=delta_percentage,
            unit=kpi.unit,
            period=period,
            comparison_period=comparison_period,
            metadata={
                "dataset": kpi.dataset,
                "aggregation": kpi.aggregation,
                "measure": kpi.measure,
                "domain": kpi.domain.value,
                "row_count": len(current_rows),
            },
        )

        self._audit(
            "business.kpi.computed",
            context,
            {
                "kpi_id": kpi.kpi_id,
                "value": current_value,
                "status": status.value,
            },
        )

        self.metrics_backend.increment(
            "business.kpi.computed.total",
            tags={
                "kpi_id": kpi.kpi_id,
                "domain": kpi.domain.value,
                "status": status.value,
            },
        )

        return result

    def compute_scorecard(
        self,
        name: str,
        domain: BusinessDomain,
        period: BusinessPeriod,
        context: Optional[BusinessAnalyticsContext] = None,
        comparison_period: Optional[BusinessPeriod] = None,
    ) -> BusinessScorecard:
        context = context or BusinessAnalyticsContext()

        kpis = self.kpi_repository.list_all(domain=domain)

        results = [
            self.compute_kpi(
                kpi.kpi_id,
                period=period,
                context=context,
                comparison_period=comparison_period,
            )
            for kpi in kpis
        ]

        health_score = self._calculate_health_score(results)
        status = self._status_from_health_score(health_score)
        insights = self._generate_insights(domain, results)

        scorecard = BusinessScorecard(
            scorecard_id=str(uuid.uuid4()),
            name=name,
            domain=domain,
            kpis=results,
            health_score=health_score,
            status=status,
            insights=insights,
            metadata={
                "kpi_count": len(results),
                "tenant_id": context.tenant_id,
            },
        )

        self._audit(
            "business.scorecard.generated",
            context,
            {
                "scorecard_id": scorecard.scorecard_id,
                "domain": domain.value,
                "health_score": health_score,
                "status": status.value,
            },
        )

        self.metrics_backend.gauge(
            "business.scorecard.health_score",
            health_score,
            tags={"domain": domain.value},
        )

        return scorecard

    def generate_report(
        self,
        title: str,
        domains: Iterable[BusinessDomain],
        period: BusinessPeriod,
        context: Optional[BusinessAnalyticsContext] = None,
        comparison_period: Optional[BusinessPeriod] = None,
    ) -> BusinessAnalyticsReport:
        context = context or BusinessAnalyticsContext()

        scorecards = [
            self.compute_scorecard(
                name=f"Scorecard {domain.value}",
                domain=domain,
                period=period,
                context=context,
                comparison_period=comparison_period,
            )
            for domain in domains
        ]

        executive_summary = self._build_executive_summary(scorecards)

        report = BusinessAnalyticsReport(
            report_id=str(uuid.uuid4()),
            title=title,
            context=context,
            period=period,
            comparison_period=comparison_period,
            scorecards=scorecards,
            executive_summary=executive_summary,
            metadata={
                "domains": [domain.value for domain in domains],
                "scorecard_count": len(scorecards),
            },
        )

        self._audit(
            "business.report.generated",
            context,
            {
                "report_id": report.report_id,
                "title": title,
                "domains": [domain.value for domain in domains],
            },
        )

        return report

    def export_report_json(self, report: BusinessAnalyticsReport) -> str:
        return json.dumps(
            self._report_to_dict(report),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    @staticmethod
    def _calculate_health_score(results: List[KPIResult]) -> float:
        if not results:
            return 0.0

        score_by_status = {
            PerformanceStatus.EXCELLENT: 100.0,
            PerformanceStatus.GOOD: 85.0,
            PerformanceStatus.WARNING: 60.0,
            PerformanceStatus.CRITICAL: 30.0,
            PerformanceStatus.UNKNOWN: 50.0,
        }

        values = [score_by_status[result.status] for result in results]
        return round(sum(values) / len(values), 2)

    @staticmethod
    def _status_from_health_score(score: float) -> PerformanceStatus:
        if score >= 90:
            return PerformanceStatus.EXCELLENT
        if score >= 75:
            return PerformanceStatus.GOOD
        if score >= 50:
            return PerformanceStatus.WARNING
        return PerformanceStatus.CRITICAL

    @staticmethod
    def _generate_insights(
        domain: BusinessDomain,
        results: List[KPIResult],
    ) -> List[BusinessInsight]:
        insights: List[BusinessInsight] = []

        for result in results:
            if result.status == PerformanceStatus.EXCELLENT:
                insights.append(
                    BusinessInsight(
                        insight_id=str(uuid.uuid4()),
                        title=f"{result.name} acima da meta",
                        message=(
                            f"O KPI {result.name} está performando acima do esperado "
                            f"com atingimento de {result.target_achievement:.2f}%."
                            if result.target_achievement is not None
                            else f"O KPI {result.name} está com excelente desempenho."
                        ),
                        severity=InsightSeverity.POSITIVE,
                        domain=domain,
                        kpi_id=result.kpi_id,
                        value=result.value,
                        recommendation="Avaliar fatores de sucesso e replicar boas práticas.",
                    )
                )

            elif result.status == PerformanceStatus.CRITICAL:
                insights.append(
                    BusinessInsight(
                        insight_id=str(uuid.uuid4()),
                        title=f"{result.name} em situação crítica",
                        message=(
                            f"O KPI {result.name} está muito abaixo da meta."
                            if result.target is not None
                            else f"O KPI {result.name} apresenta desempenho crítico."
                        ),
                        severity=InsightSeverity.CRITICAL,
                        domain=domain,
                        kpi_id=result.kpi_id,
                        value=result.value,
                        recommendation="Priorizar análise de causa raiz e plano de ação imediato.",
                    )
                )

            elif result.status == PerformanceStatus.WARNING:
                insights.append(
                    BusinessInsight(
                        insight_id=str(uuid.uuid4()),
                        title=f"{result.name} requer atenção",
                        message=f"O KPI {result.name} está em zona de alerta.",
                        severity=InsightSeverity.WARNING,
                        domain=domain,
                        kpi_id=result.kpi_id,
                        value=result.value,
                        recommendation="Monitorar tendência e revisar alavancas operacionais.",
                    )
                )

            if result.delta_percentage is not None:
                if result.delta_percentage <= -20:
                    insights.append(
                        BusinessInsight(
                            insight_id=str(uuid.uuid4()),
                            title=f"Queda relevante em {result.name}",
                            message=(
                                f"{result.name} caiu {abs(result.delta_percentage):.2f}% "
                                f"em relação ao período comparativo."
                            ),
                            severity=InsightSeverity.WARNING,
                            domain=domain,
                            kpi_id=result.kpi_id,
                            value=result.value,
                            recommendation="Investigar sazonalidade, ruptura, preço, canal e mix.",
                        )
                    )

                elif result.delta_percentage >= 20:
                    insights.append(
                        BusinessInsight(
                            insight_id=str(uuid.uuid4()),
                            title=f"Crescimento relevante em {result.name}",
                            message=(
                                f"{result.name} cresceu {result.delta_percentage:.2f}% "
                                f"em relação ao período comparativo."
                            ),
                            severity=InsightSeverity.POSITIVE,
                            domain=domain,
                            kpi_id=result.kpi_id,
                            value=result.value,
                            recommendation="Mapear os drivers do crescimento.",
                        )
                    )

        if not insights:
            insights.append(
                BusinessInsight(
                    insight_id=str(uuid.uuid4()),
                    title="Operação estável",
                    message="Nenhum desvio crítico foi identificado no scorecard.",
                    severity=InsightSeverity.INFO,
                    domain=domain,
                    recommendation="Manter acompanhamento periódico.",
                )
            )

        return insights

    @staticmethod
    def _build_executive_summary(scorecards: List[BusinessScorecard]) -> str:
        if not scorecards:
            return "Nenhum scorecard foi gerado."

        avg_health = sum(item.health_score for item in scorecards) / len(scorecards)

        critical = [
            item for item in scorecards
            if item.status == PerformanceStatus.CRITICAL
        ]

        warning = [
            item for item in scorecards
            if item.status == PerformanceStatus.WARNING
        ]

        excellent = [
            item for item in scorecards
            if item.status == PerformanceStatus.EXCELLENT
        ]

        parts = [
            f"Health score médio do negócio: {avg_health:.2f}.",
            f"Scorecards excelentes: {len(excellent)}.",
            f"Scorecards em alerta: {len(warning)}.",
            f"Scorecards críticos: {len(critical)}.",
        ]

        if critical:
            parts.append(
                "Atenção imediata recomendada para: "
                + ", ".join(item.domain.value for item in critical)
                + "."
            )

        return " ".join(parts)

    def _audit(
        self,
        event_type: str,
        context: BusinessAnalyticsContext,
        details: Dict[str, Any],
    ) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "tenant_id": context.tenant_id,
                "domain": context.domain,
                "user_id": context.user_id,
                "correlation_id": context.correlation_id,
                "details": details,
            }
        )

    @staticmethod
    def _report_to_dict(report: BusinessAnalyticsReport) -> Dict[str, Any]:
        data = asdict(report)

        data["generated_at"] = report.generated_at.isoformat()
        data["period"]["start"] = report.period.start.isoformat()
        data["period"]["end"] = report.period.end.isoformat()

        if report.comparison_period:
            data["comparison_period"]["start"] = report.comparison_period.start.isoformat()
            data["comparison_period"]["end"] = report.comparison_period.end.isoformat()

        for scorecard_index, scorecard in enumerate(report.scorecards):
            data["scorecards"][scorecard_index]["domain"] = scorecard.domain.value
            data["scorecards"][scorecard_index]["status"] = scorecard.status.value
            data["scorecards"][scorecard_index]["generated_at"] = scorecard.generated_at.isoformat()

            for kpi_index, kpi in enumerate(scorecard.kpis):
                serialized_kpi = data["scorecards"][scorecard_index]["kpis"][kpi_index]
                serialized_kpi["status"] = kpi.status.value
                serialized_kpi["trend"] = kpi.trend.value

                if kpi.period:
                    serialized_kpi["period"]["start"] = kpi.period.start.isoformat()
                    serialized_kpi["period"]["end"] = kpi.period.end.isoformat()

                if kpi.comparison_period:
                    serialized_kpi["comparison_period"]["start"] = kpi.comparison_period.start.isoformat()
                    serialized_kpi["comparison_period"]["end"] = kpi.comparison_period.end.isoformat()

            for insight_index, insight in enumerate(scorecard.insights):
                data["scorecards"][scorecard_index]["insights"][insight_index]["severity"] = insight.severity.value
                data["scorecards"][scorecard_index]["insights"][insight_index]["domain"] = insight.domain.value

        return data


# =============================================================================
# Default Catalog
# =============================================================================

def build_default_business_kpis() -> List[KPIDefinition]:
    return [
        KPIDefinition(
            kpi_id="gross_revenue",
            name="Receita Bruta",
            kpi_type=KPIType.REVENUE,
            domain=BusinessDomain.SALES,
            dataset="sales_orders",
            measure="gross_amount",
            aggregation="sum",
            target=100000.0,
            higher_is_better=True,
            unit="BRL",
            tags={"executive": "true", "sales": "true"},
        ),
        KPIDefinition(
            kpi_id="net_revenue",
            name="Receita Líquida",
            kpi_type=KPIType.REVENUE,
            domain=BusinessDomain.FINANCE,
            dataset="sales_orders",
            measure="net_amount",
            aggregation="sum",
            target=85000.0,
            higher_is_better=True,
            unit="BRL",
            tags={"executive": "true", "finance": "true"},
        ),
        KPIDefinition(
            kpi_id="orders_count",
            name="Quantidade de Pedidos",
            kpi_type=KPIType.ORDERS,
            domain=BusinessDomain.SALES,
            dataset="sales_orders",
            measure="order_id",
            aggregation="count",
            target=1000.0,
            higher_is_better=True,
            unit="orders",
            tags={"sales": "true"},
        ),
        KPIDefinition(
            kpi_id="gross_margin",
            name="Margem Bruta",
            kpi_type=KPIType.MARGIN,
            domain=BusinessDomain.FINANCE,
            dataset="sales_orders",
            measure="gross_margin",
            aggregation="avg",
            target=0.32,
            higher_is_better=True,
            unit="ratio",
            tags={"finance": "true"},
        ),
        KPIDefinition(
            kpi_id="customer_count",
            name="Clientes Ativos",
            kpi_type=KPIType.CUSTOMERS,
            domain=BusinessDomain.CUSTOMER,
            dataset="customers",
            measure="customer_id",
            aggregation="count",
            target=5000.0,
            higher_is_better=True,
            unit="customers",
            tags={"customer": "true"},
        ),
        KPIDefinition(
            kpi_id="marketing_spend",
            name="Investimento em Marketing",
            kpi_type=KPIType.COST,
            domain=BusinessDomain.MARKETING,
            dataset="marketing_campaigns",
            measure="spend",
            aggregation="sum",
            target=25000.0,
            higher_is_better=False,
            unit="BRL",
            tags={"marketing": "true"},
        ),
    ]


def create_default_business_analytics_engine(
    datasets: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> BusinessAnalyticsEngine:
    return BusinessAnalyticsEngine(
        kpi_repository=KPIRepository(build_default_business_kpis()),
        data_provider=InMemoryBusinessDataProvider(datasets or {}),
    )


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    from datetime import timedelta

    now = datetime.now(timezone.utc)

    datasets = {
        "sales_orders": [
            {
                "order_id": "o1",
                "tenant_id": "tenant-default",
                "timestamp": now - timedelta(days=2),
                "gross_amount": 300.0,
                "net_amount": 260.0,
                "gross_margin": 0.34,
            },
            {
                "order_id": "o2",
                "tenant_id": "tenant-default",
                "timestamp": now - timedelta(days=1),
                "gross_amount": 500.0,
                "net_amount": 430.0,
                "gross_margin": 0.30,
            },
            {
                "order_id": "o3",
                "tenant_id": "tenant-default",
                "timestamp": now,
                "gross_amount": 700.0,
                "net_amount": 620.0,
                "gross_margin": 0.37,
            },
        ],
        "customers": [
            {
                "customer_id": "c1",
                "tenant_id": "tenant-default",
                "timestamp": now - timedelta(days=1),
            },
            {
                "customer_id": "c2",
                "tenant_id": "tenant-default",
                "timestamp": now,
            },
        ],
        "marketing_campaigns": [
            {
                "campaign_id": "m1",
                "tenant_id": "tenant-default",
                "timestamp": now - timedelta(days=1),
                "spend": 1000.0,
            }
        ],
    }

    engine = create_default_business_analytics_engine(datasets)

    context = BusinessAnalyticsContext(
        tenant_id="tenant-default",
        domain="executive",
        user_id="business-admin",
        correlation_id="corr-business-001",
    )

    period = BusinessPeriod(
        start=now - timedelta(days=7),
        end=now + timedelta(seconds=1),
        label="Últimos 7 dias",
    )

    previous = BusinessPeriod(
        start=now - timedelta(days=14),
        end=now - timedelta(days=7),
        label="7 dias anteriores",
    )

    report = engine.generate_report(
        title="Relatório Executivo de Negócio",
        domains=[
            BusinessDomain.SALES,
            BusinessDomain.FINANCE,
            BusinessDomain.CUSTOMER,
            BusinessDomain.MARKETING,
        ],
        period=period,
        comparison_period=previous,
        context=context,
    )

    print(engine.export_report_json(report))


if __name__ == "__main__":
    example_usage()
"""
data.analytics

Enterprise Analytics Package.

Este pacote centraliza componentes analíticos para uma arquitetura enterprise:

- Métricas analíticas
- Agregações
- Segmentações
- Cohort analysis
- Forecasting
- Anomaly analytics
- KPI engine
- OLAP helpers
- Experiment analytics
- Feature analytics
- Model analytics
- Auditabilidade
- Versionamento
- Observabilidade
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


__version__ = "1.0.0"
__package_name__ = "data.analytics"


class AnalyticsLayer(str, Enum):
    RAW = "raw"
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    MART = "mart"
    SEMANTIC = "semantic"


class MetricType(str, Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    RATIO = "ratio"
    PERCENTAGE = "percentage"
    CURRENCY = "currency"
    DURATION = "duration"
    SCORE = "score"


class AggregationType(str, Enum):
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    MEDIAN = "median"
    PERCENTILE = "percentile"
    WEIGHTED_AVG = "weighted_avg"


class AnalyticsStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class AnalyticsMetadata:
    name: str
    owner: str
    domain: str
    description: str = ""
    version: str = "1.0.0"
    layer: AnalyticsLayer = AnalyticsLayer.GOLD
    status: AnalyticsStatus = AnalyticsStatus.ACTIVE
    tags: Dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    name: str
    metric_type: MetricType
    aggregation: AggregationType
    source: str
    expression: Optional[str] = None
    numerator: Optional[str] = None
    denominator: Optional[str] = None
    dimensions: List[str] = field(default_factory=list)
    filters: Dict[str, Any] = field(default_factory=dict)
    metadata: Optional[AnalyticsMetadata] = None


@dataclass(frozen=True)
class DimensionDefinition:
    dimension_id: str
    name: str
    source_field: str
    data_type: str
    description: str = ""
    hierarchy: List[str] = field(default_factory=list)
    allowed_values: Optional[List[Any]] = None
    metadata: Optional[AnalyticsMetadata] = None


@dataclass(frozen=True)
class AnalyticsExecutionContext:
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    correlation_id: Optional[str] = None
    triggered_by: Optional[str] = None
    execution_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalyticsResult:
    name: str
    value: Any
    metric_id: Optional[str] = None
    dimensions: Dict[str, Any] = field(default_factory=dict)
    context: Optional[AnalyticsExecutionContext] = None
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


class AnalyticsError(Exception):
    """Erro base do pacote de analytics."""


class MetricDefinitionError(AnalyticsError):
    """Erro em definição de métrica."""


class DimensionDefinitionError(AnalyticsError):
    """Erro em definição de dimensão."""


class AnalyticsExecutionError(AnalyticsError):
    """Erro durante execução analítica."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def package_info() -> Dict[str, Any]:
    return {
        "package": __package_name__,
        "version": __version__,
        "description": "Enterprise analytics foundation package",
        "generated_at": utc_now().isoformat(),
        "layers": [layer.value for layer in AnalyticsLayer],
        "metric_types": [item.value for item in MetricType],
        "aggregation_types": [item.value for item in AggregationType],
    }


__all__ = [
    "__version__",
    "__package_name__",
    "AnalyticsLayer",
    "MetricType",
    "AggregationType",
    "AnalyticsStatus",
    "AnalyticsMetadata",
    "MetricDefinition",
    "DimensionDefinition",
    "AnalyticsExecutionContext",
    "AnalyticsResult",
    "AnalyticsError",
    "MetricDefinitionError",
    "DimensionDefinitionError",
    "AnalyticsExecutionError",
    "utc_now",
    "package_info",
]
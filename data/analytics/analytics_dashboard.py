"""
data/analytics/analytics_dashboard.py

Enterprise Analytics Dashboard Engine.

Recursos:
- Definição de dashboards, páginas, widgets e layouts
- KPIs, gráficos, tabelas, cards, funis e séries temporais
- Filtros globais e por widget
- Permissões por tenant, domínio, usuário e role
- Refresh agendado/manual
- Auditoria e métricas plugáveis
- Exportação para JSON
- Estrutura pronta para BI interno, portal analytics ou data apps
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class DashboardStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class WidgetType(str, Enum):
    KPI = "kpi"
    LINE_CHART = "line_chart"
    BAR_CHART = "bar_chart"
    PIE_CHART = "pie_chart"
    AREA_CHART = "area_chart"
    TABLE = "table"
    HEATMAP = "heatmap"
    FUNNEL = "funnel"
    SCATTER = "scatter"
    GAUGE = "gauge"
    TEXT = "text"
    CUSTOM = "custom"


class RefreshStrategy(str, Enum):
    MANUAL = "manual"
    INTERVAL = "interval"
    ON_OPEN = "on_open"
    EVENT_DRIVEN = "event_driven"


class FilterOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    IN = "in"
    NOT_IN = "not_in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    BETWEEN = "between"
    CONTAINS = "contains"


class AccessLevel(str, Enum):
    VIEWER = "viewer"
    EDITOR = "editor"
    OWNER = "owner"
    ADMIN = "admin"


# =============================================================================
# Exceptions
# =============================================================================

class DashboardError(Exception):
    """Erro base de dashboard."""


class DashboardNotFound(DashboardError):
    """Dashboard não encontrado."""


class WidgetNotFound(DashboardError):
    """Widget não encontrado."""


class DashboardAccessDenied(DashboardError):
    """Acesso negado ao dashboard."""


class DashboardValidationError(DashboardError):
    """Erro de validação do dashboard."""


class DashboardRenderError(DashboardError):
    """Erro ao renderizar dashboard."""


# =============================================================================
# Protocols
# =============================================================================

class AnalyticsQueryExecutor(Protocol):
    def execute(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        context: Optional["DashboardExecutionContext"] = None,
    ) -> Any:
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

    def timing(
        self,
        metric_name: str,
        value_ms: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        ...


# =============================================================================
# Default Backends
# =============================================================================

class LoggingAuditBackend:
    def write_event(self, event: Dict[str, Any]) -> None:
        logger.info("dashboard_audit=%s", json.dumps(event, ensure_ascii=False, default=str))


class LoggingMetricsBackend:
    def increment(
        self,
        metric_name: str,
        value: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("metric=%s value=%s tags=%s", metric_name, value, tags or {})

    def timing(
        self,
        metric_name: str,
        value_ms: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("timing=%s value_ms=%s tags=%s", metric_name, value_ms, tags or {})


class InMemoryQueryExecutor:
    """
    Executor simples para testes.

    Em produção, substitua por executor SQL, Spark, Trino, BigQuery,
    Snowflake, DuckDB, ElasticSearch ou API interna.
    """

    def __init__(self, datasets: Optional[Dict[str, Any]] = None) -> None:
        self.datasets = datasets or {}

    def execute(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        context: Optional["DashboardExecutionContext"] = None,
    ) -> Any:
        return {
            "query": query,
            "parameters": parameters or {},
            "tenant_id": context.tenant_id if context else None,
            "data": self.datasets.get(query, []),
        }


# =============================================================================
# Models
# =============================================================================

@dataclass(frozen=True)
class DashboardExecutionContext:
    user_id: str
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    roles: List[str] = field(default_factory=list)
    locale: str = "pt-BR"
    timezone: str = "UTC"
    correlation_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DashboardPermission:
    subject: str
    access_level: AccessLevel
    subject_type: str = "user"
    tenant_id: Optional[str] = None
    domain: Optional[str] = None


@dataclass(frozen=True)
class DashboardFilter:
    filter_id: str
    field_name: str
    operator: FilterOperator
    value: Any
    label: Optional[str] = None
    required: bool = False
    widget_scope: Optional[List[str]] = None

    def to_query_parameter(self) -> Dict[str, Any]:
        return {
            self.filter_id: {
                "field": self.field_name,
                "operator": self.operator.value,
                "value": self.value,
            }
        }


@dataclass(frozen=True)
class WidgetLayout:
    x: int
    y: int
    width: int
    height: int
    min_width: int = 1
    min_height: int = 1

    def validate(self) -> None:
        if self.x < 0 or self.y < 0:
            raise DashboardValidationError("x/y do layout não podem ser negativos")

        if self.width < self.min_width:
            raise DashboardValidationError("width menor que min_width")

        if self.height < self.min_height:
            raise DashboardValidationError("height menor que min_height")


@dataclass(frozen=True)
class WidgetDefinition:
    widget_id: str
    title: str
    widget_type: WidgetType
    query: Optional[str] = None
    metric_id: Optional[str] = None
    description: str = ""
    layout: WidgetLayout = field(default_factory=lambda: WidgetLayout(0, 0, 4, 3))
    filters: List[DashboardFilter] = field(default_factory=list)
    visualization_config: Dict[str, Any] = field(default_factory=dict)
    refresh_strategy: RefreshStrategy = RefreshStrategy.MANUAL
    refresh_interval_seconds: Optional[int] = None
    cache_ttl_seconds: Optional[int] = 300
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.widget_id:
            raise DashboardValidationError("widget_id é obrigatório")

        if not self.title:
            raise DashboardValidationError("title é obrigatório")

        if self.widget_type != WidgetType.TEXT and not self.query and not self.metric_id:
            raise DashboardValidationError(
                f"Widget {self.widget_id} precisa de query ou metric_id"
            )

        self.layout.validate()


@dataclass(frozen=True)
class DashboardPage:
    page_id: str
    title: str
    widgets: List[WidgetDefinition] = field(default_factory=list)
    order: int = 0
    description: str = ""

    def validate(self) -> None:
        if not self.page_id:
            raise DashboardValidationError("page_id é obrigatório")

        ids = set()
        for widget in self.widgets:
            widget.validate()
            if widget.widget_id in ids:
                raise DashboardValidationError(f"widget_id duplicado: {widget.widget_id}")
            ids.add(widget.widget_id)


@dataclass(frozen=True)
class DashboardDefinition:
    dashboard_id: str
    title: str
    owner: str
    domain: str
    pages: List[DashboardPage]
    description: str = ""
    status: DashboardStatus = DashboardStatus.ACTIVE
    version: str = "1.0.0"
    tenant_id: Optional[str] = None
    tags: Dict[str, str] = field(default_factory=dict)
    filters: List[DashboardFilter] = field(default_factory=list)
    permissions: List[DashboardPermission] = field(default_factory=list)
    refresh_strategy: RefreshStrategy = RefreshStrategy.ON_OPEN
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.dashboard_id:
            raise DashboardValidationError("dashboard_id é obrigatório")

        if not self.title:
            raise DashboardValidationError("title é obrigatório")

        if not self.pages:
            raise DashboardValidationError("Dashboard precisa ter pelo menos uma página")

        page_ids = set()
        for page in self.pages:
            page.validate()
            if page.page_id in page_ids:
                raise DashboardValidationError(f"page_id duplicado: {page.page_id}")
            page_ids.add(page.page_id)


@dataclass
class WidgetRenderResult:
    widget_id: str
    title: str
    widget_type: WidgetType
    data: Any
    rendered_at: datetime
    status: str = "success"
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DashboardRenderResult:
    dashboard_id: str
    title: str
    rendered_at: datetime
    pages: Dict[str, List[WidgetRenderResult]]
    status: str = "success"
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Repository
# =============================================================================

class DashboardRepository:
    def __init__(self, dashboards: Optional[List[DashboardDefinition]] = None) -> None:
        self._dashboards: Dict[str, DashboardDefinition] = {}

        for dashboard in dashboards or []:
            self.save(dashboard)

    def save(self, dashboard: DashboardDefinition) -> None:
        dashboard.validate()
        self._dashboards[dashboard.dashboard_id] = dashboard

    def get(self, dashboard_id: str) -> DashboardDefinition:
        dashboard = self._dashboards.get(dashboard_id)
        if not dashboard:
            raise DashboardNotFound(dashboard_id)
        return dashboard

    def list_all(
        self,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
        status: Optional[DashboardStatus] = None,
    ) -> List[DashboardDefinition]:
        items = list(self._dashboards.values())

        if tenant_id is not None:
            items = [d for d in items if d.tenant_id == tenant_id]

        if domain is not None:
            items = [d for d in items if d.domain == domain]

        if status is not None:
            items = [d for d in items if d.status == status]

        return items

    def delete(self, dashboard_id: str) -> None:
        if dashboard_id not in self._dashboards:
            raise DashboardNotFound(dashboard_id)
        del self._dashboards[dashboard_id]


# =============================================================================
# Access Control
# =============================================================================

class DashboardAccessController:
    def can_view(
        self,
        dashboard: DashboardDefinition,
        context: DashboardExecutionContext,
    ) -> bool:
        return self._has_access(
            dashboard,
            context,
            allowed={
                AccessLevel.VIEWER,
                AccessLevel.EDITOR,
                AccessLevel.OWNER,
                AccessLevel.ADMIN,
            },
        )

    def can_edit(
        self,
        dashboard: DashboardDefinition,
        context: DashboardExecutionContext,
    ) -> bool:
        return self._has_access(
            dashboard,
            context,
            allowed={
                AccessLevel.EDITOR,
                AccessLevel.OWNER,
                AccessLevel.ADMIN,
            },
        )

    def _has_access(
        self,
        dashboard: DashboardDefinition,
        context: DashboardExecutionContext,
        allowed: set[AccessLevel],
    ) -> bool:
        if "admin" in context.roles:
            return True

        if dashboard.owner == context.user_id:
            return True

        if dashboard.tenant_id and dashboard.tenant_id != context.tenant_id:
            return False

        for permission in dashboard.permissions:
            if permission.tenant_id and permission.tenant_id != context.tenant_id:
                continue

            if permission.domain and permission.domain != context.domain:
                continue

            if permission.access_level not in allowed:
                continue

            if permission.subject_type == "user" and permission.subject == context.user_id:
                return True

            if permission.subject_type == "role" and permission.subject in context.roles:
                return True

            if permission.subject_type == "tenant" and permission.subject == context.tenant_id:
                return True

        return False


# =============================================================================
# Cache
# =============================================================================

@dataclass
class CacheEntry:
    value: Any
    expires_at: datetime


class DashboardCache:
    def __init__(self) -> None:
        self._items: Dict[str, CacheEntry] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._items.get(key)
        if not entry:
            return None

        if datetime.now(timezone.utc) > entry.expires_at:
            self._items.pop(key, None)
            return None

        return entry.value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._items[key] = CacheEntry(
            value=value,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        )

    def clear(self) -> None:
        self._items.clear()


# =============================================================================
# Dashboard Engine
# =============================================================================

class AnalyticsDashboardEngine:
    def __init__(
        self,
        repository: DashboardRepository,
        query_executor: Optional[AnalyticsQueryExecutor] = None,
        access_controller: Optional[DashboardAccessController] = None,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
        cache: Optional[DashboardCache] = None,
    ) -> None:
        self.repository = repository
        self.query_executor = query_executor or InMemoryQueryExecutor()
        self.access_controller = access_controller or DashboardAccessController()
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()
        self.cache = cache or DashboardCache()

    def render_dashboard(
        self,
        dashboard_id: str,
        context: DashboardExecutionContext,
        filters: Optional[List[DashboardFilter]] = None,
        use_cache: bool = True,
    ) -> DashboardRenderResult:
        started_at = datetime.now(timezone.utc)
        dashboard = self.repository.get(dashboard_id)

        if not self.access_controller.can_view(dashboard, context):
            self._audit(
                "dashboard.access_denied",
                dashboard,
                context,
                {"reason": "view_denied"},
            )
            raise DashboardAccessDenied(dashboard_id)

        pages_result: Dict[str, List[WidgetRenderResult]] = {}
        errors: List[str] = []

        active_filters = list(dashboard.filters) + list(filters or [])

        for page in sorted(dashboard.pages, key=lambda p: p.order):
            page_widgets: List[WidgetRenderResult] = []

            for widget in page.widgets:
                if not widget.enabled:
                    continue

                try:
                    page_widgets.append(
                        self.render_widget(
                            dashboard=dashboard,
                            widget=widget,
                            context=context,
                            filters=active_filters + widget.filters,
                            use_cache=use_cache,
                        )
                    )
                except Exception as exc:
                    logger.exception("Erro ao renderizar widget %s", widget.widget_id)
                    errors.append(f"{widget.widget_id}: {exc}")
                    page_widgets.append(
                        WidgetRenderResult(
                            widget_id=widget.widget_id,
                            title=widget.title,
                            widget_type=widget.widget_type,
                            data=None,
                            rendered_at=datetime.now(timezone.utc),
                            status="error",
                            error=str(exc),
                        )
                    )

            pages_result[page.page_id] = page_widgets

        elapsed_ms = (datetime.now(timezone.utc) - started_at).total_seconds() * 1000

        self.metrics_backend.timing(
            "analytics.dashboard.render.duration_ms",
            elapsed_ms,
            tags={
                "dashboard_id": dashboard.dashboard_id,
                "tenant_id": context.tenant_id or "-",
            },
        )

        self._audit(
            "dashboard.rendered",
            dashboard,
            context,
            {
                "duration_ms": elapsed_ms,
                "errors": errors,
            },
        )

        return DashboardRenderResult(
            dashboard_id=dashboard.dashboard_id,
            title=dashboard.title,
            rendered_at=datetime.now(timezone.utc),
            pages=pages_result,
            status="partial" if errors else "success",
            errors=errors,
            metadata={
                "version": dashboard.version,
                "tenant_id": dashboard.tenant_id,
                "domain": dashboard.domain,
                "duration_ms": elapsed_ms,
            },
        )

    def render_widget(
        self,
        dashboard: DashboardDefinition,
        widget: WidgetDefinition,
        context: DashboardExecutionContext,
        filters: Optional[List[DashboardFilter]] = None,
        use_cache: bool = True,
    ) -> WidgetRenderResult:
        started_at = datetime.now(timezone.utc)

        cache_key = self._cache_key(
            dashboard.dashboard_id,
            widget.widget_id,
            context,
            filters or [],
        )

        if use_cache and widget.cache_ttl_seconds:
            cached = self.cache.get(cache_key)
            if cached is not None:
                self.metrics_backend.increment(
                    "analytics.dashboard.widget.cache_hit",
                    tags={"widget_id": widget.widget_id},
                )
                return cached

        parameters = self._build_query_parameters(context, filters or [])

        if widget.widget_type == WidgetType.TEXT:
            data = {
                "text": widget.visualization_config.get("text", widget.description)
            }
        else:
            query = widget.query or f"metric:{widget.metric_id}"
            data = self.query_executor.execute(
                query=query,
                parameters=parameters,
                context=context,
            )

        result = WidgetRenderResult(
            widget_id=widget.widget_id,
            title=widget.title,
            widget_type=widget.widget_type,
            data=data,
            rendered_at=datetime.now(timezone.utc),
            metadata={
                "layout": asdict(widget.layout),
                "visualization_config": widget.visualization_config,
                "duration_ms": (
                    datetime.now(timezone.utc) - started_at
                ).total_seconds() * 1000,
            },
        )

        if use_cache and widget.cache_ttl_seconds:
            self.cache.set(cache_key, result, widget.cache_ttl_seconds)

        self.metrics_backend.increment(
            "analytics.dashboard.widget.rendered",
            tags={
                "dashboard_id": dashboard.dashboard_id,
                "widget_id": widget.widget_id,
                "widget_type": widget.widget_type.value,
            },
        )

        return result

    def list_dashboards_for_user(
        self,
        context: DashboardExecutionContext,
        domain: Optional[str] = None,
    ) -> List[DashboardDefinition]:
        dashboards = self.repository.list_all(
            tenant_id=context.tenant_id,
            domain=domain,
            status=DashboardStatus.ACTIVE,
        )

        return [
            dashboard
            for dashboard in dashboards
            if self.access_controller.can_view(dashboard, context)
        ]

    def export_dashboard_definition(self, dashboard_id: str) -> str:
        dashboard = self.repository.get(dashboard_id)
        return json.dumps(
            self._dashboard_to_dict(dashboard),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    @staticmethod
    def _build_query_parameters(
        context: DashboardExecutionContext,
        filters: List[DashboardFilter],
    ) -> Dict[str, Any]:
        parameters: Dict[str, Any] = dict(context.parameters)

        for dashboard_filter in filters:
            parameters.update(dashboard_filter.to_query_parameter())

        return parameters

    @staticmethod
    def _cache_key(
        dashboard_id: str,
        widget_id: str,
        context: DashboardExecutionContext,
        filters: List[DashboardFilter],
    ) -> str:
        raw = json.dumps(
            {
                "dashboard_id": dashboard_id,
                "widget_id": widget_id,
                "tenant_id": context.tenant_id,
                "user_id": context.user_id,
                "parameters": context.parameters,
                "filters": [asdict(f) for f in filters],
            },
            sort_keys=True,
            default=str,
        )
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, raw))

    def _audit(
        self,
        event_type: str,
        dashboard: DashboardDefinition,
        context: DashboardExecutionContext,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "dashboard_id": dashboard.dashboard_id,
                "dashboard_title": dashboard.title,
                "tenant_id": context.tenant_id,
                "domain": dashboard.domain,
                "user_id": context.user_id,
                "correlation_id": context.correlation_id,
                "details": details or {},
            }
        )

    @staticmethod
    def _dashboard_to_dict(dashboard: DashboardDefinition) -> Dict[str, Any]:
        data = asdict(dashboard)
        data["status"] = dashboard.status.value
        data["refresh_strategy"] = dashboard.refresh_strategy.value
        data["created_at"] = dashboard.created_at.isoformat()
        data["updated_at"] = dashboard.updated_at.isoformat() if dashboard.updated_at else None

        for permission in data["permissions"]:
            permission["access_level"] = permission["access_level"].value

        for dashboard_filter in data["filters"]:
            dashboard_filter["operator"] = dashboard_filter["operator"].value

        for page in data["pages"]:
            for widget in page["widgets"]:
                widget["widget_type"] = widget["widget_type"].value
                widget["refresh_strategy"] = widget["refresh_strategy"].value
                for widget_filter in widget["filters"]:
                    widget_filter["operator"] = widget_filter["operator"].value

        return data


# =============================================================================
# Factory
# =============================================================================

def create_dashboard(
    title: str,
    owner: str,
    domain: str,
    widgets: List[WidgetDefinition],
    tenant_id: Optional[str] = None,
    dashboard_id: Optional[str] = None,
    description: str = "",
) -> DashboardDefinition:
    return DashboardDefinition(
        dashboard_id=dashboard_id or str(uuid.uuid4()),
        title=title,
        owner=owner,
        domain=domain,
        tenant_id=tenant_id,
        description=description,
        pages=[
            DashboardPage(
                page_id="main",
                title="Principal",
                widgets=widgets,
                order=0,
            )
        ],
        permissions=[
            DashboardPermission(
                subject=owner,
                subject_type="user",
                access_level=AccessLevel.OWNER,
                tenant_id=tenant_id,
            )
        ],
    )


def build_default_sales_dashboard() -> DashboardDefinition:
    widgets = [
        WidgetDefinition(
            widget_id="gross_revenue",
            title="Receita Bruta",
            widget_type=WidgetType.KPI,
            query="sales.gross_revenue",
            layout=WidgetLayout(x=0, y=0, width=3, height=2),
            visualization_config={
                "format": "currency",
                "currency": "BRL",
                "show_delta": True,
            },
        ),
        WidgetDefinition(
            widget_id="orders_count",
            title="Pedidos",
            widget_type=WidgetType.KPI,
            query="sales.orders_count",
            layout=WidgetLayout(x=3, y=0, width=3, height=2),
            visualization_config={
                "format": "number",
                "show_delta": True,
            },
        ),
        WidgetDefinition(
            widget_id="revenue_by_day",
            title="Receita por Dia",
            widget_type=WidgetType.LINE_CHART,
            query="sales.revenue_by_day",
            layout=WidgetLayout(x=0, y=2, width=6, height=4),
            visualization_config={
                "x_axis": "date",
                "y_axis": "revenue",
                "series": ["revenue"],
            },
        ),
        WidgetDefinition(
            widget_id="top_products",
            title="Top Produtos",
            widget_type=WidgetType.TABLE,
            query="sales.top_products",
            layout=WidgetLayout(x=6, y=2, width=6, height=4),
            visualization_config={
                "columns": ["product_name", "quantity", "revenue"],
                "page_size": 10,
            },
        ),
    ]

    return create_dashboard(
        dashboard_id="sales-executive-dashboard",
        title="Dashboard Executivo de Vendas",
        owner="analytics-admin",
        domain="sales",
        tenant_id="tenant-default",
        description="Visão executiva de vendas, receita, pedidos e produtos.",
        widgets=widgets,
    )


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    dashboard = build_default_sales_dashboard()

    repository = DashboardRepository([dashboard])

    executor = InMemoryQueryExecutor(
        datasets={
            "sales.gross_revenue": [{"value": 158900.75, "delta": 0.12}],
            "sales.orders_count": [{"value": 1240, "delta": 0.08}],
            "sales.revenue_by_day": [
                {"date": "2026-05-01", "revenue": 12000},
                {"date": "2026-05-02", "revenue": 14200},
                {"date": "2026-05-03", "revenue": 13750},
            ],
            "sales.top_products": [
                {"product_name": "Arroz 5kg", "quantity": 300, "revenue": 7500},
                {"product_name": "Feijão 1kg", "quantity": 250, "revenue": 3200},
            ],
        }
    )

    engine = AnalyticsDashboardEngine(
        repository=repository,
        query_executor=executor,
    )

    context = DashboardExecutionContext(
        user_id="analytics-admin",
        tenant_id="tenant-default",
        domain="sales",
        roles=["admin"],
        correlation_id="corr-dashboard-001",
    )

    result = engine.render_dashboard(
        dashboard_id="sales-executive-dashboard",
        context=context,
        filters=[
            DashboardFilter(
                filter_id="period",
                field_name="date",
                operator=FilterOperator.BETWEEN,
                value=["2026-05-01", "2026-05-31"],
                label="Período",
            )
        ],
    )

    print(json.dumps(asdict(result), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    example_usage()
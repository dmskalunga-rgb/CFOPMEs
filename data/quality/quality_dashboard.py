"""
data/quality/quality_dashboard.py

Enterprise-grade Data Quality Dashboard module.

This module consolidates quality outputs from accuracy, completeness,
consistency, freshness, null analysis, profiling, validation, audit, and
custom checks into executive, operational, and technical dashboard views.

Main capabilities:
- Unified KPI model for data quality dashboards
- Dataset/domain/pipeline level scorecards
- Severity and status aggregation
- Trend snapshots and historical comparison
- SLA and policy violation summaries
- Finding and recommendation consolidation
- JSON and standalone HTML export
- Pluggable metrics/audit sinks
- Lightweight dependency footprint
- Optional pandas support for tabular inputs

Designed for enterprise data platforms, lakehouse observability portals,
quality command centers, orchestration monitoring, governance dashboards,
and compliance evidence views.
"""

from __future__ import annotations

import html
import json
import logging
import math
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

try:
    import pandas as pd
except Exception:  # pragma: no cover - optional dependency isolation
    pd = None  # type: ignore


# =============================================================================
# Logging
# =============================================================================

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# =============================================================================
# Exceptions
# =============================================================================


class QualityDashboardError(Exception):
    """Base exception for quality dashboard failures."""


class QualityDashboardConfigurationError(QualityDashboardError):
    """Raised when dashboard configuration is invalid."""


class QualityDashboardExecutionError(QualityDashboardError):
    """Raised when dashboard generation fails."""


# =============================================================================
# Enums
# =============================================================================


class DashboardStatus(str, Enum):
    """Overall dashboard health status."""

    HEALTHY = "healthy"
    WARNING = "warning"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class QualityDimension(str, Enum):
    """Standard data quality dimensions."""

    ACCURACY = "accuracy"
    COMPLETENESS = "completeness"
    CONSISTENCY = "consistency"
    FRESHNESS = "freshness"
    VALIDITY = "validity"
    UNIQUENESS = "uniqueness"
    INTEGRITY = "integrity"
    NULL_ANALYSIS = "null_analysis"
    PROFILING = "profiling"
    GOVERNANCE = "governance"
    SECURITY = "security"
    CUSTOM = "custom"


class QualityStatus(str, Enum):
    """Normalized status for quality results."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    """Normalized severity."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TrendDirection(str, Enum):
    """Trend direction for quality indicators."""

    IMPROVING = "improving"
    STABLE = "stable"
    DEGRADING = "degrading"
    UNKNOWN = "unknown"


class WidgetType(str, Enum):
    """Dashboard widget categories."""

    KPI_CARD = "kpi_card"
    SCORECARD = "scorecard"
    STATUS_BREAKDOWN = "status_breakdown"
    SEVERITY_BREAKDOWN = "severity_breakdown"
    DIMENSION_HEALTH = "dimension_health"
    DATASET_RANKING = "dataset_ranking"
    FINDINGS_TABLE = "findings_table"
    RECOMMENDATIONS_TABLE = "recommendations_table"
    TREND = "trend"
    AUDIT_SUMMARY = "audit_summary"


# =============================================================================
# Protocols
# =============================================================================


class MetricsSink(Protocol):
    """Optional sink for publishing dashboard metrics."""

    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def timing(self, metric_name: str, value_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...


class AuditSink(Protocol):
    """Optional sink for dashboard audit events."""

    def write_event(self, event: Mapping[str, Any]) -> None:
        ...


# =============================================================================
# Config
# =============================================================================


@dataclass(frozen=True)
class DashboardThresholds:
    """Thresholds used to classify dashboard health."""

    healthy_score: float = 0.98
    warning_score: float = 0.95
    degraded_score: float = 0.85
    max_critical_findings_for_warning: int = 0
    max_failed_checks_for_warning: int = 0
    trend_significant_delta: float = 0.02

    def validate(self) -> None:
        for value in [self.healthy_score, self.warning_score, self.degraded_score, self.trend_significant_delta]:
            if value < 0 or value > 1:
                raise QualityDashboardConfigurationError("Score thresholds must be between 0 and 1.")
        if not (self.healthy_score >= self.warning_score >= self.degraded_score):
            raise QualityDashboardConfigurationError(
                "Expected healthy_score >= warning_score >= degraded_score."
            )
        if self.max_critical_findings_for_warning < 0:
            raise QualityDashboardConfigurationError("max_critical_findings_for_warning cannot be negative.")
        if self.max_failed_checks_for_warning < 0:
            raise QualityDashboardConfigurationError("max_failed_checks_for_warning cannot be negative.")


@dataclass(frozen=True)
class DashboardConfig:
    """Configuration for dashboard generation."""

    title: str = "Enterprise Data Quality Dashboard"
    environment: str = "default"
    domain: Optional[str] = None
    include_findings_limit: int = 200
    include_recommendations_limit: int = 100
    include_history_limit: int = 50
    thresholds: DashboardThresholds = field(default_factory=DashboardThresholds)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.title.strip():
            raise QualityDashboardConfigurationError("Dashboard title is required.")
        if self.include_findings_limit < 0:
            raise QualityDashboardConfigurationError("include_findings_limit cannot be negative.")
        if self.include_recommendations_limit < 0:
            raise QualityDashboardConfigurationError("include_recommendations_limit cannot be negative.")
        if self.include_history_limit < 0:
            raise QualityDashboardConfigurationError("include_history_limit cannot be negative.")
        self.thresholds.validate()


# =============================================================================
# Models
# =============================================================================


@dataclass
class QualityFindingSummary:
    """Normalized finding displayed in the dashboard."""

    finding_id: str
    dataset_name: Optional[str]
    dimension: QualityDimension
    severity: Severity
    status: QualityStatus
    message: str
    column: Optional[str] = None
    rule_name: Optional[str] = None
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    threshold: Optional[float] = None
    created_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["dimension"] = self.dimension.value
        data["severity"] = self.severity.value
        data["status"] = self.status.value
        return _json_safe(data)


@dataclass
class QualityRecommendationSummary:
    """Normalized recommendation displayed in the dashboard."""

    recommendation_id: str
    priority: Severity
    title: str
    description: str
    dataset_name: Optional[str] = None
    dimension: Optional[QualityDimension] = None
    columns: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["priority"] = self.priority.value
        data["dimension"] = self.dimension.value if self.dimension else None
        return _json_safe(data)


@dataclass
class QualityCheckSummary:
    """Normalized quality check summary."""

    check_name: str
    dataset_name: Optional[str]
    dimension: QualityDimension
    status: QualityStatus
    score: Optional[float]
    severity: Severity = Severity.INFO
    evaluated_records: Optional[int] = None
    failed_records: Optional[int] = None
    duration_ms: Optional[float] = None
    executed_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["dimension"] = self.dimension.value
        data["status"] = self.status.value
        data["severity"] = self.severity.value
        return _json_safe(data)


@dataclass
class DatasetQualityScorecard:
    """Dataset-level quality scorecard."""

    dataset_name: str
    overall_score: float
    status: DashboardStatus
    checks_total: int
    checks_passed: int
    checks_warning: int
    checks_failed: int
    checks_error: int
    findings_total: int
    critical_findings: int
    high_findings: int
    dimension_scores: Dict[str, float] = field(default_factory=dict)
    last_execution_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return _json_safe(data)


@dataclass
class DashboardKPI:
    """Dashboard KPI card."""

    name: str
    value: Any
    label: str
    status: DashboardStatus = DashboardStatus.UNKNOWN
    trend: TrendDirection = TrendDirection.UNKNOWN
    previous_value: Optional[Any] = None
    delta: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["trend"] = self.trend.value
        return _json_safe(data)


@dataclass
class DashboardWidget:
    """Generic dashboard widget."""

    widget_id: str
    widget_type: WidgetType
    title: str
    data: Any
    order: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["widget_type"] = self.widget_type.value
        return _json_safe(data)


@dataclass
class DashboardSnapshot:
    """Historical dashboard snapshot."""

    snapshot_id: str
    generated_at: str
    overall_score: float
    status: DashboardStatus
    dataset_count: int
    check_count: int
    finding_count: int
    critical_finding_count: int
    failed_check_count: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return _json_safe(data)


@dataclass
class QualityDashboardReport:
    """Complete generated dashboard report."""

    dashboard_id: str
    title: str
    generated_at: str
    environment: str
    domain: Optional[str]
    status: DashboardStatus
    overall_score: float
    kpis: List[DashboardKPI]
    widgets: List[DashboardWidget]
    dataset_scorecards: List[DatasetQualityScorecard]
    checks: List[QualityCheckSummary]
    findings: List[QualityFindingSummary]
    recommendations: List[QualityRecommendationSummary]
    history: List[DashboardSnapshot] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dashboard_id": self.dashboard_id,
            "title": self.title,
            "generated_at": self.generated_at,
            "environment": self.environment,
            "domain": self.domain,
            "status": self.status.value,
            "overall_score": self.overall_score,
            "metadata": _json_safe(self.metadata),
            "kpis": [kpi.to_dict() for kpi in self.kpis],
            "widgets": [widget.to_dict() for widget in self.widgets],
            "dataset_scorecards": [scorecard.to_dict() for scorecard in self.dataset_scorecards],
            "checks": [check.to_dict() for check in self.checks],
            "findings": [finding.to_dict() for finding in self.findings],
            "recommendations": [rec.to_dict() for rec in self.recommendations],
            "history": [snapshot.to_dict() for snapshot in self.history],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save_json(self, path: str | Path, indent: int = 2) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(self.to_json(indent=indent), encoding="utf-8")
        return output

    def save_html(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_dashboard_html(self), encoding="utf-8")
        return output


# =============================================================================
# Helpers
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _safe_score(values: Sequence[Optional[float]], default: float = 1.0) -> float:
    clean = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    if not clean:
        return default
    return round(sum(clean) / len(clean), 8)


def _normalize_status(value: Any) -> QualityStatus:
    if value is None:
        return QualityStatus.UNKNOWN
    raw = str(value).lower()
    mapping = {
        "passed": QualityStatus.PASSED,
        "pass": QualityStatus.PASSED,
        "succeeded": QualityStatus.PASSED,
        "success": QualityStatus.PASSED,
        "healthy": QualityStatus.PASSED,
        "warning": QualityStatus.WARNING,
        "warn": QualityStatus.WARNING,
        "failed": QualityStatus.FAILED,
        "fail": QualityStatus.FAILED,
        "degraded": QualityStatus.FAILED,
        "critical": QualityStatus.FAILED,
        "error": QualityStatus.ERROR,
        "skipped": QualityStatus.SKIPPED,
        "unknown": QualityStatus.UNKNOWN,
    }
    return mapping.get(raw, QualityStatus.UNKNOWN)


def _normalize_severity(value: Any) -> Severity:
    if value is None:
        return Severity.INFO
    raw = str(value).lower()
    mapping = {
        "debug": Severity.INFO,
        "info": Severity.INFO,
        "low": Severity.LOW,
        "medium": Severity.MEDIUM,
        "warning": Severity.MEDIUM,
        "warn": Severity.MEDIUM,
        "high": Severity.HIGH,
        "critical": Severity.CRITICAL,
        "severe": Severity.CRITICAL,
    }
    return mapping.get(raw, Severity.INFO)


def _dimension_from_text(value: Any) -> QualityDimension:
    if value is None:
        return QualityDimension.CUSTOM
    raw = str(value).lower()
    for dimension in QualityDimension:
        if dimension.value == raw:
            return dimension
    if "accuracy" in raw:
        return QualityDimension.ACCURACY
    if "complete" in raw:
        return QualityDimension.COMPLETENESS
    if "consistent" in raw:
        return QualityDimension.CONSISTENCY
    if "fresh" in raw:
        return QualityDimension.FRESHNESS
    if "null" in raw:
        return QualityDimension.NULL_ANALYSIS
    if "profil" in raw:
        return QualityDimension.PROFILING
    return QualityDimension.CUSTOM


def _dashboard_status_from_score(
    score: float,
    critical_findings: int,
    failed_checks: int,
    thresholds: DashboardThresholds,
) -> DashboardStatus:
    if critical_findings > thresholds.max_critical_findings_for_warning:
        return DashboardStatus.CRITICAL
    if failed_checks > thresholds.max_failed_checks_for_warning:
        return DashboardStatus.DEGRADED
    if score >= thresholds.healthy_score:
        return DashboardStatus.HEALTHY
    if score >= thresholds.warning_score:
        return DashboardStatus.WARNING
    if score >= thresholds.degraded_score:
        return DashboardStatus.DEGRADED
    return DashboardStatus.CRITICAL


def _trend(current: Optional[float], previous: Optional[float], significant_delta: float) -> Tuple[TrendDirection, Optional[float]]:
    if current is None or previous is None:
        return TrendDirection.UNKNOWN, None
    delta = round(float(current) - float(previous), 8)
    if abs(delta) < significant_delta:
        return TrendDirection.STABLE, delta
    if delta > 0:
        return TrendDirection.IMPROVING, delta
    return TrendDirection.DEGRADING, delta


def _count_by(values: Iterable[Any]) -> Dict[str, int]:
    counter = Counter(str(v) for v in values)
    return dict(sorted(counter.items()))


# =============================================================================
# Sinks
# =============================================================================


class NoopMetricsSink:
    """Default metrics sink that intentionally does nothing."""

    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        return None

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        return None

    def timing(self, metric_name: str, value_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        return None


class InMemoryAuditSink:
    """Simple audit sink useful for tests, local execution, and debugging."""

    def __init__(self) -> None:
        self.events: List[Mapping[str, Any]] = []

    def write_event(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))


# =============================================================================
# Dashboard Engine
# =============================================================================


class QualityDashboardEngine:
    """
    Enterprise dashboard generator for data quality reports.

    The engine accepts already-normalized summaries or raw report dictionaries
    from quality modules, then builds scorecards, KPIs, widgets, and exportable
    dashboard reports.
    """

    def __init__(
        self,
        config: Optional[DashboardConfig] = None,
        *,
        metrics_sink: Optional[MetricsSink] = None,
        audit_sink: Optional[AuditSink] = None,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or DashboardConfig()
        self.config.validate()
        self.metrics_sink = metrics_sink or NoopMetricsSink()
        self.audit_sink = audit_sink
        self.logger = logger_ or logger

    def build(
        self,
        *,
        checks: Optional[Sequence[QualityCheckSummary | Mapping[str, Any]]] = None,
        findings: Optional[Sequence[QualityFindingSummary | Mapping[str, Any]]] = None,
        recommendations: Optional[Sequence[QualityRecommendationSummary | Mapping[str, Any]]] = None,
        reports: Optional[Sequence[Mapping[str, Any]]] = None,
        history: Optional[Sequence[DashboardSnapshot | Mapping[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> QualityDashboardReport:
        """Build a complete dashboard report."""
        started = time.perf_counter()
        dashboard_id = str(uuid.uuid4())
        metadata = {**self.config.metadata, **(metadata or {})}

        try:
            normalized_checks = [self._normalize_check(item) for item in (checks or [])]
            normalized_findings = [self._normalize_finding(item) for item in (findings or [])]
            normalized_recommendations = [self._normalize_recommendation(item) for item in (recommendations or [])]

            for report in reports or []:
                extracted_checks, extracted_findings, extracted_recommendations = self._extract_from_report(report)
                normalized_checks.extend(extracted_checks)
                normalized_findings.extend(extracted_findings)
                normalized_recommendations.extend(extracted_recommendations)

            normalized_history = [self._normalize_snapshot(item) for item in (history or [])]
            normalized_history = sorted(normalized_history, key=lambda item: item.generated_at)[-self.config.include_history_limit :]

            scorecards = self._build_scorecards(normalized_checks, normalized_findings)
            overall_score = self._overall_score(scorecards, normalized_checks)
            critical_findings = sum(1 for item in normalized_findings if item.severity == Severity.CRITICAL)
            failed_checks = sum(1 for item in normalized_checks if item.status in {QualityStatus.FAILED, QualityStatus.ERROR})
            status = _dashboard_status_from_score(
                overall_score,
                critical_findings,
                failed_checks,
                self.config.thresholds,
            )

            current_snapshot = DashboardSnapshot(
                snapshot_id=str(uuid.uuid4()),
                generated_at=utc_now_iso(),
                overall_score=overall_score,
                status=status,
                dataset_count=len(scorecards),
                check_count=len(normalized_checks),
                finding_count=len(normalized_findings),
                critical_finding_count=critical_findings,
                failed_check_count=failed_checks,
            )
            history_with_current = [*normalized_history, current_snapshot]
            previous_snapshot = normalized_history[-1] if normalized_history else None

            kpis = self._build_kpis(
                scorecards,
                normalized_checks,
                normalized_findings,
                normalized_recommendations,
                current_snapshot,
                previous_snapshot,
            )
            widgets = self._build_widgets(
                scorecards,
                normalized_checks,
                normalized_findings,
                normalized_recommendations,
                history_with_current,
            )

            report = QualityDashboardReport(
                dashboard_id=dashboard_id,
                title=self.config.title,
                generated_at=current_snapshot.generated_at,
                environment=self.config.environment,
                domain=self.config.domain,
                status=status,
                overall_score=overall_score,
                kpis=kpis,
                widgets=widgets,
                dataset_scorecards=scorecards,
                checks=normalized_checks,
                findings=sorted(
                    normalized_findings,
                    key=lambda f: _severity_rank(f.severity),
                    reverse=True,
                )[: self.config.include_findings_limit],
                recommendations=sorted(
                    normalized_recommendations,
                    key=lambda r: _severity_rank(r.priority),
                    reverse=True,
                )[: self.config.include_recommendations_limit],
                history=history_with_current,
                metadata=metadata,
            )

            duration_ms = (time.perf_counter() - started) * 1000
            self._publish_metrics(report, duration_ms)
            self._write_audit(report, duration_ms)
            self.logger.info(
                "Quality dashboard built dashboard_id=%s status=%s score=%.5f duration_ms=%.2f",
                dashboard_id,
                report.status.value,
                report.overall_score,
                duration_ms,
            )
            return report

        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Failed to build quality dashboard")
            self.metrics_sink.increment("data_quality.dashboard.build.error")
            raise QualityDashboardExecutionError(str(exc)) from exc

    def _normalize_check(self, item: QualityCheckSummary | Mapping[str, Any]) -> QualityCheckSummary:
        if isinstance(item, QualityCheckSummary):
            return item
        payload = dict(item)
        dimension = _dimension_from_text(payload.get("dimension") or payload.get("rule_type") or payload.get("type"))
        score = _first_float(
            payload,
            ["score", "quality_score", "overall_score", "weighted_score", "accuracy_score", "completeness_score", "consistency_score", "freshness_score"],
        )
        return QualityCheckSummary(
            check_name=str(payload.get("check_name") or payload.get("rule_name") or payload.get("name") or "quality_check"),
            dataset_name=payload.get("dataset_name"),
            dimension=dimension,
            status=_normalize_status(payload.get("status")),
            score=score,
            severity=_normalize_severity(payload.get("severity")),
            evaluated_records=_first_int(payload, ["evaluated_records", "total_records"]),
            failed_records=_first_int(payload, ["failed_records", "incomplete_records", "inconsistent_records", "stale_records", "error_records"]),
            duration_ms=_first_float(payload, ["duration_ms"]),
            executed_at=payload.get("executed_at") or payload.get("finished_at") or payload.get("started_at"),
            metadata={k: _json_safe(v) for k, v in payload.items()},
        )

    def _normalize_finding(self, item: QualityFindingSummary | Mapping[str, Any]) -> QualityFindingSummary:
        if isinstance(item, QualityFindingSummary):
            return item
        payload = dict(item)
        return QualityFindingSummary(
            finding_id=str(payload.get("finding_id") or payload.get("id") or uuid.uuid4()),
            dataset_name=payload.get("dataset_name"),
            dimension=_dimension_from_text(payload.get("dimension") or payload.get("rule_type")),
            severity=_normalize_severity(payload.get("severity")),
            status=_normalize_status(payload.get("status") or "failed"),
            message=str(payload.get("message") or payload.get("description") or "Quality finding"),
            column=payload.get("column"),
            rule_name=payload.get("rule_name"),
            metric_name=payload.get("metric_name"),
            metric_value=_first_float(payload, ["metric_value", "value", "score"]),
            threshold=_first_float(payload, ["threshold"]),
            created_at=payload.get("created_at") or payload.get("timestamp"),
            metadata={k: _json_safe(v) for k, v in payload.items()},
        )

    def _normalize_recommendation(
        self,
        item: QualityRecommendationSummary | Mapping[str, Any],
    ) -> QualityRecommendationSummary:
        if isinstance(item, QualityRecommendationSummary):
            return item
        payload = dict(item)
        return QualityRecommendationSummary(
            recommendation_id=str(payload.get("recommendation_id") or payload.get("id") or uuid.uuid4()),
            priority=_normalize_severity(payload.get("priority") or payload.get("severity")),
            title=str(payload.get("title") or "Quality recommendation"),
            description=str(payload.get("description") or payload.get("message") or "Review quality recommendation."),
            dataset_name=payload.get("dataset_name"),
            dimension=_dimension_from_text(payload.get("dimension")) if payload.get("dimension") else None,
            columns=list(payload.get("columns") or ([] if payload.get("column") is None else [payload.get("column")])),
            metadata={k: _json_safe(v) for k, v in payload.items()},
        )

    def _normalize_snapshot(self, item: DashboardSnapshot | Mapping[str, Any]) -> DashboardSnapshot:
        if isinstance(item, DashboardSnapshot):
            return item
        payload = dict(item)
        status_value = payload.get("status")
        status = DashboardStatus(status_value) if status_value in {s.value for s in DashboardStatus} else DashboardStatus.UNKNOWN
        return DashboardSnapshot(
            snapshot_id=str(payload.get("snapshot_id") or uuid.uuid4()),
            generated_at=str(payload.get("generated_at") or utc_now_iso()),
            overall_score=float(payload.get("overall_score") or 0.0),
            status=status,
            dataset_count=int(payload.get("dataset_count") or 0),
            check_count=int(payload.get("check_count") or 0),
            finding_count=int(payload.get("finding_count") or 0),
            critical_finding_count=int(payload.get("critical_finding_count") or 0),
            failed_check_count=int(payload.get("failed_check_count") or 0),
            metadata=dict(payload.get("metadata") or {}),
        )

    def _extract_from_report(
        self,
        report: Mapping[str, Any],
    ) -> Tuple[List[QualityCheckSummary], List[QualityFindingSummary], List[QualityRecommendationSummary]]:
        payload = dict(report)
        dataset_name = payload.get("dataset_name") or payload.get("name")
        dimension = _dimension_from_text(payload.get("dimension") or payload.get("report_type") or payload.get("type"))
        report_status = _normalize_status(payload.get("status"))
        report_score = _first_float(payload, ["weighted_score", "overall_score", "quality_score"])

        checks: List[QualityCheckSummary] = []
        findings: List[QualityFindingSummary] = []
        recommendations: List[QualityRecommendationSummary] = []

        if report_score is not None or payload.get("status") is not None:
            checks.append(
                QualityCheckSummary(
                    check_name=str(payload.get("check_name") or payload.get("report_id") or "quality_report"),
                    dataset_name=dataset_name,
                    dimension=dimension,
                    status=report_status,
                    score=report_score,
                    duration_ms=_first_float(payload, ["duration_ms"]),
                    executed_at=payload.get("finished_at") or payload.get("generated_at") or payload.get("started_at"),
                    metadata={"source": "report", **_json_safe(payload)},
                )
            )

        for result in payload.get("rule_results") or []:
            item = dict(result)
            item.setdefault("dataset_name", dataset_name)
            item.setdefault("dimension", dimension.value)
            checks.append(self._normalize_check(item))
            for finding in item.get("findings") or []:
                f = dict(finding)
                f.setdefault("dataset_name", dataset_name)
                f.setdefault("dimension", dimension.value)
                f.setdefault("rule_name", item.get("rule_name"))
                findings.append(self._normalize_finding(f))

        for finding in payload.get("findings") or []:
            f = dict(finding)
            f.setdefault("dataset_name", dataset_name)
            f.setdefault("dimension", dimension.value)
            findings.append(self._normalize_finding(f))

        for rec in payload.get("recommendations") or []:
            r = dict(rec)
            r.setdefault("dataset_name", dataset_name)
            r.setdefault("dimension", dimension.value)
            recommendations.append(self._normalize_recommendation(r))

        return checks, findings, recommendations

    def _build_scorecards(
        self,
        checks: Sequence[QualityCheckSummary],
        findings: Sequence[QualityFindingSummary],
    ) -> List[DatasetQualityScorecard]:
        datasets = sorted(
            set(filter(None, [c.dataset_name for c in checks] + [f.dataset_name for f in findings]))
        )
        if not datasets and checks:
            datasets = ["dataset"]

        scorecards: List[DatasetQualityScorecard] = []
        for dataset in datasets:
            dataset_checks = [c for c in checks if (c.dataset_name or "dataset") == dataset]
            dataset_findings = [f for f in findings if (f.dataset_name or "dataset") == dataset]
            dimension_scores: Dict[str, float] = {}
            for dimension in QualityDimension:
                dim_scores = [c.score for c in dataset_checks if c.dimension == dimension]
                if dim_scores:
                    dimension_scores[dimension.value] = _safe_score(dim_scores, default=1.0)

            score = _safe_score([c.score for c in dataset_checks], default=1.0)
            checks_total = len(dataset_checks)
            checks_passed = sum(1 for c in dataset_checks if c.status == QualityStatus.PASSED)
            checks_warning = sum(1 for c in dataset_checks if c.status == QualityStatus.WARNING)
            checks_failed = sum(1 for c in dataset_checks if c.status == QualityStatus.FAILED)
            checks_error = sum(1 for c in dataset_checks if c.status == QualityStatus.ERROR)
            critical_findings = sum(1 for f in dataset_findings if f.severity == Severity.CRITICAL)
            high_findings = sum(1 for f in dataset_findings if f.severity == Severity.HIGH)
            status = _dashboard_status_from_score(
                score,
                critical_findings,
                checks_failed + checks_error,
                self.config.thresholds,
            )
            last_execution_at = max([c.executed_at for c in dataset_checks if c.executed_at], default=None)

            scorecards.append(
                DatasetQualityScorecard(
                    dataset_name=dataset,
                    overall_score=score,
                    status=status,
                    checks_total=checks_total,
                    checks_passed=checks_passed,
                    checks_warning=checks_warning,
                    checks_failed=checks_failed,
                    checks_error=checks_error,
                    findings_total=len(dataset_findings),
                    critical_findings=critical_findings,
                    high_findings=high_findings,
                    dimension_scores=dimension_scores,
                    last_execution_at=last_execution_at,
                )
            )

        return sorted(scorecards, key=lambda s: (s.status.value, s.overall_score))

    def _overall_score(
        self,
        scorecards: Sequence[DatasetQualityScorecard],
        checks: Sequence[QualityCheckSummary],
    ) -> float:
        if scorecards:
            return _safe_score([s.overall_score for s in scorecards], default=1.0)
        return _safe_score([c.score for c in checks], default=1.0)

    def _build_kpis(
        self,
        scorecards: Sequence[DatasetQualityScorecard],
        checks: Sequence[QualityCheckSummary],
        findings: Sequence[QualityFindingSummary],
        recommendations: Sequence[QualityRecommendationSummary],
        current: DashboardSnapshot,
        previous: Optional[DashboardSnapshot],
    ) -> List[DashboardKPI]:
        trend, delta = _trend(
            current.overall_score,
            previous.overall_score if previous else None,
            self.config.thresholds.trend_significant_delta,
        )
        return [
            DashboardKPI(
                name="overall_score",
                value=current.overall_score,
                label="Overall Quality Score",
                status=current.status,
                trend=trend,
                previous_value=previous.overall_score if previous else None,
                delta=delta,
            ),
            DashboardKPI(
                name="datasets_monitored",
                value=len(scorecards),
                label="Datasets Monitored",
                status=DashboardStatus.HEALTHY if scorecards else DashboardStatus.UNKNOWN,
            ),
            DashboardKPI(
                name="checks_total",
                value=len(checks),
                label="Quality Checks",
                status=DashboardStatus.HEALTHY if checks else DashboardStatus.UNKNOWN,
            ),
            DashboardKPI(
                name="failed_checks",
                value=current.failed_check_count,
                label="Failed/Error Checks",
                status=DashboardStatus.CRITICAL if current.failed_check_count else DashboardStatus.HEALTHY,
            ),
            DashboardKPI(
                name="critical_findings",
                value=current.critical_finding_count,
                label="Critical Findings",
                status=DashboardStatus.CRITICAL if current.critical_finding_count else DashboardStatus.HEALTHY,
            ),
            DashboardKPI(
                name="recommendations",
                value=len(recommendations),
                label="Recommendations",
                status=DashboardStatus.WARNING if recommendations else DashboardStatus.HEALTHY,
            ),
        ]

    def _build_widgets(
        self,
        scorecards: Sequence[DatasetQualityScorecard],
        checks: Sequence[QualityCheckSummary],
        findings: Sequence[QualityFindingSummary],
        recommendations: Sequence[QualityRecommendationSummary],
        history: Sequence[DashboardSnapshot],
    ) -> List[DashboardWidget]:
        severity_breakdown = _count_by(f.severity.value for f in findings)
        status_breakdown = _count_by(c.status.value for c in checks)
        dimension_scores: Dict[str, List[float]] = defaultdict(list)
        for check in checks:
            if check.score is not None:
                dimension_scores[check.dimension.value].append(check.score)
        dimension_health = {
            dimension: _safe_score(scores, default=1.0)
            for dimension, scores in sorted(dimension_scores.items())
        }

        return [
            DashboardWidget(
                widget_id=str(uuid.uuid4()),
                widget_type=WidgetType.DATASET_RANKING,
                title="Dataset Quality Ranking",
                data=[s.to_dict() for s in sorted(scorecards, key=lambda item: item.overall_score)],
                order=10,
            ),
            DashboardWidget(
                widget_id=str(uuid.uuid4()),
                widget_type=WidgetType.STATUS_BREAKDOWN,
                title="Check Status Breakdown",
                data=status_breakdown,
                order=20,
            ),
            DashboardWidget(
                widget_id=str(uuid.uuid4()),
                widget_type=WidgetType.SEVERITY_BREAKDOWN,
                title="Finding Severity Breakdown",
                data=severity_breakdown,
                order=30,
            ),
            DashboardWidget(
                widget_id=str(uuid.uuid4()),
                widget_type=WidgetType.DIMENSION_HEALTH,
                title="Quality Dimension Health",
                data=dimension_health,
                order=40,
            ),
            DashboardWidget(
                widget_id=str(uuid.uuid4()),
                widget_type=WidgetType.FINDINGS_TABLE,
                title="Top Findings",
                data=[f.to_dict() for f in sorted(findings, key=lambda f: _severity_rank(f.severity), reverse=True)[: self.config.include_findings_limit]],
                order=50,
            ),
            DashboardWidget(
                widget_id=str(uuid.uuid4()),
                widget_type=WidgetType.RECOMMENDATIONS_TABLE,
                title="Top Recommendations",
                data=[r.to_dict() for r in sorted(recommendations, key=lambda r: _severity_rank(r.priority), reverse=True)[: self.config.include_recommendations_limit]],
                order=60,
            ),
            DashboardWidget(
                widget_id=str(uuid.uuid4()),
                widget_type=WidgetType.TREND,
                title="Quality Score Trend",
                data=[h.to_dict() for h in history],
                order=70,
            ),
        ]

    def _publish_metrics(self, report: QualityDashboardReport, duration_ms: float) -> None:
        tags = {"environment": report.environment, "status": report.status.value, "domain": report.domain or "unknown"}
        self.metrics_sink.gauge("data_quality.dashboard.overall_score", report.overall_score, tags=tags)
        self.metrics_sink.gauge("data_quality.dashboard.dataset_count", len(report.dataset_scorecards), tags=tags)
        self.metrics_sink.gauge("data_quality.dashboard.check_count", len(report.checks), tags=tags)
        self.metrics_sink.gauge("data_quality.dashboard.finding_count", len(report.findings), tags=tags)
        self.metrics_sink.gauge(
            "data_quality.dashboard.critical_finding_count",
            sum(1 for f in report.findings if f.severity == Severity.CRITICAL),
            tags=tags,
        )
        self.metrics_sink.timing("data_quality.dashboard.build_duration_ms", duration_ms, tags=tags)
        self.metrics_sink.increment("data_quality.dashboard.generated", tags=tags)

    def _write_audit(self, report: QualityDashboardReport, duration_ms: float) -> None:
        if not self.audit_sink:
            return
        self.audit_sink.write_event(
            {
                "event_type": "quality_dashboard_generated",
                "dashboard_id": report.dashboard_id,
                "timestamp": utc_now_iso(),
                "status": report.status.value,
                "overall_score": report.overall_score,
                "duration_ms": duration_ms,
                "dataset_count": len(report.dataset_scorecards),
                "check_count": len(report.checks),
                "finding_count": len(report.findings),
            }
        )


# =============================================================================
# Utility functions
# =============================================================================


def _first_float(payload: Mapping[str, Any], keys: Sequence[str]) -> Optional[float]:
    for key in keys:
        if key in payload and payload[key] is not None:
            try:
                return float(payload[key])
            except (TypeError, ValueError):
                continue
    return None


def _first_int(payload: Mapping[str, Any], keys: Sequence[str]) -> Optional[int]:
    for key in keys:
        if key in payload and payload[key] is not None:
            try:
                return int(payload[key])
            except (TypeError, ValueError):
                continue
    return None


def _severity_rank(severity: Severity) -> int:
    return {
        Severity.INFO: 0,
        Severity.LOW: 1,
        Severity.MEDIUM: 2,
        Severity.HIGH: 3,
        Severity.CRITICAL: 4,
    }.get(severity, 0)


# =============================================================================
# HTML Rendering
# =============================================================================


def render_dashboard_html(report: QualityDashboardReport) -> str:
    """Render a standalone HTML dashboard with no external dependencies."""
    status_class = html.escape(report.status.value)
    kpi_cards = "\n".join(_render_kpi(kpi) for kpi in report.kpis)
    scorecard_rows = "\n".join(_render_scorecard_row(item) for item in report.dataset_scorecards)
    finding_rows = "\n".join(_render_finding_row(item) for item in report.findings[:100])
    recommendation_rows = "\n".join(_render_recommendation_row(item) for item in report.recommendations[:100])
    dimension_widget = next((w for w in report.widgets if w.widget_type == WidgetType.DIMENSION_HEALTH), None)
    dimension_rows = ""
    if dimension_widget and isinstance(dimension_widget.data, dict):
        dimension_rows = "\n".join(
            f"<tr><td>{html.escape(str(k))}</td><td>{float(v):.4f}</td></tr>"
            for k, v in dimension_widget.data.items()
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(report.title)}</title>
  <style>
    :root {{
      --bg: #0f172a;
      --panel: #111827;
      --card: #1f2937;
      --text: #e5e7eb;
      --muted: #9ca3af;
      --border: #374151;
      --healthy: #22c55e;
      --warning: #f59e0b;
      --degraded: #fb923c;
      --critical: #ef4444;
      --unknown: #94a3b8;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Inter, Segoe UI, Arial, sans-serif; background: var(--bg); color: var(--text); }}
    header {{ padding: 28px; border-bottom: 1px solid var(--border); background: linear-gradient(135deg, #111827, #0f172a); }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    .meta {{ color: var(--muted); font-size: 14px; }}
    .status {{ display:inline-block; padding: 6px 10px; border-radius: 999px; font-weight: 700; text-transform: uppercase; font-size: 12px; }}
    .status.healthy {{ background: rgba(34,197,94,.18); color: var(--healthy); }}
    .status.warning {{ background: rgba(245,158,11,.18); color: var(--warning); }}
    .status.degraded {{ background: rgba(251,146,60,.18); color: var(--degraded); }}
    .status.critical {{ background: rgba(239,68,68,.18); color: var(--critical); }}
    .status.unknown {{ background: rgba(148,163,184,.18); color: var(--unknown); }}
    main {{ padding: 28px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }}
    .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 18px; box-shadow: 0 12px 30px rgba(0,0,0,.18); }}
    .kpi-label {{ color: var(--muted); font-size: 13px; margin-bottom: 8px; }}
    .kpi-value {{ font-size: 28px; font-weight: 800; }}
    .section {{ margin: 28px 0; }}
    h2 {{ font-size: 20px; margin: 0 0 14px; }}
    table {{ width: 100%; border-collapse: collapse; overflow: hidden; border-radius: 12px; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ color: var(--muted); background: #111827; }}
    tr:hover td {{ background: rgba(255,255,255,.03); }}
    .small {{ color: var(--muted); font-size: 12px; }}
    .score {{ font-variant-numeric: tabular-nums; font-weight: 700; }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(report.title)}</h1>
    <div class="meta">
      Generated at {html.escape(report.generated_at)} · Environment: {html.escape(report.environment)} ·
      Status: <span class="status {status_class}">{html.escape(report.status.value)}</span> ·
      Overall score: <strong>{report.overall_score:.4f}</strong>
    </div>
  </header>
  <main>
    <section class="grid">{kpi_cards}</section>

    <section class="section card">
      <h2>Dataset Scorecards</h2>
      <table>
        <thead><tr><th>Dataset</th><th>Status</th><th>Score</th><th>Checks</th><th>Findings</th><th>Critical</th><th>Last Execution</th></tr></thead>
        <tbody>{scorecard_rows}</tbody>
      </table>
    </section>

    <section class="section card">
      <h2>Quality Dimension Health</h2>
      <table><thead><tr><th>Dimension</th><th>Score</th></tr></thead><tbody>{dimension_rows}</tbody></table>
    </section>

    <section class="section card">
      <h2>Top Findings</h2>
      <table>
        <thead><tr><th>Severity</th><th>Dataset</th><th>Dimension</th><th>Column</th><th>Message</th></tr></thead>
        <tbody>{finding_rows}</tbody>
      </table>
    </section>

    <section class="section card">
      <h2>Recommendations</h2>
      <table>
        <thead><tr><th>Priority</th><th>Dataset</th><th>Title</th><th>Description</th><th>Columns</th></tr></thead>
        <tbody>{recommendation_rows}</tbody>
      </table>
    </section>
  </main>
</body>
</html>"""


def _render_kpi(kpi: DashboardKPI) -> str:
    value = kpi.value
    if isinstance(value, float):
        value_text = f"{value:.4f}"
    else:
        value_text = str(value)
    return f"""
    <div class="card">
      <div class="kpi-label">{html.escape(kpi.label)}</div>
      <div class="kpi-value">{html.escape(value_text)}</div>
      <div class="small">Status: {html.escape(kpi.status.value)} · Trend: {html.escape(kpi.trend.value)}</div>
    </div>"""


def _render_scorecard_row(item: DatasetQualityScorecard) -> str:
    return f"""
    <tr>
      <td>{html.escape(item.dataset_name)}</td>
      <td><span class="status {html.escape(item.status.value)}">{html.escape(item.status.value)}</span></td>
      <td class="score">{item.overall_score:.4f}</td>
      <td>{item.checks_total} total · {item.checks_failed} failed · {item.checks_error} error</td>
      <td>{item.findings_total}</td>
      <td>{item.critical_findings}</td>
      <td>{html.escape(str(item.last_execution_at or '-'))}</td>
    </tr>"""


def _render_finding_row(item: QualityFindingSummary) -> str:
    return f"""
    <tr>
      <td>{html.escape(item.severity.value)}</td>
      <td>{html.escape(str(item.dataset_name or '-'))}</td>
      <td>{html.escape(item.dimension.value)}</td>
      <td>{html.escape(str(item.column or '-'))}</td>
      <td>{html.escape(item.message)}</td>
    </tr>"""


def _render_recommendation_row(item: QualityRecommendationSummary) -> str:
    return f"""
    <tr>
      <td>{html.escape(item.priority.value)}</td>
      <td>{html.escape(str(item.dataset_name or '-'))}</td>
      <td>{html.escape(item.title)}</td>
      <td>{html.escape(item.description)}</td>
      <td>{html.escape(', '.join(item.columns))}</td>
    </tr>"""


# =============================================================================
# Convenience API
# =============================================================================


def build_quality_dashboard(
    *,
    checks: Optional[Sequence[QualityCheckSummary | Mapping[str, Any]]] = None,
    findings: Optional[Sequence[QualityFindingSummary | Mapping[str, Any]]] = None,
    recommendations: Optional[Sequence[QualityRecommendationSummary | Mapping[str, Any]]] = None,
    reports: Optional[Sequence[Mapping[str, Any]]] = None,
    history: Optional[Sequence[DashboardSnapshot | Mapping[str, Any]]] = None,
    title: str = "Enterprise Data Quality Dashboard",
    environment: str = "default",
    domain: Optional[str] = None,
) -> QualityDashboardReport:
    """Convenience function for one-shot dashboard generation."""
    engine = QualityDashboardEngine(
        DashboardConfig(title=title, environment=environment, domain=domain)
    )
    return engine.build(
        checks=checks,
        findings=findings,
        recommendations=recommendations,
        reports=reports,
        history=history,
    )


# =============================================================================
# Local Smoke Example
# =============================================================================


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    checks = [
        QualityCheckSummary(
            check_name="customer_id_completeness",
            dataset_name="customers",
            dimension=QualityDimension.COMPLETENESS,
            status=QualityStatus.FAILED,
            score=0.97,
            severity=Severity.CRITICAL,
            evaluated_records=10000,
            failed_records=300,
            executed_at=utc_now_iso(),
        ),
        QualityCheckSummary(
            check_name="email_accuracy",
            dataset_name="customers",
            dimension=QualityDimension.ACCURACY,
            status=QualityStatus.WARNING,
            score=0.96,
            severity=Severity.MEDIUM,
            evaluated_records=10000,
            failed_records=400,
            executed_at=utc_now_iso(),
        ),
        QualityCheckSummary(
            check_name="orders_freshness",
            dataset_name="orders",
            dimension=QualityDimension.FRESHNESS,
            status=QualityStatus.PASSED,
            score=0.995,
            severity=Severity.INFO,
            evaluated_records=50000,
            failed_records=0,
            executed_at=utc_now_iso(),
        ),
    ]

    findings = [
        QualityFindingSummary(
            finding_id="finding_customer_id_nulls",
            dataset_name="customers",
            dimension=QualityDimension.COMPLETENESS,
            severity=Severity.CRITICAL,
            status=QualityStatus.FAILED,
            message="Critical customer_id field contains null values.",
            column="customer_id",
            rule_name="customer_id_required",
            metric_name="null_rate",
            metric_value=0.03,
            threshold=0.0,
            created_at=utc_now_iso(),
        )
    ]

    recommendations = [
        QualityRecommendationSummary(
            recommendation_id="rec_customer_contract",
            priority=Severity.CRITICAL,
            title="Enforce customer_id non-null contract",
            description="Reject or quarantine records without customer_id before publishing the curated customer table.",
            dataset_name="customers",
            dimension=QualityDimension.COMPLETENESS,
            columns=["customer_id"],
        )
    ]

    dashboard = build_quality_dashboard(
        checks=checks,
        findings=findings,
        recommendations=recommendations,
        title="Data Quality Command Center",
        environment="dev",
        domain="customer-operations",
    )

    print(dashboard.to_json())

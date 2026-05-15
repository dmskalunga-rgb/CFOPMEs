# ml/governance/alerts.py
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Any, Dict, List, Optional


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"


class AlertStatus(str, Enum):
    OPEN = "open"
    FIRING = "firing"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"


class AlertCategory(str, Enum):
    FRAUD = "fraud"
    UEBA = "ueba"
    API = "api"
    CASHFLOW = "cashflow"
    SECURITY = "security"
    MODEL_DRIFT = "model_drift"
    DATA_QUALITY = "data_quality"
    PERFORMANCE = "performance"
    COMPLIANCE = "compliance"
    INFRASTRUCTURE = "infrastructure"
    PIPELINE = "pipeline"
    BUSINESS = "business"
    SYSTEM = "system"


class AlertNotFoundError(KeyError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EnterpriseAlert:
    tenant_id: str
    category: AlertCategory
    priority: AlertPriority
    title: str
    description: str
    source: str
    severity: AlertSeverity = AlertSeverity.WARNING
    status: AlertStatus = AlertStatus.OPEN
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    assigned_to: Optional[str] = None
    resolution_notes: Optional[str] = None
    alert_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self, *, enum_values: bool = True) -> Dict[str, Any]:
        data = asdict(self)

        if enum_values:
            data["category"] = self.category.value
            data["priority"] = self.priority.value
            data["severity"] = self.severity.value
            data["status"] = self.status.value
        else:
            data["category"] = self.category
            data["priority"] = self.priority
            data["severity"] = self.severity
            data["status"] = self.status

        return data

    def __getitem__(self, key: str) -> Any:
        return self.to_dict(enum_values=False)[key]


class EnterpriseAlertEngine:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.config = dict(kwargs)
        self.enterprise_mode = True
        self._alerts: Dict[str, EnterpriseAlert] = {}
        self._lock = RLock()

    def create_alert(
        self,
        tenant_id: str,
        category: AlertCategory | str,
        priority: AlertPriority | str,
        title: str,
        description: str,
        source: str,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        severity: AlertSeverity | str = AlertSeverity.WARNING,
        **kwargs: Any,
    ) -> EnterpriseAlert:
        alert = EnterpriseAlert(
            tenant_id=tenant_id,
            category=AlertCategory(category),
            priority=AlertPriority(priority),
            title=title,
            description=description,
            source=source,
            severity=AlertSeverity(severity),
            metadata={**dict(metadata or {}), **kwargs},
            tags=list(tags or []),
        )

        with self._lock:
            self._alerts[alert.alert_id] = alert

        return alert

    def load_alert(self, alert_id: str) -> Dict[str, Any]:
        with self._lock:
            alert = self._alerts.get(alert_id)

        if alert is None:
            raise AlertNotFoundError(f"Alert not found: {alert_id}")

        return alert.to_dict(enum_values=False)

    def get_alert(self, alert_id: str) -> EnterpriseAlert:
        with self._lock:
            alert = self._alerts.get(alert_id)

        if alert is None:
            raise AlertNotFoundError(f"Alert not found: {alert_id}")

        return alert

    def list_alerts(
        self,
        tenant_id: Optional[str] = None,
        category: Optional[AlertCategory | str] = None,
        priority: Optional[AlertPriority | str] = None,
        status: Optional[AlertStatus | str] = None,
    ) -> List[EnterpriseAlert]:
        with self._lock:
            alerts = list(self._alerts.values())

        if tenant_id is not None:
            alerts = [item for item in alerts if item.tenant_id == tenant_id]

        if category is not None:
            selected_category = AlertCategory(category)
            alerts = [item for item in alerts if item.category == selected_category]

        if priority is not None:
            selected_priority = AlertPriority(priority)
            alerts = [item for item in alerts if item.priority == selected_priority]

        if status is not None:
            selected_status = AlertStatus(status)
            alerts = [item for item in alerts if item.status == selected_status]

        return alerts

    def update_status(
        self,
        alert_id: str,
        status: AlertStatus | str,
        assigned_to: Optional[str] = None,
        resolution_notes: Optional[str] = None,
    ) -> bool:
        with self._lock:
            alert = self._alerts.get(alert_id)

            if alert is None:
                raise AlertNotFoundError(f"Alert not found: {alert_id}")

            alert.status = AlertStatus(status)

            if assigned_to is not None:
                alert.assigned_to = assigned_to

            if resolution_notes is not None:
                alert.resolution_notes = resolution_notes

            alert.updated_at = utc_now()

        return True

    def acknowledge(
        self,
        alert_id: str,
        assigned_to: Optional[str] = None,
        resolution_notes: Optional[str] = None,
    ) -> bool:
        return self.update_status(
            alert_id=alert_id,
            status=AlertStatus.ACKNOWLEDGED,
            assigned_to=assigned_to,
            resolution_notes=resolution_notes,
        )

    def resolve(
        self,
        alert_id: str,
        assigned_to: Optional[str] = None,
        resolution_notes: Optional[str] = None,
    ) -> bool:
        return self.update_status(
            alert_id=alert_id,
            status=AlertStatus.RESOLVED,
            assigned_to=assigned_to,
            resolution_notes=resolution_notes,
        )

    def suppress(
        self,
        alert_id: str,
        assigned_to: Optional[str] = None,
        resolution_notes: Optional[str] = None,
    ) -> bool:
        return self.update_status(
            alert_id=alert_id,
            status=AlertStatus.SUPPRESSED,
            assigned_to=assigned_to,
            resolution_notes=resolution_notes,
        )

    def delete_alert(self, alert_id: str) -> bool:
        with self._lock:
            if alert_id not in self._alerts:
                raise AlertNotFoundError(f"Alert not found: {alert_id}")
            del self._alerts[alert_id]
        return True

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            alerts = list(self._alerts.values())

        return {
            "total": len(alerts),
            "open": sum(1 for item in alerts if item.status == AlertStatus.OPEN),
            "firing": sum(1 for item in alerts if item.status == AlertStatus.FIRING),
            "acknowledged": sum(1 for item in alerts if item.status == AlertStatus.ACKNOWLEDGED),
            "resolved": sum(1 for item in alerts if item.status == AlertStatus.RESOLVED),
            "suppressed": sum(1 for item in alerts if item.status == AlertStatus.SUPPRESSED),
            "critical": sum(
                1
                for item in alerts
                if item.priority in (AlertPriority.CRITICAL, AlertPriority.P0)
                or item.severity == AlertSeverity.CRITICAL
            ),
        }

    def health(self) -> Dict[str, Any]:
        summary = self.summary()

        return {
            "status": "healthy",
            "enterprise_mode": True,
            "engine": "EnterpriseAlertEngine",
            "storage": "in_memory",
            "alerts_total": summary["total"],
            "critical_alerts": summary["critical"],
            "summary": summary,
        }

    def clear(self) -> None:
        with self._lock:
            self._alerts.clear()

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            alerts = list(self._alerts.values())

        return {
            "engine": "EnterpriseAlertEngine",
            "mode": "enterprise",
            "enterprise_mode": True,
            "summary": self.summary(),
            "alerts": [alert.to_dict(enum_values=True) for alert in alerts],
        }


def package_info() -> Dict[str, str]:
    return {
        "package": "ml.governance.alerts",
        "status": "active",
        "architecture": "enterprise_governance",
        "purpose": "alerting_and_observability",
    }


__all__ = [
    "AlertSeverity",
    "AlertPriority",
    "AlertStatus",
    "AlertCategory",
    "AlertNotFoundError",
    "EnterpriseAlert",
    "EnterpriseAlertEngine",
    "package_info",
]
"""
ml/monitoring/alerts.py

Enterprise-grade alerting system for ML platforms.

Features:
- Alert rules and policies
- Severity levels
- Cooldown and deduplication
- Escalation policy
- Pluggable notification channels
- Structured audit logs
- In-memory alert state
- Metric threshold evaluation
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, Sequence


logger = logging.getLogger(__name__)


class AlertingError(Exception):
    """Base alerting error."""


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"


class AlertOperator(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    NEQ = "neq"


class NotificationChannel(Protocol):
    def send(self, alert: "AlertEvent") -> None:
        ...


@dataclass(frozen=True)
class AlertRule:
    name: str
    metric_name: str
    operator: AlertOperator
    threshold: float
    severity: AlertSeverity = AlertSeverity.WARNING
    enabled: bool = True
    cooldown_seconds: int = 300
    description: str | None = None
    labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EscalationPolicy:
    enabled: bool = True
    critical_after_occurrences: int = 3
    escalation_window_seconds: int = 900


@dataclass(frozen=True)
class AlertManagerConfig:
    service_name: str = "ml-monitoring"
    environment: str = "dev"
    deduplicate: bool = True
    audit_enabled: bool = True
    escalation: EscalationPolicy = field(default_factory=EscalationPolicy)


@dataclass
class MetricPoint:
    name: str
    value: float
    timestamp: str | None = None
    labels: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class AlertEvent:
    alert_id: str
    fingerprint: str
    rule_name: str
    metric_name: str
    metric_value: float
    threshold: float
    operator: str
    severity: str
    status: str
    message: str
    created_at: str
    updated_at: str
    occurrences: int = 1
    labels: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AlertDispatchResult:
    alert: AlertEvent
    sent: bool
    suppressed: bool
    channels_notified: list[str] = field(default_factory=list)
    error: str | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_alert_id() -> str:
    return str(uuid.uuid4())


def fingerprint_for(rule: AlertRule, metric: MetricPoint) -> str:
    payload = {
        "rule": rule.name,
        "metric": metric.name,
        "labels": dict(sorted(metric.labels.items())),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def evaluate_operator(value: float, operator: AlertOperator, threshold: float) -> bool:
    if operator == AlertOperator.GT:
        return value > threshold
    if operator == AlertOperator.GTE:
        return value >= threshold
    if operator == AlertOperator.LT:
        return value < threshold
    if operator == AlertOperator.LTE:
        return value <= threshold
    if operator == AlertOperator.EQ:
        return value == threshold
    if operator == AlertOperator.NEQ:
        return value != threshold

    raise AlertingError(f"Unsupported operator: {operator}")


def build_alert_message(rule: AlertRule, metric: MetricPoint) -> str:
    return (
        f"Alert '{rule.name}' triggered: metric '{metric.name}' "
        f"value={metric.value} {rule.operator.value} threshold={rule.threshold}"
    )


class ConsoleNotificationChannel:
    def send(self, alert: AlertEvent) -> None:
        logger.warning(
            "alert.notification.console",
            extra={
                "alert": alert.to_dict(),
            },
        )


class WebhookNotificationChannel:
    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 5.0,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.headers = dict(headers or {})

    def send(self, alert: AlertEvent) -> None:
        try:
            import requests
        except ImportError as exc:
            raise AlertingError("requests is required for WebhookNotificationChannel.") from exc

        response = requests.post(
            self.url,
            json=alert.to_dict(),
            timeout=self.timeout_seconds,
            headers=self.headers,
        )
        response.raise_for_status()


class AlertManager:
    def __init__(
        self,
        *,
        rules: Sequence[AlertRule] | None = None,
        channels: Mapping[str, NotificationChannel] | None = None,
        config: AlertManagerConfig | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.rules = list(rules or [])
        self.channels = dict(channels or {"console": ConsoleNotificationChannel()})
        self.config = config or AlertManagerConfig()
        self.clock = clock or time.time
        self.active_alerts: dict[str, AlertEvent] = {}
        self.last_sent_at: dict[str, float] = {}
        self.occurrence_history: dict[str, list[float]] = {}

    def add_rule(self, rule: AlertRule) -> None:
        self.rules.append(rule)

    def add_channel(self, name: str, channel: NotificationChannel) -> None:
        self.channels[name] = channel

    def evaluate(self, metric: MetricPoint) -> list[AlertDispatchResult]:
        results: list[AlertDispatchResult] = []

        for rule in self.rules:
            if not rule.enabled or rule.metric_name != metric.name:
                continue

            if evaluate_operator(metric.value, rule.operator, rule.threshold):
                result = self.trigger(rule, metric)
                results.append(result)

        return results

    def evaluate_many(self, metrics: Sequence[MetricPoint]) -> list[AlertDispatchResult]:
        results: list[AlertDispatchResult] = []

        for metric in metrics:
            results.extend(self.evaluate(metric))

        return results

    def trigger(self, rule: AlertRule, metric: MetricPoint) -> AlertDispatchResult:
        now_ts = self.clock()
        now_iso = utc_now_iso()
        fingerprint = fingerprint_for(rule, metric)

        existing = self.active_alerts.get(fingerprint)

        if existing:
            existing.occurrences += 1
            existing.updated_at = now_iso
            existing.metric_value = metric.value
            existing.metadata = {
                **dict(existing.metadata),
                **dict(metric.metadata),
            }
            alert = existing
        else:
            alert = AlertEvent(
                alert_id=make_alert_id(),
                fingerprint=fingerprint,
                rule_name=rule.name,
                metric_name=metric.name,
                metric_value=metric.value,
                threshold=rule.threshold,
                operator=rule.operator.value,
                severity=rule.severity.value,
                status=AlertStatus.OPEN.value,
                message=build_alert_message(rule, metric),
                created_at=now_iso,
                updated_at=now_iso,
                labels={
                    **dict(rule.labels),
                    **dict(metric.labels),
                    "service": self.config.service_name,
                    "environment": self.config.environment,
                },
                metadata=dict(metric.metadata),
            )
            self.active_alerts[fingerprint] = alert

        self._record_occurrence(fingerprint, now_ts)
        self._maybe_escalate(alert, fingerprint, now_ts)

        if self._is_in_cooldown(fingerprint, rule.cooldown_seconds, now_ts):
            alert.status = AlertStatus.SUPPRESSED.value
            self._audit("alert.suppressed", alert)
            return AlertDispatchResult(alert=alert, sent=False, suppressed=True)

        channels_notified: list[str] = []

        try:
            for name, channel in self.channels.items():
                channel.send(alert)
                channels_notified.append(name)

            self.last_sent_at[fingerprint] = now_ts
            self._audit("alert.sent", alert)

            return AlertDispatchResult(
                alert=alert,
                sent=True,
                suppressed=False,
                channels_notified=channels_notified,
            )

        except Exception as exc:
            logger.exception("alert.dispatch.failed")
            return AlertDispatchResult(
                alert=alert,
                sent=False,
                suppressed=False,
                channels_notified=channels_notified,
                error=str(exc),
            )

    def acknowledge(self, fingerprint: str) -> AlertEvent:
        alert = self._get_alert(fingerprint)
        alert.status = AlertStatus.ACKNOWLEDGED.value
        alert.updated_at = utc_now_iso()
        self._audit("alert.acknowledged", alert)
        return alert

    def resolve(self, fingerprint: str) -> AlertEvent:
        alert = self._get_alert(fingerprint)
        alert.status = AlertStatus.RESOLVED.value
        alert.updated_at = utc_now_iso()
        self._audit("alert.resolved", alert)
        return alert

    def list_active(self) -> list[AlertEvent]:
        return [
            alert
            for alert in self.active_alerts.values()
            if alert.status in {
                AlertStatus.OPEN.value,
                AlertStatus.ACKNOWLEDGED.value,
                AlertStatus.SUPPRESSED.value,
            }
        ]

    def snapshot(self) -> dict[str, Any]:
        return {
            "service_name": self.config.service_name,
            "environment": self.config.environment,
            "rules": len(self.rules),
            "channels": list(self.channels.keys()),
            "active_alerts": len(self.list_active()),
            "total_alerts": len(self.active_alerts),
        }

    def _get_alert(self, fingerprint: str) -> AlertEvent:
        try:
            return self.active_alerts[fingerprint]
        except KeyError as exc:
            raise AlertingError(f"Alert not found: {fingerprint}") from exc

    def _is_in_cooldown(
        self,
        fingerprint: str,
        cooldown_seconds: int,
        now_ts: float,
    ) -> bool:
        last = self.last_sent_at.get(fingerprint)

        if last is None:
            return False

        return now_ts - last < cooldown_seconds

    def _record_occurrence(self, fingerprint: str, now_ts: float) -> None:
        history = self.occurrence_history.get(fingerprint, [])
        window = self.config.escalation.escalation_window_seconds
        history = [item for item in history if now_ts - item <= window]
        history.append(now_ts)
        self.occurrence_history[fingerprint] = history

    def _maybe_escalate(
        self,
        alert: AlertEvent,
        fingerprint: str,
        now_ts: float,
    ) -> None:
        policy = self.config.escalation

        if not policy.enabled:
            return

        occurrences = self.occurrence_history.get(fingerprint, [])

        if (
            len(occurrences) >= policy.critical_after_occurrences
            and alert.severity != AlertSeverity.CRITICAL.value
        ):
            alert.severity = AlertSeverity.CRITICAL.value
            alert.updated_at = utc_now_iso()
            alert.metadata = {
                **dict(alert.metadata),
                "escalated": True,
                "escalated_at": alert.updated_at,
                "occurrences_in_window": len(occurrences),
            }

    def _audit(self, event: str, alert: AlertEvent) -> None:
        if not self.config.audit_enabled:
            return

        logger.info(
            event,
            extra={
                "event": event,
                "timestamp": utc_now_iso(),
                "alert": alert.to_dict(),
            },
        )


def create_default_ml_alert_rules() -> list[AlertRule]:
    return [
        AlertRule(
            name="high_prediction_latency",
            metric_name="prediction_latency_ms",
            operator=AlertOperator.GTE,
            threshold=1000,
            severity=AlertSeverity.WARNING,
            description="Prediction latency is above expected threshold.",
        ),
        AlertRule(
            name="critical_prediction_latency",
            metric_name="prediction_latency_ms",
            operator=AlertOperator.GTE,
            threshold=3000,
            severity=AlertSeverity.CRITICAL,
            cooldown_seconds=120,
            description="Prediction latency is critically high.",
        ),
        AlertRule(
            name="high_error_rate",
            metric_name="error_rate",
            operator=AlertOperator.GTE,
            threshold=0.05,
            severity=AlertSeverity.ERROR,
            description="ML service error rate is above 5%.",
        ),
        AlertRule(
            name="model_drift_detected",
            metric_name="drift_score",
            operator=AlertOperator.GTE,
            threshold=0.30,
            severity=AlertSeverity.ERROR,
            description="Model drift score exceeded threshold.",
        ),
        AlertRule(
            name="low_prediction_confidence",
            metric_name="avg_prediction_confidence",
            operator=AlertOperator.LTE,
            threshold=0.55,
            severity=AlertSeverity.WARNING,
            description="Average prediction confidence is too low.",
        ),
    ]


def evaluate_metrics(
    metrics: Mapping[str, float],
    *,
    labels: Mapping[str, str] | None = None,
    manager: AlertManager | None = None,
) -> list[AlertDispatchResult]:
    alert_manager = manager or AlertManager(rules=create_default_ml_alert_rules())

    points = [
        MetricPoint(
            name=name,
            value=float(value),
            labels=labels or {},
        )
        for name, value in metrics.items()
    ]

    return alert_manager.evaluate_many(points)


__all__ = [
    "AlertDispatchResult",
    "AlertEvent",
    "AlertManager",
    "AlertManagerConfig",
    "AlertOperator",
    "AlertRule",
    "AlertSeverity",
    "AlertStatus",
    "AlertingError",
    "ConsoleNotificationChannel",
    "EscalationPolicy",
    "MetricPoint",
    "NotificationChannel",
    "WebhookNotificationChannel",
    "build_alert_message",
    "create_default_ml_alert_rules",
    "evaluate_metrics",
    "evaluate_operator",
    "fingerprint_for",
    "make_alert_id",
    "utc_now_iso",
]
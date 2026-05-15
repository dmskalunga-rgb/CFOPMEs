#!/usr/bin/env python3
"""
api/routes/anomaly.py

Enterprise-grade Anomaly API Router.

Objetivo:
- Expor endpoints REST para avaliação de anomalias e geração de triggers.
- Padronizar request/response com Pydantic, request context, autenticação/autorização e auditoria.
- Suportar avaliação single e batch.
- Integrar com ai_engine.anomaly_trigger quando disponível, mantendo fallback seguro rule-based.

Endpoints:
    GET  /health
    POST /evaluate
    POST /batch
    GET  /policy

Montagem sugerida:
    from fastapi import FastAPI
    from api.routes.anomaly import router

    app.include_router(router, prefix="/v1/anomaly", tags=["anomaly"])
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

try:
    from api.core.dependencies import RequestContext, audit_dependency, get_request_context, require_permission
except Exception:  # pragma: no cover
    RequestContext = Any  # type: ignore

    def get_request_context() -> Any:  # type: ignore
        return None

    def audit_dependency(action: str):  # type: ignore
        return lambda: None

    def require_permission(permission: str):  # type: ignore
        return lambda: None

try:
    from ai_engine.anomaly_trigger import (
        AnomalyEvent,
        AnomalyTriggerEngine,
        AnomalyEventParser,
        TriggerPolicy,
    )
except Exception:  # pragma: no cover
    AnomalyEvent = None  # type: ignore
    AnomalyTriggerEngine = None  # type: ignore
    AnomalyEventParser = None  # type: ignore
    TriggerPolicy = None  # type: ignore


router = APIRouter()
logger = logging.getLogger(__name__)
UTC = timezone.utc
ROUTER_VERSION = "1.0.0"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Decision(str, Enum):
    IGNORE = "ignore"
    MONITOR = "monitor"
    ALERT = "alert"
    ESCALATE = "escalate"
    INCIDENT = "incident"


class TriggerStatus(str, Enum):
    FIRED = "fired"
    SUPPRESSED = "suppressed"
    DEDUPED = "deduped"
    COOLDOWN = "cooldown"
    BELOW_THRESHOLD = "below_threshold"


class AnomalyPolicySchema(BaseModel):
    medium_threshold: float = Field(default=35, ge=0, le=100)
    high_threshold: float = Field(default=65, ge=0, le=100)
    critical_threshold: float = Field(default=85, ge=0, le=100)
    cooldown_minutes: int = Field(default=30, ge=0, le=1440)
    dedupe_window_minutes: int = Field(default=60, ge=1, le=10080)
    aggregation_window_minutes: int = Field(default=15, ge=1, le=1440)
    burst_count_threshold: int = Field(default=5, ge=1, le=100000)
    dynamic_threshold_enabled: bool = True
    suppress_low_severity: bool = False
    hash_entity_ids: bool = True


class AnomalyEvaluateRequest(BaseModel):
    event_id: str = Field(default_factory=lambda: f"anom_evt_{uuid.uuid4().hex[:16]}")
    entity_id: str = Field(min_length=1, max_length=256)
    timestamp: str = Field(default_factory=lambda: datetime.now(tz=UTC).isoformat())
    score: float = Field(ge=0, le=100)
    metric_name: Optional[str] = Field(default=None, max_length=128)
    metric_value: Optional[float] = None
    baseline_value: Optional[float] = None
    zscore: Optional[float] = None
    source: Optional[str] = Field(default=None, max_length=128)
    domain: Optional[str] = Field(default=None, max_length=128)
    category: Optional[str] = Field(default=None, max_length=128)
    signal: Optional[str] = Field(default=None, max_length=128)
    severity_hint: Optional[Severity] = None
    status: Optional[str] = Field(default=None, max_length=64)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    policy: Optional[AnomalyPolicySchema] = None

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        parse_datetime(value)
        return value


class AnomalyBatchRequest(BaseModel):
    events: List[AnomalyEvaluateRequest] = Field(min_length=1, max_length=10000)
    policy: Optional[AnomalyPolicySchema] = None


class TriggerResponse(BaseModel):
    trigger_id: str
    event_id: str
    entity_id_hash: str
    timestamp: str
    status: str
    decision: str
    severity: str
    route: str
    score: float
    threshold_used: float
    reasons: List[str]
    recommended_actions: List[str]
    dedupe_key: str
    correlation_key: str
    payload: Dict[str, Any]
    latency_ms: float


class BatchSummaryResponse(BaseModel):
    total_events: int
    total_triggers: int
    fired: int
    suppressed: int
    deduped: int
    cooldown: int
    below_threshold: int
    critical: int
    high: int
    medium: int
    low: int
    avg_latency_ms: float


class BatchTriggerResponse(BaseModel):
    summary: BatchSummaryResponse
    triggers: List[TriggerResponse]


class HealthResponse(BaseModel):
    status: str
    router: str
    version: str
    engine_available: bool
    timestamp: str


@router.get("/health", response_model=HealthResponse)
async def anomaly_health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        router="anomaly",
        version=ROUTER_VERSION,
        engine_available=AnomalyTriggerEngine is not None,
        timestamp=datetime.now(tz=UTC).isoformat(),
    )


@router.get("/policy", response_model=AnomalyPolicySchema)
async def get_default_policy(_: Any = Depends(require_permission("alerts:read"))) -> AnomalyPolicySchema:
    return AnomalyPolicySchema()


@router.post(
    "/evaluate",
    response_model=TriggerResponse,
    dependencies=[Depends(audit_dependency("anomaly:evaluate"))],
)
async def evaluate_anomaly(
    payload: AnomalyEvaluateRequest,
    ctx: Any = Depends(get_request_context),
    _: Any = Depends(require_permission("alerts:write")),
) -> TriggerResponse:
    started = time.perf_counter()
    try:
        result = _evaluate_single(payload, payload.policy)
        response = _to_trigger_response(result, elapsed_ms(started))
        logger.info(
            "anomaly_evaluated",
            extra={
                "request_id": getattr(ctx, "request_id", None),
                "event_id": payload.event_id,
                "severity": response.severity,
                "decision": response.decision,
            },
        )
        return response
    except Exception as exc:  # noqa: BLE001
        logger.exception("anomaly_evaluation_failed", extra={"event_id": payload.event_id})
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post(
    "/batch",
    response_model=BatchTriggerResponse,
    dependencies=[Depends(audit_dependency("anomaly:batch"))],
)
async def evaluate_anomaly_batch(
    payload: AnomalyBatchRequest,
    ctx: Any = Depends(get_request_context),
    _: Any = Depends(require_permission("alerts:write")),
) -> BatchTriggerResponse:
    started = time.perf_counter()
    try:
        triggers = [_to_trigger_response(_evaluate_single(event, event.policy or payload.policy), 0.0) for event in payload.events]
        total_latency = elapsed_ms(started)
        avg_latency = round(total_latency / max(len(triggers), 1), 4)
        triggers = [item.model_copy(update={"latency_ms": avg_latency}) for item in triggers]
        summary = _batch_summary(triggers, avg_latency)
        logger.info(
            "anomaly_batch_evaluated",
            extra={"request_id": getattr(ctx, "request_id", None), "events": len(payload.events)},
        )
        return BatchTriggerResponse(summary=summary, triggers=triggers)
    except Exception as exc:  # noqa: BLE001
        logger.exception("anomaly_batch_failed")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _evaluate_single(payload: AnomalyEvaluateRequest, policy_schema: Optional[AnomalyPolicySchema]) -> Dict[str, Any]:
    if AnomalyTriggerEngine is not None and AnomalyEventParser is not None and TriggerPolicy is not None:
        policy = _to_engine_policy(policy_schema or AnomalyPolicySchema())
        row = _request_to_engine_row(payload)
        event = AnomalyEventParser.parse(row, policy)
        engine = AnomalyTriggerEngine(policy)
        result = engine.evaluate_one(event)
        return result.to_dict()
    return _fallback_evaluate(payload, policy_schema or AnomalyPolicySchema())


def _to_engine_policy(schema: AnomalyPolicySchema) -> Any:
    return TriggerPolicy(
        medium_threshold=Decimal(str(schema.medium_threshold)),
        high_threshold=Decimal(str(schema.high_threshold)),
        critical_threshold=Decimal(str(schema.critical_threshold)),
        cooldown_minutes=schema.cooldown_minutes,
        dedupe_window_minutes=schema.dedupe_window_minutes,
        aggregation_window_minutes=schema.aggregation_window_minutes,
        burst_count_threshold=schema.burst_count_threshold,
        dynamic_threshold_enabled=schema.dynamic_threshold_enabled,
        suppress_low_severity=schema.suppress_low_severity,
        hash_entity_ids=schema.hash_entity_ids,
    )


def _request_to_engine_row(payload: AnomalyEvaluateRequest) -> Dict[str, Any]:
    return {
        "event_id": payload.event_id,
        "entity_id": payload.entity_id,
        "timestamp": payload.timestamp,
        "score": payload.score,
        "metric_name": payload.metric_name,
        "metric_value": payload.metric_value,
        "baseline_value": payload.baseline_value,
        "zscore": payload.zscore,
        "source": payload.source,
        "domain": payload.domain,
        "category": payload.category,
        "signal": payload.signal,
        "severity_hint": payload.severity_hint.value if payload.severity_hint else None,
        "status": payload.status,
        "metadata": json.dumps(payload.metadata, ensure_ascii=False),
    }


def _fallback_evaluate(payload: AnomalyEvaluateRequest, policy: AnomalyPolicySchema) -> Dict[str, Any]:
    score = Decimal(str(payload.score))
    threshold = Decimal(str(policy.medium_threshold))
    severity = _severity_from_score(score, policy)
    status_value = TriggerStatus.FIRED.value if score >= threshold else TriggerStatus.BELOW_THRESHOLD.value
    decision = _decision_from_severity(severity) if status_value == TriggerStatus.FIRED.value else Decision.IGNORE.value
    route = _route_from_domain(payload.domain)
    entity_hash = hash_identifier(payload.entity_id)
    dedupe_key = hash_identifier("|".join([entity_hash, payload.domain or "", payload.signal or payload.metric_name or ""]))
    correlation_key = hash_identifier("|".join([entity_hash, payload.domain or "unknown", payload.source or "unknown"]))
    reasons = ["score_threshold_evaluated"]
    if payload.zscore is not None:
        reasons.append(f"zscore={payload.zscore}")
    if payload.metric_value is not None and payload.baseline_value not in {None, 0}:
        variance = ((Decimal(str(payload.metric_value)) - Decimal(str(payload.baseline_value))) / abs(Decimal(str(payload.baseline_value)))) * Decimal("100")
        reasons.append(f"variance_percent={decimal_str(variance)}")
    return {
        "trigger_id": "trg_" + uuid.uuid4().hex[:20],
        "event_id": payload.event_id,
        "entity_id_hash": entity_hash,
        "timestamp": parse_datetime(payload.timestamp).isoformat(),
        "status": status_value,
        "decision": decision,
        "severity": severity,
        "route": route,
        "score": decimal_str(score),
        "threshold_used": decimal_str(threshold),
        "reasons": reasons,
        "recommended_actions": _actions(decision),
        "dedupe_key": dedupe_key,
        "correlation_key": correlation_key,
        "payload": {
            "event_id": payload.event_id,
            "entity_id_hash": entity_hash,
            "domain": payload.domain,
            "category": payload.category,
            "signal": payload.signal,
            "metric_name": payload.metric_name,
            "metadata": payload.metadata,
            "engine": "fallback_rule_based",
        },
    }


def _to_trigger_response(result: Dict[str, Any], latency_ms_value: float) -> TriggerResponse:
    return TriggerResponse(
        trigger_id=str(result.get("trigger_id")),
        event_id=str(result.get("event_id")),
        entity_id_hash=str(result.get("entity_id_hash")),
        timestamp=str(result.get("timestamp")),
        status=str(result.get("status")),
        decision=str(result.get("decision")),
        severity=str(result.get("severity")),
        route=str(result.get("route")),
        score=float(result.get("score", 0)),
        threshold_used=float(result.get("threshold_used", 0)),
        reasons=list(result.get("reasons") or []),
        recommended_actions=list(result.get("recommended_actions") or []),
        dedupe_key=str(result.get("dedupe_key")),
        correlation_key=str(result.get("correlation_key")),
        payload=dict(result.get("payload") or {}),
        latency_ms=latency_ms_value,
    )


def _batch_summary(triggers: Sequence[TriggerResponse], avg_latency_ms: float) -> BatchSummaryResponse:
    return BatchSummaryResponse(
        total_events=len(triggers),
        total_triggers=len(triggers),
        fired=sum(1 for item in triggers if item.status == TriggerStatus.FIRED.value),
        suppressed=sum(1 for item in triggers if item.status == TriggerStatus.SUPPRESSED.value),
        deduped=sum(1 for item in triggers if item.status == TriggerStatus.DEDUPED.value),
        cooldown=sum(1 for item in triggers if item.status == TriggerStatus.COOLDOWN.value),
        below_threshold=sum(1 for item in triggers if item.status == TriggerStatus.BELOW_THRESHOLD.value),
        critical=sum(1 for item in triggers if item.severity == Severity.CRITICAL.value),
        high=sum(1 for item in triggers if item.severity == Severity.HIGH.value),
        medium=sum(1 for item in triggers if item.severity == Severity.MEDIUM.value),
        low=sum(1 for item in triggers if item.severity == Severity.LOW.value),
        avg_latency_ms=avg_latency_ms,
    )


def _severity_from_score(score: Decimal, policy: AnomalyPolicySchema) -> str:
    if score >= Decimal(str(policy.critical_threshold)):
        return Severity.CRITICAL.value
    if score >= Decimal(str(policy.high_threshold)):
        return Severity.HIGH.value
    if score >= Decimal(str(policy.medium_threshold)):
        return Severity.MEDIUM.value
    if score > 0:
        return Severity.LOW.value
    return Severity.INFO.value


def _decision_from_severity(severity: str) -> str:
    if severity == Severity.CRITICAL.value:
        return Decision.INCIDENT.value
    if severity == Severity.HIGH.value:
        return Decision.ESCALATE.value
    if severity == Severity.MEDIUM.value:
        return Decision.ALERT.value
    if severity == Severity.LOW.value:
        return Decision.MONITOR.value
    return Decision.IGNORE.value


def _route_from_domain(domain: Optional[str]) -> str:
    value = (domain or "ops").lower()
    if value in {"fraud", "payments", "transaction"}:
        return "fraud"
    if value in {"security", "ueba", "iam", "soc"}:
        return "security"
    if value in {"finance", "liquidity", "cashflow", "revenue", "payroll"}:
        return "finance"
    if value in {"risk", "credit", "compliance"}:
        return "risk"
    if value in {"data", "ml", "ai", "quality"}:
        return "data"
    return "ops"


def _actions(decision: str) -> List[str]:
    if decision == Decision.INCIDENT.value:
        return ["open_incident", "page_on_call", "create_case", "executive_visibility"]
    if decision == Decision.ESCALATE.value:
        return ["create_case", "notify_owner_team", "manual_review"]
    if decision == Decision.ALERT.value:
        return ["send_alert", "add_to_review_queue", "monitor_next_events"]
    if decision == Decision.MONITOR.value:
        return ["record_signal", "monitor_trend"]
    return ["no_action"]


def parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def hash_identifier(value: str, length: int = 32) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def decimal_str(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 4)

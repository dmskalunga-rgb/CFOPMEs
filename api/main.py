#!/usr/bin/env python3
"""
api/main.py

Enterprise-grade API entrypoint.

Objetivo:
- Servir como ponto principal da API HTTP do sistema.
- Expor endpoints de health, readiness, metadata, inferência, alertas e scoring genérico.
- Aplicar padrões enterprise: request-id, logging estruturado, CORS, tratamento global de erros,
  validação Pydantic, métricas básicas, versionamento, segurança por API Key opcional e lifecycle.

Execução:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

Variáveis de ambiente suportadas:
    API_NAME=Enterprise AI API
    API_VERSION=1.0.0
    API_ENV=development|staging|production
    API_KEY=opcional
    API_CORS_ORIGINS=*
    API_LOG_LEVEL=INFO
    API_ENABLE_DOCS=true|false

Endpoints principais:
    GET  /health
    GET  /ready
    GET  /metadata
    GET  /metrics
    POST /v1/inference/echo
    POST /v1/inference/rule-score
    POST /v1/alerts/anomaly
    POST /v1/alerts/financial
    POST /v1/fraud/realtime
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional

try:
    from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "Dependências ausentes. Instale com: pip install fastapi uvicorn pydantic"
    ) from exc


APP_NAME = os.getenv("API_NAME", "Enterprise AI API")
APP_VERSION = os.getenv("API_VERSION", "1.0.0")
APP_ENV = os.getenv("API_ENV", "development")
API_KEY = os.getenv("API_KEY")
LOG_LEVEL = os.getenv("API_LOG_LEVEL", "INFO").upper()
ENABLE_DOCS = os.getenv("API_ENABLE_DOCS", "true").lower() in {"1", "true", "yes", "sim"}
CORS_ORIGINS = [item.strip() for item in os.getenv("API_CORS_ORIGINS", "*").split(",") if item.strip()]
STARTED_AT = datetime.now(tz=timezone.utc)


class ApiStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    ERROR = "error"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Decision(str, Enum):
    ALLOW = "allow"
    MONITOR = "monitor"
    CHALLENGE = "challenge"
    REVIEW = "review"
    ESCALATE = "escalate"


@dataclass
class RuntimeMetrics:
    request_count: int = 0
    error_count: int = 0
    total_latency_ms: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        if self.request_count == 0:
            return 0.0
        return round(self.total_latency_ms / self.request_count, 4)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_count": self.request_count,
            "error_count": self.error_count,
            "avg_latency_ms": self.avg_latency_ms,
            "uptime_seconds": round((datetime.now(tz=timezone.utc) - STARTED_AT).total_seconds(), 2),
        }


runtime_metrics = RuntimeMetrics()
logger = logging.getLogger("api.main")


class HealthResponse(BaseModel):
    status: ApiStatus
    app: str
    version: str
    environment: str
    timestamp: str
    uptime_seconds: float


class MetadataResponse(BaseModel):
    app: str
    version: str
    environment: str
    docs_enabled: bool
    started_at: str
    routes: List[str]


class InferenceRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: f"req_{uuid.uuid4().hex[:16]}")
    entity_id: Optional[str] = None
    operation: str = "predict"
    payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InferenceResponse(BaseModel):
    request_id: str
    status: str
    handler: str
    result: Dict[str, Any]
    latency_ms: float
    warnings: List[str] = Field(default_factory=list)


class MetricEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:16]}")
    entity_id: str
    score: float = Field(ge=0, le=100)
    domain: Optional[str] = None
    category: Optional[str] = None
    signal: Optional[str] = None
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    baseline_value: Optional[float] = None
    timestamp: str = Field(default_factory=utc_now_iso)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FinancialMetricRequest(BaseModel):
    metric_id: str = Field(default_factory=lambda: f"met_{uuid.uuid4().hex[:16]}")
    entity_id: str
    metric_name: str
    metric_value: float
    baseline_value: Optional[float] = None
    target_value: Optional[float] = None
    budget_value: Optional[float] = None
    currency: str = "BRL"
    period: Optional[str] = None
    domain: Optional[str] = "finance"
    category: Optional[str] = None
    timestamp: str = Field(default_factory=utc_now_iso)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FraudRealtimeRequest(BaseModel):
    event_id: str = Field(default_factory=lambda: f"frd_evt_{uuid.uuid4().hex[:16]}")
    entity_id: str
    event_type: str
    amount: float = 0.0
    currency: str = "BRL"
    direction: str = "debit"
    channel: Optional[str] = None
    ip_address: Optional[str] = None
    device_id: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    counterparty: Optional[str] = None
    merchant_id: Optional[str] = None
    reference_id: Optional[str] = None
    status: Optional[str] = None
    success: Optional[bool] = None
    timestamp: str = Field(default_factory=utc_now_iso)


class AlertResponse(BaseModel):
    id: str
    status: str
    decision: str
    severity: str
    route: str
    score: float
    reasons: List[str]
    recommended_actions: List[str]
    payload: Dict[str, Any]


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info(
        "API starting",
        extra={"app": APP_NAME, "version": APP_VERSION, "environment": APP_ENV},
    )
    app.state.started_at = STARTED_AT
    app.state.ready = True
    yield
    app.state.ready = False
    logger.info("API shutting down")


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="Enterprise AI/API gateway for inference, alerts, scoring and realtime fraud decisions.",
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next: Callable[..., Any]) -> Response:
    request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"
    started = time.perf_counter()
    request.state.request_id = request_id

    try:
        response = await call_next(request)
    except Exception as exc:  # pragma: no cover
        runtime_metrics.error_count += 1
        logger.exception("Unhandled request error", extra={"request_id": request_id})
        response = JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_payload("internal_server_error", str(exc), request_id),
        )

    latency_ms = round((time.perf_counter() - started) * 1000, 4)
    runtime_metrics.request_count += 1
    runtime_metrics.total_latency_ms += latency_ms
    response.headers["x-request-id"] = request_id
    response.headers["x-response-time-ms"] = str(latency_ms)

    logger.info(
        "request_completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
        },
    )
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    runtime_metrics.error_count += 1
    request_id = getattr(request.state, "request_id", None) or f"req_{uuid.uuid4().hex}"
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload("http_error", str(exc.detail), request_id),
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    runtime_metrics.error_count += 1
    request_id = getattr(request.state, "request_id", None) or f"req_{uuid.uuid4().hex}"
    logger.exception("global_exception", extra={"request_id": request_id})
    message = str(exc) if APP_ENV != "production" else "Internal server error"
    payload = error_payload("internal_server_error", message, request_id)
    if APP_ENV != "production":
        payload["traceback"] = traceback.format_exc()
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=payload)


async def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    if not API_KEY:
        return
    if x_api_key != API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(
        status=ApiStatus.OK,
        app=APP_NAME,
        version=APP_VERSION,
        environment=APP_ENV,
        timestamp=utc_now_iso(),
        uptime_seconds=round((datetime.now(tz=timezone.utc) - STARTED_AT).total_seconds(), 2),
    )


@app.get("/ready", response_model=HealthResponse, tags=["system"])
async def ready(request: Request) -> HealthResponse:
    is_ready = bool(getattr(request.app.state, "ready", False))
    return HealthResponse(
        status=ApiStatus.OK if is_ready else ApiStatus.DEGRADED,
        app=APP_NAME,
        version=APP_VERSION,
        environment=APP_ENV,
        timestamp=utc_now_iso(),
        uptime_seconds=round((datetime.now(tz=timezone.utc) - STARTED_AT).total_seconds(), 2),
    )


@app.get("/metadata", response_model=MetadataResponse, tags=["system"])
async def metadata() -> MetadataResponse:
    routes = sorted({route.path for route in app.routes})
    return MetadataResponse(
        app=APP_NAME,
        version=APP_VERSION,
        environment=APP_ENV,
        docs_enabled=ENABLE_DOCS,
        started_at=STARTED_AT.isoformat(),
        routes=routes,
    )


@app.get("/metrics", tags=["system"])
async def metrics(_: None = Depends(require_api_key)) -> Dict[str, Any]:
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "environment": APP_ENV,
        "runtime": runtime_metrics.to_dict(),
    }


@app.post("/v1/inference/echo", response_model=InferenceResponse, tags=["inference"])
async def inference_echo(payload: InferenceRequest, _: None = Depends(require_api_key)) -> InferenceResponse:
    started = time.perf_counter()
    result = {
        "echo": payload.payload,
        "operation": payload.operation,
        "metadata": payload.metadata,
        "entity_id_hash": hash_identifier(payload.entity_id) if payload.entity_id else None,
    }
    return InferenceResponse(
        request_id=payload.request_id,
        status="success",
        handler="echo",
        result=result,
        latency_ms=elapsed_ms(started),
    )


@app.post("/v1/inference/rule-score", response_model=InferenceResponse, tags=["inference"])
async def inference_rule_score(payload: InferenceRequest, _: None = Depends(require_api_key)) -> InferenceResponse:
    started = time.perf_counter()
    score = Decimal("0")
    reasons: List[str] = []

    for key, value in payload.payload.items():
        try:
            number = Decimal(str(value))
        except Exception:
            continue
        if number > 0:
            score += min(number, Decimal("100")) / Decimal("10")
            reasons.append(f"positive_numeric_feature:{key}")

    score = clamp_decimal(score, Decimal("0"), Decimal("100"))
    result = {
        "score": decimal_to_float(score),
        "risk_level": risk_level(score),
        "reasons": reasons or ["no_numeric_signal"],
    }
    return InferenceResponse(
        request_id=payload.request_id,
        status="success",
        handler="rule_score",
        result=result,
        latency_ms=elapsed_ms(started),
    )


@app.post("/v1/alerts/anomaly", response_model=AlertResponse, tags=["alerts"])
async def anomaly_alert(event: MetricEvent, _: None = Depends(require_api_key)) -> AlertResponse:
    score = Decimal(str(event.score))
    severity = severity_from_score(score)
    decision = decision_from_severity(severity)
    route = route_from_domain(event.domain)
    reasons = ["score_threshold_evaluated"]
    if event.metric_value is not None and event.baseline_value not in {None, 0}:
        variance = ((Decimal(str(event.metric_value)) - Decimal(str(event.baseline_value))) / abs(Decimal(str(event.baseline_value)))) * Decimal("100")
        reasons.append(f"variance_percent={decimal_str(variance)}")

    return AlertResponse(
        id=f"anom_{uuid.uuid4().hex[:20]}",
        status="fired" if score >= Decimal("35") else "below_threshold",
        decision=decision,
        severity=severity,
        route=route,
        score=decimal_to_float(score),
        reasons=reasons,
        recommended_actions=actions_for_decision(decision),
        payload={
            "event_id": event.event_id,
            "entity_id_hash": hash_identifier(event.entity_id),
            "domain": event.domain,
            "category": event.category,
            "signal": event.signal,
            "metric_name": event.metric_name,
            "timestamp": event.timestamp,
            "metadata": event.metadata,
        },
    )


@app.post("/v1/alerts/financial", response_model=AlertResponse, tags=["alerts"])
async def financial_alert(metric: FinancialMetricRequest, _: None = Depends(require_api_key)) -> AlertResponse:
    score, reasons = financial_metric_score(metric)
    severity = severity_from_score(score)
    decision = decision_from_severity(severity)
    route = financial_route(metric.metric_name, metric.domain)

    return AlertResponse(
        id=f"fin_{uuid.uuid4().hex[:20]}",
        status="fired" if score >= Decimal("35") else "below_threshold",
        decision=decision,
        severity=severity,
        route=route,
        score=decimal_to_float(score),
        reasons=reasons,
        recommended_actions=financial_actions(metric.metric_name, decision),
        payload={
            "metric_id": metric.metric_id,
            "entity_id_hash": hash_identifier(metric.entity_id),
            "period": metric.period,
            "metric_name": metric.metric_name,
            "metric_value": metric.metric_value,
            "currency": metric.currency,
            "timestamp": metric.timestamp,
            "metadata": metric.metadata,
        },
    )


@app.post("/v1/fraud/realtime", response_model=AlertResponse, tags=["fraud"])
async def fraud_realtime(event: FraudRealtimeRequest, _: None = Depends(require_api_key)) -> AlertResponse:
    score = Decimal("0")
    reasons: List[str] = []

    amount = Decimal(str(abs(event.amount)))
    if amount >= Decimal("5000"):
        score += Decimal("25")
        reasons.append("high_amount")
    if event.status and event.status.lower() in {"declined", "failed", "chargeback", "refund"}:
        score += Decimal("25")
        reasons.append("risky_status")
    if event.ip_address and is_private_or_invalid_ip(event.ip_address):
        score += Decimal("6")
        reasons.append("private_or_invalid_ip")
    if event.country and event.country.upper() not in {"BR", "BRA", "BRASIL", "BRAZIL"}:
        score += Decimal("15")
        reasons.append("foreign_country")
    if event.device_id is None:
        score += Decimal("8")
        reasons.append("missing_device_id")

    score = clamp_decimal(score, Decimal("0"), Decimal("100"))
    severity = severity_from_score(score)
    decision = fraud_decision_from_severity(severity)

    return AlertResponse(
        id=f"frt_{uuid.uuid4().hex[:20]}",
        status="fired" if score >= Decimal("35") else "below_threshold",
        decision=decision,
        severity=severity,
        route="fraud",
        score=decimal_to_float(score),
        reasons=reasons or ["baseline_normal"],
        recommended_actions=fraud_actions(decision),
        payload={
            "event_id": event.event_id,
            "entity_id_hash": hash_identifier(event.entity_id),
            "event_type": event.event_type,
            "amount": event.amount,
            "currency": event.currency,
            "channel": event.channel,
            "timestamp": event.timestamp,
        },
    )


def financial_metric_score(metric: FinancialMetricRequest) -> tuple[Decimal, List[str]]:
    name = metric.metric_name.strip().lower().replace("-", "_").replace(" ", "_")
    value = Decimal(str(metric.metric_value))
    score = Decimal("0")
    reasons: List[str] = []

    thresholds = {
        "cash_balance": Decimal("10000"),
        "closing_balance": Decimal("10000"),
        "current_ratio": Decimal("1.20"),
        "runway_periods": Decimal("3"),
        "gross_margin_percent": Decimal("20"),
        "net_margin_percent": Decimal("5"),
    }

    if name in {"cash_balance", "closing_balance"} and value < thresholds[name]:
        score = ((thresholds[name] - value) / thresholds[name]) * Decimal("100")
        reasons.append("cash_balance_below_minimum")
    elif name == "current_ratio" and value < thresholds[name]:
        score = ((thresholds[name] - value) / thresholds[name]) * Decimal("100")
        reasons.append("current_ratio_below_minimum")
    elif name == "runway_periods" and value < thresholds[name]:
        score = ((thresholds[name] - value) / thresholds[name]) * Decimal("100")
        reasons.append("runway_below_minimum")
    elif name in {"gross_margin_percent", "net_margin_percent"} and value < thresholds[name]:
        score = ((thresholds[name] - value) / max(thresholds[name], Decimal("1"))) * Decimal("100")
        reasons.append(f"{name}_below_minimum")
    elif name in {"budget_variance_percent", "cost_variance_percent"} and abs(value) > Decimal("10"):
        score = min(abs(value), Decimal("100"))
        reasons.append(f"{name}_above_limit")
    elif metric.budget_value not in {None, 0}:
        budget = Decimal(str(metric.budget_value))
        variance = ((value - budget) / abs(budget)) * Decimal("100")
        if abs(variance) >= Decimal("10"):
            score = min(abs(variance), Decimal("100"))
            reasons.append("budget_variance_above_limit")

    score = clamp_decimal(score, Decimal("0"), Decimal("100"))
    return score, reasons or ["metric_within_policy"]


def configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 4)


def hash_identifier(value: Optional[str], length: int = 32) -> Optional[str]:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def clamp_decimal(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(value, high))


def decimal_str(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def decimal_to_float(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def risk_level(score: Decimal) -> str:
    if score >= Decimal("85"):
        return RiskLevel.CRITICAL.value
    if score >= Decimal("65"):
        return RiskLevel.HIGH.value
    if score >= Decimal("35"):
        return RiskLevel.MEDIUM.value
    return RiskLevel.LOW.value


def severity_from_score(score: Decimal) -> str:
    if score >= Decimal("85"):
        return "critical"
    if score >= Decimal("65"):
        return "high"
    if score >= Decimal("35"):
        return "medium"
    if score > 0:
        return "low"
    return "info"


def decision_from_severity(severity: str) -> str:
    if severity == "critical":
        return Decision.ESCALATE.value
    if severity == "high":
        return Decision.REVIEW.value
    if severity == "medium":
        return Decision.CHALLENGE.value
    if severity == "low":
        return Decision.MONITOR.value
    return Decision.ALLOW.value


def fraud_decision_from_severity(severity: str) -> str:
    if severity == "critical":
        return "decline_candidate"
    if severity == "high":
        return "review"
    if severity == "medium":
        return "challenge"
    if severity == "low":
        return "monitor"
    return "approve"


def route_from_domain(domain: Optional[str]) -> str:
    value = (domain or "ops").lower()
    if value in {"fraud", "payments", "transaction"}:
        return "fraud"
    if value in {"security", "ueba", "iam", "soc"}:
        return "security"
    if value in {"finance", "cashflow", "liquidity", "revenue", "payroll"}:
        return "finance"
    if value in {"risk", "credit", "compliance"}:
        return "risk"
    return "ops"


def financial_route(metric_name: str, domain: Optional[str]) -> str:
    name = metric_name.lower()
    if name in {"cash_balance", "closing_balance", "runway_periods", "liquidity_gap", "current_ratio"}:
        return "treasury"
    if name in {"payroll_cost"} or (domain or "").lower() == "payroll":
        return "payroll"
    if name in {"debt_to_equity", "coverage_ratio"}:
        return "risk"
    return "fpna"


def actions_for_decision(decision: str) -> List[str]:
    if decision == Decision.ESCALATE.value:
        return ["open_incident", "notify_owner_team", "manual_review"]
    if decision == Decision.REVIEW.value:
        return ["create_case", "manual_review", "increase_monitoring"]
    if decision == Decision.CHALLENGE.value:
        return ["send_alert", "review_queue"]
    if decision == Decision.MONITOR.value:
        return ["monitor_trend"]
    return ["no_action"]


def financial_actions(metric_name: str, decision: str) -> List[str]:
    name = metric_name.lower()
    if decision == Decision.ALLOW.value:
        return ["no_action"]
    if name in {"cash_balance", "closing_balance", "runway_periods", "liquidity_gap"}:
        return ["review_cash_position", "accelerate_collections", "prioritize_payments"]
    if name in {"gross_margin_percent", "net_margin_percent"}:
        return ["review_pricing", "analyze_cost_structure", "prepare_margin_recovery_plan"]
    if name in {"budget_variance_percent", "cost_variance_percent", "payroll_cost"}:
        return ["review_budget_variance", "identify_cost_drivers"]
    return ["manual_financial_review"]


def fraud_actions(decision: str) -> List[str]:
    if decision == "decline_candidate":
        return ["hold_or_decline_candidate", "manual_fraud_review", "step_up_authentication"]
    if decision == "review":
        return ["manual_review", "enhanced_monitoring"]
    if decision == "challenge":
        return ["challenge_mfa", "monitor_next_events"]
    if decision == "monitor":
        return ["record_signal", "monitor_velocity"]
    return ["approve"]


def is_private_or_invalid_ip(value: str) -> bool:
    try:
        import ipaddress

        return ipaddress.ip_address(value).is_private
    except ValueError:
        return True


def error_payload(code: str, message: str, request_id: str) -> Dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "timestamp": utc_now_iso(),
        }
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=APP_ENV == "development")

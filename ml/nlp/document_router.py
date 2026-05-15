"""
ml/pipelines/document_router.py

Enterprise-grade document routing pipeline.

Responsabilidades:
- Receber documentos/textos/metadados
- Normalizar e validar entrada
- Extrair sinais do documento
- Classificar tipo, prioridade e destino
- Aplicar regras determinísticas e modelo ML opcional
- Roteamento para filas, departamentos, handlers ou workflows
- Auditar decisões e gerar explicabilidade básica
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

logger = logging.getLogger(__name__)


class DocumentRouterError(Exception):
    """Erro base do roteador de documentos."""


class DocumentValidationError(DocumentRouterError):
    """Erro de validação de documento."""


class RouteDecision(str, Enum):
    ACCEPT = "accept"
    REVIEW = "review"
    REJECT = "reject"
    ESCALATE = "escalate"


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class DocumentType(str, Enum):
    UNKNOWN = "unknown"
    INVOICE = "invoice"
    RECEIPT = "receipt"
    CONTRACT = "contract"
    REPORT = "report"
    SUPPORT_TICKET = "support_ticket"
    LEGAL_NOTICE = "legal_notice"
    IDENTITY_DOCUMENT = "identity_document"
    FINANCIAL_STATEMENT = "financial_statement"


class RouterModelProtocol(Protocol):
    def predict(self, data: Any) -> Any:
        ...


@dataclass(frozen=True)
class DocumentInput:
    content: str
    document_id: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    source: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteRule:
    name: str
    destination: str
    document_type: DocumentType | str = DocumentType.UNKNOWN
    priority: Priority | str = Priority.NORMAL
    decision: RouteDecision | str = RouteDecision.ACCEPT
    keywords: tuple[str, ...] = ()
    regex_patterns: tuple[str, ...] = ()
    required_metadata: Mapping[str, Any] = field(default_factory=dict)
    score: float = 1.0


@dataclass(frozen=True)
class DocumentRouterConfig:
    pipeline_name: str = "document_router"
    environment: str = "dev"
    model_name: str = "document_router_model"
    model_version: str = "unknown"
    min_content_length: int = 3
    max_content_length: int = 2_000_000
    default_destination: str = "manual_review"
    default_priority: Priority = Priority.NORMAL
    default_decision: RouteDecision = RouteDecision.REVIEW
    confidence_threshold: float = 0.65
    require_tenant_id: bool = False
    enable_rules: bool = True
    enable_model: bool = True
    enable_audit: bool = True
    rules: tuple[RouteRule, ...] = ()


@dataclass
class DocumentSignals:
    document_hash: str
    content_length: int
    word_count: int
    line_count: int
    has_email: bool
    has_phone: bool
    has_currency: bool
    has_dates: bool
    language_hint: str | None = None
    keyword_hits: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RouteResult:
    request_id: str
    document_id: str
    status: str
    decision: str
    destination: str
    document_type: str
    priority: str
    confidence: float
    duration_ms: int
    model_name: str
    model_version: str
    reasons: tuple[str, ...] = ()
    signals: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "document_id": self.document_id,
            "status": self.status,
            "decision": self.decision,
            "destination": self.destination,
            "document_type": self.document_type,
            "priority": self.priority,
            "confidence": self.confidence,
            "duration_ms": self.duration_ms,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "reasons": list(self.reasons),
            "signals": dict(self.signals),
            "metadata": dict(self.metadata),
            "error": dict(self.error) if self.error else None,
        }


@dataclass
class DocumentRouterMetrics:
    total_documents: int = 0
    routed_documents: int = 0
    failed_documents: int = 0
    reviewed_documents: int = 0
    total_duration_ms: int = 0

    def record(self, result: RouteResult) -> None:
        self.total_documents += 1
        self.total_duration_ms += result.duration_ms

        if result.status == "success":
            self.routed_documents += 1
        else:
            self.failed_documents += 1

        if result.decision == RouteDecision.REVIEW.value:
            self.reviewed_documents += 1

    def to_dict(self) -> dict[str, Any]:
        avg = self.total_duration_ms / self.total_documents if self.total_documents else 0.0
        return {
            "total_documents": self.total_documents,
            "routed_documents": self.routed_documents,
            "failed_documents": self.failed_documents,
            "reviewed_documents": self.reviewed_documents,
            "total_duration_ms": self.total_duration_ms,
            "avg_duration_ms": round(avg, 2),
        }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_request_id() -> str:
    return str(uuid.uuid4())


def make_document_id(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def load_document_text(path: str | Path, encoding: str = "utf-8") -> str:
    return Path(path).read_text(encoding=encoding)


def validate_document(document: DocumentInput, config: DocumentRouterConfig) -> None:
    if config.require_tenant_id and not document.tenant_id:
        raise DocumentValidationError("tenant_id é obrigatório.")

    content = document.content or ""

    if len(content) < config.min_content_length:
        raise DocumentValidationError("Conteúdo do documento é muito curto.")

    if len(content) > config.max_content_length:
        raise DocumentValidationError("Conteúdo do documento excede o limite máximo.")


def extract_document_signals(document: DocumentInput) -> DocumentSignals:
    content = document.content
    normalized = normalize_text(content)
    lower = normalized.lower()

    keyword_candidates = [
        "invoice",
        "nota fiscal",
        "fatura",
        "contrato",
        "contract",
        "recibo",
        "receipt",
        "relatório",
        "report",
        "jurídico",
        "legal",
        "pagamento",
        "payment",
        "saldo",
        "balance",
        "suporte",
        "ticket",
    ]

    hits = [keyword for keyword in keyword_candidates if keyword in lower]

    return DocumentSignals(
        document_hash=make_document_id(content),
        content_length=len(content),
        word_count=len(normalized.split()) if normalized else 0,
        line_count=content.count("\n") + 1 if content else 0,
        has_email=bool(re.search(r"[\w\.-]+@[\w\.-]+\.\w+", content)),
        has_phone=bool(re.search(r"(\+?\d{1,3})?[\s.-]?\(?\d{2,3}\)?[\s.-]?\d{4,5}[\s.-]?\d{4}", content)),
        has_currency=bool(re.search(r"(R\$|USD|EUR|\$|€)\s?\d+", content, re.IGNORECASE)),
        has_dates=bool(re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", content)),
        language_hint="pt" if any(word in lower for word in ["nota fiscal", "contrato", "recibo", "relatório"]) else None,
        keyword_hits=hits,
    )


def infer_type_from_signals(signals: DocumentSignals) -> DocumentType:
    hits = set(signals.keyword_hits)

    if {"invoice", "fatura", "nota fiscal"} & hits:
        return DocumentType.INVOICE

    if {"recibo", "receipt"} & hits:
        return DocumentType.RECEIPT

    if {"contrato", "contract"} & hits:
        return DocumentType.CONTRACT

    if {"relatório", "report"} & hits:
        return DocumentType.REPORT

    if {"suporte", "ticket"} & hits:
        return DocumentType.SUPPORT_TICKET

    if {"jurídico", "legal"} & hits:
        return DocumentType.LEGAL_NOTICE

    if {"saldo", "balance"} & hits:
        return DocumentType.FINANCIAL_STATEMENT

    return DocumentType.UNKNOWN


def metadata_matches(required: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    for key, expected in required.items():
        if actual.get(key) != expected:
            return False
    return True


def evaluate_rule(
    rule: RouteRule,
    document: DocumentInput,
    signals: DocumentSignals,
) -> float:
    content = document.content.lower()
    score = 0.0

    if rule.keywords:
        hits = sum(1 for keyword in rule.keywords if keyword.lower() in content)
        score += hits / max(len(rule.keywords), 1)

    if rule.regex_patterns:
        hits = sum(
            1
            for pattern in rule.regex_patterns
            if re.search(pattern, document.content, re.IGNORECASE)
        )
        score += hits / max(len(rule.regex_patterns), 1)

    if rule.required_metadata:
        score += 1.0 if metadata_matches(rule.required_metadata, document.metadata) else 0.0

    inferred_type = infer_type_from_signals(signals)

    if str(rule.document_type) != DocumentType.UNKNOWN.value and str(rule.document_type) == inferred_type.value:
        score += 1.0

    return score * rule.score


def route_by_rules(
    document: DocumentInput,
    signals: DocumentSignals,
    rules: Sequence[RouteRule],
) -> tuple[RouteRule | None, float]:
    best_rule: RouteRule | None = None
    best_score = 0.0

    for rule in rules:
        score = evaluate_rule(rule, document, signals)

        if score > best_score:
            best_rule = rule
            best_score = score

    return best_rule, min(best_score, 1.0)


def normalize_model_prediction(output: Any) -> dict[str, Any]:
    if isinstance(output, Mapping):
        return {
            "destination": output.get("destination"),
            "document_type": output.get("document_type") or output.get("type"),
            "priority": output.get("priority"),
            "decision": output.get("decision"),
            "confidence": float(output.get("confidence", 0.0)),
            "reasons": tuple(output.get("reasons", ()) or ()),
        }

    if isinstance(output, str):
        return {
            "destination": output,
            "document_type": DocumentType.UNKNOWN.value,
            "priority": Priority.NORMAL.value,
            "decision": RouteDecision.ACCEPT.value,
            "confidence": 0.7,
            "reasons": ("model_string_prediction",),
        }

    return {
        "destination": None,
        "document_type": DocumentType.UNKNOWN.value,
        "priority": Priority.NORMAL.value,
        "decision": RouteDecision.REVIEW.value,
        "confidence": 0.0,
        "reasons": ("unsupported_model_output",),
    }


class DocumentRouterPipeline:
    def __init__(
        self,
        model: RouterModelProtocol | Callable[[Any], Any] | None = None,
        *,
        config: DocumentRouterConfig | None = None,
        dispatcher: Callable[[RouteResult, DocumentInput], Any] | None = None,
    ) -> None:
        self.model = model
        self.config = config or DocumentRouterConfig()
        self.dispatcher = dispatcher
        self.metrics = DocumentRouterMetrics()

    def route(self, payload: DocumentInput | Mapping[str, Any]) -> RouteResult:
        started = time.perf_counter()
        request_id = make_request_id()

        try:
            document = self._normalize_payload(payload)
            document_id = document.document_id or make_document_id(document.content)

            validate_document(document, self.config)

            signals = extract_document_signals(document)

            result = self._decide_route(
                request_id=request_id,
                document_id=document_id,
                document=document,
                signals=signals,
                duration_ms=elapsed_ms(started),
            )

            if self.dispatcher and result.status == "success":
                self.dispatcher(result, document)

            self.metrics.record(result)

            self._audit("info", "document_router.routed", result.to_dict())

            return result

        except Exception as exc:
            duration = elapsed_ms(started)

            document_id = "unknown"
            if isinstance(payload, DocumentInput):
                document_id = payload.document_id or make_document_id(payload.content)
            elif isinstance(payload, Mapping) and payload.get("content"):
                document_id = str(payload.get("document_id") or make_document_id(str(payload["content"])))

            result = RouteResult(
                request_id=request_id,
                document_id=document_id,
                status="failed",
                decision=RouteDecision.REJECT.value,
                destination=self.config.default_destination,
                document_type=DocumentType.UNKNOWN.value,
                priority=Priority.NORMAL.value,
                confidence=0.0,
                duration_ms=duration,
                model_name=self.config.model_name,
                model_version=self.config.model_version,
                reasons=("routing_failed",),
                error={
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            )

            self.metrics.record(result)
            self._audit("error", "document_router.failed", result.to_dict())

            return result

    def route_many(
        self,
        documents: Sequence[DocumentInput | Mapping[str, Any]],
    ) -> list[RouteResult]:
        return [self.route(document) for document in documents]

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "pipeline_name": self.config.pipeline_name,
            "environment": self.config.environment,
            "model_name": self.config.model_name,
            "model_version": self.config.model_version,
            "has_model": self.model is not None,
            "rules_count": len(self.config.rules),
            "metrics": self.metrics.to_dict(),
            "timestamp": utc_now_iso(),
        }

    def _normalize_payload(self, payload: DocumentInput | Mapping[str, Any]) -> DocumentInput:
        if isinstance(payload, DocumentInput):
            return payload

        if not isinstance(payload, Mapping):
            raise DocumentValidationError("Payload precisa ser DocumentInput ou mapping.")

        content = payload.get("content")

        if not isinstance(content, str):
            raise DocumentValidationError("Campo content é obrigatório e precisa ser string.")

        return DocumentInput(
            content=content,
            document_id=payload.get("document_id"),  # type: ignore[arg-type]
            filename=payload.get("filename"),  # type: ignore[arg-type]
            mime_type=payload.get("mime_type"),  # type: ignore[arg-type]
            tenant_id=payload.get("tenant_id"),  # type: ignore[arg-type]
            user_id=payload.get("user_id"),  # type: ignore[arg-type]
            source=payload.get("source"),  # type: ignore[arg-type]
            metadata=payload.get("metadata") or {},  # type: ignore[arg-type]
        )

    def _decide_route(
        self,
        *,
        request_id: str,
        document_id: str,
        document: DocumentInput,
        signals: DocumentSignals,
        duration_ms: int,
    ) -> RouteResult:
        reasons: list[str] = []
        inferred_type = infer_type_from_signals(signals)

        destination = self.config.default_destination
        priority = self.config.default_priority.value
        decision = self.config.default_decision.value
        document_type = inferred_type.value
        confidence = 0.0

        if self.config.enable_rules and self.config.rules:
            rule, rule_confidence = route_by_rules(document, signals, self.config.rules)

            if rule and rule_confidence >= self.config.confidence_threshold:
                destination = rule.destination
                priority = str(rule.priority)
                decision = str(rule.decision)
                document_type = str(rule.document_type)
                confidence = rule_confidence
                reasons.append(f"matched_rule:{rule.name}")

        if (
            self.config.enable_model
            and self.model is not None
            and confidence < self.config.confidence_threshold
        ):
            model_input = {
                "content": document.content,
                "metadata": dict(document.metadata),
                "signals": signals.to_dict(),
            }

            raw_output = (
                self.model(model_input)
                if callable(self.model) and not hasattr(self.model, "predict")
                else self.model.predict(model_input)  # type: ignore[union-attr]
            )

            prediction = normalize_model_prediction(raw_output)

            if prediction["confidence"] >= confidence:
                destination = prediction["destination"] or destination
                document_type = prediction["document_type"] or document_type
                priority = prediction["priority"] or priority
                decision = prediction["decision"] or decision
                confidence = prediction["confidence"]
                reasons.extend(prediction["reasons"])
                reasons.append("model_prediction")

        if confidence < self.config.confidence_threshold:
            decision = RouteDecision.REVIEW.value
            destination = self.config.default_destination
            reasons.append("low_confidence_fallback")

        return RouteResult(
            request_id=request_id,
            document_id=document_id,
            status="success",
            decision=decision,
            destination=destination,
            document_type=document_type,
            priority=priority,
            confidence=round(float(confidence), 4),
            duration_ms=duration_ms,
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            reasons=tuple(reasons),
            signals=signals.to_dict(),
            metadata={
                "tenant_id": document.tenant_id,
                "user_id": document.user_id,
                "filename": document.filename,
                "mime_type": document.mime_type,
                "source": document.source,
                "routed_at": utc_now_iso(),
            },
        )

    def _audit(self, level: str, event: str, data: Mapping[str, Any]) -> None:
        if not self.config.enable_audit:
            return

        log_fn = {
            "debug": logger.debug,
            "info": logger.info,
            "warning": logger.warning,
            "warn": logger.warning,
            "error": logger.error,
        }.get(level, logger.info)

        log_fn(
            event,
            extra={
                "event": event,
                "timestamp": utc_now_iso(),
                **dict(data),
            },
        )


def run_document_router(
    content: str,
    *,
    model: RouterModelProtocol | Callable[[Any], Any] | None = None,
    config: DocumentRouterConfig | None = None,
    dispatcher: Callable[[RouteResult, DocumentInput], Any] | None = None,
    document_id: str | None = None,
    filename: str | None = None,
    mime_type: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    source: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> RouteResult:
    pipeline = DocumentRouterPipeline(
        model=model,
        config=config,
        dispatcher=dispatcher,
    )

    return pipeline.route(
        DocumentInput(
            content=content,
            document_id=document_id,
            filename=filename,
            mime_type=mime_type,
            tenant_id=tenant_id,
            user_id=user_id,
            source=source,
            metadata=metadata or {},
        )
    )


__all__ = [
    "DocumentInput",
    "DocumentRouterConfig",
    "DocumentRouterError",
    "DocumentRouterMetrics",
    "DocumentRouterPipeline",
    "DocumentSignals",
    "DocumentType",
    "DocumentValidationError",
    "Priority",
    "RouteDecision",
    "RouteResult",
    "RouteRule",
    "RouterModelProtocol",
    "elapsed_ms",
    "evaluate_rule",
    "extract_document_signals",
    "infer_type_from_signals",
    "load_document_text",
    "make_document_id",
    "make_request_id",
    "metadata_matches",
    "normalize_model_prediction",
    "normalize_text",
    "route_by_rules",
    "run_document_router",
    "utc_now_iso",
    "validate_document",
]
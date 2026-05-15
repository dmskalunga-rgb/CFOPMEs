"""
data/ai/model_router.py

Enterprise-grade model routing layer for AI inference workloads.

This module centralizes model/provider selection for production AI systems. It is
intended to be used by data/ai/inference_pipeline.py through its ModelRouter
protocol, but can also be used standalone.

Core capabilities:

- Model and provider registry
- Rule-based routing
- Tenant/application/domain policies
- Capability matching
- Cost-aware routing
- Latency-aware routing
- Quality-priority routing
- Weighted load balancing
- Fallback chains
- Health-aware selection
- Circuit breaker per model endpoint
- Cooldown and recovery
- Audit and metrics hooks
- Explainable route decisions
- Safe defaults for enterprise governance

Python:
    3.10+
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class ModelRouterError(Exception):
    """Base exception for model routing errors."""


class ModelRouterConfigurationError(ModelRouterError):
    """Raised when router configuration is invalid."""


class ModelNotFoundError(ModelRouterError):
    """Raised when no model matches a route request."""


class ProviderNotFoundError(ModelRouterError):
    """Raised when a configured provider is missing."""


class RoutePolicyError(ModelRouterError):
    """Raised when routing policy blocks selection."""


class ModelUnavailableError(ModelRouterError):
    """Raised when matching models are unavailable."""


# =============================================================================
# Enums
# =============================================================================


class RoutingStrategy(str, Enum):
    """Supported model routing strategies."""

    EXPLICIT = "explicit"
    RULE_BASED = "rule_based"
    LOWEST_COST = "lowest_cost"
    LOWEST_LATENCY = "lowest_latency"
    HIGHEST_QUALITY = "highest_quality"
    BALANCED = "balanced"
    WEIGHTED_RANDOM = "weighted_random"
    ROUND_ROBIN = "round_robin"
    FALLBACK_CHAIN = "fallback_chain"


class ModelCapability(str, Enum):
    """Model capabilities."""

    CHAT = "chat"
    COMPLETION = "completion"
    EMBEDDING = "embedding"
    CLASSIFICATION = "classification"
    RERANK = "rerank"
    TOOL_CALLING = "tool_calling"
    JSON_MODE = "json_mode"
    STREAMING = "streaming"
    VISION = "vision"
    AUDIO = "audio"
    LONG_CONTEXT = "long_context"
    LOW_LATENCY = "low_latency"
    HIGH_REASONING = "high_reasoning"
    MULTILINGUAL = "multilingual"
    PII_SAFE = "pii_safe"
    ON_PREM = "on_prem"


class EndpointStatus(str, Enum):
    """Endpoint health status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    DISABLED = "disabled"


class CircuitState(str, Enum):
    """Circuit breaker state."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class RouteDecisionType(str, Enum):
    """Route decision classification."""

    SELECTED = "selected"
    FALLBACK_SELECTED = "fallback_selected"
    BLOCKED = "blocked"
    NO_MATCH = "no_match"


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class RouterConfig:
    """Global router configuration."""

    default_strategy: RoutingStrategy = RoutingStrategy.BALANCED
    allow_cross_tenant_fallback: bool = False
    prefer_healthy_endpoints: bool = True
    allow_degraded_endpoints: bool = True
    allow_unhealthy_endpoints: bool = False
    enable_circuit_breaker: bool = True
    circuit_failure_threshold: int = 5
    circuit_recovery_seconds: float = 30.0
    health_score_decay_seconds: float = 300.0
    latency_smoothing_factor: float = 0.25
    max_candidates: int = 20
    random_seed: Optional[int] = None
    audit_enabled: bool = True
    metrics_enabled: bool = True
    version: str = "1.0.0"

    def validate(self) -> None:
        if self.circuit_failure_threshold <= 0:
            raise ModelRouterConfigurationError("circuit_failure_threshold must be positive")
        if self.circuit_recovery_seconds <= 0:
            raise ModelRouterConfigurationError("circuit_recovery_seconds must be positive")
        if self.health_score_decay_seconds <= 0:
            raise ModelRouterConfigurationError("health_score_decay_seconds must be positive")
        if not 0 < self.latency_smoothing_factor <= 1:
            raise ModelRouterConfigurationError("latency_smoothing_factor must be in (0, 1]")
        if self.max_candidates <= 0:
            raise ModelRouterConfigurationError("max_candidates must be positive")


@dataclass(frozen=True)
class RoutingContext:
    """Context used to route a request."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    application: Optional[str] = None
    domain: Optional[str] = None
    locale: Optional[str] = None
    trace_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelEndpoint:
    """A deployable model endpoint."""

    model_id: str
    provider_id: str
    provider_model_name: str
    capabilities: Set[ModelCapability]
    status: EndpointStatus = EndpointStatus.HEALTHY
    enabled: bool = True
    priority: int = 100
    weight: float = 1.0
    quality_score: float = 0.75
    cost_per_1k_input_tokens: float = 0.0
    cost_per_1k_output_tokens: float = 0.0
    estimated_latency_ms: float = 1000.0
    max_context_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    regions: Set[str] = field(default_factory=set)
    allowed_tenants: Set[str] = field(default_factory=set)
    blocked_tenants: Set[str] = field(default_factory=set)
    allowed_applications: Set[str] = field(default_factory=set)
    blocked_applications: Set[str] = field(default_factory=set)
    allowed_domains: Set[str] = field(default_factory=set)
    blocked_domains: Set[str] = field(default_factory=set)
    tags: Set[str] = field(default_factory=set)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.model_id:
            raise ModelRouterConfigurationError("model_id is required")
        if not self.provider_id:
            raise ModelRouterConfigurationError("provider_id is required")
        if not self.provider_model_name:
            raise ModelRouterConfigurationError("provider_model_name is required")
        if self.weight < 0:
            raise ModelRouterConfigurationError("weight must be >= 0")
        if not 0 <= self.quality_score <= 1:
            raise ModelRouterConfigurationError("quality_score must be between 0 and 1")
        if self.cost_per_1k_input_tokens < 0 or self.cost_per_1k_output_tokens < 0:
            raise ModelRouterConfigurationError("cost values must be >= 0")
        if self.estimated_latency_ms < 0:
            raise ModelRouterConfigurationError("estimated_latency_ms must be >= 0")


@dataclass(frozen=True)
class RouteRequirement:
    """Requirements extracted from an inference request."""

    mode: str
    required_capabilities: Set[ModelCapability] = field(default_factory=set)
    preferred_capabilities: Set[ModelCapability] = field(default_factory=set)
    requested_model: Optional[str] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    region: Optional[str] = None
    tags: Set[str] = field(default_factory=set)
    strategy: Optional[RoutingStrategy] = None
    fallback_model_ids: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateScore:
    """Score details for one endpoint candidate."""

    model_id: str
    provider_id: str
    provider_model_name: str
    total_score: float
    quality_component: float
    cost_component: float
    latency_component: float
    health_component: float
    priority_component: float
    capability_component: float
    reasons: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class RouteDecision:
    """Explainable route decision."""

    decision_id: str
    request_id: str
    decision_type: RouteDecisionType
    selected_model_id: Optional[str]
    provider_id: Optional[str]
    provider_model_name: Optional[str]
    strategy: RoutingStrategy
    candidates_considered: int
    scores: Sequence[CandidateScore]
    reason: str
    created_at: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class EndpointRuntimeState:
    """Mutable runtime state for an endpoint."""

    status: EndpointStatus = EndpointStatus.HEALTHY
    circuit_state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    opened_at: Optional[float] = None
    success_count: int = 0
    request_count: int = 0
    last_success_at: Optional[float] = None
    last_failure_at: Optional[float] = None
    smoothed_latency_ms: Optional[float] = None
    last_error: Optional[str] = None


@dataclass(frozen=True)
class RouteRule:
    """Rule for routing requests matching specific conditions."""

    rule_id: str
    priority: int = 100
    enabled: bool = True
    tenant_ids: Set[str] = field(default_factory=set)
    applications: Set[str] = field(default_factory=set)
    domains: Set[str] = field(default_factory=set)
    modes: Set[str] = field(default_factory=set)
    required_tags: Set[str] = field(default_factory=set)
    target_model_ids: Sequence[str] = field(default_factory=tuple)
    strategy: RoutingStrategy = RoutingStrategy.BALANCED
    fallback_model_ids: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def matches(self, context: RoutingContext, requirement: RouteRequirement) -> bool:
        if not self.enabled:
            return False
        if self.tenant_ids and (context.tenant_id not in self.tenant_ids):
            return False
        if self.applications and (context.application not in self.applications):
            return False
        if self.domains and (context.domain not in self.domains):
            return False
        if self.modes and (requirement.mode not in self.modes):
            return False
        if self.required_tags and not self.required_tags.issubset(requirement.tags):
            return False
        return True


# =============================================================================
# Protocols
# =============================================================================


class ModelProvider(Protocol):
    """Provider protocol compatible with inference_pipeline.py."""

    @property
    def name(self) -> str:
        """Provider name."""

    async def infer(self, request: Any) -> Any:
        """Execute inference."""

    async def stream(self, request: Any) -> Any:
        """Execute streaming inference."""


class AuditSink(Protocol):
    """Audit sink protocol."""

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        """Emit audit event."""


class MetricsSink(Protocol):
    """Metrics sink protocol."""

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Increment metric."""

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Observe metric."""


# =============================================================================
# Utility Functions
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def normalize_minmax(value: float, minimum: float, maximum: float, *, invert: bool = False) -> float:
    if maximum <= minimum:
        score = 1.0
    else:
        score = (value - minimum) / (maximum - minimum)
    score = clamp(score)
    return 1.0 - score if invert else score


def estimate_tokens_from_request(request: Any) -> Tuple[int, int]:
    """Best-effort token estimation from inference request-like objects."""

    text_size = 0
    prompt = getattr(request, "prompt", None)
    if prompt:
        text_size += len(str(prompt))
    for message in getattr(request, "messages", ()) or ():
        text_size += len(str(getattr(message, "content", "")))
    for item in getattr(request, "input_texts", ()) or ():
        text_size += len(str(item))
    options = getattr(request, "options", None)
    max_output = getattr(options, "max_tokens", None) if options is not None else None
    input_tokens = max(1, int(text_size / 4)) if text_size else 0
    output_tokens = int(max_output or 512)
    return input_tokens, output_tokens


def mode_to_capability(mode: str) -> Optional[ModelCapability]:
    mapping = {
        "chat": ModelCapability.CHAT,
        "completion": ModelCapability.COMPLETION,
        "embedding": ModelCapability.EMBEDDING,
        "classification": ModelCapability.CLASSIFICATION,
        "rerank": ModelCapability.RERANK,
    }
    return mapping.get(mode)


# =============================================================================
# Default sinks
# =============================================================================


class LoggingAuditSink:
    """Audit sink using logging."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        self.logger.info("model_router_audit=%s payload=%s", event_name, safe_json(payload))


class LoggingMetricsSink:
    """Metrics sink using logging."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("model_router_metric_counter=%s value=%s tags=%s", name, value, dict(tags or {}))

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("model_router_metric_observe=%s value=%s tags=%s", name, value, dict(tags or {}))


# =============================================================================
# Router
# =============================================================================


class EnterpriseModelRouter:
    """Enterprise model router compatible with inference_pipeline.ModelRouter."""

    def __init__(
        self,
        *,
        config: Optional[RouterConfig] = None,
        endpoints: Sequence[ModelEndpoint] = (),
        providers: Optional[Mapping[str, ModelProvider]] = None,
        rules: Sequence[RouteRule] = (),
        audit_sink: Optional[AuditSink] = None,
        metrics_sink: Optional[MetricsSink] = None,
    ) -> None:
        self.config = config or RouterConfig()
        self.config.validate()
        self._endpoints: Dict[str, ModelEndpoint] = {}
        self._providers: Dict[str, ModelProvider] = dict(providers or {})
        self._rules: List[RouteRule] = sorted(list(rules), key=lambda r: r.priority)
        self._state: Dict[str, EndpointRuntimeState] = {}
        self._round_robin_index: Dict[str, int] = {}
        self._last_decision_by_request: Dict[str, RouteDecision] = {}
        self.audit_sink = audit_sink or LoggingAuditSink()
        self.metrics_sink = metrics_sink or LoggingMetricsSink()
        self._rng = random.Random(self.config.random_seed)
        self._lock = asyncio.Lock()

        for endpoint in endpoints:
            self.register_endpoint(endpoint)

    def register_provider(self, provider_id: str, provider: ModelProvider) -> None:
        if not provider_id:
            raise ModelRouterConfigurationError("provider_id is required")
        self._providers[provider_id] = provider

    def register_endpoint(self, endpoint: ModelEndpoint) -> None:
        endpoint.validate()
        self._endpoints[endpoint.model_id] = endpoint
        self._state.setdefault(endpoint.model_id, EndpointRuntimeState(status=endpoint.status))

    def register_rule(self, rule: RouteRule) -> None:
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority)

    def get_last_decision(self, request_id: str) -> Optional[RouteDecision]:
        return self._last_decision_by_request.get(request_id)

    def list_endpoints(self) -> Sequence[ModelEndpoint]:
        return tuple(self._endpoints.values())

    def list_providers(self) -> Sequence[str]:
        return tuple(sorted(self._providers.keys()))

    async def route(self, request: Any) -> Tuple[str, ModelProvider]:
        """Route an inference request to a provider.

        Returns:
            Tuple of provider model name and provider instance. This shape is
            compatible with inference_pipeline.ModelRouter.
        """

        started = time.perf_counter()
        context = self._context_from_request(request)
        requirement = self._requirement_from_request(request)

        try:
            decision, endpoint = await self.decide(context, requirement)
            if not endpoint:
                raise ModelNotFoundError(decision.reason)
            provider = self._providers.get(endpoint.provider_id)
            if provider is None:
                raise ProviderNotFoundError(f"Provider not registered: {endpoint.provider_id}")

            async with self._lock:
                self._last_decision_by_request[context.request_id] = decision
                self._state[endpoint.model_id].request_count += 1

            latency_ms = (time.perf_counter() - started) * 1000
            await self._metrics_success(endpoint, decision, latency_ms)
            await self._audit("model_route_selected", context, decision, {"latency_ms": round(latency_ms, 3)})
            return endpoint.provider_model_name, provider

        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            await self._metrics_failure(requirement, exc, latency_ms)
            await self._audit_failure("model_route_failed", context, requirement, exc, latency_ms)
            raise

    async def decide(self, context: RoutingContext, requirement: RouteRequirement) -> Tuple[RouteDecision, Optional[ModelEndpoint]]:
        """Return explainable decision and selected endpoint."""

        matched_rule = self._find_matching_rule(context, requirement)
        strategy = requirement.strategy or (matched_rule.strategy if matched_rule else self.config.default_strategy)

        candidate_pool = list(self._endpoints.values())
        if matched_rule and matched_rule.target_model_ids:
            allowed = set(matched_rule.target_model_ids)
            candidate_pool = [ep for ep in candidate_pool if ep.model_id in allowed]

        candidates = self._filter_candidates(candidate_pool, context, requirement)

        if not candidates and matched_rule and matched_rule.fallback_model_ids:
            candidates = self._filter_candidates(
                [self._endpoints[mid] for mid in matched_rule.fallback_model_ids if mid in self._endpoints],
                context,
                requirement,
                allow_relaxed=True,
            )
            strategy = RoutingStrategy.FALLBACK_CHAIN

        if not candidates and requirement.fallback_model_ids:
            candidates = self._filter_candidates(
                [self._endpoints[mid] for mid in requirement.fallback_model_ids if mid in self._endpoints],
                context,
                requirement,
                allow_relaxed=True,
            )
            strategy = RoutingStrategy.FALLBACK_CHAIN

        if not candidates:
            decision = RouteDecision(
                decision_id=str(uuid.uuid4()),
                request_id=context.request_id,
                decision_type=RouteDecisionType.NO_MATCH,
                selected_model_id=None,
                provider_id=None,
                provider_model_name=None,
                strategy=strategy,
                candidates_considered=0,
                scores=tuple(),
                reason="No model endpoint matched the routing requirements.",
                created_at=utc_now_iso(),
                metadata={"matched_rule_id": matched_rule.rule_id if matched_rule else None},
            )
            return decision, None

        scores = self._score_candidates(candidates, requirement)
        selected = self._select_candidate(candidates, scores, strategy, context, requirement)
        decision_type = RouteDecisionType.FALLBACK_SELECTED if strategy == RoutingStrategy.FALLBACK_CHAIN else RouteDecisionType.SELECTED

        decision = RouteDecision(
            decision_id=str(uuid.uuid4()),
            request_id=context.request_id,
            decision_type=decision_type,
            selected_model_id=selected.model_id,
            provider_id=selected.provider_id,
            provider_model_name=selected.provider_model_name,
            strategy=strategy,
            candidates_considered=len(candidates),
            scores=tuple(scores[: self.config.max_candidates]),
            reason=self._decision_reason(selected, strategy, matched_rule),
            created_at=utc_now_iso(),
            metadata={"matched_rule_id": matched_rule.rule_id if matched_rule else None},
        )
        return decision, selected

    async def record_success(self, model_id: str, *, latency_ms: Optional[float] = None) -> None:
        """Record a successful call for health/circuit state."""

        async with self._lock:
            state = self._state_for(model_id)
            state.success_count += 1
            state.last_success_at = time.monotonic()
            state.failure_count = 0
            state.last_error = None
            if self.config.enable_circuit_breaker:
                state.circuit_state = CircuitState.CLOSED
                state.opened_at = None
            if latency_ms is not None:
                if state.smoothed_latency_ms is None:
                    state.smoothed_latency_ms = latency_ms
                else:
                    alpha = self.config.latency_smoothing_factor
                    state.smoothed_latency_ms = (alpha * latency_ms) + ((1 - alpha) * state.smoothed_latency_ms)

    async def record_failure(self, model_id: str, error: Optional[BaseException] = None) -> None:
        """Record a failed call for health/circuit state."""

        async with self._lock:
            state = self._state_for(model_id)
            state.failure_count += 1
            state.last_failure_at = time.monotonic()
            state.last_error = str(error) if error else None
            if self.config.enable_circuit_breaker and state.failure_count >= self.config.circuit_failure_threshold:
                state.circuit_state = CircuitState.OPEN
                state.opened_at = time.monotonic()

    async def set_endpoint_status(self, model_id: str, status: EndpointStatus) -> None:
        async with self._lock:
            self._state_for(model_id).status = status

    def endpoint_state(self, model_id: str) -> EndpointRuntimeState:
        return self._state_for(model_id)

    def _context_from_request(self, request: Any) -> RoutingContext:
        raw_context = getattr(request, "context", None)
        return RoutingContext(
            request_id=getattr(raw_context, "request_id", str(uuid.uuid4())),
            tenant_id=getattr(raw_context, "tenant_id", None),
            user_id=getattr(raw_context, "user_id", None),
            application=getattr(raw_context, "application", None),
            domain=getattr(raw_context, "domain", None),
            locale=getattr(raw_context, "locale", None),
            trace_id=getattr(raw_context, "trace_id", None),
            metadata=getattr(raw_context, "metadata", {}) or {},
        )

    def _requirement_from_request(self, request: Any) -> RouteRequirement:
        mode_obj = getattr(request, "mode", None)
        mode = getattr(mode_obj, "value", str(mode_obj))
        requested_model = getattr(request, "model", None)
        options = getattr(request, "options", None)
        extra = getattr(options, "extra", {}) if options is not None else {}
        metadata = getattr(request, "metadata", {}) or {}
        input_tokens, output_tokens = estimate_tokens_from_request(request)

        required: Set[ModelCapability] = set()
        mode_capability = mode_to_capability(mode)
        if mode_capability:
            required.add(mode_capability)
        if getattr(options, "stream", False):
            required.add(ModelCapability.STREAMING)
        if getattr(options, "tools", None):
            required.add(ModelCapability.TOOL_CALLING)
        if getattr(options, "response_format", None):
            required.add(ModelCapability.JSON_MODE)

        preferred = set()
        for item in extra.get("preferred_capabilities", []) if isinstance(extra, Mapping) else []:
            preferred.add(ModelCapability(str(item)))

        fallback_ids = tuple(extra.get("fallback_model_ids", ()) if isinstance(extra, Mapping) else ())
        strategy_value = extra.get("routing_strategy") if isinstance(extra, Mapping) else None
        strategy = RoutingStrategy(strategy_value) if strategy_value else None
        tags = set(metadata.get("routing_tags", ()) or extra.get("routing_tags", ()) if isinstance(extra, Mapping) else ())

        return RouteRequirement(
            mode=mode,
            required_capabilities=required,
            preferred_capabilities=preferred,
            requested_model=requested_model,
            max_input_tokens=input_tokens,
            max_output_tokens=output_tokens,
            region=metadata.get("region") or (extra.get("region") if isinstance(extra, Mapping) else None),
            tags=tags,
            strategy=strategy,
            fallback_model_ids=fallback_ids,
            metadata=metadata,
        )

    def _find_matching_rule(self, context: RoutingContext, requirement: RouteRequirement) -> Optional[RouteRule]:
        for rule in self._rules:
            if rule.matches(context, requirement):
                return rule
        return None

    def _filter_candidates(
        self,
        endpoints: Sequence[ModelEndpoint],
        context: RoutingContext,
        requirement: RouteRequirement,
        *,
        allow_relaxed: bool = False,
    ) -> List[ModelEndpoint]:
        result: List[ModelEndpoint] = []
        for endpoint in endpoints:
            if not self._endpoint_basic_allowed(endpoint, context, requirement, allow_relaxed=allow_relaxed):
                continue
            if not self._endpoint_health_allowed(endpoint):
                continue
            result.append(endpoint)
        return result[: self.config.max_candidates]

    def _endpoint_basic_allowed(
        self,
        endpoint: ModelEndpoint,
        context: RoutingContext,
        requirement: RouteRequirement,
        *,
        allow_relaxed: bool,
    ) -> bool:
        if not endpoint.enabled:
            return False
        if requirement.requested_model and endpoint.model_id != requirement.requested_model and endpoint.provider_model_name != requirement.requested_model:
            return False
        if endpoint.blocked_tenants and context.tenant_id in endpoint.blocked_tenants:
            return False
        if endpoint.allowed_tenants and context.tenant_id not in endpoint.allowed_tenants:
            return False
        if endpoint.blocked_applications and context.application in endpoint.blocked_applications:
            return False
        if endpoint.allowed_applications and context.application not in endpoint.allowed_applications:
            return False
        if endpoint.blocked_domains and context.domain in endpoint.blocked_domains:
            return False
        if endpoint.allowed_domains and context.domain not in endpoint.allowed_domains:
            return False
        if requirement.region and endpoint.regions and requirement.region not in endpoint.regions:
            return False
        if requirement.tags and not requirement.tags.issubset(endpoint.tags):
            return False
        if not allow_relaxed and not requirement.required_capabilities.issubset(endpoint.capabilities):
            return False
        if requirement.max_input_tokens and endpoint.max_context_tokens and requirement.max_input_tokens > endpoint.max_context_tokens:
            return False
        if requirement.max_output_tokens and endpoint.max_output_tokens and requirement.max_output_tokens > endpoint.max_output_tokens:
            return False
        return True

    def _endpoint_health_allowed(self, endpoint: ModelEndpoint) -> bool:
        state = self._state_for(endpoint.model_id)
        status = state.status if state.status else endpoint.status
        if status == EndpointStatus.DISABLED:
            return False
        if status == EndpointStatus.UNHEALTHY and not self.config.allow_unhealthy_endpoints:
            return False
        if status == EndpointStatus.DEGRADED and not self.config.allow_degraded_endpoints:
            return False
        if self.config.enable_circuit_breaker and state.circuit_state == CircuitState.OPEN:
            if state.opened_at is not None and time.monotonic() - state.opened_at >= self.config.circuit_recovery_seconds:
                state.circuit_state = CircuitState.HALF_OPEN
                return True
            return False
        return True

    def _score_candidates(self, candidates: Sequence[ModelEndpoint], requirement: RouteRequirement) -> List[CandidateScore]:
        if not candidates:
            return []
        costs = [self._estimated_cost(endpoint, requirement) for endpoint in candidates]
        latencies = [self._runtime_latency(endpoint) for endpoint in candidates]
        min_cost, max_cost = min(costs), max(costs)
        min_latency, max_latency = min(latencies), max(latencies)

        scores: List[CandidateScore] = []
        for endpoint, cost, latency in zip(candidates, costs, latencies):
            quality_component = clamp(endpoint.quality_score)
            cost_component = normalize_minmax(cost, min_cost, max_cost, invert=True)
            latency_component = normalize_minmax(latency, min_latency, max_latency, invert=True)
            health_component = self._health_component(endpoint)
            priority_component = clamp(1.0 - ((max(endpoint.priority, 0)) / 1000.0))
            capability_component = self._capability_component(endpoint, requirement)

            total_score = clamp(
                (quality_component * 0.30)
                + (cost_component * 0.18)
                + (latency_component * 0.18)
                + (health_component * 0.18)
                + (priority_component * 0.08)
                + (capability_component * 0.08)
            )

            reasons = [
                f"quality={quality_component:.2f}",
                f"cost_score={cost_component:.2f}",
                f"latency_score={latency_component:.2f}",
                f"health={health_component:.2f}",
                f"priority={endpoint.priority}",
            ]
            scores.append(
                CandidateScore(
                    model_id=endpoint.model_id,
                    provider_id=endpoint.provider_id,
                    provider_model_name=endpoint.provider_model_name,
                    total_score=total_score,
                    quality_component=quality_component,
                    cost_component=cost_component,
                    latency_component=latency_component,
                    health_component=health_component,
                    priority_component=priority_component,
                    capability_component=capability_component,
                    reasons=tuple(reasons),
                )
            )
        return sorted(scores, key=lambda score: score.total_score, reverse=True)

    def _select_candidate(
        self,
        candidates: Sequence[ModelEndpoint],
        scores: Sequence[CandidateScore],
        strategy: RoutingStrategy,
        context: RoutingContext,
        requirement: RouteRequirement,
    ) -> ModelEndpoint:
        by_id = {endpoint.model_id: endpoint for endpoint in candidates}

        if strategy == RoutingStrategy.EXPLICIT and requirement.requested_model:
            for endpoint in candidates:
                if endpoint.model_id == requirement.requested_model or endpoint.provider_model_name == requirement.requested_model:
                    return endpoint
            raise ModelNotFoundError(f"Requested model not available: {requirement.requested_model}")

        if strategy == RoutingStrategy.LOWEST_COST:
            return min(candidates, key=lambda endpoint: self._estimated_cost(endpoint, requirement))

        if strategy == RoutingStrategy.LOWEST_LATENCY:
            return min(candidates, key=self._runtime_latency)

        if strategy == RoutingStrategy.HIGHEST_QUALITY:
            return max(candidates, key=lambda endpoint: endpoint.quality_score)

        if strategy == RoutingStrategy.WEIGHTED_RANDOM:
            return self._weighted_random(candidates)

        if strategy == RoutingStrategy.ROUND_ROBIN:
            key = self._round_robin_key(context, requirement)
            index = self._round_robin_index.get(key, 0)
            endpoint = list(candidates)[index % len(candidates)]
            self._round_robin_index[key] = index + 1
            return endpoint

        if strategy == RoutingStrategy.FALLBACK_CHAIN:
            ordered_ids = list(requirement.fallback_model_ids)
            for model_id in ordered_ids:
                if model_id in by_id:
                    return by_id[model_id]
            return by_id[scores[0].model_id]

        return by_id[scores[0].model_id]

    def _weighted_random(self, candidates: Sequence[ModelEndpoint]) -> ModelEndpoint:
        total = sum(max(0.0, endpoint.weight) for endpoint in candidates)
        if total <= 0:
            return self._rng.choice(list(candidates))
        pick = self._rng.uniform(0, total)
        cursor = 0.0
        for endpoint in candidates:
            cursor += max(0.0, endpoint.weight)
            if cursor >= pick:
                return endpoint
        return candidates[-1]

    def _estimated_cost(self, endpoint: ModelEndpoint, requirement: RouteRequirement) -> float:
        input_tokens = requirement.max_input_tokens or 0
        output_tokens = requirement.max_output_tokens or 0
        return (input_tokens / 1000.0 * endpoint.cost_per_1k_input_tokens) + (
            output_tokens / 1000.0 * endpoint.cost_per_1k_output_tokens
        )

    def _runtime_latency(self, endpoint: ModelEndpoint) -> float:
        state = self._state_for(endpoint.model_id)
        return state.smoothed_latency_ms if state.smoothed_latency_ms is not None else endpoint.estimated_latency_ms

    def _health_component(self, endpoint: ModelEndpoint) -> float:
        state = self._state_for(endpoint.model_id)
        status = state.status if state.status else endpoint.status
        if status == EndpointStatus.HEALTHY:
            base = 1.0
        elif status == EndpointStatus.DEGRADED:
            base = 0.55
        elif status == EndpointStatus.UNHEALTHY:
            base = 0.15
        else:
            base = 0.0

        if state.circuit_state == CircuitState.OPEN:
            base *= 0.0
        elif state.circuit_state == CircuitState.HALF_OPEN:
            base *= 0.45

        if state.failure_count:
            base *= max(0.2, 1.0 - (state.failure_count * 0.12))
        return clamp(base)

    def _capability_component(self, endpoint: ModelEndpoint, requirement: RouteRequirement) -> float:
        if not requirement.preferred_capabilities:
            return 0.7
        matched = len(endpoint.capabilities & requirement.preferred_capabilities)
        return matched / max(len(requirement.preferred_capabilities), 1)

    def _state_for(self, model_id: str) -> EndpointRuntimeState:
        if model_id not in self._state:
            self._state[model_id] = EndpointRuntimeState()
        return self._state[model_id]

    def _round_robin_key(self, context: RoutingContext, requirement: RouteRequirement) -> str:
        return stable_hash(f"{context.tenant_id}|{context.application}|{requirement.mode}|{sorted(requirement.tags)}")

    def _decision_reason(self, endpoint: ModelEndpoint, strategy: RoutingStrategy, rule: Optional[RouteRule]) -> str:
        rule_text = f" using rule {rule.rule_id}" if rule else ""
        return f"Selected model {endpoint.model_id} with strategy {strategy.value}{rule_text}."

    async def _metrics_success(self, endpoint: ModelEndpoint, decision: RouteDecision, latency_ms: float) -> None:
        if not self.config.metrics_enabled:
            return
        tags = {
            "provider_id": endpoint.provider_id,
            "model_id": endpoint.model_id,
            "strategy": decision.strategy.value,
            "decision_type": decision.decision_type.value,
        }
        await self.metrics_sink.increment("ai.model_router.route.success", 1, tags)
        await self.metrics_sink.observe("ai.model_router.route.latency_ms", latency_ms, tags)
        await self.metrics_sink.observe("ai.model_router.candidates", decision.candidates_considered, tags)

    async def _metrics_failure(self, requirement: RouteRequirement, exc: BaseException, latency_ms: float) -> None:
        if not self.config.metrics_enabled:
            return
        tags = {
            "mode": requirement.mode,
            "error_type": type(exc).__name__,
        }
        await self.metrics_sink.increment("ai.model_router.route.failure", 1, tags)
        await self.metrics_sink.observe("ai.model_router.route.failure_latency_ms", latency_ms, tags)

    async def _audit(
        self,
        event_name: str,
        context: RoutingContext,
        decision: RouteDecision,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if not self.config.audit_enabled:
            return
        payload = {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "router_version": self.config.version,
            "request_id": context.request_id,
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "application": context.application,
            "domain": context.domain,
            "trace_id": context.trace_id,
            "decision": asdict(decision),
        }
        if extra:
            payload.update(dict(extra))
        await self.audit_sink.emit(event_name, payload)

    async def _audit_failure(
        self,
        event_name: str,
        context: RoutingContext,
        requirement: RouteRequirement,
        exc: BaseException,
        latency_ms: float,
    ) -> None:
        if not self.config.audit_enabled:
            return
        payload = {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "router_version": self.config.version,
            "request_id": context.request_id,
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "application": context.application,
            "domain": context.domain,
            "trace_id": context.trace_id,
            "requirement": asdict(requirement),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "latency_ms": round(latency_ms, 3),
        }
        await self.audit_sink.emit(event_name, payload)


# =============================================================================
# Provider wrappers
# =============================================================================


class RoutedProviderProxy:
    """Provider proxy that records success/failure in the router.

    This can be used when callers want the router to learn runtime health from
    downstream provider execution. It wraps a provider selected by the router and
    updates the endpoint state after inference calls.
    """

    def __init__(self, *, router: EnterpriseModelRouter, endpoint: ModelEndpoint, provider: ModelProvider) -> None:
        self.router = router
        self.endpoint = endpoint
        self.provider = provider

    @property
    def name(self) -> str:
        return self.provider.name

    async def infer(self, request: Any) -> Any:
        started = time.perf_counter()
        try:
            response = await self.provider.infer(request)
            await self.router.record_success(self.endpoint.model_id, latency_ms=(time.perf_counter() - started) * 1000)
            return response
        except Exception as exc:
            await self.router.record_failure(self.endpoint.model_id, exc)
            raise

    async def stream(self, request: Any) -> Any:
        started = time.perf_counter()
        try:
            async for chunk in self.provider.stream(request):
                yield chunk
            await self.router.record_success(self.endpoint.model_id, latency_ms=(time.perf_counter() - started) * 1000)
        except Exception as exc:
            await self.router.record_failure(self.endpoint.model_id, exc)
            raise


class LearningEnterpriseModelRouter(EnterpriseModelRouter):
    """Router variant that returns a provider proxy to auto-record health."""

    async def route(self, request: Any) -> Tuple[str, ModelProvider]:
        context = self._context_from_request(request)
        requirement = self._requirement_from_request(request)
        decision, endpoint = await self.decide(context, requirement)
        if not endpoint:
            raise ModelNotFoundError(decision.reason)
        provider = self._providers.get(endpoint.provider_id)
        if provider is None:
            raise ProviderNotFoundError(f"Provider not registered: {endpoint.provider_id}")
        async with self._lock:
            self._last_decision_by_request[context.request_id] = decision
            self._state[endpoint.model_id].request_count += 1
        await self._audit("model_route_selected", context, decision)
        return endpoint.provider_model_name, RoutedProviderProxy(router=self, endpoint=endpoint, provider=provider)


# =============================================================================
# Factory helpers
# =============================================================================


def build_chat_endpoint(
    *,
    model_id: str,
    provider_id: str,
    provider_model_name: Optional[str] = None,
    quality_score: float = 0.75,
    cost_per_1k_input_tokens: float = 0.0,
    cost_per_1k_output_tokens: float = 0.0,
    estimated_latency_ms: float = 1000.0,
    max_context_tokens: Optional[int] = None,
    max_output_tokens: Optional[int] = None,
    capabilities: Optional[Set[ModelCapability]] = None,
    tags: Optional[Set[str]] = None,
) -> ModelEndpoint:
    base_capabilities = {
        ModelCapability.CHAT,
        ModelCapability.STREAMING,
        ModelCapability.JSON_MODE,
        ModelCapability.MULTILINGUAL,
    }
    if capabilities:
        base_capabilities |= capabilities
    return ModelEndpoint(
        model_id=model_id,
        provider_id=provider_id,
        provider_model_name=provider_model_name or model_id,
        capabilities=base_capabilities,
        quality_score=quality_score,
        cost_per_1k_input_tokens=cost_per_1k_input_tokens,
        cost_per_1k_output_tokens=cost_per_1k_output_tokens,
        estimated_latency_ms=estimated_latency_ms,
        max_context_tokens=max_context_tokens,
        max_output_tokens=max_output_tokens,
        tags=tags or set(),
    )


def build_embedding_endpoint(
    *,
    model_id: str,
    provider_id: str,
    provider_model_name: Optional[str] = None,
    quality_score: float = 0.75,
    cost_per_1k_input_tokens: float = 0.0,
    estimated_latency_ms: float = 500.0,
    max_context_tokens: Optional[int] = None,
    tags: Optional[Set[str]] = None,
) -> ModelEndpoint:
    return ModelEndpoint(
        model_id=model_id,
        provider_id=provider_id,
        provider_model_name=provider_model_name or model_id,
        capabilities={ModelCapability.EMBEDDING, ModelCapability.LOW_LATENCY},
        quality_score=quality_score,
        cost_per_1k_input_tokens=cost_per_1k_input_tokens,
        cost_per_1k_output_tokens=0.0,
        estimated_latency_ms=estimated_latency_ms,
        max_context_tokens=max_context_tokens,
        tags=tags or set(),
    )


def build_default_model_router(
    *,
    providers: Mapping[str, ModelProvider],
    default_provider_id: Optional[str] = None,
    config_overrides: Optional[Mapping[str, Any]] = None,
) -> EnterpriseModelRouter:
    """Build a default router with common chat and embedding endpoint examples.

    The model ids are provider-neutral aliases. Adjust provider_model_name for
    your actual model provider.
    """

    if not providers:
        raise ModelRouterConfigurationError("At least one provider is required")
    provider_id = default_provider_id or next(iter(providers.keys()))
    config_data = asdict(RouterConfig())
    if config_overrides:
        config_data.update(dict(config_overrides))
    config = RouterConfig(**config_data)

    endpoints = [
        build_chat_endpoint(
            model_id="general-chat-fast",
            provider_id=provider_id,
            provider_model_name="gpt-4o-mini",
            quality_score=0.78,
            estimated_latency_ms=850,
            cost_per_1k_input_tokens=0.00015,
            cost_per_1k_output_tokens=0.00060,
            max_context_tokens=128_000,
            tags={"general", "fast"},
        ),
        build_chat_endpoint(
            model_id="general-chat-quality",
            provider_id=provider_id,
            provider_model_name="gpt-4o",
            quality_score=0.90,
            estimated_latency_ms=1500,
            cost_per_1k_input_tokens=0.0025,
            cost_per_1k_output_tokens=0.0100,
            max_context_tokens=128_000,
            tags={"general", "quality"},
            capabilities={ModelCapability.HIGH_REASONING, ModelCapability.TOOL_CALLING},
        ),
        build_embedding_endpoint(
            model_id="embedding-default",
            provider_id=provider_id,
            provider_model_name="text-embedding-3-small",
            quality_score=0.82,
            estimated_latency_ms=400,
            cost_per_1k_input_tokens=0.00002,
            max_context_tokens=8191,
            tags={"embedding", "default"},
        ),
    ]

    rules = [
        RouteRule(
            rule_id="embedding-default-route",
            priority=10,
            modes={"embedding"},
            target_model_ids=("embedding-default",),
            strategy=RoutingStrategy.LOWEST_LATENCY,
        ),
        RouteRule(
            rule_id="quality-domain-route",
            priority=20,
            domains={"legal", "medical", "financial", "risk", "governance"},
            target_model_ids=("general-chat-quality",),
            strategy=RoutingStrategy.HIGHEST_QUALITY,
            fallback_model_ids=("general-chat-fast",),
        ),
        RouteRule(
            rule_id="default-chat-route",
            priority=100,
            modes={"chat", "completion"},
            target_model_ids=("general-chat-fast", "general-chat-quality"),
            strategy=RoutingStrategy.BALANCED,
        ),
    ]

    return LearningEnterpriseModelRouter(
        config=config,
        providers=providers,
        endpoints=endpoints,
        rules=rules,
    )


# =============================================================================
# Demo provider and local test
# =============================================================================


class DemoProvider:
    """Minimal provider for local router tests."""

    def __init__(self, name: str = "demo-provider") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def infer(self, request: Any) -> Any:
        await asyncio.sleep(0)
        return {"ok": True, "model": getattr(request, "model", None)}

    async def stream(self, request: Any) -> Any:
        yield {"delta": "demo"}


async def _demo_async() -> None:
    logging.basicConfig(level=logging.INFO)

    try:
        from data.ai.inference_pipeline import InferenceContext, InferenceMode, InferenceOptions, InferenceRequest, ChatMessage, MessageRole
    except Exception:  # noqa: BLE001
        from inference_pipeline import InferenceContext, InferenceMode, InferenceOptions, InferenceRequest, ChatMessage, MessageRole  # type: ignore

    router = build_default_model_router(providers={"demo": DemoProvider("demo")}, default_provider_id="demo")
    request = InferenceRequest(
        mode=InferenceMode.CHAT,
        messages=(ChatMessage(role=MessageRole.USER, content="Explique model routing enterprise."),),
        options=InferenceOptions(temperature=0.2, max_tokens=300),
        context=InferenceContext(
            tenant_id="demo-tenant",
            application="ai-platform",
            domain="governance",
            trace_id=str(uuid.uuid4()),
        ),
    )

    model_name, provider = await router.route(request)
    print("Selected provider model:", model_name)
    print("Selected provider:", provider.name)
    decision = router.get_last_decision(request.context.request_id)
    print(json.dumps(asdict(decision), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(_demo_async())

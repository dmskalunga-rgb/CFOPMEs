"""
data/ai/inference_pipeline.py

Enterprise-grade AI inference pipeline.

This module provides a robust, extensible and production-oriented inference
orchestration layer for AI/LLM workloads. It is designed to sit between
applications and model providers, centralizing:

- Request validation
- Prompt/message normalization
- Model routing
- Policy checks
- PII/sensitive-data hooks
- Caching
- Retries and timeout control
- Rate-limit aware execution
- Streaming and non-streaming inference abstractions
- Post-processing
- Guardrail integration
- Audit events
- Metrics and traces
- Error normalization
- Batch inference

Recommended package position:
    data/ai/inference_pipeline.py

Python:
    3.10+
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
import uuid
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Union,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class InferencePipelineError(Exception):
    """Base exception for inference pipeline failures."""


class InferenceValidationError(InferencePipelineError):
    """Raised when an inference request is invalid."""


class InferencePolicyError(InferencePipelineError):
    """Raised when a request or response violates policy."""


class InferenceProviderError(InferencePipelineError):
    """Raised when the model provider fails."""


class InferenceTimeoutError(InferencePipelineError):
    """Raised when inference exceeds configured timeout."""


class InferenceRateLimitError(InferencePipelineError):
    """Raised when provider or pipeline rate limit is exceeded."""


class InferenceConfigurationError(InferencePipelineError):
    """Raised when pipeline configuration is invalid."""


# =============================================================================
# Enums
# =============================================================================


class MessageRole(str, Enum):
    """Supported chat message roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class InferenceMode(str, Enum):
    """Inference execution mode."""

    CHAT = "chat"
    COMPLETION = "completion"
    EMBEDDING = "embedding"
    CLASSIFICATION = "classification"
    RERANK = "rerank"


class FinishReason(str, Enum):
    """Normalized model finish reasons."""

    STOP = "stop"
    LENGTH = "length"
    TOOL_CALL = "tool_call"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    UNKNOWN = "unknown"


class PipelineStage(str, Enum):
    """Named pipeline stages for audit and metrics."""

    VALIDATION = "validation"
    PRE_PROCESSING = "pre_processing"
    POLICY_PRE_CHECK = "policy_pre_check"
    CACHE_LOOKUP = "cache_lookup"
    MODEL_ROUTING = "model_routing"
    PROVIDER_EXECUTION = "provider_execution"
    POST_PROCESSING = "post_processing"
    POLICY_POST_CHECK = "policy_post_check"
    CACHE_STORE = "cache_store"
    AUDIT = "audit"


class RiskDecision(str, Enum):
    """Policy decision result."""

    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class InferenceConfig:
    """Global pipeline configuration."""

    default_model: str = "default-model"
    max_input_chars: int = 120_000
    max_output_tokens: int = 2048
    request_timeout_seconds: float = 60.0
    provider_timeout_seconds: float = 55.0
    max_retries: int = 2
    retry_base_delay_seconds: float = 0.4
    retry_max_delay_seconds: float = 6.0
    retry_jitter: bool = True
    enable_cache: bool = True
    cache_ttl_seconds: int = 900
    cache_max_items: int = 5_000
    cache_streaming_responses: bool = False
    enable_audit: bool = True
    enable_metrics: bool = True
    enable_policy_checks: bool = True
    enable_post_processing: bool = True
    redact_audit_payloads: bool = True
    version: str = "1.0.0"

    def validate(self) -> None:
        if not self.default_model:
            raise InferenceConfigurationError("default_model is required")
        if self.max_input_chars <= 0:
            raise InferenceConfigurationError("max_input_chars must be positive")
        if self.max_output_tokens <= 0:
            raise InferenceConfigurationError("max_output_tokens must be positive")
        if self.request_timeout_seconds <= 0:
            raise InferenceConfigurationError("request_timeout_seconds must be positive")
        if self.provider_timeout_seconds <= 0:
            raise InferenceConfigurationError("provider_timeout_seconds must be positive")
        if self.max_retries < 0:
            raise InferenceConfigurationError("max_retries must be >= 0")
        if self.cache_ttl_seconds <= 0:
            raise InferenceConfigurationError("cache_ttl_seconds must be positive")
        if self.cache_max_items <= 0:
            raise InferenceConfigurationError("cache_max_items must be positive")


@dataclass(frozen=True)
class InferenceContext:
    """Context metadata for a pipeline request."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    application: Optional[str] = None
    trace_id: Optional[str] = None
    locale: Optional[str] = None
    domain: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatMessage:
    """Normalized chat message."""

    role: MessageRole
    content: str
    name: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InferenceOptions:
    """Provider-neutral inference options."""

    temperature: float = 0.2
    top_p: float = 1.0
    max_tokens: Optional[int] = None
    stop: Sequence[str] = field(default_factory=tuple)
    seed: Optional[int] = None
    stream: bool = False
    tools: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    response_format: Optional[Mapping[str, Any]] = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InferenceRequest:
    """Canonical inference request."""

    mode: InferenceMode
    messages: Sequence[ChatMessage] = field(default_factory=tuple)
    prompt: Optional[str] = None
    input_texts: Sequence[str] = field(default_factory=tuple)
    model: Optional[str] = None
    options: InferenceOptions = field(default_factory=InferenceOptions)
    context: InferenceContext = field(default_factory=InferenceContext)
    cache_key: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TokenUsage:
    """Normalized token usage."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class ToolCall:
    """Normalized tool call representation."""

    id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InferenceResponse:
    """Canonical inference response."""

    request_id: str
    model: str
    output_text: str = ""
    output_messages: Sequence[ChatMessage] = field(default_factory=tuple)
    embeddings: Sequence[Sequence[float]] = field(default_factory=tuple)
    scores: Sequence[float] = field(default_factory=tuple)
    tool_calls: Sequence[ToolCall] = field(default_factory=tuple)
    finish_reason: FinishReason = FinishReason.UNKNOWN
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: float = 0.0
    cached: bool = False
    provider: Optional[str] = None
    raw_response: Optional[Mapping[str, Any]] = None
    warnings: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


@dataclass(frozen=True)
class StreamChunk:
    """Streaming output chunk."""

    request_id: str
    delta: str = ""
    finish_reason: Optional[FinishReason] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyResult:
    """Policy evaluation result."""

    decision: RiskDecision
    reason: str
    risk_score: float = 0.0
    violations: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StageTiming:
    """Timing information for one pipeline stage."""

    stage: PipelineStage
    latency_ms: float
    success: bool
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BatchInferenceResult:
    """Batch inference result."""

    batch_id: str
    created_at: str
    total_items: int
    responses: Sequence[InferenceResponse]
    failures: Sequence[Mapping[str, Any]] = field(default_factory=tuple)


# =============================================================================
# Protocols
# =============================================================================


class ModelProvider(Protocol):
    """Provider abstraction for model execution."""

    @property
    def name(self) -> str:
        """Provider name."""

    async def infer(self, request: InferenceRequest) -> InferenceResponse:
        """Execute non-streaming inference."""

    async def stream(self, request: InferenceRequest) -> AsyncIterator[StreamChunk]:
        """Execute streaming inference."""


class ModelRouter(Protocol):
    """Routes a request to a model/provider."""

    async def route(self, request: InferenceRequest) -> Tuple[str, ModelProvider]:
        """Return selected model name and provider."""


class PolicyEngine(Protocol):
    """Policy/guardrail evaluation interface."""

    async def evaluate_request(self, request: InferenceRequest) -> PolicyResult:
        """Evaluate a request before provider execution."""

    async def evaluate_response(self, request: InferenceRequest, response: InferenceResponse) -> PolicyResult:
        """Evaluate a response after provider execution."""


class CacheBackend(Protocol):
    """Cache interface."""

    async def get(self, key: str) -> Optional[InferenceResponse]:
        """Get cached response."""

    async def set(self, key: str, value: InferenceResponse, ttl_seconds: int) -> None:
        """Store cached response."""


class AuditSink(Protocol):
    """Audit event sink."""

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        """Emit audit event."""


class MetricsSink(Protocol):
    """Metrics sink interface."""

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Increment a counter."""

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Observe a distribution value."""


class PreProcessor(Protocol):
    """Request pre-processor."""

    async def process(self, request: InferenceRequest) -> InferenceRequest:
        """Return transformed request."""


class PostProcessor(Protocol):
    """Response post-processor."""

    async def process(self, request: InferenceRequest, response: InferenceResponse) -> InferenceResponse:
        """Return transformed response."""


# =============================================================================
# Utility Functions
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def estimate_tokens(text: str) -> int:
    """Very rough token estimation for provider-neutral metrics.

    Production deployments should replace this with provider/model-specific
    tokenizers where exact billing or truncation matters.
    """

    if not text:
        return 0
    return max(1, int(len(text) / 4))


def request_text_size(request: InferenceRequest) -> int:
    total = len(request.prompt or "")
    total += sum(len(message.content) for message in request.messages)
    total += sum(len(text) for text in request.input_texts)
    return total


def redact_text(text: str) -> str:
    """Basic redaction for audit payloads."""

    if not text:
        return text
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", text)
    text = re.sub(r"\b(?:\+?\d[\d\s().-]{7,}\d)\b", "[REDACTED_PHONE]", text)
    text = re.sub(r"\b\d{3}[.-]?\d{2}[.-]?\d{4}\b", "[REDACTED_ID]", text)
    return text


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def build_cache_key(request: InferenceRequest) -> str:
    payload = {
        "mode": request.mode.value,
        "messages": [asdict(message) for message in request.messages],
        "prompt": request.prompt,
        "input_texts": list(request.input_texts),
        "model": request.model,
        "options": asdict(request.options),
        "tenant_id": request.context.tenant_id,
        "application": request.context.application,
    }
    return stable_hash(safe_json(payload))


# =============================================================================
# Default Infrastructure Adapters
# =============================================================================


class InMemoryTTLCache:
    """Simple async in-memory TTL LRU cache."""

    def __init__(self, max_items: int = 5_000) -> None:
        self.max_items = max_items
        self._items: OrderedDict[str, Tuple[float, InferenceResponse]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[InferenceResponse]:
        async with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, response = item
            if expires_at < time.time():
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return InferenceResponse(**{**response.to_dict(), "cached": True})

    async def set(self, key: str, value: InferenceResponse, ttl_seconds: int) -> None:
        async with self._lock:
            expires_at = time.time() + ttl_seconds
            self._items[key] = (expires_at, value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)


class LoggingAuditSink:
    """Audit sink using Python logging."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        self.logger.info("audit_event=%s payload=%s", event_name, safe_json(payload))


class LoggingMetricsSink:
    """Metrics sink using Python logging."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("metric_counter=%s value=%s tags=%s", name, value, dict(tags or {}))

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("metric_observe=%s value=%s tags=%s", name, value, dict(tags or {}))


class BasicPolicyEngine:
    """Default lightweight policy engine.

    This implementation is intentionally conservative and dependency-light.
    Enterprise deployments can replace it with a full governance service.
    """

    BLOCKED_PATTERNS = (
        re.compile(r"\b(password|secret key|private key|api[_ -]?key)\b", re.IGNORECASE),
    )

    async def evaluate_request(self, request: InferenceRequest) -> PolicyResult:
        text = self._request_text(request)
        violations = ["sensitive_secret_reference"] if any(pattern.search(text) for pattern in self.BLOCKED_PATTERNS) else []
        if violations:
            return PolicyResult(
                decision=RiskDecision.REVIEW,
                reason="Request may contain sensitive secret references.",
                risk_score=0.65,
                violations=tuple(violations),
            )
        return PolicyResult(decision=RiskDecision.ALLOW, reason="Request passed default policy checks.")

    async def evaluate_response(self, request: InferenceRequest, response: InferenceResponse) -> PolicyResult:
        if any(pattern.search(response.output_text) for pattern in self.BLOCKED_PATTERNS):
            return PolicyResult(
                decision=RiskDecision.REVIEW,
                reason="Response may contain sensitive secret references.",
                risk_score=0.70,
                violations=("sensitive_secret_reference",),
            )
        return PolicyResult(decision=RiskDecision.ALLOW, reason="Response passed default policy checks.")

    def _request_text(self, request: InferenceRequest) -> str:
        return "\n".join(
            [request.prompt or ""]
            + [message.content for message in request.messages]
            + list(request.input_texts)
        )


class StaticModelRouter:
    """Router that sends all requests to one provider/model unless overridden."""

    def __init__(self, provider: ModelProvider, default_model: str) -> None:
        self.provider = provider
        self.default_model = default_model

    async def route(self, request: InferenceRequest) -> Tuple[str, ModelProvider]:
        return request.model or self.default_model, self.provider


class EchoModelProvider:
    """Test provider that echoes request content.

    Useful for integration tests and local pipeline validation.
    Do not use as a real model provider.
    """

    def __init__(self, provider_name: str = "echo") -> None:
        self._name = provider_name

    @property
    def name(self) -> str:
        return self._name

    async def infer(self, request: InferenceRequest) -> InferenceResponse:
        started = time.perf_counter()
        await asyncio.sleep(0)
        text = request.prompt or "\n".join(message.content for message in request.messages) or "\n".join(request.input_texts)
        output = f"Echo response: {text[:1000]}"
        usage = TokenUsage(
            input_tokens=estimate_tokens(text),
            output_tokens=estimate_tokens(output),
            total_tokens=estimate_tokens(text) + estimate_tokens(output),
        )
        return InferenceResponse(
            request_id=request.context.request_id,
            model=request.model or "echo-model",
            output_text=output,
            finish_reason=FinishReason.STOP,
            usage=usage,
            latency_ms=(time.perf_counter() - started) * 1000,
            provider=self.name,
        )

    async def stream(self, request: InferenceRequest) -> AsyncIterator[StreamChunk]:
        response = await self.infer(request)
        words = response.output_text.split()
        for word in words:
            await asyncio.sleep(0)
            yield StreamChunk(request_id=request.context.request_id, delta=word + " ")
        yield StreamChunk(request_id=request.context.request_id, finish_reason=FinishReason.STOP)


# =============================================================================
# Pre/Post Processors
# =============================================================================


class WhitespacePreProcessor:
    """Normalizes whitespace in prompts and messages."""

    async def process(self, request: InferenceRequest) -> InferenceRequest:
        messages = tuple(
            ChatMessage(
                role=message.role,
                content=normalize_whitespace(message.content),
                name=message.name,
                metadata=message.metadata,
            )
            for message in request.messages
        )
        input_texts = tuple(normalize_whitespace(text) for text in request.input_texts)
        prompt = normalize_whitespace(request.prompt) if request.prompt else request.prompt
        return InferenceRequest(
            mode=request.mode,
            messages=messages,
            prompt=prompt,
            input_texts=input_texts,
            model=request.model,
            options=request.options,
            context=request.context,
            cache_key=request.cache_key,
            metadata=request.metadata,
        )


class OutputTrimPostProcessor:
    """Trims excess whitespace from response output."""

    async def process(self, request: InferenceRequest, response: InferenceResponse) -> InferenceResponse:
        data = response.to_dict()
        data["output_text"] = response.output_text.strip()
        return InferenceResponse(**data)


# =============================================================================
# Validation
# =============================================================================


class InferenceRequestValidator:
    """Validates canonical inference requests."""

    def __init__(self, config: InferenceConfig) -> None:
        self.config = config

    def validate(self, request: InferenceRequest) -> None:
        if not isinstance(request.mode, InferenceMode):
            raise InferenceValidationError("request.mode must be an InferenceMode")

        size = request_text_size(request)
        if size <= 0:
            raise InferenceValidationError("request must include messages, prompt, or input_texts")
        if size > self.config.max_input_chars:
            raise InferenceValidationError(
                f"request input is too large: {size} chars > {self.config.max_input_chars}"
            )

        if request.mode == InferenceMode.CHAT and not request.messages:
            raise InferenceValidationError("chat mode requires messages")
        if request.mode == InferenceMode.COMPLETION and not request.prompt:
            raise InferenceValidationError("completion mode requires prompt")
        if request.mode in {InferenceMode.EMBEDDING, InferenceMode.CLASSIFICATION, InferenceMode.RERANK} and not request.input_texts:
            raise InferenceValidationError(f"{request.mode.value} mode requires input_texts")

        for message in request.messages:
            if not isinstance(message.role, MessageRole):
                raise InferenceValidationError("message.role must be a MessageRole")
            if not isinstance(message.content, str) or not message.content.strip():
                raise InferenceValidationError("message.content must be a non-empty string")

        options = request.options
        if not 0 <= options.temperature <= 2:
            raise InferenceValidationError("temperature must be between 0 and 2")
        if not 0 <= options.top_p <= 1:
            raise InferenceValidationError("top_p must be between 0 and 1")
        if options.max_tokens is not None and options.max_tokens <= 0:
            raise InferenceValidationError("max_tokens must be positive")
        if options.max_tokens is not None and options.max_tokens > self.config.max_output_tokens:
            raise InferenceValidationError(
                f"max_tokens exceeds configured limit: {options.max_tokens} > {self.config.max_output_tokens}"
            )


# =============================================================================
# Pipeline
# =============================================================================


class InferencePipeline:
    """Enterprise inference orchestration pipeline."""

    def __init__(
        self,
        *,
        config: Optional[InferenceConfig] = None,
        router: ModelRouter,
        cache: Optional[CacheBackend] = None,
        policy_engine: Optional[PolicyEngine] = None,
        audit_sink: Optional[AuditSink] = None,
        metrics_sink: Optional[MetricsSink] = None,
        pre_processors: Sequence[PreProcessor] = (),
        post_processors: Sequence[PostProcessor] = (),
    ) -> None:
        self.config = config or InferenceConfig()
        self.config.validate()
        self.router = router
        self.cache = cache or InMemoryTTLCache(max_items=self.config.cache_max_items)
        self.policy_engine = policy_engine or BasicPolicyEngine()
        self.audit_sink = audit_sink or LoggingAuditSink()
        self.metrics_sink = metrics_sink or LoggingMetricsSink()
        self.pre_processors = tuple(pre_processors)
        self.post_processors = tuple(post_processors)
        self.validator = InferenceRequestValidator(self.config)

    async def run(self, request: InferenceRequest) -> InferenceResponse:
        """Execute a non-streaming inference request."""

        pipeline_started = time.perf_counter()
        timings: List[StageTiming] = []
        final_model = request.model or self.config.default_model
        provider_name: Optional[str] = None

        try:
            request = await self._stage(
                PipelineStage.VALIDATION,
                timings,
                lambda: self._validate_request(request),
            )

            request = await self._stage(
                PipelineStage.PRE_PROCESSING,
                timings,
                lambda: self._run_pre_processors(request),
            )

            if self.config.enable_policy_checks:
                await self._stage(
                    PipelineStage.POLICY_PRE_CHECK,
                    timings,
                    lambda: self._enforce_request_policy(request),
                )

            cache_key = request.cache_key or build_cache_key(request)
            if self.config.enable_cache and not request.options.stream:
                cached = await self._stage(
                    PipelineStage.CACHE_LOOKUP,
                    timings,
                    lambda: self.cache.get(cache_key),
                )
                if cached is not None:
                    await self._record_success_metrics(cached, cached=True)
                    await self._audit_event("inference_cache_hit", request, cached, timings)
                    return self._with_pipeline_metadata(cached, timings, pipeline_started)

            final_model, provider = await self._stage(
                PipelineStage.MODEL_ROUTING,
                timings,
                lambda: self.router.route(request),
            )
            provider_name = provider.name
            routed_request = self._replace_request_model(request, final_model)

            response = await self._stage(
                PipelineStage.PROVIDER_EXECUTION,
                timings,
                lambda: self._execute_with_retry(provider, routed_request),
            )

            if self.config.enable_post_processing:
                response = await self._stage(
                    PipelineStage.POST_PROCESSING,
                    timings,
                    lambda: self._run_post_processors(routed_request, response),
                )

            if self.config.enable_policy_checks:
                await self._stage(
                    PipelineStage.POLICY_POST_CHECK,
                    timings,
                    lambda: self._enforce_response_policy(routed_request, response),
                )

            if self.config.enable_cache:
                await self._stage(
                    PipelineStage.CACHE_STORE,
                    timings,
                    lambda: self.cache.set(cache_key, response, self.config.cache_ttl_seconds),
                )

            response = self._with_pipeline_metadata(response, timings, pipeline_started)
            await self._record_success_metrics(response, cached=False)
            await self._audit_event("inference_completed", routed_request, response, timings)
            return response

        except Exception as exc:
            latency_ms = (time.perf_counter() - pipeline_started) * 1000
            await self._record_failure_metrics(exc, final_model, provider_name, latency_ms)
            await self._audit_failure("inference_failed", request, exc, timings, latency_ms)
            raise

    async def stream(self, request: InferenceRequest) -> AsyncIterator[StreamChunk]:
        """Execute a streaming inference request.

        Streaming responses are not cached by default. If stream caching is
        enabled, callers should use run() with a provider that returns a full
        response instead of using this method.
        """

        request = self._replace_options(request, stream=True)
        started = time.perf_counter()
        timings: List[StageTiming] = []

        try:
            request = await self._stage(PipelineStage.VALIDATION, timings, lambda: self._validate_request(request))
            request = await self._stage(PipelineStage.PRE_PROCESSING, timings, lambda: self._run_pre_processors(request))

            if self.config.enable_policy_checks:
                await self._stage(PipelineStage.POLICY_PRE_CHECK, timings, lambda: self._enforce_request_policy(request))

            model, provider = await self._stage(PipelineStage.MODEL_ROUTING, timings, lambda: self.router.route(request))
            routed_request = self._replace_request_model(request, model)

            await self._audit_event("inference_stream_started", routed_request, None, timings)
            collected_chars = 0

            async for chunk in self._stream_with_timeout(provider, routed_request):
                collected_chars += len(chunk.delta or "")
                yield chunk

            latency_ms = (time.perf_counter() - started) * 1000
            await self.metrics_sink.observe("ai.inference.stream.latency_ms", latency_ms, self._metric_tags(model, provider.name, False))
            await self.metrics_sink.observe("ai.inference.stream.output_chars", collected_chars, self._metric_tags(model, provider.name, False))
            await self._audit_event(
                "inference_stream_completed",
                routed_request,
                None,
                timings,
                extra={"latency_ms": round(latency_ms, 3), "output_chars": collected_chars},
            )

        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            await self._record_failure_metrics(exc, request.model or self.config.default_model, None, latency_ms)
            await self._audit_failure("inference_stream_failed", request, exc, timings, latency_ms)
            raise

    async def run_batch(
        self,
        requests: Sequence[InferenceRequest],
        *,
        concurrency: int = 5,
        continue_on_error: bool = True,
    ) -> BatchInferenceResult:
        """Execute multiple inference requests with bounded concurrency."""

        if concurrency <= 0:
            raise InferenceValidationError("concurrency must be positive")

        batch_id = str(uuid.uuid4())
        semaphore = asyncio.Semaphore(concurrency)
        responses: List[Optional[InferenceResponse]] = [None] * len(requests)
        failures: List[Mapping[str, Any]] = []

        async def run_one(index: int, item: InferenceRequest) -> None:
            async with semaphore:
                try:
                    responses[index] = await self.run(item)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Batch inference failed for index=%s", index)
                    failures.append({
                        "index": index,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "request_id": item.context.request_id,
                    })
                    if not continue_on_error:
                        raise

        await asyncio.gather(*(run_one(index, request) for index, request in enumerate(requests)))

        return BatchInferenceResult(
            batch_id=batch_id,
            created_at=utc_now_iso(),
            total_items=len(requests),
            responses=tuple(response for response in responses if response is not None),
            failures=tuple(failures),
        )

    async def _validate_request(self, request: InferenceRequest) -> InferenceRequest:
        self.validator.validate(request)
        return request

    async def _run_pre_processors(self, request: InferenceRequest) -> InferenceRequest:
        result = request
        for processor in self.pre_processors:
            result = await processor.process(result)
        return result

    async def _run_post_processors(self, request: InferenceRequest, response: InferenceResponse) -> InferenceResponse:
        result = response
        for processor in self.post_processors:
            result = await processor.process(request, result)
        return result

    async def _enforce_request_policy(self, request: InferenceRequest) -> PolicyResult:
        result = await self.policy_engine.evaluate_request(request)
        if result.decision == RiskDecision.BLOCK:
            raise InferencePolicyError(f"Request blocked by policy: {result.reason}")
        return result

    async def _enforce_response_policy(self, request: InferenceRequest, response: InferenceResponse) -> PolicyResult:
        result = await self.policy_engine.evaluate_response(request, response)
        if result.decision == RiskDecision.BLOCK:
            raise InferencePolicyError(f"Response blocked by policy: {result.reason}")
        return result

    async def _execute_with_retry(self, provider: ModelProvider, request: InferenceRequest) -> InferenceResponse:
        last_error: Optional[BaseException] = None
        attempts = self.config.max_retries + 1

        for attempt in range(attempts):
            try:
                return await asyncio.wait_for(provider.infer(request), timeout=self.config.provider_timeout_seconds)
            except asyncio.TimeoutError as exc:
                last_error = exc
                normalized = InferenceTimeoutError(
                    f"Provider inference timed out after {self.config.provider_timeout_seconds}s"
                )
                if attempt >= attempts - 1:
                    raise normalized from exc
            except InferenceRateLimitError as exc:
                last_error = exc
                if attempt >= attempts - 1:
                    raise
            except InferenceProviderError as exc:
                last_error = exc
                if attempt >= attempts - 1:
                    raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt >= attempts - 1:
                    raise InferenceProviderError(str(exc)) from exc

            await asyncio.sleep(self._retry_delay(attempt))

        raise InferenceProviderError(f"Provider failed after retries: {last_error}")

    async def _stream_with_timeout(self, provider: ModelProvider, request: InferenceRequest) -> AsyncIterator[StreamChunk]:
        deadline = time.perf_counter() + self.config.provider_timeout_seconds
        try:
            async for chunk in provider.stream(request):
                if time.perf_counter() > deadline:
                    raise InferenceTimeoutError(
                        f"Provider stream timed out after {self.config.provider_timeout_seconds}s"
                    )
                yield chunk
        except asyncio.TimeoutError as exc:
            raise InferenceTimeoutError(
                f"Provider stream timed out after {self.config.provider_timeout_seconds}s"
            ) from exc

    def _retry_delay(self, attempt: int) -> float:
        delay = min(self.config.retry_max_delay_seconds, self.config.retry_base_delay_seconds * (2 ** attempt))
        if self.config.retry_jitter:
            delay *= random.uniform(0.75, 1.25)
        return delay

    async def _stage(
        self,
        stage: PipelineStage,
        timings: List[StageTiming],
        func: Callable[[], Any],
    ) -> Any:
        started = time.perf_counter()
        try:
            result = func()
            if asyncio.iscoroutine(result):
                result = await result
            timings.append(StageTiming(stage=stage, latency_ms=(time.perf_counter() - started) * 1000, success=True))
            return result
        except Exception:
            timings.append(StageTiming(stage=stage, latency_ms=(time.perf_counter() - started) * 1000, success=False))
            raise

    def _replace_request_model(self, request: InferenceRequest, model: str) -> InferenceRequest:
        return InferenceRequest(
            mode=request.mode,
            messages=request.messages,
            prompt=request.prompt,
            input_texts=request.input_texts,
            model=model,
            options=request.options,
            context=request.context,
            cache_key=request.cache_key,
            metadata=request.metadata,
        )

    def _replace_options(self, request: InferenceRequest, **changes: Any) -> InferenceRequest:
        options_data = asdict(request.options)
        options_data.update(changes)
        return InferenceRequest(
            mode=request.mode,
            messages=request.messages,
            prompt=request.prompt,
            input_texts=request.input_texts,
            model=request.model,
            options=InferenceOptions(**options_data),
            context=request.context,
            cache_key=request.cache_key,
            metadata=request.metadata,
        )

    def _with_pipeline_metadata(
        self,
        response: InferenceResponse,
        timings: Sequence[StageTiming],
        pipeline_started: float,
    ) -> InferenceResponse:
        data = response.to_dict()
        metadata = dict(response.metadata or {})
        metadata.update({
            "pipeline_version": self.config.version,
            "pipeline_latency_ms": round((time.perf_counter() - pipeline_started) * 1000, 3),
            "stage_timings": [asdict(timing) for timing in timings],
        })
        data["metadata"] = metadata
        return InferenceResponse(**data)

    async def _record_success_metrics(self, response: InferenceResponse, *, cached: bool) -> None:
        if not self.config.enable_metrics:
            return
        tags = self._metric_tags(response.model, response.provider, cached)
        await self.metrics_sink.increment("ai.inference.success", 1, tags)
        await self.metrics_sink.observe("ai.inference.latency_ms", response.latency_ms, tags)
        await self.metrics_sink.observe("ai.inference.tokens.total", response.usage.total_tokens, tags)

    async def _record_failure_metrics(
        self,
        exc: BaseException,
        model: str,
        provider: Optional[str],
        latency_ms: float,
    ) -> None:
        if not self.config.enable_metrics:
            return
        tags = self._metric_tags(model, provider, False)
        tags = {**tags, "error_type": type(exc).__name__}
        await self.metrics_sink.increment("ai.inference.failure", 1, tags)
        await self.metrics_sink.observe("ai.inference.failure.latency_ms", latency_ms, tags)

    def _metric_tags(self, model: Optional[str], provider: Optional[str], cached: bool) -> Mapping[str, str]:
        return {
            "model": model or "unknown",
            "provider": provider or "unknown",
            "cached": str(cached).lower(),
        }

    async def _audit_event(
        self,
        event_name: str,
        request: InferenceRequest,
        response: Optional[InferenceResponse],
        timings: Sequence[StageTiming],
        extra: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if not self.config.enable_audit:
            return
        payload: Dict[str, Any] = {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": request.context.request_id,
            "tenant_id": request.context.tenant_id,
            "user_id": request.context.user_id,
            "application": request.context.application,
            "trace_id": request.context.trace_id,
            "mode": request.mode.value,
            "model": request.model,
            "input_chars": request_text_size(request),
            "stage_timings": [asdict(timing) for timing in timings],
        }
        if response is not None:
            payload.update({
                "provider": response.provider,
                "finish_reason": response.finish_reason.value,
                "cached": response.cached,
                "latency_ms": response.latency_ms,
                "usage": asdict(response.usage),
                "output_chars": len(response.output_text or ""),
            })
        if extra:
            payload.update(dict(extra))
        if self.config.redact_audit_payloads:
            payload = self._redact_payload(payload)
        await self.audit_sink.emit(event_name, payload)

    async def _audit_failure(
        self,
        event_name: str,
        request: InferenceRequest,
        exc: BaseException,
        timings: Sequence[StageTiming],
        latency_ms: float,
    ) -> None:
        if not self.config.enable_audit:
            return
        payload = {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": request.context.request_id,
            "tenant_id": request.context.tenant_id,
            "user_id": request.context.user_id,
            "application": request.context.application,
            "trace_id": request.context.trace_id,
            "mode": request.mode.value,
            "model": request.model,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "latency_ms": round(latency_ms, 3),
            "stage_timings": [asdict(timing) for timing in timings],
        }
        if self.config.redact_audit_payloads:
            payload = self._redact_payload(payload)
        await self.audit_sink.emit(event_name, payload)

    def _redact_payload(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        def redact(value: Any) -> Any:
            if isinstance(value, str):
                return redact_text(value)
            if isinstance(value, Mapping):
                return {k: redact(v) for k, v in value.items()}
            if isinstance(value, list):
                return [redact(v) for v in value]
            return value

        return dict(redact(dict(payload)))


# =============================================================================
# Builder / Factory
# =============================================================================


def build_default_inference_pipeline(
    *,
    provider: Optional[ModelProvider] = None,
    default_model: str = "echo-model",
    config_overrides: Optional[Mapping[str, Any]] = None,
) -> InferencePipeline:
    """Build a ready-to-run default pipeline.

    By default this uses EchoModelProvider for local validation. Inject a real
    provider in production.
    """

    config_data = asdict(InferenceConfig(default_model=default_model))
    if config_overrides:
        config_data.update(dict(config_overrides))
    config = InferenceConfig(**config_data)
    active_provider = provider or EchoModelProvider()
    router = StaticModelRouter(provider=active_provider, default_model=config.default_model)
    return InferencePipeline(
        config=config,
        router=router,
        pre_processors=(WhitespacePreProcessor(),),
        post_processors=(OutputTrimPostProcessor(),),
    )


# =============================================================================
# Convenience Request Builders
# =============================================================================


def chat_request(
    messages: Sequence[Union[ChatMessage, Mapping[str, Any]]],
    *,
    model: Optional[str] = None,
    options: Optional[InferenceOptions] = None,
    context: Optional[InferenceContext] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> InferenceRequest:
    normalized: List[ChatMessage] = []
    for message in messages:
        if isinstance(message, ChatMessage):
            normalized.append(message)
        else:
            normalized.append(
                ChatMessage(
                    role=MessageRole(str(message["role"])),
                    content=str(message["content"]),
                    name=message.get("name"),
                    metadata=message.get("metadata", {}),
                )
            )
    return InferenceRequest(
        mode=InferenceMode.CHAT,
        messages=tuple(normalized),
        model=model,
        options=options or InferenceOptions(),
        context=context or InferenceContext(),
        metadata=metadata or {},
    )


def completion_request(
    prompt: str,
    *,
    model: Optional[str] = None,
    options: Optional[InferenceOptions] = None,
    context: Optional[InferenceContext] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> InferenceRequest:
    return InferenceRequest(
        mode=InferenceMode.COMPLETION,
        prompt=prompt,
        model=model,
        options=options or InferenceOptions(),
        context=context or InferenceContext(),
        metadata=metadata or {},
    )


def embedding_request(
    input_texts: Sequence[str],
    *,
    model: Optional[str] = None,
    context: Optional[InferenceContext] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> InferenceRequest:
    return InferenceRequest(
        mode=InferenceMode.EMBEDDING,
        input_texts=tuple(input_texts),
        model=model,
        options=InferenceOptions(temperature=0.0),
        context=context or InferenceContext(),
        metadata=metadata or {},
    )


# =============================================================================
# Demo
# =============================================================================


async def _demo_async() -> None:
    logging.basicConfig(level=logging.INFO)

    pipeline = build_default_inference_pipeline()
    request = chat_request(
        [
            {"role": "system", "content": "Você é um assistente enterprise."},
            {"role": "user", "content": "Explique uma arquitetura de inferência robusta."},
        ],
        context=InferenceContext(
            tenant_id="demo",
            user_id="user-001",
            application="ai-platform",
            trace_id=str(uuid.uuid4()),
            locale="pt-BR",
        ),
        options=InferenceOptions(temperature=0.2, max_tokens=512),
    )

    response = await pipeline.run(request)
    print(response.to_json(indent=2))

    print("\nStreaming demo:")
    async for chunk in pipeline.stream(request):
        if chunk.delta:
            print(chunk.delta, end="")
    print()


if __name__ == "__main__":
    asyncio.run(_demo_async())

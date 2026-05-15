"""
data/ai/llm_connector.py

Enterprise-grade LLM connector layer.

This module provides provider-agnostic connectors for Large Language Model
services and a production-oriented OpenAI-compatible HTTP connector. It is
intended to be used by data/ai/inference_pipeline.py through the ModelProvider
protocol, while remaining standalone enough for direct use in services.

Main capabilities:

- Provider-neutral request/response models
- OpenAI-compatible chat/completions/embeddings APIs
- Async HTTP execution through the Python standard library fallback
- Optional aiohttp/httpx adapters can be injected externally
- Retry with exponential backoff and jitter
- Timeout handling
- Rate limiting
- Circuit breaker
- Streaming Server-Sent Events parsing
- Token usage normalization
- Error normalization
- Audit and metrics hooks
- Secret-safe logging
- Multi-provider registry

Python:
    3.10+
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import ssl
import time
import uuid
from abc import ABC, abstractmethod
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
from urllib import error as urllib_error
from urllib import request as urllib_request

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class LLMConnectorError(Exception):
    """Base exception for LLM connector failures."""


class LLMConfigurationError(LLMConnectorError):
    """Raised when connector configuration is invalid."""


class LLMAuthenticationError(LLMConnectorError):
    """Raised when provider authentication fails."""


class LLMRateLimitError(LLMConnectorError):
    """Raised when provider or local rate limit is exceeded."""


class LLMTimeoutError(LLMConnectorError):
    """Raised when a provider request times out."""


class LLMProviderError(LLMConnectorError):
    """Raised when provider returns an error."""


class LLMTransientError(LLMProviderError):
    """Raised for transient provider failures that can be retried."""


class LLMValidationError(LLMConnectorError):
    """Raised when request data is invalid."""


class LLMCircuitOpenError(LLMConnectorError):
    """Raised when connector circuit breaker is open."""


# =============================================================================
# Enums
# =============================================================================


class LLMProviderType(str, Enum):
    """Known provider families."""

    OPENAI_COMPATIBLE = "openai_compatible"
    AZURE_OPENAI = "azure_openai"
    ANTHROPIC_COMPATIBLE = "anthropic_compatible"
    LOCAL = "local"
    CUSTOM = "custom"


class LLMMessageRole(str, Enum):
    """Chat roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LLMFinishReason(str, Enum):
    """Normalized finish reasons."""

    STOP = "stop"
    LENGTH = "length"
    TOOL_CALL = "tool_call"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    UNKNOWN = "unknown"


class CircuitState(str, Enum):
    """Circuit breaker state."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class LLMConnectorConfig:
    """Connector configuration."""

    provider_name: str
    provider_type: LLMProviderType = LLMProviderType.OPENAI_COMPATIBLE
    base_url: str = "https://api.openai.com/v1"
    api_key_env: Optional[str] = "OPENAI_API_KEY"
    api_key: Optional[str] = None
    organization: Optional[str] = None
    project: Optional[str] = None
    default_chat_model: str = "gpt-4o-mini"
    default_embedding_model: str = "text-embedding-3-small"
    timeout_seconds: float = 60.0
    connect_timeout_seconds: float = 10.0
    max_retries: int = 2
    retry_base_delay_seconds: float = 0.35
    retry_max_delay_seconds: float = 8.0
    retry_jitter: bool = True
    max_concurrent_requests: int = 50
    local_rate_limit_per_minute: Optional[int] = None
    user_agent: str = "enterprise-llm-connector/1.0"
    verify_ssl: bool = True
    enable_circuit_breaker: bool = True
    circuit_failure_threshold: int = 5
    circuit_recovery_seconds: float = 30.0
    redact_logs: bool = True
    include_raw_response: bool = False
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    extra_params: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def resolved_api_key(self) -> Optional[str]:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.getenv(self.api_key_env)
        return None

    def validate(self) -> None:
        if not self.provider_name:
            raise LLMConfigurationError("provider_name is required")
        if not self.base_url:
            raise LLMConfigurationError("base_url is required")
        if self.timeout_seconds <= 0:
            raise LLMConfigurationError("timeout_seconds must be positive")
        if self.connect_timeout_seconds <= 0:
            raise LLMConfigurationError("connect_timeout_seconds must be positive")
        if self.max_retries < 0:
            raise LLMConfigurationError("max_retries must be >= 0")
        if self.max_concurrent_requests <= 0:
            raise LLMConfigurationError("max_concurrent_requests must be positive")
        if self.local_rate_limit_per_minute is not None and self.local_rate_limit_per_minute <= 0:
            raise LLMConfigurationError("local_rate_limit_per_minute must be positive when provided")
        if self.enable_circuit_breaker:
            if self.circuit_failure_threshold <= 0:
                raise LLMConfigurationError("circuit_failure_threshold must be positive")
            if self.circuit_recovery_seconds <= 0:
                raise LLMConfigurationError("circuit_recovery_seconds must be positive")


@dataclass(frozen=True)
class LLMContext:
    """Request context for audit and provider metadata."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    application: Optional[str] = None
    trace_id: Optional[str] = None
    session_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMMessage:
    """Provider-neutral chat message."""

    role: LLMMessageRole
    content: str
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMToolCall:
    """Normalized tool call."""

    id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMUsage:
    """Token usage."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class LLMChatRequest:
    """Chat completion request."""

    messages: Sequence[LLMMessage]
    model: Optional[str] = None
    temperature: float = 0.2
    top_p: float = 1.0
    max_tokens: Optional[int] = None
    stop: Sequence[str] = field(default_factory=tuple)
    stream: bool = False
    tools: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    tool_choice: Optional[Union[str, Mapping[str, Any]]] = None
    response_format: Optional[Mapping[str, Any]] = None
    seed: Optional[int] = None
    context: LLMContext = field(default_factory=LLMContext)
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMCompletionRequest:
    """Text completion request."""

    prompt: str
    model: Optional[str] = None
    temperature: float = 0.2
    top_p: float = 1.0
    max_tokens: Optional[int] = None
    stop: Sequence[str] = field(default_factory=tuple)
    stream: bool = False
    context: LLMContext = field(default_factory=LLMContext)
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMEmbeddingRequest:
    """Embedding request."""

    input_texts: Sequence[str]
    model: Optional[str] = None
    dimensions: Optional[int] = None
    context: LLMContext = field(default_factory=LLMContext)
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMChatResponse:
    """Chat completion response."""

    request_id: str
    provider: str
    model: str
    content: str
    finish_reason: LLMFinishReason = LLMFinishReason.UNKNOWN
    tool_calls: Sequence[LLMToolCall] = field(default_factory=tuple)
    usage: LLMUsage = field(default_factory=LLMUsage)
    latency_ms: float = 0.0
    raw_response: Optional[Mapping[str, Any]] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMCompletionResponse:
    """Text completion response."""

    request_id: str
    provider: str
    model: str
    text: str
    finish_reason: LLMFinishReason = LLMFinishReason.UNKNOWN
    usage: LLMUsage = field(default_factory=LLMUsage)
    latency_ms: float = 0.0
    raw_response: Optional[Mapping[str, Any]] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMEmbeddingResponse:
    """Embedding response."""

    request_id: str
    provider: str
    model: str
    embeddings: Sequence[Sequence[float]]
    usage: LLMUsage = field(default_factory=LLMUsage)
    latency_ms: float = 0.0
    raw_response: Optional[Mapping[str, Any]] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMStreamChunk:
    """Streaming chat/completion chunk."""

    request_id: str
    provider: str
    model: str
    delta: str = ""
    finish_reason: Optional[LLMFinishReason] = None
    tool_calls: Sequence[LLMToolCall] = field(default_factory=tuple)
    raw_chunk: Optional[Mapping[str, Any]] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HTTPRequest:
    """Internal HTTP request."""

    method: str
    url: str
    headers: Mapping[str, str]
    json_body: Optional[Mapping[str, Any]] = None
    timeout_seconds: Optional[float] = None
    stream: bool = False


@dataclass(frozen=True)
class HTTPResponse:
    """Internal HTTP response."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> Mapping[str, Any]:
        if not self.body:
            return {}
        return json.loads(self.body.decode("utf-8"))


# =============================================================================
# Protocols
# =============================================================================


class AsyncHTTPClient(Protocol):
    """Minimal async HTTP client protocol."""

    async def request(self, request: HTTPRequest) -> HTTPResponse:
        """Execute HTTP request."""

    async def stream(self, request: HTTPRequest) -> AsyncIterator[bytes]:
        """Execute streaming HTTP request and yield raw byte chunks."""


class ConnectorAuditSink(Protocol):
    """Audit sink protocol."""

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        """Emit audit event."""


class ConnectorMetricsSink(Protocol):
    """Metrics sink protocol."""

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Increment counter."""

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Observe metric value."""


# =============================================================================
# Utilities
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_json(data: Mapping[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def join_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / 4))


def redact_secret(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if len(value) <= 8:
        return "[REDACTED]"
    return f"{value[:4]}...[REDACTED]...{value[-4:]}"


def redact_text(text: str) -> str:
    if not text:
        return text
    text = re.sub(r"Bearer\s+[A-Za-z0-9._\-]+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", text)
    text = re.sub(r"\b(?:sk-|xoxb-|ghp_)[A-Za-z0-9_\-]{12,}\b", "[REDACTED_SECRET]", text)
    return text


def redact_mapping(data: Mapping[str, Any]) -> Dict[str, Any]:
    secret_keys = {"authorization", "api_key", "apikey", "token", "password", "secret"}

    def redact(value: Any, key: Optional[str] = None) -> Any:
        if key and key.lower() in secret_keys:
            return "[REDACTED]"
        if isinstance(value, str):
            return redact_text(value)
        if isinstance(value, Mapping):
            return {k: redact(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [redact(v) for v in value]
        return value

    return dict(redact(dict(data)))


def normalize_finish_reason(value: Optional[str]) -> LLMFinishReason:
    if not value:
        return LLMFinishReason.UNKNOWN
    normalized = value.lower()
    if normalized in {"stop", "end_turn"}:
        return LLMFinishReason.STOP
    if normalized in {"length", "max_tokens", "max_output_tokens"}:
        return LLMFinishReason.LENGTH
    if normalized in {"tool_calls", "tool_call", "function_call"}:
        return LLMFinishReason.TOOL_CALL
    if normalized in {"content_filter", "safety"}:
        return LLMFinishReason.CONTENT_FILTER
    return LLMFinishReason.UNKNOWN


# =============================================================================
# HTTP Client: stdlib fallback
# =============================================================================


class UrllibAsyncHTTPClient:
    """Async HTTP client implemented with urllib in worker threads.

    This fallback avoids hard dependencies. In production, prefer injecting an
    aiohttp/httpx-based implementation for higher performance.
    """

    def __init__(self, *, verify_ssl: bool = True) -> None:
        self.verify_ssl = verify_ssl

    async def request(self, request: HTTPRequest) -> HTTPResponse:
        return await asyncio.to_thread(self._request_sync, request)

    async def stream(self, request: HTTPRequest) -> AsyncIterator[bytes]:
        queue: asyncio.Queue[Union[bytes, BaseException, None]] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def worker() -> None:
            try:
                for chunk in self._stream_sync(request):
                    asyncio.run_coroutine_threadsafe(queue.put(chunk), loop).result()
                asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()
            except BaseException as exc:  # noqa: BLE001
                asyncio.run_coroutine_threadsafe(queue.put(exc), loop).result()

        task = asyncio.create_task(asyncio.to_thread(worker))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            await task

    def _request_sync(self, request: HTTPRequest) -> HTTPResponse:
        data = compact_json(request.json_body) if request.json_body is not None else None
        req = urllib_request.Request(
            url=request.url,
            data=data,
            headers=dict(request.headers),
            method=request.method.upper(),
        )
        context = ssl.create_default_context() if self.verify_ssl else ssl._create_unverified_context()  # noqa: SLF001
        try:
            with urllib_request.urlopen(req, timeout=request.timeout_seconds, context=context) as resp:  # noqa: S310
                body = resp.read()
                return HTTPResponse(status_code=resp.status, headers=dict(resp.headers), body=body)
        except urllib_error.HTTPError as exc:
            body = exc.read() if exc.fp else b""
            return HTTPResponse(status_code=exc.code, headers=dict(exc.headers), body=body)
        except TimeoutError as exc:
            raise LLMTimeoutError(str(exc)) from exc
        except urllib_error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise LLMTimeoutError(str(exc)) from exc
            raise LLMTransientError(str(exc)) from exc

    def _stream_sync(self, request: HTTPRequest) -> Iterable[bytes]:
        data = compact_json(request.json_body) if request.json_body is not None else None
        req = urllib_request.Request(
            url=request.url,
            data=data,
            headers=dict(request.headers),
            method=request.method.upper(),
        )
        context = ssl.create_default_context() if self.verify_ssl else ssl._create_unverified_context()  # noqa: SLF001
        try:
            with urllib_request.urlopen(req, timeout=request.timeout_seconds, context=context) as resp:  # noqa: S310
                while True:
                    chunk = resp.readline()
                    if not chunk:
                        break
                    yield chunk
        except urllib_error.HTTPError as exc:
            body = exc.read() if exc.fp else b""
            raise self._exception_from_http_response(HTTPResponse(exc.code, dict(exc.headers), body)) from exc
        except TimeoutError as exc:
            raise LLMTimeoutError(str(exc)) from exc
        except urllib_error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise LLMTimeoutError(str(exc)) from exc
            raise LLMTransientError(str(exc)) from exc

    def _exception_from_http_response(self, response: HTTPResponse) -> LLMConnectorError:
        try:
            payload = response.json()
            message = str(payload.get("error", {}).get("message") or payload.get("message") or response.body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            message = response.body.decode("utf-8", errors="replace")
        if response.status_code in {401, 403}:
            return LLMAuthenticationError(message)
        if response.status_code == 429:
            return LLMRateLimitError(message)
        if response.status_code >= 500:
            return LLMTransientError(message)
        return LLMProviderError(message)


# =============================================================================
# Rate limiter and Circuit breaker
# =============================================================================


class AsyncTokenBucketRateLimiter:
    """Simple async token bucket rate limiter."""

    def __init__(self, rate_per_minute: Optional[int]) -> None:
        self.rate_per_minute = rate_per_minute
        self.capacity = float(rate_per_minute or 0)
        self.tokens = float(rate_per_minute or 0)
        self.updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self.rate_per_minute is None:
            return
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.updated_at
            refill = elapsed * (self.rate_per_minute / 60.0)
            self.tokens = min(self.capacity, self.tokens + refill)
            self.updated_at = now
            if self.tokens >= 1:
                self.tokens -= 1
                return
            wait_seconds = (1 - self.tokens) / (self.rate_per_minute / 60.0)
        await asyncio.sleep(wait_seconds)
        await self.acquire()


class CircuitBreaker:
    """Small async circuit breaker."""

    def __init__(self, *, failure_threshold: int, recovery_seconds: float) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.opened_at: Optional[float] = None
        self._lock = asyncio.Lock()

    async def before_call(self) -> None:
        async with self._lock:
            if self.state == CircuitState.OPEN:
                assert self.opened_at is not None
                if time.monotonic() - self.opened_at >= self.recovery_seconds:
                    self.state = CircuitState.HALF_OPEN
                    return
                raise LLMCircuitOpenError("Circuit breaker is open")

    async def record_success(self) -> None:
        async with self._lock:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.opened_at = None

    async def record_failure(self) -> None:
        async with self._lock:
            self.failure_count += 1
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                self.opened_at = time.monotonic()


# =============================================================================
# Audit / Metrics defaults
# =============================================================================


class LoggingConnectorAuditSink:
    """Audit sink using logging."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        self.logger.info("llm_audit_event=%s payload=%s", event_name, safe_json(payload))


class LoggingConnectorMetricsSink:
    """Metrics sink using logging."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("llm_metric_counter=%s value=%s tags=%s", name, value, dict(tags or {}))

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("llm_metric_observe=%s value=%s tags=%s", name, value, dict(tags or {}))


# =============================================================================
# Base Connector
# =============================================================================


class BaseLLMConnector(ABC):
    """Abstract base connector."""

    def __init__(
        self,
        config: LLMConnectorConfig,
        *,
        http_client: Optional[AsyncHTTPClient] = None,
        audit_sink: Optional[ConnectorAuditSink] = None,
        metrics_sink: Optional[ConnectorMetricsSink] = None,
    ) -> None:
        config.validate()
        self.config = config
        self.http_client = http_client or UrllibAsyncHTTPClient(verify_ssl=config.verify_ssl)
        self.audit_sink = audit_sink or LoggingConnectorAuditSink()
        self.metrics_sink = metrics_sink or LoggingConnectorMetricsSink()
        self._semaphore = asyncio.Semaphore(config.max_concurrent_requests)
        self._rate_limiter = AsyncTokenBucketRateLimiter(config.local_rate_limit_per_minute)
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=config.circuit_failure_threshold,
            recovery_seconds=config.circuit_recovery_seconds,
        )

    @property
    def name(self) -> str:
        return self.config.provider_name

    @abstractmethod
    async def chat(self, request: LLMChatRequest) -> LLMChatResponse:
        """Execute chat request."""

    @abstractmethod
    async def stream_chat(self, request: LLMChatRequest) -> AsyncIterator[LLMStreamChunk]:
        """Execute streaming chat request."""

    @abstractmethod
    async def complete(self, request: LLMCompletionRequest) -> LLMCompletionResponse:
        """Execute completion request."""

    @abstractmethod
    async def embed(self, request: LLMEmbeddingRequest) -> LLMEmbeddingResponse:
        """Execute embedding request."""

    async def _guarded_execute(self, operation: str, context: LLMContext, func: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        tags = self._metric_tags(operation)
        await self._rate_limiter.acquire()
        if self.config.enable_circuit_breaker:
            await self._circuit_breaker.before_call()

        async with self._semaphore:
            try:
                result = await self._execute_with_retry(operation, func)
                latency_ms = (time.perf_counter() - started) * 1000
                if self.config.enable_circuit_breaker:
                    await self._circuit_breaker.record_success()
                await self.metrics_sink.increment("llm.connector.success", 1, tags)
                await self.metrics_sink.observe("llm.connector.latency_ms", latency_ms, tags)
                await self._audit("llm_connector_success", context, {
                    "operation": operation,
                    "latency_ms": round(latency_ms, 3),
                })
                return result
            except Exception as exc:
                latency_ms = (time.perf_counter() - started) * 1000
                if self.config.enable_circuit_breaker and self._is_circuit_failure(exc):
                    await self._circuit_breaker.record_failure()
                fail_tags = {**tags, "error_type": type(exc).__name__}
                await self.metrics_sink.increment("llm.connector.failure", 1, fail_tags)
                await self.metrics_sink.observe("llm.connector.failure_latency_ms", latency_ms, fail_tags)
                await self._audit("llm_connector_failure", context, {
                    "operation": operation,
                    "latency_ms": round(latency_ms, 3),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })
                raise

    async def _execute_with_retry(self, operation: str, func: Callable[[], Any]) -> Any:
        attempts = self.config.max_retries + 1
        for attempt in range(attempts):
            try:
                result = func()
                if asyncio.iscoroutine(result):
                    result = await result
                return result
            except (LLMRateLimitError, LLMTransientError, LLMTimeoutError) as exc:
                if attempt >= attempts - 1:
                    raise
                await asyncio.sleep(self._retry_delay(attempt, exc))
            except Exception:
                raise
        raise LLMProviderError(f"Operation {operation} failed after retries")

    def _retry_delay(self, attempt: int, exc: BaseException) -> float:
        delay = min(self.config.retry_max_delay_seconds, self.config.retry_base_delay_seconds * (2 ** attempt))
        if isinstance(exc, LLMRateLimitError):
            delay = max(delay, 1.0)
        if self.config.retry_jitter:
            delay *= random.uniform(0.75, 1.25)
        return delay

    def _metric_tags(self, operation: str) -> Mapping[str, str]:
        return {
            "provider": self.config.provider_name,
            "provider_type": self.config.provider_type.value,
            "operation": operation,
        }

    def _is_circuit_failure(self, exc: BaseException) -> bool:
        return isinstance(exc, (LLMTransientError, LLMTimeoutError, LLMProviderError)) and not isinstance(
            exc,
            (LLMRateLimitError, LLMAuthenticationError, LLMValidationError),
        )

    async def _audit(self, event_name: str, context: LLMContext, payload: Mapping[str, Any]) -> None:
        data = {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "provider": self.config.provider_name,
            "provider_type": self.config.provider_type.value,
            "request_id": context.request_id,
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "application": context.application,
            "trace_id": context.trace_id,
            **dict(payload),
        }
        if self.config.redact_logs:
            data = redact_mapping(data)
        await self.audit_sink.emit(event_name, data)


# =============================================================================
# OpenAI-compatible connector
# =============================================================================


class OpenAICompatibleConnector(BaseLLMConnector):
    """Connector for OpenAI-compatible APIs.

    Compatible endpoints:
        POST /chat/completions
        POST /completions
        POST /embeddings

    Many providers expose these shapes, including local gateways and enterprise
    model routers. Provider-specific parameters can be passed through `extra`.
    """

    async def chat(self, request: LLMChatRequest) -> LLMChatResponse:
        self._validate_chat_request(request)
        return await self._guarded_execute("chat", request.context, lambda: self._chat_once(request))

    async def stream_chat(self, request: LLMChatRequest) -> AsyncIterator[LLMStreamChunk]:
        self._validate_chat_request(request)
        streamed_request = LLMChatRequest(
            messages=request.messages,
            model=request.model,
            temperature=request.temperature,
            top_p=request.top_p,
            max_tokens=request.max_tokens,
            stop=request.stop,
            stream=True,
            tools=request.tools,
            tool_choice=request.tool_choice,
            response_format=request.response_format,
            seed=request.seed,
            context=request.context,
            extra=request.extra,
        )
        await self._rate_limiter.acquire()
        if self.config.enable_circuit_breaker:
            await self._circuit_breaker.before_call()
        async with self._semaphore:
            try:
                async for chunk in self._stream_chat_once(streamed_request):
                    yield chunk
                if self.config.enable_circuit_breaker:
                    await self._circuit_breaker.record_success()
            except Exception as exc:
                if self.config.enable_circuit_breaker and self._is_circuit_failure(exc):
                    await self._circuit_breaker.record_failure()
                await self._audit("llm_connector_stream_failure", request.context, {
                    "operation": "stream_chat",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })
                raise

    async def complete(self, request: LLMCompletionRequest) -> LLMCompletionResponse:
        self._validate_completion_request(request)
        return await self._guarded_execute("completion", request.context, lambda: self._complete_once(request))

    async def embed(self, request: LLMEmbeddingRequest) -> LLMEmbeddingResponse:
        self._validate_embedding_request(request)
        return await self._guarded_execute("embedding", request.context, lambda: self._embed_once(request))

    async def _chat_once(self, request: LLMChatRequest) -> LLMChatResponse:
        started = time.perf_counter()
        model = request.model or self.config.default_chat_model
        payload = self._build_chat_payload(request, model, stream=False)
        response = await self._post_json("/chat/completions", payload)
        data = self._parse_http_json(response)
        choice = self._first_choice(data)
        message = choice.get("message", {}) if isinstance(choice, Mapping) else {}
        content = message.get("content") or ""
        tool_calls = self._parse_tool_calls(message.get("tool_calls") or [])
        usage = self._parse_usage(data.get("usage", {}), fallback_input=self._chat_input_text(request), fallback_output=content)
        return LLMChatResponse(
            request_id=request.context.request_id,
            provider=self.name,
            model=str(data.get("model") or model),
            content=content,
            finish_reason=normalize_finish_reason(choice.get("finish_reason") if isinstance(choice, Mapping) else None),
            tool_calls=tuple(tool_calls),
            usage=usage,
            latency_ms=(time.perf_counter() - started) * 1000,
            raw_response=data if self.config.include_raw_response else None,
            metadata={"response_id": data.get("id")},
        )

    async def _stream_chat_once(self, request: LLMChatRequest) -> AsyncIterator[LLMStreamChunk]:
        model = request.model or self.config.default_chat_model
        payload = self._build_chat_payload(request, model, stream=True)
        http_request = self._build_http_request("/chat/completions", payload, stream=True)
        async for raw_line in self.http_client.stream(http_request):
            for event_data in self._parse_sse_line(raw_line):
                if event_data == "[DONE]":
                    yield LLMStreamChunk(
                        request_id=request.context.request_id,
                        provider=self.name,
                        model=model,
                        finish_reason=LLMFinishReason.STOP,
                    )
                    return
                try:
                    chunk_data = json.loads(event_data)
                except json.JSONDecodeError:
                    continue
                choice = self._first_choice(chunk_data)
                delta_obj = choice.get("delta", {}) if isinstance(choice, Mapping) else {}
                delta = delta_obj.get("content") or ""
                finish_reason = normalize_finish_reason(choice.get("finish_reason")) if isinstance(choice, Mapping) else None
                tool_calls = self._parse_tool_calls(delta_obj.get("tool_calls") or [])
                yield LLMStreamChunk(
                    request_id=request.context.request_id,
                    provider=self.name,
                    model=str(chunk_data.get("model") or model),
                    delta=delta,
                    finish_reason=finish_reason if finish_reason != LLMFinishReason.UNKNOWN else None,
                    tool_calls=tuple(tool_calls),
                    raw_chunk=chunk_data if self.config.include_raw_response else None,
                    metadata={"response_id": chunk_data.get("id")},
                )

    async def _complete_once(self, request: LLMCompletionRequest) -> LLMCompletionResponse:
        started = time.perf_counter()
        model = request.model or self.config.default_chat_model
        payload = {
            "model": model,
            "prompt": request.prompt,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "stream": False,
            **self.config.extra_params,
            **request.extra,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.stop:
            payload["stop"] = list(request.stop)
        response = await self._post_json("/completions", payload)
        data = self._parse_http_json(response)
        choice = self._first_choice(data)
        text = str(choice.get("text") or "") if isinstance(choice, Mapping) else ""
        usage = self._parse_usage(data.get("usage", {}), fallback_input=request.prompt, fallback_output=text)
        return LLMCompletionResponse(
            request_id=request.context.request_id,
            provider=self.name,
            model=str(data.get("model") or model),
            text=text,
            finish_reason=normalize_finish_reason(choice.get("finish_reason") if isinstance(choice, Mapping) else None),
            usage=usage,
            latency_ms=(time.perf_counter() - started) * 1000,
            raw_response=data if self.config.include_raw_response else None,
            metadata={"response_id": data.get("id")},
        )

    async def _embed_once(self, request: LLMEmbeddingRequest) -> LLMEmbeddingResponse:
        started = time.perf_counter()
        model = request.model or self.config.default_embedding_model
        payload: Dict[str, Any] = {
            "model": model,
            "input": list(request.input_texts),
            **self.config.extra_params,
            **request.extra,
        }
        if request.dimensions is not None:
            payload["dimensions"] = request.dimensions
        response = await self._post_json("/embeddings", payload)
        data = self._parse_http_json(response)
        embeddings = [item.get("embedding", []) for item in data.get("data", [])]
        usage = self._parse_usage(
            data.get("usage", {}),
            fallback_input="\n".join(request.input_texts),
            fallback_output="",
        )
        return LLMEmbeddingResponse(
            request_id=request.context.request_id,
            provider=self.name,
            model=str(data.get("model") or model),
            embeddings=tuple(tuple(float(x) for x in embedding) for embedding in embeddings),
            usage=usage,
            latency_ms=(time.perf_counter() - started) * 1000,
            raw_response=data if self.config.include_raw_response else None,
            metadata={"response_id": data.get("id")},
        )

    def _build_chat_payload(self, request: LLMChatRequest, model: str, *, stream: bool) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [self._message_to_provider(message) for message in request.messages],
            "temperature": request.temperature,
            "top_p": request.top_p,
            "stream": stream,
            **self.config.extra_params,
            **request.extra,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.stop:
            payload["stop"] = list(request.stop)
        if request.tools:
            payload["tools"] = list(request.tools)
        if request.tool_choice is not None:
            payload["tool_choice"] = request.tool_choice
        if request.response_format is not None:
            payload["response_format"] = dict(request.response_format)
        if request.seed is not None:
            payload["seed"] = request.seed
        return payload

    def _message_to_provider(self, message: LLMMessage) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "role": message.role.value,
            "content": message.content,
        }
        if message.name:
            data["name"] = message.name
        if message.tool_call_id:
            data["tool_call_id"] = message.tool_call_id
        return data

    async def _post_json(self, path: str, payload: Mapping[str, Any]) -> HTTPResponse:
        request = self._build_http_request(path, payload, stream=False)
        response = await self.http_client.request(request)
        if response.status_code >= 400:
            raise self._exception_from_response(response)
        return response

    def _build_http_request(self, path: str, payload: Mapping[str, Any], *, stream: bool) -> HTTPRequest:
        headers = self._headers(stream=stream)
        return HTTPRequest(
            method="POST",
            url=join_url(self.config.base_url, path),
            headers=headers,
            json_body=payload,
            timeout_seconds=self.config.timeout_seconds,
            stream=stream,
        )

    def _headers(self, *, stream: bool = False) -> Dict[str, str]:
        api_key = self.config.resolved_api_key()
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": self.config.user_agent,
            **dict(self.config.extra_headers),
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if self.config.organization:
            headers["OpenAI-Organization"] = self.config.organization
        if self.config.project:
            headers["OpenAI-Project"] = self.config.project
        return headers

    def _parse_http_json(self, response: HTTPResponse) -> Mapping[str, Any]:
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise LLMProviderError("Provider returned invalid JSON") from exc

    def _exception_from_response(self, response: HTTPResponse) -> LLMConnectorError:
        try:
            payload = response.json()
            error_payload = payload.get("error", {}) if isinstance(payload, Mapping) else {}
            message = str(error_payload.get("message") or payload.get("message") or response.body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            message = response.body.decode("utf-8", errors="replace")

        if response.status_code in {401, 403}:
            return LLMAuthenticationError(message)
        if response.status_code == 429:
            return LLMRateLimitError(message)
        if response.status_code in {408, 409, 425} or response.status_code >= 500:
            return LLMTransientError(message)
        return LLMProviderError(message)

    def _first_choice(self, data: Mapping[str, Any]) -> Mapping[str, Any]:
        choices = data.get("choices") or []
        if not choices:
            return {}
        first = choices[0]
        return first if isinstance(first, Mapping) else {}

    def _parse_usage(self, usage: Mapping[str, Any], *, fallback_input: str, fallback_output: str) -> LLMUsage:
        input_tokens = int(
            usage.get("prompt_tokens")
            or usage.get("input_tokens")
            or estimate_tokens(fallback_input)
        )
        output_tokens = int(
            usage.get("completion_tokens")
            or usage.get("output_tokens")
            or estimate_tokens(fallback_output)
        )
        total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
        return LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens)

    def _parse_tool_calls(self, raw_tool_calls: Sequence[Mapping[str, Any]]) -> List[LLMToolCall]:
        result: List[LLMToolCall] = []
        for item in raw_tool_calls or []:
            if not isinstance(item, Mapping):
                continue
            function = item.get("function") or {}
            name = function.get("name") or item.get("name") or "unknown_tool"
            raw_args = function.get("arguments") or item.get("arguments") or {}
            args: Mapping[str, Any]
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    args = {"_raw": raw_args}
            elif isinstance(raw_args, Mapping):
                args = raw_args
            else:
                args = {"_raw": raw_args}
            result.append(
                LLMToolCall(
                    id=str(item.get("id") or uuid.uuid4()),
                    name=str(name),
                    arguments=args,
                )
            )
        return result

    def _parse_sse_line(self, raw_line: bytes) -> Iterable[str]:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or line.startswith(":"):
            return []
        if line.startswith("data:"):
            return [line[len("data:") :].strip()]
        return []

    def _chat_input_text(self, request: LLMChatRequest) -> str:
        return "\n".join(f"{message.role.value}: {message.content}" for message in request.messages)

    def _validate_chat_request(self, request: LLMChatRequest) -> None:
        if not request.messages:
            raise LLMValidationError("chat request requires at least one message")
        for message in request.messages:
            if not isinstance(message.role, LLMMessageRole):
                raise LLMValidationError("message.role must be LLMMessageRole")
            if not isinstance(message.content, str):
                raise LLMValidationError("message.content must be a string")
        self._validate_sampling(request.temperature, request.top_p, request.max_tokens)

    def _validate_completion_request(self, request: LLMCompletionRequest) -> None:
        if not request.prompt:
            raise LLMValidationError("completion request requires prompt")
        self._validate_sampling(request.temperature, request.top_p, request.max_tokens)

    def _validate_embedding_request(self, request: LLMEmbeddingRequest) -> None:
        if not request.input_texts:
            raise LLMValidationError("embedding request requires input_texts")
        if any(not isinstance(text, str) or not text for text in request.input_texts):
            raise LLMValidationError("all embedding input_texts must be non-empty strings")
        if request.dimensions is not None and request.dimensions <= 0:
            raise LLMValidationError("dimensions must be positive")

    def _validate_sampling(self, temperature: float, top_p: float, max_tokens: Optional[int]) -> None:
        if not 0 <= temperature <= 2:
            raise LLMValidationError("temperature must be between 0 and 2")
        if not 0 <= top_p <= 1:
            raise LLMValidationError("top_p must be between 0 and 1")
        if max_tokens is not None and max_tokens <= 0:
            raise LLMValidationError("max_tokens must be positive")


# =============================================================================
# Registry
# =============================================================================


class LLMConnectorRegistry:
    """Registry for multiple LLM connectors."""

    def __init__(self) -> None:
        self._connectors: Dict[str, BaseLLMConnector] = {}
        self._default_name: Optional[str] = None

    def register(self, connector: BaseLLMConnector, *, default: bool = False) -> None:
        self._connectors[connector.name] = connector
        if default or self._default_name is None:
            self._default_name = connector.name

    def get(self, name: Optional[str] = None) -> BaseLLMConnector:
        connector_name = name or self._default_name
        if connector_name is None:
            raise LLMConfigurationError("No connector registered")
        try:
            return self._connectors[connector_name]
        except KeyError as exc:
            raise LLMConfigurationError(f"Connector not found: {connector_name}") from exc

    def list_names(self) -> Sequence[str]:
        return tuple(sorted(self._connectors.keys()))


# =============================================================================
# Adapter for inference_pipeline.py ModelProvider protocol
# =============================================================================


class InferencePipelineProviderAdapter:
    """Adapter exposing this connector as inference_pipeline.ModelProvider.

    This avoids a hard import dependency on inference_pipeline.py. The adapter
    expects objects with compatible attribute names.
    """

    def __init__(self, connector: OpenAICompatibleConnector) -> None:
        self.connector = connector

    @property
    def name(self) -> str:
        return self.connector.name

    async def infer(self, request: Any) -> Any:
        mode = getattr(request, "mode")
        mode_value = getattr(mode, "value", str(mode))
        if mode_value == "chat":
            llm_request = self._to_chat_request(request)
            llm_response = await self.connector.chat(llm_request)
            return self._to_inference_response(request, llm_response)
        if mode_value == "completion":
            llm_request = self._to_completion_request(request)
            llm_response = await self.connector.complete(llm_request)
            return self._to_inference_completion_response(request, llm_response)
        if mode_value == "embedding":
            llm_request = self._to_embedding_request(request)
            llm_response = await self.connector.embed(llm_request)
            return self._to_inference_embedding_response(request, llm_response)
        raise LLMValidationError(f"Unsupported inference mode: {mode_value}")

    async def stream(self, request: Any) -> AsyncIterator[Any]:
        llm_request = self._to_chat_request(request, stream=True)
        async for chunk in self.connector.stream_chat(llm_request):
            yield self._to_stream_chunk(chunk)

    def _to_context(self, request: Any) -> LLMContext:
        context = getattr(request, "context")
        return LLMContext(
            request_id=getattr(context, "request_id", str(uuid.uuid4())),
            tenant_id=getattr(context, "tenant_id", None),
            user_id=getattr(context, "user_id", None),
            application=getattr(context, "application", None),
            trace_id=getattr(context, "trace_id", None),
            session_id=getattr(context, "session_id", None),
            metadata=getattr(context, "metadata", {}) or {},
        )

    def _to_chat_request(self, request: Any, *, stream: Optional[bool] = None) -> LLMChatRequest:
        options = getattr(request, "options")
        messages = []
        for message in getattr(request, "messages", ()):
            role = getattr(getattr(message, "role"), "value", str(getattr(message, "role")))
            messages.append(
                LLMMessage(
                    role=LLMMessageRole(role),
                    content=getattr(message, "content"),
                    name=getattr(message, "name", None),
                    metadata=getattr(message, "metadata", {}) or {},
                )
            )
        return LLMChatRequest(
            messages=tuple(messages),
            model=getattr(request, "model", None),
            temperature=getattr(options, "temperature", 0.2),
            top_p=getattr(options, "top_p", 1.0),
            max_tokens=getattr(options, "max_tokens", None),
            stop=tuple(getattr(options, "stop", ()) or ()),
            stream=getattr(options, "stream", False) if stream is None else stream,
            tools=tuple(getattr(options, "tools", ()) or ()),
            response_format=getattr(options, "response_format", None),
            seed=getattr(options, "seed", None),
            context=self._to_context(request),
            extra=getattr(options, "extra", {}) or {},
        )

    def _to_completion_request(self, request: Any) -> LLMCompletionRequest:
        options = getattr(request, "options")
        return LLMCompletionRequest(
            prompt=getattr(request, "prompt"),
            model=getattr(request, "model", None),
            temperature=getattr(options, "temperature", 0.2),
            top_p=getattr(options, "top_p", 1.0),
            max_tokens=getattr(options, "max_tokens", None),
            stop=tuple(getattr(options, "stop", ()) or ()),
            stream=getattr(options, "stream", False),
            context=self._to_context(request),
            extra=getattr(options, "extra", {}) or {},
        )

    def _to_embedding_request(self, request: Any) -> LLMEmbeddingRequest:
        return LLMEmbeddingRequest(
            input_texts=tuple(getattr(request, "input_texts", ()) or ()),
            model=getattr(request, "model", None),
            context=self._to_context(request),
        )

    def _to_inference_response(self, request: Any, response: LLMChatResponse) -> Any:
        return self._build_inference_response(
            request=request,
            model=response.model,
            output_text=response.content,
            embeddings=(),
            usage=response.usage,
            finish_reason=response.finish_reason,
            latency_ms=response.latency_ms,
            provider=response.provider,
            raw_response=response.raw_response,
            metadata=response.metadata,
        )

    def _to_inference_completion_response(self, request: Any, response: LLMCompletionResponse) -> Any:
        return self._build_inference_response(
            request=request,
            model=response.model,
            output_text=response.text,
            embeddings=(),
            usage=response.usage,
            finish_reason=response.finish_reason,
            latency_ms=response.latency_ms,
            provider=response.provider,
            raw_response=response.raw_response,
            metadata=response.metadata,
        )

    def _to_inference_embedding_response(self, request: Any, response: LLMEmbeddingResponse) -> Any:
        return self._build_inference_response(
            request=request,
            model=response.model,
            output_text="",
            embeddings=response.embeddings,
            usage=response.usage,
            finish_reason=LLMFinishReason.STOP,
            latency_ms=response.latency_ms,
            provider=response.provider,
            raw_response=response.raw_response,
            metadata=response.metadata,
        )

    def _build_inference_response(
        self,
        *,
        request: Any,
        model: str,
        output_text: str,
        embeddings: Sequence[Sequence[float]],
        usage: LLMUsage,
        finish_reason: LLMFinishReason,
        latency_ms: float,
        provider: str,
        raw_response: Optional[Mapping[str, Any]],
        metadata: Mapping[str, Any],
    ) -> Any:
        try:
            from data.ai.inference_pipeline import FinishReason, InferenceResponse, TokenUsage
        except Exception:  # noqa: BLE001
            from inference_pipeline import FinishReason, InferenceResponse, TokenUsage  # type: ignore

        finish_map = {
            LLMFinishReason.STOP: FinishReason.STOP,
            LLMFinishReason.LENGTH: FinishReason.LENGTH,
            LLMFinishReason.TOOL_CALL: FinishReason.TOOL_CALL,
            LLMFinishReason.CONTENT_FILTER: FinishReason.CONTENT_FILTER,
            LLMFinishReason.ERROR: FinishReason.ERROR,
            LLMFinishReason.UNKNOWN: FinishReason.UNKNOWN,
        }
        return InferenceResponse(
            request_id=getattr(getattr(request, "context"), "request_id"),
            model=model,
            output_text=output_text,
            embeddings=tuple(tuple(float(x) for x in row) for row in embeddings),
            finish_reason=finish_map.get(finish_reason, FinishReason.UNKNOWN),
            usage=TokenUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
            ),
            latency_ms=latency_ms,
            provider=provider,
            raw_response=raw_response,
            metadata=metadata,
        )

    def _to_stream_chunk(self, chunk: LLMStreamChunk) -> Any:
        try:
            from data.ai.inference_pipeline import FinishReason, StreamChunk
        except Exception:  # noqa: BLE001
            from inference_pipeline import FinishReason, StreamChunk  # type: ignore

        finish_map = {
            LLMFinishReason.STOP: FinishReason.STOP,
            LLMFinishReason.LENGTH: FinishReason.LENGTH,
            LLMFinishReason.TOOL_CALL: FinishReason.TOOL_CALL,
            LLMFinishReason.CONTENT_FILTER: FinishReason.CONTENT_FILTER,
            LLMFinishReason.ERROR: FinishReason.ERROR,
            LLMFinishReason.UNKNOWN: FinishReason.UNKNOWN,
            None: None,
        }
        return StreamChunk(
            request_id=chunk.request_id,
            delta=chunk.delta,
            finish_reason=finish_map.get(chunk.finish_reason),
            metadata=chunk.metadata,
        )


# =============================================================================
# Factories
# =============================================================================


def build_openai_compatible_connector(
    *,
    provider_name: str = "openai",
    base_url: str = "https://api.openai.com/v1",
    api_key_env: Optional[str] = "OPENAI_API_KEY",
    api_key: Optional[str] = None,
    default_chat_model: str = "gpt-4o-mini",
    default_embedding_model: str = "text-embedding-3-small",
    config_overrides: Optional[Mapping[str, Any]] = None,
    http_client: Optional[AsyncHTTPClient] = None,
) -> OpenAICompatibleConnector:
    """Build an OpenAI-compatible connector."""

    config_data = asdict(
        LLMConnectorConfig(
            provider_name=provider_name,
            provider_type=LLMProviderType.OPENAI_COMPATIBLE,
            base_url=base_url,
            api_key_env=api_key_env,
            api_key=api_key,
            default_chat_model=default_chat_model,
            default_embedding_model=default_embedding_model,
        )
    )
    if config_overrides:
        config_data.update(dict(config_overrides))
    config = LLMConnectorConfig(**config_data)
    return OpenAICompatibleConnector(config=config, http_client=http_client)


def build_local_openai_compatible_connector(
    *,
    provider_name: str = "local-openai-compatible",
    base_url: str = "http://localhost:8000/v1",
    default_chat_model: str = "local-model",
    default_embedding_model: str = "local-embedding-model",
    config_overrides: Optional[Mapping[str, Any]] = None,
) -> OpenAICompatibleConnector:
    """Build connector for local OpenAI-compatible gateways."""

    overrides = {
        "api_key_env": None,
        "api_key": None,
        "verify_ssl": False,
        **dict(config_overrides or {}),
    }
    return build_openai_compatible_connector(
        provider_name=provider_name,
        base_url=base_url,
        api_key_env=None,
        api_key=None,
        default_chat_model=default_chat_model,
        default_embedding_model=default_embedding_model,
        config_overrides=overrides,
    )


# =============================================================================
# Demo
# =============================================================================


async def _demo_async() -> None:
    logging.basicConfig(level=logging.INFO)

    connector = build_openai_compatible_connector(
        provider_name="demo-openai-compatible",
        api_key_env="OPENAI_API_KEY",
        config_overrides={"include_raw_response": False},
    )

    request = LLMChatRequest(
        messages=(
            LLMMessage(role=LLMMessageRole.SYSTEM, content="Você é um assistente técnico enterprise."),
            LLMMessage(role=LLMMessageRole.USER, content="Explique em uma frase o papel de um LLM connector."),
        ),
        temperature=0.2,
        max_tokens=120,
        context=LLMContext(application="demo", tenant_id="local"),
    )

    if not connector.config.resolved_api_key():
        print("OPENAI_API_KEY não configurada. Demo real não será executada.")
        print("Configure a variável de ambiente OPENAI_API_KEY para testar o conector.")
        return

    response = await connector.chat(request)
    print(json.dumps(asdict(response), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(_demo_async())

"""
data/utils/decorators.py

Enterprise-grade decorators utilities.

Este módulo centraliza decorators reutilizáveis para funções síncronas e
assíncronas em pipelines de dados, validação, ingestão, IA, APIs internas e
jobs corporativos.

Capacidades principais:
- Logging de execução com duração e erros.
- Retry com backoff exponencial e jitter.
- Timeout para funções síncronas e assíncronas.
- Cache TTL thread-safe para funções puras/idempotentes.
- Métricas e auditoria plugáveis.
- Rate limiting simples.
- Circuit breaker.
- Validação de argumentos por predicados.
- Deprecation warning padronizado.
- Decorators compatíveis com sync e async quando aplicável.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import hashlib
import inspect
import json
import logging
import random
import threading
import time
import warnings
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Deque, Dict, Mapping, MutableMapping, Optional, Tuple, Type, TypeVar, Union, cast


logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])
R = TypeVar("R")


class DecoratorError(Exception):
    """Erro base para decorators."""


class TimeoutError(DecoratorError):
    """Timeout de execução."""


class RateLimitError(DecoratorError):
    """Rate limit excedido."""


class CircuitBreakerError(DecoratorError):
    """Circuit breaker aberto."""


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass(frozen=True)
class RetryConfig:
    attempts: int = 3
    initial_delay_seconds: float = 0.2
    max_delay_seconds: float = 10.0
    multiplier: float = 2.0
    jitter_seconds: float = 0.1
    exceptions: Tuple[Type[BaseException], ...] = (Exception,)

    def delay(self, attempt: int) -> float:
        base = min(self.max_delay_seconds, self.initial_delay_seconds * (self.multiplier ** max(0, attempt - 1)))
        jitter = random.uniform(0, self.jitter_seconds) if self.jitter_seconds > 0 else 0.0
        return base + jitter


@dataclass(frozen=True)
class CacheEntry:
    value: Any
    expires_at: Optional[float]
    created_at: float

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and time.monotonic() >= self.expires_at


class SimpleCircuitBreaker:
    """Circuit breaker thread-safe para uso via decorator."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 30.0,
        success_threshold: int = 2,
        exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.success_threshold = success_threshold
        self.exceptions = exceptions
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at: Optional[float] = None
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._refresh()
            return self._state

    def before_call(self) -> None:
        with self._lock:
            self._refresh()
            if self._state == CircuitState.OPEN:
                raise CircuitBreakerError("Circuit breaker is open")

    def after_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._successes += 1
                if self._successes >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failures = 0
                    self._successes = 0
                    self._opened_at = None
            else:
                self._failures = 0

    def after_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._successes = 0
            if self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

    def _refresh(self) -> None:
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self.recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                self._successes = 0


class SlidingWindowRateLimiter:
    """Rate limiter simples baseado em janela deslizante."""

    def __init__(self, calls: int, period_seconds: float) -> None:
        if calls <= 0:
            raise ValueError("calls must be positive")
        if period_seconds <= 0:
            raise ValueError("period_seconds must be positive")
        self.calls = calls
        self.period_seconds = period_seconds
        self._timestamps: Deque[float] = deque()
        self._lock = threading.RLock()

    def acquire(self) -> bool:
        with self._lock:
            now = time.monotonic()
            while self._timestamps and now - self._timestamps[0] >= self.period_seconds:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.calls:
                return False
            self._timestamps.append(now)
            return True


def log_execution(
    *,
    level: int = logging.INFO,
    log_args: bool = False,
    log_result: bool = False,
    logger_name: Optional[str] = None,
) -> Callable[[F], F]:
    """Loga início, fim, duração e erro de uma função sync/async."""

    def decorator(func: F) -> F:
        target_logger = logging.getLogger(logger_name or func.__module__)
        is_async = inspect.iscoroutinefunction(func)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            call_payload = _call_payload(args, kwargs) if log_args else "hidden"
            target_logger.log(level, "Starting %s args=%s", func.__qualname__, call_payload)
            try:
                result = await cast(Callable[..., Awaitable[Any]], func)(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000.0
                target_logger.log(level, "Finished %s duration_ms=%.2f result=%s", func.__qualname__, duration_ms, _safe_repr(result) if log_result else "hidden")
                return result
            except Exception:
                duration_ms = (time.perf_counter() - start) * 1000.0
                target_logger.exception("Failed %s duration_ms=%.2f", func.__qualname__, duration_ms)
                raise

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            call_payload = _call_payload(args, kwargs) if log_args else "hidden"
            target_logger.log(level, "Starting %s args=%s", func.__qualname__, call_payload)
            try:
                result = func(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000.0
                target_logger.log(level, "Finished %s duration_ms=%.2f result=%s", func.__qualname__, duration_ms, _safe_repr(result) if log_result else "hidden")
                return result
            except Exception:
                duration_ms = (time.perf_counter() - start) * 1000.0
                target_logger.exception("Failed %s duration_ms=%.2f", func.__qualname__, duration_ms)
                raise

        return cast(F, async_wrapper if is_async else sync_wrapper)

    return decorator


def retry(
    *,
    attempts: int = 3,
    initial_delay_seconds: float = 0.2,
    max_delay_seconds: float = 10.0,
    multiplier: float = 2.0,
    jitter_seconds: float = 0.1,
    exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    on_retry: Optional[Callable[[int, BaseException], None]] = None,
) -> Callable[[F], F]:
    """Retry sync/async com backoff exponencial."""
    config = RetryConfig(attempts, initial_delay_seconds, max_delay_seconds, multiplier, jitter_seconds, exceptions)

    def decorator(func: F) -> F:
        is_async = inspect.iscoroutinefunction(func)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: Optional[BaseException] = None
            for attempt in range(1, config.attempts + 1):
                try:
                    return await cast(Callable[..., Awaitable[Any]], func)(*args, **kwargs)
                except config.exceptions as exc:
                    last_error = exc
                    if attempt >= config.attempts:
                        break
                    if on_retry:
                        on_retry(attempt, exc)
                    await asyncio.sleep(config.delay(attempt))
            raise last_error  # type: ignore[misc]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: Optional[BaseException] = None
            for attempt in range(1, config.attempts + 1):
                try:
                    return func(*args, **kwargs)
                except config.exceptions as exc:
                    last_error = exc
                    if attempt >= config.attempts:
                        break
                    if on_retry:
                        on_retry(attempt, exc)
                    time.sleep(config.delay(attempt))
            raise last_error  # type: ignore[misc]

        return cast(F, async_wrapper if is_async else sync_wrapper)

    return decorator


def timeout(seconds: float) -> Callable[[F], F]:
    """Aplica timeout em função sync/async.

    Para funções síncronas, usa ThreadPoolExecutor. Para funções assíncronas,
    usa asyncio.wait_for.
    """
    if seconds <= 0:
        raise ValueError("timeout seconds must be positive")

    def decorator(func: F) -> F:
        is_async = inspect.iscoroutinefunction(func)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await asyncio.wait_for(cast(Callable[..., Awaitable[Any]], func)(*args, **kwargs), timeout=seconds)
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f"Function {func.__qualname__} timed out after {seconds}s") from exc

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(func, *args, **kwargs)
                try:
                    return future.result(timeout=seconds)
                except concurrent.futures.TimeoutError as exc:
                    future.cancel()
                    raise TimeoutError(f"Function {func.__qualname__} timed out after {seconds}s") from exc

        return cast(F, async_wrapper if is_async else sync_wrapper)

    return decorator


def ttl_cache(
    *,
    ttl_seconds: Optional[float] = 300.0,
    maxsize: int = 1024,
    key_func: Optional[Callable[..., str]] = None,
) -> Callable[[F], F]:
    """Cache TTL thread-safe para funções sync/async."""
    if maxsize <= 0:
        raise ValueError("maxsize must be positive")
    cache: MutableMapping[str, CacheEntry] = {}
    lock = threading.RLock()

    def decorator(func: F) -> F:
        is_async = inspect.iscoroutinefunction(func)

        def make_key(args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> str:
            if key_func:
                return key_func(*args, **dict(kwargs))
            return _stable_hash({"func": func.__qualname__, "args": args, "kwargs": kwargs})

        def get_cached(key: str) -> Tuple[bool, Any]:
            with lock:
                entry = cache.get(key)
                if entry is None:
                    return False, None
                if entry.expired:
                    cache.pop(key, None)
                    return False, None
                return True, entry.value

        def set_cached(key: str, value: Any) -> None:
            with lock:
                if len(cache) >= maxsize:
                    oldest_key = min(cache.items(), key=lambda item: item[1].created_at)[0]
                    cache.pop(oldest_key, None)
                expires_at = None if ttl_seconds is None else time.monotonic() + ttl_seconds
                cache[key] = CacheEntry(value=value, expires_at=expires_at, created_at=time.monotonic())

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            key = make_key(args, kwargs)
            hit, value = get_cached(key)
            if hit:
                return value
            value = await cast(Callable[..., Awaitable[Any]], func)(*args, **kwargs)
            set_cached(key, value)
            return value

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            key = make_key(args, kwargs)
            hit, value = get_cached(key)
            if hit:
                return value
            value = func(*args, **kwargs)
            set_cached(key, value)
            return value

        wrapper = async_wrapper if is_async else sync_wrapper
        setattr(wrapper, "cache_clear", lambda: cache.clear())
        setattr(wrapper, "cache_info", lambda: {"size": len(cache), "maxsize": maxsize, "ttl_seconds": ttl_seconds})
        return cast(F, wrapper)

    return decorator


def rate_limit(*, calls: int, period_seconds: float) -> Callable[[F], F]:
    """Rate limit sync/async por função."""
    limiter = SlidingWindowRateLimiter(calls, period_seconds)

    def decorator(func: F) -> F:
        is_async = inspect.iscoroutinefunction(func)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            if not limiter.acquire():
                raise RateLimitError(f"Rate limit exceeded for {func.__qualname__}: {calls}/{period_seconds}s")
            return await cast(Callable[..., Awaitable[Any]], func)(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if not limiter.acquire():
                raise RateLimitError(f"Rate limit exceeded for {func.__qualname__}: {calls}/{period_seconds}s")
            return func(*args, **kwargs)

        return cast(F, async_wrapper if is_async else sync_wrapper)

    return decorator


def circuit_breaker(
    *,
    failure_threshold: int = 5,
    recovery_timeout_seconds: float = 30.0,
    success_threshold: int = 2,
    exceptions: Tuple[Type[BaseException], ...] = (Exception,),
) -> Callable[[F], F]:
    """Protege função com circuit breaker sync/async."""
    breaker = SimpleCircuitBreaker(
        failure_threshold=failure_threshold,
        recovery_timeout_seconds=recovery_timeout_seconds,
        success_threshold=success_threshold,
        exceptions=exceptions,
    )

    def decorator(func: F) -> F:
        is_async = inspect.iscoroutinefunction(func)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            breaker.before_call()
            try:
                result = await cast(Callable[..., Awaitable[Any]], func)(*args, **kwargs)
            except exceptions:
                breaker.after_failure()
                raise
            else:
                breaker.after_success()
                return result

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            breaker.before_call()
            try:
                result = func(*args, **kwargs)
            except exceptions:
                breaker.after_failure()
                raise
            else:
                breaker.after_success()
                return result

        wrapper = async_wrapper if is_async else sync_wrapper
        setattr(wrapper, "circuit_breaker", breaker)
        return cast(F, wrapper)

    return decorator


def measure_metrics(
    *,
    metrics_sink: Any,
    metric_prefix: str,
    tags: Optional[Mapping[str, str]] = None,
) -> Callable[[F], F]:
    """Publica métricas de execução em sink com métodos increment/gauge/timing."""
    tags_dict = dict(tags or {})

    def decorator(func: F) -> F:
        is_async = inspect.iscoroutinefunction(func)

        def publish(status: str, duration_ms: float) -> None:
            metric_tags = {**tags_dict, "function": func.__qualname__, "status": status}
            if hasattr(metrics_sink, "increment"):
                metrics_sink.increment(f"{metric_prefix}.executed", tags=metric_tags)
                if status != "success":
                    metrics_sink.increment(f"{metric_prefix}.failed", tags=metric_tags)
            if hasattr(metrics_sink, "timing"):
                metrics_sink.timing(f"{metric_prefix}.duration_ms", duration_ms, tags=metric_tags)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                result = await cast(Callable[..., Awaitable[Any]], func)(*args, **kwargs)
                publish("success", (time.perf_counter() - start) * 1000.0)
                return result
            except Exception:
                publish("error", (time.perf_counter() - start) * 1000.0)
                raise

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                publish("success", (time.perf_counter() - start) * 1000.0)
                return result
            except Exception:
                publish("error", (time.perf_counter() - start) * 1000.0)
                raise

        return cast(F, async_wrapper if is_async else sync_wrapper)

    return decorator


def audit_call(
    *,
    audit_sink: Any,
    event_name: Optional[str] = None,
    include_args: bool = False,
    include_result: bool = False,
) -> Callable[[F], F]:
    """Emite eventos de auditoria antes/depois da chamada."""

    def decorator(func: F) -> F:
        is_async = inspect.iscoroutinefunction(func)
        name = event_name or func.__qualname__

        def emit(status: str, payload: Mapping[str, Any]) -> None:
            if hasattr(audit_sink, "emit"):
                audit_sink.emit(
                    {
                        "event_name": name,
                        "status": status,
                        "function": func.__qualname__,
                        "emitted_at": datetime.now(timezone.utc).isoformat(),
                        "payload": _safe_json(payload),
                    }
                )

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            payload = {"args": _call_payload(args, kwargs)} if include_args else {}
            emit("started", payload)
            start = time.perf_counter()
            try:
                result = await cast(Callable[..., Awaitable[Any]], func)(*args, **kwargs)
                done_payload = {"duration_ms": (time.perf_counter() - start) * 1000.0}
                if include_result:
                    done_payload["result"] = _safe_repr(result)
                emit("succeeded", done_payload)
                return result
            except Exception as exc:
                emit("failed", {"duration_ms": (time.perf_counter() - start) * 1000.0, "error": str(exc), "error_type": type(exc).__name__})
                raise

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            payload = {"args": _call_payload(args, kwargs)} if include_args else {}
            emit("started", payload)
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                done_payload = {"duration_ms": (time.perf_counter() - start) * 1000.0}
                if include_result:
                    done_payload["result"] = _safe_repr(result)
                emit("succeeded", done_payload)
                return result
            except Exception as exc:
                emit("failed", {"duration_ms": (time.perf_counter() - start) * 1000.0, "error": str(exc), "error_type": type(exc).__name__})
                raise

        return cast(F, async_wrapper if is_async else sync_wrapper)

    return decorator


def validate_args(*validators: Callable[..., bool]) -> Callable[[F], F]:
    """Valida argumentos antes da execução usando predicados.

    Cada validator recebe os mesmos *args/**kwargs da função. Se retornar False,
    a execução é bloqueada.
    """

    def decorator(func: F) -> F:
        is_async = inspect.iscoroutinefunction(func)

        def run_validators(args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> None:
            for validator in validators:
                if not validator(*args, **dict(kwargs)):
                    raise ValueError(f"Argument validation failed for {func.__qualname__}: {getattr(validator, '__name__', repr(validator))}")

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            run_validators(args, kwargs)
            return await cast(Callable[..., Awaitable[Any]], func)(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            run_validators(args, kwargs)
            return func(*args, **kwargs)

        return cast(F, async_wrapper if is_async else sync_wrapper)

    return decorator


def deprecated(*, reason: str, version: Optional[str] = None, replacement: Optional[str] = None) -> Callable[[F], F]:
    """Marca função como depreciada com warning padronizado."""

    def decorator(func: F) -> F:
        message = f"{func.__qualname__} is deprecated"
        if version:
            message += f" since version {version}"
        message += f": {reason}"
        if replacement:
            message += f". Use {replacement} instead."

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            warnings.warn(message, DeprecationWarning, stacklevel=2)
            return func(*args, **kwargs)

        return cast(F, wrapper)

    return decorator


def singleton(cls: Type[R]) -> Callable[..., R]:
    """Decorator simples para singleton thread-safe em classes."""
    instances: Dict[Type[R], R] = {}
    lock = threading.RLock()

    @functools.wraps(cls)
    def get_instance(*args: Any, **kwargs: Any) -> R:
        with lock:
            if cls not in instances:
                instances[cls] = cls(*args, **kwargs)
            return instances[cls]

    return get_instance


def synchronized(lock: Optional[threading.RLock] = None) -> Callable[[F], F]:
    """Sincroniza execução de função com lock."""
    actual_lock = lock or threading.RLock()

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with actual_lock:
                return func(*args, **kwargs)

        return cast(F, wrapper)

    return decorator


def _stable_hash(value: Any) -> str:
    try:
        payload = json.dumps(_safe_json(value), ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        payload = repr(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_json(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        json.dumps(value)
        return value
    except Exception:
        return repr(value)


def _safe_repr(value: Any, max_length: int = 500) -> str:
    text = repr(value)
    return text if len(text) <= max_length else text[:max_length] + "..."


def _call_payload(args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"args": [_safe_repr(arg) for arg in args], "kwargs": {str(k): _safe_repr(v) for k, v in kwargs.items()}}


__all__ = [
    "CacheEntry",
    "CircuitBreakerError",
    "CircuitState",
    "DecoratorError",
    "RateLimitError",
    "RetryConfig",
    "SimpleCircuitBreaker",
    "SlidingWindowRateLimiter",
    "TimeoutError",
    "audit_call",
    "circuit_breaker",
    "deprecated",
    "log_execution",
    "measure_metrics",
    "rate_limit",
    "retry",
    "singleton",
    "synchronized",
    "timeout",
    "ttl_cache",
    "validate_args",
]

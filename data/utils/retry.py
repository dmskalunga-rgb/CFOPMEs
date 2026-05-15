"""
data/utils/retry.py

Enterprise-grade retry utilities.

Este módulo centraliza estratégias robustas de retry para chamadas síncronas e
assíncronas em pipelines de dados, ingestão, validação, APIs, bancos de dados,
serviços externos, mensageria e IA.

Capacidades principais:
- Retry sync e async.
- Estratégias de backoff: fixed, linear, exponential e custom.
- Jitter: none, full, equal e additive.
- Políticas declarativas com limites por tentativas e tempo total.
- Predicados por exceção e por resultado.
- Hooks before_attempt, after_attempt, before_sleep e on_giveup.
- Decorators para funções sync/async.
- Resultado estruturado e serializável.
- Retry budget simples.
- Sem dependências externas obrigatórias.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Generic,
    Iterable,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Type,
    TypeVar,
    Union,
    cast,
)


logger = logging.getLogger(__name__)

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])
JsonDict = Dict[str, Any]
RetryPredicate = Callable[[BaseException], bool]
ResultPredicate = Callable[[Any], bool]
DelayStrategy = Callable[[int, BaseException | None], float]
RetryHook = Callable[["RetryAttempt"], None]


class RetryStatus(str, Enum):
    """Status de uma operação com retry."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXHAUSTED = "EXHAUSTED"
    TIMEOUT = "TIMEOUT"
    NOT_RETRYABLE = "NOT_RETRYABLE"


class BackoffStrategy(str, Enum):
    """Estratégias de backoff."""

    FIXED = "FIXED"
    LINEAR = "LINEAR"
    EXPONENTIAL = "EXPONENTIAL"
    CUSTOM = "CUSTOM"


class JitterStrategy(str, Enum):
    """Estratégias de jitter."""

    NONE = "NONE"
    ADDITIVE = "ADDITIVE"
    FULL = "FULL"
    EQUAL = "EQUAL"


class RetryError(Exception):
    """Erro base do módulo de retry."""


class RetryExhaustedError(RetryError):
    """Tentativas esgotadas."""


class RetryTimeoutError(RetryError):
    """Tempo total de retry esgotado."""


class RetryBudgetExceededError(RetryError):
    """Budget de retries excedido."""


@dataclass(frozen=True)
class RetryAttempt:
    """Informação sobre uma tentativa."""

    operation_id: str
    attempt_number: int
    max_attempts: int
    started_at: datetime
    finished_at: Optional[datetime] = None
    delay_seconds: float = 0.0
    exception: Optional[BaseException] = None
    result: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        end = self.finished_at or datetime.now(timezone.utc)
        return max(0.0, (end - self.started_at).total_seconds() * 1000.0)

    @property
    def failed(self) -> bool:
        return self.exception is not None

    def to_dict(self) -> JsonDict:
        return {
            "operation_id": self.operation_id,
            "attempt_number": self.attempt_number,
            "max_attempts": self.max_attempts,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
            "delay_seconds": self.delay_seconds,
            "failed": self.failed,
            "exception_type": type(self.exception).__name__ if self.exception else None,
            "exception": str(self.exception) if self.exception else None,
            "metadata": safe_json_value(dict(self.metadata)),
        }


@dataclass(frozen=True)
class RetryPolicy:
    """Política declarativa de retry."""

    max_attempts: int = 3
    backoff_strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    jitter_strategy: JitterStrategy = JitterStrategy.ADDITIVE
    initial_delay_seconds: float = 0.2
    max_delay_seconds: float = 30.0
    multiplier: float = 2.0
    jitter_seconds: float = 0.1
    max_elapsed_seconds: Optional[float] = None
    retry_exceptions: Tuple[Type[BaseException], ...] = (Exception,)
    retry_on_exception: Optional[RetryPredicate] = None
    retry_on_result: Optional[ResultPredicate] = None
    custom_delay_strategy: Optional[DelayStrategy] = None
    reraise: bool = True
    name: str = "default"

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.initial_delay_seconds < 0:
            raise ValueError("initial_delay_seconds must be >= 0")
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("max_delay_seconds must be >= initial_delay_seconds")
        if self.multiplier < 1:
            raise ValueError("multiplier must be >= 1")
        if self.max_elapsed_seconds is not None and self.max_elapsed_seconds <= 0:
            raise ValueError("max_elapsed_seconds must be positive or None")

    def should_retry_exception(self, exc: BaseException) -> bool:
        if not isinstance(exc, self.retry_exceptions):
            return False
        if self.retry_on_exception is not None:
            return bool(self.retry_on_exception(exc))
        return True

    def should_retry_result(self, result: Any) -> bool:
        if self.retry_on_result is None:
            return False
        return bool(self.retry_on_result(result))

    def delay_for(self, attempt_number: int, exc: Optional[BaseException] = None) -> float:
        if self.custom_delay_strategy is not None:
            base = self.custom_delay_strategy(attempt_number, exc)
        elif self.backoff_strategy == BackoffStrategy.FIXED:
            base = self.initial_delay_seconds
        elif self.backoff_strategy == BackoffStrategy.LINEAR:
            base = self.initial_delay_seconds * attempt_number
        elif self.backoff_strategy == BackoffStrategy.EXPONENTIAL:
            base = self.initial_delay_seconds * (self.multiplier ** max(0, attempt_number - 1))
        else:
            base = self.initial_delay_seconds
        base = max(0.0, min(self.max_delay_seconds, base))
        return apply_jitter(base, self.jitter_strategy, self.jitter_seconds)


@dataclass(frozen=True)
class RetryResult(Generic[T]):
    """Resultado estruturado de operação com retry."""

    operation_id: str
    status: RetryStatus
    value: Optional[T]
    attempts: Tuple[RetryAttempt, ...]
    policy_name: str
    started_at: datetime
    finished_at: datetime
    error: Optional[str] = None
    error_type: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == RetryStatus.SUCCEEDED

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.finished_at - self.started_at).total_seconds() * 1000.0)

    def unwrap(self) -> T:
        if not self.ok:
            raise RetryExhaustedError(f"Retry operation failed: {self.error}")
        return self.value  # type: ignore[return-value]

    def to_dict(self) -> JsonDict:
        return {
            "operation_id": self.operation_id,
            "status": self.status.value,
            "policy_name": self.policy_name,
            "attempt_count": self.attempt_count,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
            "error": self.error,
            "error_type": self.error_type,
            "metadata": safe_json_value(dict(self.metadata)),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


class RetryBudget:
    """Budget simples de retries por janela temporal."""

    def __init__(self, *, max_retries: int, window_seconds: float) -> None:
        if max_retries <= 0:
            raise ValueError("max_retries must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_retries = max_retries
        self.window_seconds = window_seconds
        self._events: list[float] = []

    def acquire(self) -> bool:
        now = time.monotonic()
        self._events = [ts for ts in self._events if now - ts < self.window_seconds]
        if len(self._events) >= self.max_retries:
            return False
        self._events.append(now)
        return True

    def remaining(self) -> int:
        now = time.monotonic()
        self._events = [ts for ts in self._events if now - ts < self.window_seconds]
        return max(0, self.max_retries - len(self._events))


class Retrier:
    """Executor de retry para funções sync e async."""

    def __init__(
        self,
        policy: Optional[RetryPolicy] = None,
        *,
        budget: Optional[RetryBudget] = None,
        before_attempt: Optional[RetryHook] = None,
        after_attempt: Optional[RetryHook] = None,
        before_sleep: Optional[RetryHook] = None,
        on_giveup: Optional[RetryHook] = None,
    ) -> None:
        self.policy = policy or RetryPolicy()
        self.budget = budget
        self.before_attempt = before_attempt
        self.after_attempt = after_attempt
        self.before_sleep = before_sleep
        self.on_giveup = on_giveup

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> RetryResult[T]:
        operation_id = str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        attempts: list[RetryAttempt] = []
        last_error: Optional[BaseException] = None
        last_value: Optional[T] = None
        start_perf = time.monotonic()

        for attempt_number in range(1, self.policy.max_attempts + 1):
            self._check_elapsed(start_perf)
            attempt_started = datetime.now(timezone.utc)
            pending_attempt = RetryAttempt(
                operation_id=operation_id,
                attempt_number=attempt_number,
                max_attempts=self.policy.max_attempts,
                started_at=attempt_started,
            )
            self._run_hook(self.before_attempt, pending_attempt)
            try:
                value = func(*args, **kwargs)
                last_value = value
                should_retry = self.policy.should_retry_result(value)
                finished_attempt = RetryAttempt(
                    operation_id=operation_id,
                    attempt_number=attempt_number,
                    max_attempts=self.policy.max_attempts,
                    started_at=attempt_started,
                    finished_at=datetime.now(timezone.utc),
                    result=value,
                )
                attempts.append(finished_attempt)
                self._run_hook(self.after_attempt, finished_attempt)
                if not should_retry:
                    return self._result(operation_id, RetryStatus.SUCCEEDED, last_value, attempts, started)
                if attempt_number >= self.policy.max_attempts:
                    return self._result(operation_id, RetryStatus.EXHAUSTED, last_value, attempts, started, error="retry_on_result exhausted")
                delay = self._prepare_sleep(attempt_number, None, finished_attempt)
                time.sleep(delay)
            except BaseException as exc:
                last_error = exc
                retryable = self.policy.should_retry_exception(exc)
                finished_attempt = RetryAttempt(
                    operation_id=operation_id,
                    attempt_number=attempt_number,
                    max_attempts=self.policy.max_attempts,
                    started_at=attempt_started,
                    finished_at=datetime.now(timezone.utc),
                    exception=exc,
                )
                attempts.append(finished_attempt)
                self._run_hook(self.after_attempt, finished_attempt)
                if not retryable:
                    result = self._result(operation_id, RetryStatus.NOT_RETRYABLE, None, attempts, started, exc)
                    if self.policy.reraise:
                        raise exc
                    return result
                if attempt_number >= self.policy.max_attempts:
                    self._run_hook(self.on_giveup, finished_attempt)
                    result = self._result(operation_id, RetryStatus.EXHAUSTED, None, attempts, started, exc)
                    if self.policy.reraise:
                        raise RetryExhaustedError(str(exc)) from exc
                    return result
                delay = self._prepare_sleep(attempt_number, exc, finished_attempt)
                time.sleep(delay)

        return self._result(operation_id, RetryStatus.FAILED, last_value, attempts, started, last_error)

    async def call_async(self, func: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> RetryResult[T]:
        operation_id = str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        attempts: list[RetryAttempt] = []
        last_error: Optional[BaseException] = None
        last_value: Optional[T] = None
        start_perf = time.monotonic()

        for attempt_number in range(1, self.policy.max_attempts + 1):
            self._check_elapsed(start_perf)
            attempt_started = datetime.now(timezone.utc)
            pending_attempt = RetryAttempt(
                operation_id=operation_id,
                attempt_number=attempt_number,
                max_attempts=self.policy.max_attempts,
                started_at=attempt_started,
            )
            self._run_hook(self.before_attempt, pending_attempt)
            try:
                value = await func(*args, **kwargs)
                last_value = value
                should_retry = self.policy.should_retry_result(value)
                finished_attempt = RetryAttempt(
                    operation_id=operation_id,
                    attempt_number=attempt_number,
                    max_attempts=self.policy.max_attempts,
                    started_at=attempt_started,
                    finished_at=datetime.now(timezone.utc),
                    result=value,
                )
                attempts.append(finished_attempt)
                self._run_hook(self.after_attempt, finished_attempt)
                if not should_retry:
                    return self._result(operation_id, RetryStatus.SUCCEEDED, last_value, attempts, started)
                if attempt_number >= self.policy.max_attempts:
                    return self._result(operation_id, RetryStatus.EXHAUSTED, last_value, attempts, started, error="retry_on_result exhausted")
                delay = self._prepare_sleep(attempt_number, None, finished_attempt)
                await asyncio.sleep(delay)
            except BaseException as exc:
                last_error = exc
                retryable = self.policy.should_retry_exception(exc)
                finished_attempt = RetryAttempt(
                    operation_id=operation_id,
                    attempt_number=attempt_number,
                    max_attempts=self.policy.max_attempts,
                    started_at=attempt_started,
                    finished_at=datetime.now(timezone.utc),
                    exception=exc,
                )
                attempts.append(finished_attempt)
                self._run_hook(self.after_attempt, finished_attempt)
                if not retryable:
                    result = self._result(operation_id, RetryStatus.NOT_RETRYABLE, None, attempts, started, exc)
                    if self.policy.reraise:
                        raise exc
                    return result
                if attempt_number >= self.policy.max_attempts:
                    self._run_hook(self.on_giveup, finished_attempt)
                    result = self._result(operation_id, RetryStatus.EXHAUSTED, None, attempts, started, exc)
                    if self.policy.reraise:
                        raise RetryExhaustedError(str(exc)) from exc
                    return result
                delay = self._prepare_sleep(attempt_number, exc, finished_attempt)
                await asyncio.sleep(delay)

        return self._result(operation_id, RetryStatus.FAILED, last_value, attempts, started, last_error)

    def _prepare_sleep(self, attempt_number: int, exc: Optional[BaseException], attempt: RetryAttempt) -> float:
        if self.budget and not self.budget.acquire():
            raise RetryBudgetExceededError("Retry budget exceeded")
        delay = self.policy.delay_for(attempt_number, exc)
        sleep_attempt = RetryAttempt(
            operation_id=attempt.operation_id,
            attempt_number=attempt.attempt_number,
            max_attempts=attempt.max_attempts,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
            delay_seconds=delay,
            exception=attempt.exception,
            result=attempt.result,
            metadata=attempt.metadata,
        )
        self._run_hook(self.before_sleep, sleep_attempt)
        return delay

    def _check_elapsed(self, start_perf: float) -> None:
        if self.policy.max_elapsed_seconds is not None:
            if time.monotonic() - start_perf > self.policy.max_elapsed_seconds:
                raise RetryTimeoutError(f"Retry max elapsed exceeded: {self.policy.max_elapsed_seconds}s")

    def _run_hook(self, hook: Optional[RetryHook], attempt: RetryAttempt) -> None:
        if hook is None:
            return
        try:
            hook(attempt)
        except Exception:
            logger.exception("Retry hook failed")

    def _result(
        self,
        operation_id: str,
        status: RetryStatus,
        value: Optional[T],
        attempts: Sequence[RetryAttempt],
        started: datetime,
        exc: Optional[BaseException] = None,
        error: Optional[str] = None,
    ) -> RetryResult[T]:
        return RetryResult(
            operation_id=operation_id,
            status=status,
            value=value,
            attempts=tuple(attempts),
            policy_name=self.policy.name,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            error=error or (str(exc) if exc else None),
            error_type=type(exc).__name__ if exc else None,
        )


# =============================================================================
# Decorators and helper functions
# =============================================================================

def with_retry(policy: Optional[RetryPolicy] = None, **policy_kwargs: Any) -> Callable[[F], F]:
    """Decorator para aplicar retry em função sync/async."""
    active_policy = policy or RetryPolicy(**policy_kwargs)

    def decorator(func: F) -> F:
        is_async = inspect.iscoroutinefunction(func)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            retrier = Retrier(active_policy)
            result = await retrier.call_async(cast(Callable[..., Awaitable[Any]], func), *args, **kwargs)
            return result.unwrap()

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            retrier = Retrier(active_policy)
            result = retrier.call(func, *args, **kwargs)
            return result.unwrap()

        return cast(F, async_wrapper if is_async else sync_wrapper)

    return decorator


def retry_call(func: Callable[..., T], *args: Any, policy: Optional[RetryPolicy] = None, **kwargs: Any) -> RetryResult[T]:
    """Executa função síncrona com retry e retorna RetryResult."""
    return Retrier(policy or RetryPolicy()).call(func, *args, **kwargs)


async def retry_call_async(func: Callable[..., Awaitable[T]], *args: Any, policy: Optional[RetryPolicy] = None, **kwargs: Any) -> RetryResult[T]:
    """Executa função assíncrona com retry e retorna RetryResult."""
    return await Retrier(policy or RetryPolicy()).call_async(func, *args, **kwargs)


def apply_jitter(base_delay: float, strategy: JitterStrategy, jitter_seconds: float) -> float:
    base_delay = max(0.0, base_delay)
    if strategy == JitterStrategy.NONE or jitter_seconds <= 0:
        return base_delay
    if strategy == JitterStrategy.ADDITIVE:
        return base_delay + random.uniform(0, jitter_seconds)
    if strategy == JitterStrategy.FULL:
        return random.uniform(0, base_delay)
    if strategy == JitterStrategy.EQUAL:
        return (base_delay / 2.0) + random.uniform(0, base_delay / 2.0)
    return base_delay


def retry_if_exception_type(*exception_types: Type[BaseException]) -> RetryPredicate:
    """Cria predicado de retry por tipo de exceção."""
    def predicate(exc: BaseException) -> bool:
        return isinstance(exc, exception_types)

    return predicate


def retry_if_result(predicate: ResultPredicate) -> ResultPredicate:
    """Retorna predicado de resultado para composição semântica."""
    return predicate


def retry_if_none(value: Any) -> bool:
    return value is None


def retry_if_false(value: Any) -> bool:
    return value is False


def retry_if_status_code(codes: Iterable[int]) -> ResultPredicate:
    wanted = set(codes)

    def predicate(result: Any) -> bool:
        status_code = getattr(result, "status_code", None)
        if status_code is None and isinstance(result, Mapping):
            status_code = result.get("status_code") or result.get("status")
        try:
            return int(status_code) in wanted
        except Exception:
            return False

    return predicate


def log_before_sleep(level: int = logging.WARNING) -> RetryHook:
    """Hook que loga antes de dormir entre retries."""
    def hook(attempt: RetryAttempt) -> None:
        logger.log(
            level,
            "Retrying operation_id=%s attempt=%s/%s delay_seconds=%.3f error=%s",
            attempt.operation_id,
            attempt.attempt_number,
            attempt.max_attempts,
            attempt.delay_seconds,
            attempt.exception,
        )

    return hook


def safe_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): safe_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [safe_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


DEFAULT_RETRY_POLICY = RetryPolicy()
NETWORK_RETRY_POLICY = RetryPolicy(
    name="network",
    max_attempts=5,
    initial_delay_seconds=0.5,
    max_delay_seconds=20.0,
    retry_exceptions=(TimeoutError, ConnectionError, OSError),
)
FAST_RETRY_POLICY = RetryPolicy(
    name="fast",
    max_attempts=3,
    initial_delay_seconds=0.05,
    max_delay_seconds=1.0,
)
SLOW_RETRY_POLICY = RetryPolicy(
    name="slow",
    max_attempts=7,
    initial_delay_seconds=1.0,
    max_delay_seconds=60.0,
    max_elapsed_seconds=300.0,
)


__all__ = [
    "BackoffStrategy",
    "DEFAULT_RETRY_POLICY",
    "DelayStrategy",
    "FAST_RETRY_POLICY",
    "JitterStrategy",
    "NETWORK_RETRY_POLICY",
    "ResultPredicate",
    "Retrier",
    "RetryAttempt",
    "RetryBudget",
    "RetryBudgetExceededError",
    "RetryError",
    "RetryExhaustedError",
    "RetryHook",
    "RetryPolicy",
    "RetryPredicate",
    "RetryResult",
    "RetryStatus",
    "RetryTimeoutError",
    "SLOW_RETRY_POLICY",
    "apply_jitter",
    "log_before_sleep",
    "retry_call",
    "retry_call_async",
    "retry_if_exception_type",
    "retry_if_false",
    "retry_if_none",
    "retry_if_result",
    "retry_if_status_code",
    "safe_json_value",
    "with_retry",
]

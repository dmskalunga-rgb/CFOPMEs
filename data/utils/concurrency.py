"""
data/utils/concurrency.py

Enterprise-grade concurrency utilities.

Este módulo centraliza utilitários robustos para concorrência, paralelismo leve,
execução assíncrona, retries, timeouts, rate limiting, circuit breaker e
coordenação segura entre tarefas.

Capacidades principais:
- Execução concorrente com ThreadPoolExecutor.
- Execução assíncrona com asyncio.
- Resultados estruturados por tarefa.
- Timeout, retry com backoff exponencial e jitter.
- Rate limiter thread-safe.
- Circuit breaker para proteger dependências externas.
- Semáforos nomeados e locks nomeados.
- Executor em batch com preservação opcional de ordem.
- Helpers para map, gather, fan-out/fan-in e graceful shutdown.
- Sem dependências externas obrigatórias.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import inspect
import logging
import random
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from queue import Queue
from typing import (
    Any,
    Awaitable,
    Callable,
    Deque,
    Dict,
    Generic,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Tuple,
    Type,
    TypeVar,
    Union,
)


logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")
CallableTask = Callable[..., R]
AsyncCallableTask = Callable[..., Awaitable[R]]


class TaskStatus(str, Enum):
    """Status de execução de uma tarefa."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class CircuitState(str, Enum):
    """Estado do circuit breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class ConcurrencyError(Exception):
    """Erro base do módulo de concorrência."""


class TaskExecutionError(ConcurrencyError):
    """Erro de execução de tarefa."""


class CircuitBreakerOpenError(ConcurrencyError):
    """Erro lançado quando circuit breaker está aberto."""


class RateLimitExceededError(ConcurrencyError):
    """Erro lançado quando não é possível adquirir permissão de rate limit."""


@dataclass(frozen=True)
class RetryPolicy:
    """Política de retry com backoff exponencial e jitter."""

    attempts: int = 3
    initial_delay_seconds: float = 0.2
    max_delay_seconds: float = 5.0
    multiplier: float = 2.0
    jitter_seconds: float = 0.1
    retry_exceptions: Tuple[Type[BaseException], ...] = (Exception,)

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be >= 1")
        if self.initial_delay_seconds < 0:
            raise ValueError("initial_delay_seconds must be >= 0")
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("max_delay_seconds must be >= initial_delay_seconds")
        if self.multiplier < 1:
            raise ValueError("multiplier must be >= 1")

    def delay_for_attempt(self, attempt_index: int) -> float:
        base = min(self.max_delay_seconds, self.initial_delay_seconds * (self.multiplier ** max(0, attempt_index - 1)))
        jitter = random.uniform(0, self.jitter_seconds) if self.jitter_seconds > 0 else 0.0
        return base + jitter


@dataclass(frozen=True)
class TimeoutPolicy:
    """Política de timeout."""

    seconds: Optional[float] = None

    def __post_init__(self) -> None:
        if self.seconds is not None and self.seconds <= 0:
            raise ValueError("timeout seconds must be positive or None")


@dataclass(frozen=True)
class TaskSpec(Generic[T]):
    """Especificação de uma tarefa síncrona."""

    func: Callable[..., T]
    args: Tuple[Any, ...] = field(default_factory=tuple)
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AsyncTaskSpec(Generic[T]):
    """Especificação de uma tarefa assíncrona."""

    func: Callable[..., Awaitable[T]]
    args: Tuple[Any, ...] = field(default_factory=tuple)
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskResult(Generic[T]):
    """Resultado estruturado de uma tarefa."""

    task_id: str
    name: Optional[str]
    status: TaskStatus
    value: Optional[T] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    attempts: int = 1
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.finished_at - self.started_at).total_seconds() * 1000.0)

    @property
    def ok(self) -> bool:
        return self.status == TaskStatus.SUCCEEDED

    def unwrap(self) -> T:
        if not self.ok:
            raise TaskExecutionError(f"Task {self.task_id} failed: {self.error}")
        return self.value  # type: ignore[return-value]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "status": self.status.value,
            "value": self.value,
            "error": self.error,
            "error_type": self.error_type,
            "attempts": self.attempts,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
            "metadata": dict(self.metadata),
        }


class NamedLockRegistry:
    """Registry thread-safe de locks nomeados."""

    def __init__(self) -> None:
        self._locks: MutableMapping[str, threading.RLock] = defaultdict(threading.RLock)
        self._guard = threading.RLock()

    def get(self, name: str) -> threading.RLock:
        with self._guard:
            return self._locks[name]

    def synchronized(self, name: str) -> Callable[[Callable[..., R]], Callable[..., R]]:
        def decorator(func: Callable[..., R]) -> Callable[..., R]:
            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> R:
                with self.get(name):
                    return func(*args, **kwargs)

            return wrapper

        return decorator


class NamedSemaphoreRegistry:
    """Registry thread-safe de semáforos nomeados."""

    def __init__(self) -> None:
        self._semaphores: MutableMapping[str, threading.Semaphore] = {}
        self._guard = threading.RLock()

    def get(self, name: str, value: int = 1) -> threading.Semaphore:
        if value <= 0:
            raise ValueError("semaphore value must be positive")
        with self._guard:
            if name not in self._semaphores:
                self._semaphores[name] = threading.Semaphore(value)
            return self._semaphores[name]


class RateLimiter:
    """Rate limiter thread-safe baseado em janela deslizante."""

    def __init__(self, *, max_calls: int, period_seconds: float) -> None:
        if max_calls <= 0:
            raise ValueError("max_calls must be positive")
        if period_seconds <= 0:
            raise ValueError("period_seconds must be positive")
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self._calls: Deque[float] = deque()
        self._lock = threading.RLock()

    def acquire(self, *, block: bool = True, timeout: Optional[float] = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                self._evict_old(now)
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return True
                wait_time = self.period_seconds - (now - self._calls[0])

            if not block:
                return False
            if deadline is not None and time.monotonic() + wait_time > deadline:
                return False
            time.sleep(max(0.001, min(wait_time, 0.25)))

    def __enter__(self) -> "RateLimiter":
        if not self.acquire(block=True):
            raise RateLimitExceededError("rate limit exceeded")
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def _evict_old(self, now: float) -> None:
        while self._calls and now - self._calls[0] >= self.period_seconds:
            self._calls.popleft()


class CircuitBreaker:
    """Circuit breaker thread-safe para dependências instáveis."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 30.0,
        success_threshold: int = 2,
        expected_exceptions: Tuple[Type[BaseException], ...] = (Exception,),
        name: str = "default",
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        if recovery_timeout_seconds <= 0:
            raise ValueError("recovery_timeout_seconds must be positive")
        if success_threshold <= 0:
            raise ValueError("success_threshold must be positive")
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.success_threshold = success_threshold
        self.expected_exceptions = expected_exceptions
        self.name = name
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at: Optional[float] = None
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._refresh_state()
            return self._state

    def call(self, func: Callable[..., R], *args: Any, **kwargs: Any) -> R:
        with self._lock:
            self._refresh_state()
            if self._state == CircuitState.OPEN:
                raise CircuitBreakerOpenError(f"Circuit breaker is open: {self.name}")
            if self._state == CircuitState.HALF_OPEN:
                self._success_count = 0

        try:
            result = func(*args, **kwargs)
        except self.expected_exceptions:
            self._record_failure()
            raise
        else:
            self._record_success()
            return result

    def decorate(self, func: Callable[..., R]) -> Callable[..., R]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> R:
            return self.call(func, *args, **kwargs)

        return wrapper

    def _record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._success_count = 0
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

    def _record_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    self._opened_at = None
            else:
                self._failure_count = 0

    def _refresh_state(self) -> None:
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self.recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0


class ConcurrentExecutor:
    """Executor enterprise para tarefas concorrentes síncronas."""

    def __init__(
        self,
        *,
        max_workers: Optional[int] = None,
        retry_policy: Optional[RetryPolicy] = None,
        timeout_policy: Optional[TimeoutPolicy] = None,
        preserve_order: bool = True,
        thread_name_prefix: str = "data-utils-worker",
    ) -> None:
        self.max_workers = max_workers
        self.retry_policy = retry_policy or RetryPolicy(attempts=1)
        self.timeout_policy = timeout_policy or TimeoutPolicy()
        self.preserve_order = preserve_order
        self.thread_name_prefix = thread_name_prefix

    def run_many(self, tasks: Sequence[TaskSpec[T]]) -> List[TaskResult[T]]:
        if not tasks:
            return []
        results_by_index: Dict[int, TaskResult[T]] = {}
        results: List[TaskResult[T]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix=self.thread_name_prefix) as executor:
            future_to_index = {
                executor.submit(execute_task, task, self.retry_policy): index for index, task in enumerate(tasks)
            }
            for future in concurrent.futures.as_completed(future_to_index, timeout=self.timeout_policy.seconds):
                index = future_to_index[future]
                try:
                    result = future.result(timeout=0)
                except concurrent.futures.TimeoutError:
                    task = tasks[index]
                    result = timeout_result(task)
                except Exception as exc:
                    task = tasks[index]
                    result = exception_result(task, exc)
                if self.preserve_order:
                    results_by_index[index] = result
                else:
                    results.append(result)

        if self.preserve_order:
            for index, task in enumerate(tasks):
                results.append(results_by_index.get(index) or TaskResult(
                    task_id=task.task_id,
                    name=task.name,
                    status=TaskStatus.CANCELLED,
                    error="Task was not completed",
                    error_type="Cancelled",
                    metadata=task.metadata,
                ))
        return results

    def map(self, func: Callable[[T], R], values: Sequence[T]) -> List[TaskResult[R]]:
        tasks = [TaskSpec(func=func, args=(value,), name=getattr(func, "__name__", "task")) for value in values]
        return self.run_many(tasks)


def execute_task(task: TaskSpec[T], retry_policy: Optional[RetryPolicy] = None) -> TaskResult[T]:
    """Executa uma tarefa síncrona com retry."""
    retry_policy = retry_policy or RetryPolicy(attempts=1)
    started = datetime.now(timezone.utc)
    last_error: Optional[BaseException] = None

    for attempt in range(1, retry_policy.attempts + 1):
        try:
            value = task.func(*task.args, **dict(task.kwargs))
            return TaskResult(
                task_id=task.task_id,
                name=task.name,
                status=TaskStatus.SUCCEEDED,
                value=value,
                attempts=attempt,
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                metadata=task.metadata,
            )
        except retry_policy.retry_exceptions as exc:
            last_error = exc
            if attempt >= retry_policy.attempts:
                break
            time.sleep(retry_policy.delay_for_attempt(attempt))

    return TaskResult(
        task_id=task.task_id,
        name=task.name,
        status=TaskStatus.FAILED,
        error=str(last_error),
        error_type=type(last_error).__name__ if last_error else None,
        attempts=retry_policy.attempts,
        started_at=started,
        finished_at=datetime.now(timezone.utc),
        metadata=task.metadata,
    )


async def execute_async_task(task: AsyncTaskSpec[T], retry_policy: Optional[RetryPolicy] = None, timeout_seconds: Optional[float] = None) -> TaskResult[T]:
    """Executa uma tarefa assíncrona com retry e timeout."""
    retry_policy = retry_policy or RetryPolicy(attempts=1)
    started = datetime.now(timezone.utc)
    last_error: Optional[BaseException] = None

    for attempt in range(1, retry_policy.attempts + 1):
        try:
            coroutine = task.func(*task.args, **dict(task.kwargs))
            value = await asyncio.wait_for(coroutine, timeout=timeout_seconds) if timeout_seconds else await coroutine
            return TaskResult(
                task_id=task.task_id,
                name=task.name,
                status=TaskStatus.SUCCEEDED,
                value=value,
                attempts=attempt,
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                metadata=task.metadata,
            )
        except asyncio.TimeoutError as exc:
            last_error = exc
            return TaskResult(
                task_id=task.task_id,
                name=task.name,
                status=TaskStatus.TIMEOUT,
                error="Task timed out",
                error_type="TimeoutError",
                attempts=attempt,
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                metadata=task.metadata,
            )
        except retry_policy.retry_exceptions as exc:
            last_error = exc
            if attempt >= retry_policy.attempts:
                break
            await asyncio.sleep(retry_policy.delay_for_attempt(attempt))

    return TaskResult(
        task_id=task.task_id,
        name=task.name,
        status=TaskStatus.FAILED,
        error=str(last_error),
        error_type=type(last_error).__name__ if last_error else None,
        attempts=retry_policy.attempts,
        started_at=started,
        finished_at=datetime.now(timezone.utc),
        metadata=task.metadata,
    )


async def gather_limited(
    tasks: Sequence[AsyncTaskSpec[T]],
    *,
    limit: int = 10,
    retry_policy: Optional[RetryPolicy] = None,
    timeout_seconds: Optional[float] = None,
    preserve_order: bool = True,
) -> List[TaskResult[T]]:
    """Executa tarefas assíncronas com limite de concorrência."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    semaphore = asyncio.Semaphore(limit)

    async def _run(task: AsyncTaskSpec[T]) -> TaskResult[T]:
        async with semaphore:
            return await execute_async_task(task, retry_policy=retry_policy, timeout_seconds=timeout_seconds)

    coroutines = [_run(task) for task in tasks]
    if preserve_order:
        return list(await asyncio.gather(*coroutines))

    results: List[TaskResult[T]] = []
    for future in asyncio.as_completed(coroutines):
        results.append(await future)
    return results


def run_sync(coro: Awaitable[T]) -> T:
    """Executa coroutine a partir de contexto síncrono."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    if loop.is_running():
        raise ConcurrencyError("Cannot run coroutine synchronously while an event loop is already running")
    return loop.run_until_complete(coro)


def retry(
    *,
    policy: Optional[RetryPolicy] = None,
) -> Callable[[Callable[..., R]], Callable[..., R]]:
    """Decorator de retry para funções síncronas."""
    retry_policy = policy or RetryPolicy()

    def decorator(func: Callable[..., R]) -> Callable[..., R]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> R:
            spec = TaskSpec(func=func, args=args, kwargs=kwargs, name=getattr(func, "__name__", None))
            result = execute_task(spec, retry_policy=retry_policy)
            return result.unwrap()

        return wrapper

    return decorator


def async_retry(*, policy: Optional[RetryPolicy] = None, timeout_seconds: Optional[float] = None) -> Callable[[AsyncCallableTask[R]], AsyncCallableTask[R]]:
    """Decorator de retry para funções assíncronas."""
    retry_policy = policy or RetryPolicy()

    def decorator(func: AsyncCallableTask[R]) -> AsyncCallableTask[R]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> R:
            spec = AsyncTaskSpec(func=func, args=args, kwargs=kwargs, name=getattr(func, "__name__", None))
            result = await execute_async_task(spec, retry_policy=retry_policy, timeout_seconds=timeout_seconds)
            return result.unwrap()

        return wrapper

    return decorator


def timeout_result(task: TaskSpec[Any]) -> TaskResult[Any]:
    now = datetime.now(timezone.utc)
    return TaskResult(
        task_id=task.task_id,
        name=task.name,
        status=TaskStatus.TIMEOUT,
        error="Task timed out",
        error_type="TimeoutError",
        started_at=now,
        finished_at=now,
        metadata=task.metadata,
    )


def exception_result(task: TaskSpec[Any], exc: BaseException) -> TaskResult[Any]:
    now = datetime.now(timezone.utc)
    return TaskResult(
        task_id=task.task_id,
        name=task.name,
        status=TaskStatus.FAILED,
        error=str(exc),
        error_type=type(exc).__name__,
        started_at=now,
        finished_at=now,
        metadata=task.metadata,
    )


def partition_results(results: Sequence[TaskResult[T]]) -> Tuple[List[TaskResult[T]], List[TaskResult[T]]]:
    """Separa resultados bem-sucedidos e falhos."""
    succeeded = [result for result in results if result.ok]
    failed = [result for result in results if not result.ok]
    return succeeded, failed


def unwrap_results(results: Sequence[TaskResult[T]], *, fail_fast: bool = True) -> List[T]:
    """Extrai valores dos resultados."""
    values: List[T] = []
    errors: List[str] = []
    for result in results:
        if result.ok:
            values.append(result.value)  # type: ignore[arg-type]
        else:
            errors.append(f"{result.task_id}: {result.error}")
            if fail_fast:
                raise TaskExecutionError(errors[-1])
    if errors and fail_fast:
        raise TaskExecutionError("; ".join(errors))
    return values


GLOBAL_LOCKS = NamedLockRegistry()
GLOBAL_SEMAPHORES = NamedSemaphoreRegistry()


__all__ = [
    "AsyncTaskSpec",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "CircuitState",
    "ConcurrencyError",
    "ConcurrentExecutor",
    "GLOBAL_LOCKS",
    "GLOBAL_SEMAPHORES",
    "NamedLockRegistry",
    "NamedSemaphoreRegistry",
    "RateLimitExceededError",
    "RateLimiter",
    "RetryPolicy",
    "TaskExecutionError",
    "TaskResult",
    "TaskSpec",
    "TaskStatus",
    "TimeoutPolicy",
    "async_retry",
    "exception_result",
    "execute_async_task",
    "execute_task",
    "gather_limited",
    "partition_results",
    "retry",
    "run_sync",
    "timeout_result",
    "unwrap_results",
]

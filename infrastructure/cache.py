# kwanza-ai-core/infrastructure/cache.py
from __future__ import annotations

import abc
import asyncio
import contextlib
import hashlib
import json
import logging
import pickle
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Generic,
    Iterable,
    Mapping,
    Optional,
    Protocol,
    TypeVar,
)

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


class CacheBackend(str, Enum):
    MEMORY = "memory"
    REDIS = "redis"


class SerializationMode(str, Enum):
    JSON = "json"
    PICKLE = "pickle"
    STRING = "string"


@dataclass(frozen=True)
class CacheConfig:
    backend: CacheBackend = CacheBackend.MEMORY
    namespace: str = "kwanza-ai-core"
    default_ttl_seconds: int = 300
    max_memory_items: int = 50_000
    serialization: SerializationMode = SerializationMode.JSON
    key_separator: str = ":"
    enable_metrics: bool = True
    enable_stampede_protection: bool = True
    lock_ttl_seconds: int = 30
    stale_while_revalidate_seconds: int = 60
    redis_url: Optional[str] = None
    redis_socket_timeout_seconds: float = 5.0
    redis_health_check_interval_seconds: int = 30


@dataclass
class CacheEntry:
    value: bytes
    created_at: float
    expires_at: Optional[float]
    stale_until: Optional[float]
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and time.time() >= self.expires_at

    @property
    def is_stale_available(self) -> bool:
        return self.stale_until is not None and time.time() < self.stale_until


@dataclass(frozen=True)
class CacheStats:
    hits: int = 0
    misses: int = 0
    sets: int = 0
    deletes: int = 0
    errors: int = 0
    evictions: int = 0

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return 0.0 if total == 0 else self.hits / total


class MetricsSink(Protocol):
    def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None: ...

    def timing(
        self,
        name: str,
        value_ms: float,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None: ...


class NoopMetricsSink:
    def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        return None

    def timing(
        self,
        name: str,
        value_ms: float,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        return None


class CacheError(RuntimeError):
    pass


class CacheSerializationError(CacheError):
    pass


class CacheBackendError(CacheError):
    pass


class CacheSerializer:
    def __init__(self, mode: SerializationMode = SerializationMode.JSON) -> None:
        self.mode = mode

    def dumps(self, value: Any) -> bytes:
        try:
            if self.mode == SerializationMode.JSON:
                return json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")

            if self.mode == SerializationMode.PICKLE:
                return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)

            if self.mode == SerializationMode.STRING:
                return str(value).encode("utf-8")

            raise CacheSerializationError(f"Unsupported serialization mode: {self.mode}")

        except Exception as exc:
            raise CacheSerializationError(str(exc)) from exc

    def loads(self, payload: bytes) -> Any:
        try:
            if self.mode == SerializationMode.JSON:
                return json.loads(payload.decode("utf-8"))

            if self.mode == SerializationMode.PICKLE:
                return pickle.loads(payload)

            if self.mode == SerializationMode.STRING:
                return payload.decode("utf-8")

            raise CacheSerializationError(f"Unsupported serialization mode: {self.mode}")

        except Exception as exc:
            raise CacheSerializationError(str(exc)) from exc


class CacheKeyBuilder:
    def __init__(self, namespace: str, separator: str = ":") -> None:
        self.namespace = namespace.strip(separator)
        self.separator = separator

    def build(self, *parts: Any) -> str:
        clean_parts = [
            self._clean(str(part))
            for part in parts
            if part is not None and str(part) != ""
        ]
        return self.separator.join([self.namespace, *clean_parts])

    def hash(self, *parts: Any) -> str:
        raw = json.dumps(parts, ensure_ascii=False, default=str, sort_keys=True)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return self.build("hash", digest)

    def pattern(self, *parts: Any) -> str:
        clean_parts = [self._clean(str(part)) for part in parts if part is not None]
        return self.separator.join([self.namespace, *clean_parts, "*"])

    def _clean(self, value: str) -> str:
        return (
            value.strip()
            .replace(" ", "_")
            .replace("\n", "")
            .replace("\r", "")
            .replace(self.separator * 2, self.separator)
        )


class CacheStore(abc.ABC):
    @abc.abstractmethod
    async def get_entry(self, key: str) -> Optional[CacheEntry]:
        raise NotImplementedError

    @abc.abstractmethod
    async def set_entry(self, key: str, entry: CacheEntry) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def delete(self, key: str) -> bool:
        raise NotImplementedError

    @abc.abstractmethod
    async def delete_pattern(self, pattern: str) -> int:
        raise NotImplementedError

    @abc.abstractmethod
    async def exists(self, key: str) -> bool:
        raise NotImplementedError

    @abc.abstractmethod
    async def clear(self) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


class MemoryCacheStore(CacheStore):
    def __init__(self, max_items: int = 50_000) -> None:
        self.max_items = max_items
        self._data: Dict[str, CacheEntry] = {}
        self._access_order: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def get_entry(self, key: str) -> Optional[CacheEntry]:
        async with self._lock:
            entry = self._data.get(key)
            if entry is not None:
                self._access_order[key] = time.time()
            return entry

    async def set_entry(self, key: str, entry: CacheEntry) -> None:
        async with self._lock:
            if len(self._data) >= self.max_items and key not in self._data:
                await self._evict_lru_unlocked()

            self._data[key] = entry
            self._access_order[key] = time.time()

    async def delete(self, key: str) -> bool:
        async with self._lock:
            existed = key in self._data
            self._data.pop(key, None)
            self._access_order.pop(key, None)
            return existed

    async def delete_pattern(self, pattern: str) -> int:
        async with self._lock:
            prefix = pattern.rstrip("*")
            keys = [key for key in self._data if key.startswith(prefix)]

            for key in keys:
                self._data.pop(key, None)
                self._access_order.pop(key, None)

            return len(keys)

    async def exists(self, key: str) -> bool:
        async with self._lock:
            return key in self._data

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()
            self._access_order.clear()

    async def close(self) -> None:
        await self.clear()

    async def _evict_lru_unlocked(self) -> None:
        if not self._access_order:
            return

        oldest_key = min(self._access_order, key=self._access_order.get)
        self._data.pop(oldest_key, None)
        self._access_order.pop(oldest_key, None)


class RedisCacheStore(CacheStore):
    """
    Backend Redis opcional.

    Requer:
        pip install redis

    Mantém payload único por chave contendo:
    - value serializado
    - created_at
    - expires_at
    - stale_until
    - metadata
    """

    def __init__(self, redis_url: str, socket_timeout: float = 5.0) -> None:
        if not redis_url:
            raise ValueError("redis_url is required for RedisCacheStore")

        try:
            import redis.asyncio as redis_async
        except ImportError as exc:
            raise CacheBackendError(
                "redis package is required. Install with: pip install redis"
            ) from exc

        self._redis = redis_async.from_url(
            redis_url,
            socket_timeout=socket_timeout,
            decode_responses=False,
        )

    async def get_entry(self, key: str) -> Optional[CacheEntry]:
        payload = await self._redis.get(key)
        if payload is None:
            return None

        data = pickle.loads(payload)
        return CacheEntry(
            value=data["value"],
            created_at=data["created_at"],
            expires_at=data["expires_at"],
            stale_until=data["stale_until"],
            metadata=data.get("metadata", {}),
        )

    async def set_entry(self, key: str, entry: CacheEntry) -> None:
        payload = pickle.dumps(
            {
                "value": entry.value,
                "created_at": entry.created_at,
                "expires_at": entry.expires_at,
                "stale_until": entry.stale_until,
                "metadata": entry.metadata,
            },
            protocol=pickle.HIGHEST_PROTOCOL,
        )

        ttl = None
        if entry.stale_until is not None:
            ttl = max(1, int(entry.stale_until - time.time()))
        elif entry.expires_at is not None:
            ttl = max(1, int(entry.expires_at - time.time()))

        await self._redis.set(key, payload, ex=ttl)

    async def delete(self, key: str) -> bool:
        return bool(await self._redis.delete(key))

    async def delete_pattern(self, pattern: str) -> int:
        deleted = 0
        async for key in self._redis.scan_iter(match=pattern):
            deleted += await self._redis.delete(key)
        return deleted

    async def exists(self, key: str) -> bool:
        return bool(await self._redis.exists(key))

    async def clear(self) -> None:
        await self._redis.flushdb()

    async def close(self) -> None:
        await self._redis.aclose()


class AsyncCache:
    def __init__(
        self,
        config: Optional[CacheConfig] = None,
        store: Optional[CacheStore] = None,
        metrics: Optional[MetricsSink] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or CacheConfig()
        self.metrics = metrics or NoopMetricsSink()
        self.logger = logger or logging.getLogger("kwanza.infrastructure.cache")
        self.serializer = CacheSerializer(self.config.serialization)
        self.keys = CacheKeyBuilder(self.config.namespace, self.config.key_separator)

        self._store = store or self._build_store()
        self._stats = CacheStats()
        self._locks: Dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def get(self, key: str, default: Optional[T] = None) -> Optional[T]:
        started = time.monotonic()

        try:
            entry = await self._store.get_entry(key)

            if entry is None:
                self._record_miss()
                return default

            if entry.is_expired:
                if entry.is_stale_available:
                    self._record_hit(stale=True)
                    return self.serializer.loads(entry.value)

                await self.delete(key)
                self._record_miss()
                return default

            self._record_hit()
            return self.serializer.loads(entry.value)

        except Exception as exc:
            self._record_error()
            self.logger.exception("Cache get failed: %s", key)
            return default

        finally:
            self._record_latency("cache.get.latency_ms", started)

    async def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        started = time.monotonic()

        try:
            ttl = self.config.default_ttl_seconds if ttl_seconds is None else ttl_seconds
            now = time.time()

            expires_at = None if ttl <= 0 else now + ttl
            stale_until = None

            if expires_at is not None and self.config.stale_while_revalidate_seconds > 0:
                stale_until = expires_at + self.config.stale_while_revalidate_seconds

            entry = CacheEntry(
                value=self.serializer.dumps(value),
                created_at=now,
                expires_at=expires_at,
                stale_until=stale_until,
                metadata=dict(metadata or {}),
            )

            await self._store.set_entry(key, entry)
            self._stats = CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                sets=self._stats.sets + 1,
                deletes=self._stats.deletes,
                errors=self._stats.errors,
                evictions=self._stats.evictions,
            )
            self.metrics.increment("cache.set", tags=self._tags())

        except Exception as exc:
            self._record_error()
            self.logger.exception("Cache set failed: %s", key)
            raise CacheBackendError(str(exc)) from exc

        finally:
            self._record_latency("cache.set.latency_ms", started)

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[T]],
        ttl_seconds: Optional[int] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> T:
        cached = await self.get(key)

        if cached is not None:
            return cached

        if not self.config.enable_stampede_protection:
            value = await factory()
            await self.set(key, value, ttl_seconds=ttl_seconds, metadata=metadata)
            return value

        lock = await self._get_key_lock(key)

        async with lock:
            cached = await self.get(key)
            if cached is not None:
                return cached

            value = await factory()
            await self.set(key, value, ttl_seconds=ttl_seconds, metadata=metadata)
            return value

    async def delete(self, key: str) -> bool:
        started = time.monotonic()

        try:
            deleted = await self._store.delete(key)

            if deleted:
                self._stats = CacheStats(
                    hits=self._stats.hits,
                    misses=self._stats.misses,
                    sets=self._stats.sets,
                    deletes=self._stats.deletes + 1,
                    errors=self._stats.errors,
                    evictions=self._stats.evictions,
                )
                self.metrics.increment("cache.delete", tags=self._tags())

            return deleted

        except Exception as exc:
            self._record_error()
            self.logger.exception("Cache delete failed: %s", key)
            raise CacheBackendError(str(exc)) from exc

        finally:
            self._record_latency("cache.delete.latency_ms", started)

    async def invalidate_pattern(self, pattern: str) -> int:
        started = time.monotonic()

        try:
            deleted = await self._store.delete_pattern(pattern)
            self.metrics.increment("cache.invalidate_pattern", deleted, tags=self._tags())
            return deleted

        except Exception as exc:
            self._record_error()
            self.logger.exception("Cache pattern invalidation failed: %s", pattern)
            raise CacheBackendError(str(exc)) from exc

        finally:
            self._record_latency("cache.invalidate_pattern.latency_ms", started)

    async def exists(self, key: str) -> bool:
        try:
            return await self._store.exists(key)
        except Exception:
            self._record_error()
            return False

    async def clear(self) -> None:
        await self._store.clear()

    async def close(self) -> None:
        await self._store.close()

    def stats(self) -> CacheStats:
        return self._stats

    def cached(
        self,
        *,
        key_builder: Optional[Callable[..., str]] = None,
        ttl_seconds: Optional[int] = None,
        namespace: Optional[str] = None,
    ) -> Callable[[F], F]:
        def decorator(func: F) -> F:
            @wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                if key_builder is not None:
                    key = key_builder(*args, **kwargs)
                else:
                    key = self._function_key(func, namespace, args, kwargs)

                return await self.get_or_set(
                    key,
                    lambda: func(*args, **kwargs),
                    ttl_seconds=ttl_seconds,
                    metadata={
                        "function": f"{func.__module__}.{func.__qualname__}",
                    },
                )

            return wrapper  # type: ignore[return-value]

        return decorator

    async def _get_key_lock(self, key: str) -> asyncio.Lock:
        async with self._locks_guard:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    def _function_key(
        self,
        func: Callable[..., Any],
        namespace: Optional[str],
        args: tuple[Any, ...],
        kwargs: Mapping[str, Any],
    ) -> str:
        return self.keys.hash(
            namespace or "fn",
            func.__module__,
            func.__qualname__,
            args,
            dict(sorted(kwargs.items())),
        )

    def _build_store(self) -> CacheStore:
        if self.config.backend == CacheBackend.MEMORY:
            return MemoryCacheStore(max_items=self.config.max_memory_items)

        if self.config.backend == CacheBackend.REDIS:
            if not self.config.redis_url:
                raise CacheBackendError("redis_url is required when backend=redis")

            return RedisCacheStore(
                redis_url=self.config.redis_url,
                socket_timeout=self.config.redis_socket_timeout_seconds,
            )

        raise CacheBackendError(f"Unsupported cache backend: {self.config.backend}")

    def _record_hit(self, stale: bool = False) -> None:
        self._stats = CacheStats(
            hits=self._stats.hits + 1,
            misses=self._stats.misses,
            sets=self._stats.sets,
            deletes=self._stats.deletes,
            errors=self._stats.errors,
            evictions=self._stats.evictions,
        )
        self.metrics.increment("cache.hit", tags={**self._tags(), "stale": str(stale).lower()})

    def _record_miss(self) -> None:
        self._stats = CacheStats(
            hits=self._stats.hits,
            misses=self._stats.misses + 1,
            sets=self._stats.sets,
            deletes=self._stats.deletes,
            errors=self._stats.errors,
            evictions=self._stats.evictions,
        )
        self.metrics.increment("cache.miss", tags=self._tags())

    def _record_error(self) -> None:
        self._stats = CacheStats(
            hits=self._stats.hits,
            misses=self._stats.misses,
            sets=self._stats.sets,
            deletes=self._stats.deletes,
            errors=self._stats.errors + 1,
            evictions=self._stats.evictions,
        )
        self.metrics.increment("cache.error", tags=self._tags())

    def _record_latency(self, metric: str, started: float) -> None:
        if self.config.enable_metrics:
            self.metrics.timing(
                metric,
                (time.monotonic() - started) * 1000,
                tags=self._tags(),
            )

    def _tags(self) -> Dict[str, str]:
        return {
            "backend": self.config.backend.value,
            "namespace": self.config.namespace,
        }


class CacheLock:
    """
    Lock distribuído simples usando cache store.

    Em Redis funciona como lock distribuído básico.
    Em memória funciona apenas no processo atual.
    """

    def __init__(
        self,
        cache: AsyncCache,
        name: str,
        ttl_seconds: int = 30,
        retry_delay_seconds: float = 0.1,
        acquire_timeout_seconds: float = 10.0,
    ) -> None:
        self.cache = cache
        self.name = cache.keys.build("lock", name)
        self.ttl_seconds = ttl_seconds
        self.retry_delay_seconds = retry_delay_seconds
        self.acquire_timeout_seconds = acquire_timeout_seconds
        self.token = str(uuid.uuid4())
        self.acquired = False

    async def __aenter__(self) -> "CacheLock":
        started = time.monotonic()

        while time.monotonic() - started < self.acquire_timeout_seconds:
            if not await self.cache.exists(self.name):
                await self.cache.set(
                    self.name,
                    {"token": self.token},
                    ttl_seconds=self.ttl_seconds,
                )
                self.acquired = True
                return self

            await asyncio.sleep(self.retry_delay_seconds)

        raise TimeoutError(f"Could not acquire cache lock: {self.name}")

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if not self.acquired:
            return

        current = await self.cache.get(self.name)

        if isinstance(current, dict) and current.get("token") == self.token:
            with contextlib.suppress(Exception):
                await self.cache.delete(self.name)


class CacheInvalidationBus:
    """
    Barramento local para invalidação coordenada por tópico.
    Pode ser conectado futuramente em Redis Pub/Sub, Kafka ou NATS.
    """

    def __init__(self, cache: AsyncCache) -> None:
        self.cache = cache
        self._subscriptions: Dict[str, list[str]] = {}

    def subscribe_pattern(self, topic: str, pattern: str) -> None:
        self._subscriptions.setdefault(topic, []).append(pattern)

    async def publish_invalidation(self, topic: str) -> int:
        deleted = 0

        for pattern in self._subscriptions.get(topic, []):
            deleted += await self.cache.invalidate_pattern(pattern)

        return deleted


def build_cache_from_env(namespace: str = "kwanza-ai-core") -> AsyncCache:
    backend = CacheBackend(
        str(os.getenv("CACHE_BACKEND", CacheBackend.MEMORY.value)).lower()
    )

    config = CacheConfig(
        backend=backend,
        namespace=os.getenv("CACHE_NAMESPACE", namespace),
        default_ttl_seconds=int(os.getenv("CACHE_DEFAULT_TTL_SECONDS", "300")),
        max_memory_items=int(os.getenv("CACHE_MAX_MEMORY_ITEMS", "50000")),
        serialization=SerializationMode(
            os.getenv("CACHE_SERIALIZATION", SerializationMode.JSON.value).lower()
        ),
        redis_url=os.getenv("REDIS_URL"),
        enable_stampede_protection=os.getenv("CACHE_STAMPEDE_PROTECTION", "true").lower()
        == "true",
        stale_while_revalidate_seconds=int(
            os.getenv("CACHE_STALE_WHILE_REVALIDATE_SECONDS", "60")
        ),
    )

    return AsyncCache(config=config)
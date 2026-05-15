"""
data/processing/enrichment_engine.py

Enterprise-grade data enrichment engine for data platforms.

Purpose
-------
Provides a robust enrichment engine for ETL/ELT, batch, micro-batch, streaming,
API ingestion, master-data enrichment, reference-data joins, feature enrichment,
geocoding-like lookups, identity resolution and operational metadata injection.

Core capabilities
-----------------
- Field/key based enrichment from providers.
- Static mapping provider, callable provider and cache provider.
- Multi-provider enrichment chain with priority and fallback.
- Retry with backoff, timeout soft handling and circuit breaker.
- Per-field merge strategies and conflict policies.
- Conditional enrichment rules.
- Batch enrichment with result reports and audit entries.
- Incremental cache with TTL and JSON snapshot/restore.
- Dead-letter records for failed enrichments.
- Optional telemetry integration.
- Standard library only.

Example
-------
provider = StaticMappingProvider(
    name="country_ref",
    data={"BR": {"country_name": "Brazil", "region": "LATAM"}},
)
engine = EnrichmentEngine(providers=[provider])

result = engine.enrich(
    [{"country_code": "BR"}],
    specs=[EnrichmentSpec(name="country", provider="country_ref", key_fields=("country_code",))],
)
print(result.to_json())
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import logging
import os
import random
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|session|jwt|bearer)",
    re.IGNORECASE,
)

MAX_TEXT_LENGTH = 16_384
DEFAULT_CACHE_TTL_SECONDS = 3600
DEFAULT_MAX_CACHE_ITEMS = 500_000


class EnrichmentStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_FOUND = "not_found"
    EMPTY = "empty"


class ProviderStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OPEN_CIRCUIT = "open_circuit"
    FAILED = "failed"
    UNKNOWN = "unknown"


class MergeStrategy(str, Enum):
    OVERWRITE = "overwrite"
    KEEP_EXISTING = "keep_existing"
    FILL_NULLS = "fill_nulls"
    PREFIX = "prefix"
    NEST = "nest"
    CUSTOM = "custom"


class ConflictPolicy(str, Enum):
    OVERWRITE = "overwrite"
    KEEP_EXISTING = "keep_existing"
    ERROR = "error"
    AUDIT_ONLY = "audit_only"


class MissingPolicy(str, Enum):
    KEEP = "keep"
    MARK = "mark"
    DROP = "drop"
    ERROR = "error"


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    initial_delay_seconds: float = 0.25
    max_delay_seconds: float = 10.0
    backoff_multiplier: float = 2.0
    jitter_seconds: float = 0.1

    def delay_for_attempt(self, attempt: int) -> float:
        base = min(self.max_delay_seconds, self.initial_delay_seconds * (self.backoff_multiplier ** max(0, attempt - 1)))
        return max(0.0, base + random.uniform(0, self.jitter_seconds))


@dataclass(frozen=True)
class CircuitBreakerConfig:
    enabled: bool = True
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 60.0
    half_open_max_calls: int = 1


@dataclass(frozen=True)
class EnrichmentSpec:
    name: str
    provider: str
    key_fields: Tuple[str, ...]
    output_fields: Optional[Tuple[str, ...]] = None
    merge_strategy: MergeStrategy = MergeStrategy.FILL_NULLS
    conflict_policy: ConflictPolicy = ConflictPolicy.AUDIT_ONLY
    missing_policy: MissingPolicy = MissingPolicy.MARK
    prefix: Optional[str] = None
    nest_field: Optional[str] = None
    condition: Optional[Callable[[Mapping[str, Any]], bool]] = None
    custom_merge: Optional[Callable[[Dict[str, Any], Mapping[str, Any]], Dict[str, Any]]] = None
    required: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.name:
            raise EnrichmentConfigError("EnrichmentSpec.name is required")
        if not self.provider:
            raise EnrichmentConfigError("EnrichmentSpec.provider is required")
        if not self.key_fields:
            raise EnrichmentConfigError("EnrichmentSpec.key_fields is required")
        if self.merge_strategy == MergeStrategy.PREFIX and not self.prefix:
            raise EnrichmentConfigError("prefix merge strategy requires prefix")
        if self.merge_strategy == MergeStrategy.NEST and not self.nest_field:
            raise EnrichmentConfigError("nest merge strategy requires nest_field")
        if self.merge_strategy == MergeStrategy.CUSTOM and not self.custom_merge:
            raise EnrichmentConfigError("custom merge strategy requires custom_merge")


@dataclass(frozen=True)
class EnrichmentConfig:
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS
    max_cache_items: int = DEFAULT_MAX_CACHE_ITEMS
    telemetry_enabled: bool = True
    include_rows: bool = True
    max_output_rows: int = 1_000_000
    include_audit: bool = True
    dead_letter_path: Optional[str] = None
    report_path: Optional[str] = None
    cache_snapshot_path: Optional[str] = None

    @classmethod
    def from_env(cls) -> "EnrichmentConfig":
        return cls(
            retry_policy=RetryPolicy(
                max_retries=int_env("ENRICHMENT_MAX_RETRIES", 3),
                initial_delay_seconds=float_env("ENRICHMENT_RETRY_INITIAL_DELAY", 0.25),
                max_delay_seconds=float_env("ENRICHMENT_RETRY_MAX_DELAY", 10.0),
                backoff_multiplier=float_env("ENRICHMENT_RETRY_BACKOFF", 2.0),
                jitter_seconds=float_env("ENRICHMENT_RETRY_JITTER", 0.1),
            ),
            circuit_breaker=CircuitBreakerConfig(
                enabled=bool_env("ENRICHMENT_CIRCUIT_BREAKER_ENABLED", True),
                failure_threshold=int_env("ENRICHMENT_CIRCUIT_FAILURE_THRESHOLD", 5),
                recovery_timeout_seconds=float_env("ENRICHMENT_CIRCUIT_RECOVERY_SECONDS", 60.0),
                half_open_max_calls=int_env("ENRICHMENT_CIRCUIT_HALF_OPEN_CALLS", 1),
            ),
            cache_ttl_seconds=int_env("ENRICHMENT_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS),
            max_cache_items=int_env("ENRICHMENT_MAX_CACHE_ITEMS", DEFAULT_MAX_CACHE_ITEMS),
            telemetry_enabled=bool_env("ENRICHMENT_TELEMETRY_ENABLED", True),
            include_rows=bool_env("ENRICHMENT_INCLUDE_ROWS", True),
            max_output_rows=int_env("ENRICHMENT_MAX_OUTPUT_ROWS", 1_000_000),
            include_audit=bool_env("ENRICHMENT_INCLUDE_AUDIT", True),
            dead_letter_path=os.getenv("ENRICHMENT_DEAD_LETTER_PATH"),
            report_path=os.getenv("ENRICHMENT_REPORT_PATH"),
            cache_snapshot_path=os.getenv("ENRICHMENT_CACHE_SNAPSHOT_PATH"),
        )


@dataclass(frozen=True)
class ProviderResponse:
    found: bool
    data: Dict[str, Any] = field(default_factory=dict)
    source: Optional[str] = None
    latency_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))


@dataclass(frozen=True)
class EnrichmentAuditRecord:
    id: str
    timestamp: str
    row_index: int
    spec_name: str
    provider: str
    key: str
    status: EnrichmentStatus
    fields_added: List[str] = field(default_factory=list)
    fields_updated: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    error: Optional[str] = None
    latency_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return sanitize_mapping(data)


@dataclass(frozen=True)
class EnrichmentErrorRecord:
    id: str
    timestamp: str
    row_index: int
    spec_name: str
    provider: str
    key: str
    error_type: str
    error_message: str
    row: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))


@dataclass(frozen=True)
class EnrichmentResult:
    id: str
    status: EnrichmentStatus
    started_at: str
    finished_at: str
    duration_ms: float
    input_count: int
    output_count: int
    enriched_count: int
    not_found_count: int
    skipped_count: int
    error_count: int
    cache_hit_count: int
    provider_call_count: int
    rows: List[Dict[str, Any]] = field(default_factory=list)
    audit: List[EnrichmentAuditRecord] = field(default_factory=list)
    errors: List[EnrichmentErrorRecord] = field(default_factory=list)
    provider_status: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["audit"] = [item.to_dict() for item in self.audit]
        data["errors"] = [item.to_dict() for item in self.errors]
        return sanitize_mapping(data)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True, default=safe_json_default)


class EnrichmentProvider(Protocol):
    name: str

    def lookup(self, key: Tuple[Any, ...], row: Mapping[str, Any], spec: EnrichmentSpec) -> ProviderResponse:
        ...


class EnrichmentError(Exception):
    """Base enrichment error."""


class EnrichmentConfigError(EnrichmentError):
    """Invalid enrichment configuration."""


class EnrichmentProviderError(EnrichmentError):
    """Provider lookup failed."""


class EnrichmentConflictError(EnrichmentError):
    """Enrichment conflict detected."""


class EnrichmentNotFoundError(EnrichmentError):
    """Required enrichment was not found."""


class StaticMappingProvider:
    """Provider backed by an in-memory mapping."""

    def __init__(self, name: str, data: Mapping[Any, Mapping[str, Any]]) -> None:
        self.name = name
        self.data = {normalize_provider_key(key): dict(value) for key, value in data.items()}

    def lookup(self, key: Tuple[Any, ...], row: Mapping[str, Any], spec: EnrichmentSpec) -> ProviderResponse:
        started = time.perf_counter()
        normalized = normalize_provider_key(key)
        value = self.data.get(normalized)
        return ProviderResponse(
            found=value is not None,
            data=dict(value or {}),
            source=self.name,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 3),
        )


class CallableProvider:
    """Provider backed by a user function."""

    def __init__(self, name: str, fn: Callable[[Tuple[Any, ...], Mapping[str, Any], EnrichmentSpec], Optional[Mapping[str, Any]]]) -> None:
        self.name = name
        self.fn = fn

    def lookup(self, key: Tuple[Any, ...], row: Mapping[str, Any], spec: EnrichmentSpec) -> ProviderResponse:
        started = time.perf_counter()
        data = self.fn(key, row, spec)
        return ProviderResponse(
            found=data is not None,
            data=dict(data or {}),
            source=self.name,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 3),
        )


@dataclass
class CacheEntry:
    key: str
    response: ProviderResponse
    created_at: float
    expires_at: float

    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "response": self.response.to_dict(),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CacheEntry":
        response_raw = dict(data.get("response") or {})
        return cls(
            key=str(data["key"]),
            response=ProviderResponse(
                found=bool(response_raw.get("found", False)),
                data=dict(response_raw.get("data", {})),
                source=response_raw.get("source"),
                latency_ms=float(response_raw.get("latency_ms", 0.0)),
                metadata=dict(response_raw.get("metadata", {})),
            ),
            created_at=float(data.get("created_at", time.time())),
            expires_at=float(data.get("expires_at", time.time())),
        )


class EnrichmentCache:
    def __init__(self, ttl_seconds: int, max_items: int) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._items: Dict[str, CacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[ProviderResponse]:
        with self._lock:
            entry = self._items.get(key)
            if not entry:
                return None
            if entry.expired():
                self._items.pop(key, None)
                return None
            return entry.response

    def set(self, key: str, response: ProviderResponse) -> None:
        with self._lock:
            if len(self._items) >= self.max_items:
                oldest = min(self._items.items(), key=lambda item: item[1].created_at)[0]
                self._items.pop(oldest, None)
            now = time.time()
            self._items[key] = CacheEntry(key=key, response=response, created_at=now, expires_at=now + self.ttl_seconds)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "created_at": utc_now_iso(),
                "ttl_seconds": self.ttl_seconds,
                "max_items": self.max_items,
                "items": {key: entry.to_dict() for key, entry in self._items.items() if not entry.expired()},
            }

    def restore(self, payload: Mapping[str, Any]) -> None:
        items = dict(payload.get("items") or {})
        with self._lock:
            self._items = {key: CacheEntry.from_dict(value) for key, value in items.items()}


class CircuitBreaker:
    def __init__(self, config: CircuitBreakerConfig) -> None:
        self.config = config
        self.failure_count = 0
        self.opened_at: Optional[float] = None
        self.half_open_calls = 0
        self._lock = threading.RLock()

    def allow(self) -> bool:
        if not self.config.enabled:
            return True
        with self._lock:
            if self.opened_at is None:
                return True
            if time.time() - self.opened_at >= self.config.recovery_timeout_seconds:
                return self.half_open_calls < self.config.half_open_max_calls
            return False

    def success(self) -> None:
        with self._lock:
            self.failure_count = 0
            self.opened_at = None
            self.half_open_calls = 0

    def failure(self) -> None:
        with self._lock:
            self.failure_count += 1
            if self.opened_at is not None:
                self.half_open_calls += 1
            if self.failure_count >= self.config.failure_threshold:
                self.opened_at = time.time()

    def status(self) -> ProviderStatus:
        if self.opened_at is not None:
            return ProviderStatus.OPEN_CIRCUIT
        if self.failure_count > 0:
            return ProviderStatus.DEGRADED
        return ProviderStatus.HEALTHY


class DeadLetterWriter:
    def __init__(self, path: Optional[str]) -> None:
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, error: EnrichmentErrorRecord) -> None:
        if not self.path:
            return
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(error.to_dict(), ensure_ascii=False, sort_keys=True, default=safe_json_default) + "\n")


class EnrichmentEngine:
    """Enterprise enrichment engine."""

    def __init__(self, providers: Optional[Sequence[EnrichmentProvider]] = None, config: Optional[EnrichmentConfig] = None) -> None:
        self.config = config or EnrichmentConfig.from_env()
        self.providers: Dict[str, EnrichmentProvider] = {provider.name: provider for provider in providers or []}
        self.cache = EnrichmentCache(self.config.cache_ttl_seconds, self.config.max_cache_items)
        self.dead_letter = DeadLetterWriter(self.config.dead_letter_path)
        self._breakers: Dict[str, CircuitBreaker] = defaultdict(lambda: CircuitBreaker(self.config.circuit_breaker))
        if self.config.cache_snapshot_path:
            self.restore_cache(self.config.cache_snapshot_path)

    def register_provider(self, provider: EnrichmentProvider) -> None:
        self.providers[provider.name] = provider

    def enrich(
        self,
        rows: Iterable[Any],
        *,
        specs: Sequence[EnrichmentSpec],
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> EnrichmentResult:
        for spec in specs:
            spec.validate()
            if spec.provider not in self.providers:
                raise EnrichmentConfigError(f"Provider not registered: {spec.provider}")

        started = time.perf_counter()
        started_iso = utc_now_iso()
        input_count = 0
        enriched_count = 0
        not_found_count = 0
        skipped_count = 0
        error_count = 0
        cache_hit_count = 0
        provider_call_count = 0
        output_rows: List[Dict[str, Any]] = []
        audit: List[EnrichmentAuditRecord] = []
        errors: List[EnrichmentErrorRecord] = []

        with telemetry_operation("enrichment_engine.enrich", self.config.telemetry_enabled, attributes={"specs": [s.name for s in specs]}):
            for row_index, raw in enumerate(rows):
                input_count += 1
                try:
                    row = dict(to_mapping(raw))
                    dropped = False
                    row_enriched = False
                    for spec in specs:
                        if spec.condition and not spec.condition(row):
                            skipped_count += 1
                            continue
                        key = build_key(row, spec.key_fields)
                        cache_key = build_cache_key(spec.provider, spec.name, key)
                        response = self.cache.get(cache_key)
                        cache_hit = response is not None
                        if cache_hit:
                            cache_hit_count += 1
                        else:
                            response = self._lookup_with_resilience(spec, key, row)
                            provider_call_count += 1
                            self.cache.set(cache_key, response)

                        if not response.found:
                            not_found_count += 1
                            audit_record = build_audit(row_index, spec, key, EnrichmentStatus.NOT_FOUND, response, [], [], [], None)
                            audit.append(audit_record)
                            if spec.required or spec.missing_policy == MissingPolicy.ERROR:
                                raise EnrichmentNotFoundError(f"Required enrichment not found for spec={spec.name} key={key}")
                            if spec.missing_policy == MissingPolicy.DROP:
                                dropped = True
                                break
                            if spec.missing_policy == MissingPolicy.MARK:
                                row[f"_{spec.name}_not_found"] = True
                            continue

                        before = dict(row)
                        row, fields_added, fields_updated, conflicts = merge_enrichment(row, response.data, spec)
                        if conflicts and spec.conflict_policy == ConflictPolicy.ERROR:
                            raise EnrichmentConflictError(f"Conflicts in spec={spec.name}: {conflicts}")
                        row_enriched = True
                        enriched_count += 1
                        audit.append(build_audit(row_index, spec, key, EnrichmentStatus.SUCCEEDED, response, fields_added, fields_updated, conflicts, None, before=before, after=row))
                    if dropped:
                        skipped_count += 1
                        continue
                    if self.config.include_rows and len(output_rows) < self.config.max_output_rows:
                        output_rows.append(row)
                except Exception as exc:
                    error_count += 1
                    error = build_error(row_index, specs[0].name if specs else "unknown", specs[0].provider if specs else "unknown", "", exc, raw)
                    errors.append(error)
                    self.dead_letter.write(error)

        duration_ms = (time.perf_counter() - started) * 1000.0
        status = determine_status(input_count, output_rows, error_count, not_found_count)
        result = EnrichmentResult(
            id=str(uuid.uuid4()),
            status=status,
            started_at=started_iso,
            finished_at=utc_now_iso(),
            duration_ms=round(duration_ms, 3),
            input_count=input_count,
            output_count=len(output_rows),
            enriched_count=enriched_count,
            not_found_count=not_found_count,
            skipped_count=skipped_count,
            error_count=error_count,
            cache_hit_count=cache_hit_count,
            provider_call_count=provider_call_count,
            rows=output_rows if self.config.include_rows else [],
            audit=audit if self.config.include_audit else [],
            errors=errors,
            provider_status={name: self._breakers[name].status().value for name in self.providers},
            metadata=sanitize_mapping(dict(metadata or {})),
        )
        self._save_report(result)
        telemetry_metric("enrichment.input_count", input_count, self.config.telemetry_enabled)
        telemetry_metric("enrichment.enriched_count", enriched_count, self.config.telemetry_enabled)
        telemetry_metric("enrichment.error_count", error_count, self.config.telemetry_enabled)
        telemetry_metric("enrichment.duration_ms", duration_ms, self.config.telemetry_enabled)
        return result

    def enrich_one(self, row: Any, *, specs: Sequence[EnrichmentSpec]) -> Dict[str, Any]:
        result = self.enrich([row], specs=specs)
        if result.errors:
            raise EnrichmentProviderError(result.errors[0].error_message)
        return result.rows[0] if result.rows else {}

    def snapshot_cache(self) -> Dict[str, Any]:
        return self.cache.snapshot()

    def save_cache(self, path: str | os.PathLike[str]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(self.snapshot_cache(), ensure_ascii=False, indent=2, sort_keys=True, default=safe_json_default), encoding="utf-8")
        tmp.replace(target)
        return target

    def restore_cache(self, path: str | os.PathLike[str]) -> None:
        target = Path(path)
        if not target.exists():
            return
        try:
            self.cache.restore(json.loads(target.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning("Failed to restore enrichment cache from %s: %s", target, exc)

    def _lookup_with_resilience(self, spec: EnrichmentSpec, key: Tuple[Any, ...], row: Mapping[str, Any]) -> ProviderResponse:
        provider = self.providers[spec.provider]
        breaker = self._breakers[spec.provider]
        if not breaker.allow():
            raise EnrichmentProviderError(f"Circuit breaker is open for provider={spec.provider}")
        attempts = 0
        last_exc: Optional[BaseException] = None
        while attempts <= self.config.retry_policy.max_retries:
            attempts += 1
            try:
                response = provider.lookup(key, row, spec)
                breaker.success()
                return response
            except Exception as exc:
                last_exc = exc
                breaker.failure()
                if attempts > self.config.retry_policy.max_retries:
                    break
                time.sleep(self.config.retry_policy.delay_for_attempt(attempts))
        assert last_exc is not None
        raise EnrichmentProviderError(str(last_exc)) from last_exc

    def _save_report(self, result: EnrichmentResult) -> None:
        if not self.config.report_path:
            return
        target = Path(self.config.report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(result.to_json(indent=2), encoding="utf-8")
        tmp.replace(target)


def merge_enrichment(row: Dict[str, Any], enrichment: Mapping[str, Any], spec: EnrichmentSpec) -> Tuple[Dict[str, Any], List[str], List[str], List[str]]:
    selected = dict(enrichment)
    if spec.output_fields is not None:
        selected = {field: enrichment[field] for field in spec.output_fields if field in enrichment}
    fields_added: List[str] = []
    fields_updated: List[str] = []
    conflicts: List[str] = []

    if spec.merge_strategy == MergeStrategy.CUSTOM and spec.custom_merge:
        before_keys = set(row)
        merged = spec.custom_merge(row, selected)
        after_keys = set(merged)
        return sanitize_mapping(merged), sorted(after_keys - before_keys), sorted(before_keys & after_keys), []

    if spec.merge_strategy == MergeStrategy.NEST:
        assert spec.nest_field is not None
        existing = row.get(spec.nest_field)
        if existing not in (None, {}, []):
            conflicts.append(spec.nest_field)
        row[spec.nest_field] = sanitize_mapping(selected)
        if existing is None:
            fields_added.append(spec.nest_field)
        else:
            fields_updated.append(spec.nest_field)
        return row, fields_added, fields_updated, conflicts

    for field, value in selected.items():
        target_field = f"{spec.prefix}{field}" if spec.merge_strategy == MergeStrategy.PREFIX and spec.prefix else field
        exists = target_field in row and row[target_field] is not None
        if exists and row[target_field] != value:
            conflicts.append(target_field)
        if spec.merge_strategy == MergeStrategy.KEEP_EXISTING and exists:
            continue
        if spec.merge_strategy == MergeStrategy.FILL_NULLS and exists:
            continue
        if exists and spec.conflict_policy == ConflictPolicy.KEEP_EXISTING:
            continue
        if target_field not in row:
            fields_added.append(target_field)
        else:
            fields_updated.append(target_field)
        row[target_field] = sanitize_value(value)
    return row, fields_added, fields_updated, conflicts


def build_key(row: Mapping[str, Any], fields: Sequence[str]) -> Tuple[Any, ...]:
    return tuple(get_field(row, field) for field in fields)


def build_cache_key(provider: str, spec_name: str, key: Tuple[Any, ...]) -> str:
    raw = json.dumps({"provider": provider, "spec": spec_name, "key": key}, ensure_ascii=False, sort_keys=True, default=safe_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_provider_key(key: Any) -> str:
    if isinstance(key, tuple):
        raw = json.dumps(key, ensure_ascii=False, sort_keys=True, default=safe_json_default)
    else:
        raw = json.dumps((key,), ensure_ascii=False, sort_keys=True, default=safe_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_audit(
    row_index: int,
    spec: EnrichmentSpec,
    key: Tuple[Any, ...],
    status: EnrichmentStatus,
    response: ProviderResponse,
    fields_added: Sequence[str],
    fields_updated: Sequence[str],
    conflicts: Sequence[str],
    error: Optional[str],
    *,
    before: Optional[Mapping[str, Any]] = None,
    after: Optional[Mapping[str, Any]] = None,
) -> EnrichmentAuditRecord:
    return EnrichmentAuditRecord(
        id=str(uuid.uuid4()),
        timestamp=utc_now_iso(),
        row_index=row_index,
        spec_name=spec.name,
        provider=spec.provider,
        key=hash_key_for_audit(key),
        status=status,
        fields_added=list(fields_added),
        fields_updated=list(fields_updated),
        conflicts=list(conflicts),
        error=error,
        latency_ms=response.latency_ms,
        metadata=sanitize_mapping({"source": response.source, "response_metadata": response.metadata}),
    )


def build_error(row_index: int, spec_name: str, provider: str, key: str, exc: BaseException, row: Any) -> EnrichmentErrorRecord:
    return EnrichmentErrorRecord(
        id=str(uuid.uuid4()),
        timestamp=utc_now_iso(),
        row_index=row_index,
        spec_name=spec_name,
        provider=provider,
        key=key,
        error_type=exc.__class__.__name__,
        error_message=str(exc),
        row=sanitize_mapping(dict(to_mapping(row))) if can_map(row) else {"value": sanitize_value(row)},
    )


def determine_status(input_count: int, rows: Sequence[Mapping[str, Any]], error_count: int, not_found_count: int) -> EnrichmentStatus:
    if input_count == 0:
        return EnrichmentStatus.EMPTY
    if error_count and not rows:
        return EnrichmentStatus.FAILED
    if error_count or not_found_count:
        return EnrichmentStatus.PARTIAL
    return EnrichmentStatus.SUCCEEDED


def hash_key_for_audit(key: Tuple[Any, ...]) -> str:
    raw = json.dumps(key, ensure_ascii=False, sort_keys=True, default=safe_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_field(row: Mapping[str, Any], field_path: str) -> Any:
    current: Any = row
    for part in field_path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def can_map(row: Any) -> bool:
    return isinstance(row, Mapping) or dataclasses.is_dataclass(row) or hasattr(row, "_asdict") or hasattr(row, "__dict__")


def to_mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if dataclasses.is_dataclass(row):
        return asdict(row)
    if hasattr(row, "_asdict"):
        return row._asdict()
    if hasattr(row, "__dict__"):
        return vars(row)
    raise EnrichmentConfigError(f"Unsupported row type: {type(row)!r}")


def sanitize_mapping(values: Mapping[str, Any], *, depth: int = 0) -> Dict[str, Any]:
    if depth > 6:
        return {"_truncated": "max_depth_exceeded"}
    result: Dict[str, Any] = {}
    for key, value in values.items():
        key_str = str(key)
        if SENSITIVE_KEY_PATTERN.search(key_str):
            result[key_str] = "[REDACTED]"
        elif isinstance(value, Mapping):
            result[key_str] = sanitize_mapping(value, depth=depth + 1)
        elif isinstance(value, (list, tuple, set)):
            result[key_str] = [sanitize_value(item, depth=depth + 1) for item in list(value)[:10_000]]
        else:
            result[key_str] = sanitize_value(value, depth=depth)
    return result


def sanitize_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and (value != value):
            return None
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return sanitize_mapping(asdict(value), depth=depth + 1)
    if isinstance(value, Mapping):
        return sanitize_mapping(value, depth=depth + 1)
    if isinstance(value, (list, tuple, set)):
        return [sanitize_value(item, depth=depth + 1) for item in list(value)[:10_000]]
    text = str(value)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)=([^\s&]+)", r"\1=[REDACTED]", text)
    if len(text) > MAX_TEXT_LENGTH:
        return text[: MAX_TEXT_LENGTH - 15] + "...[truncated]"
    return text


@contextlib.contextmanager
def telemetry_operation(name: str, enabled: bool, attributes: Optional[Mapping[str, Any]] = None) -> Iterator[None]:
    if not enabled:
        yield
        return
    try:
        from data.observability.telemetry import get_telemetry
        telemetry = get_telemetry()
        with telemetry.operation(name, attributes=attributes):
            yield
    except Exception:
        yield


def telemetry_metric(name: str, value: float, enabled: bool) -> None:
    if not enabled:
        return
    try:
        from data.observability.telemetry import get_telemetry
        get_telemetry().gauge(name, value)
    except Exception:
        logger.debug("Enrichment telemetry metric failed", exc_info=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return asdict(value)
    if isinstance(value, (set, tuple)):
        return list(value)
    return str(value)


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "CallableProvider",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "ConflictPolicy",
    "EnrichmentAuditRecord",
    "EnrichmentCache",
    "EnrichmentConfig",
    "EnrichmentConfigError",
    "EnrichmentConflictError",
    "EnrichmentEngine",
    "EnrichmentError",
    "EnrichmentErrorRecord",
    "EnrichmentNotFoundError",
    "EnrichmentProvider",
    "EnrichmentProviderError",
    "EnrichmentResult",
    "EnrichmentSpec",
    "EnrichmentStatus",
    "MergeStrategy",
    "MissingPolicy",
    "ProviderResponse",
    "ProviderStatus",
    "RetryPolicy",
    "StaticMappingProvider",
    "build_key",
    "merge_enrichment",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    provider = StaticMappingProvider(
        "country_ref",
        {
            "BR": {"country_name": "Brazil", "region": "LATAM"},
            "US": {"country_name": "United States", "region": "NA"},
        },
    )
    engine = EnrichmentEngine(providers=[provider], config=EnrichmentConfig(telemetry_enabled=False))
    result = engine.enrich(
        [{"id": 1, "country_code": "BR"}, {"id": 2, "country_code": "XX"}],
        specs=[
            EnrichmentSpec(
                name="country",
                provider="country_ref",
                key_fields=("country_code",),
                merge_strategy=MergeStrategy.FILL_NULLS,
                missing_policy=MissingPolicy.MARK,
            )
        ],
    )
    print(result.to_json())

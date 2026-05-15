"""
transformation_pipeline.py
==========================

Enterprise-grade transformation pipeline engine for data processing platforms.

Core capabilities
-----------------
- Declarative transformation pipeline with ordered stages.
- Sync and async transformation functions.
- Conditional execution, retries, timeout, caching and fail policies.
- Input/output contracts and validation hooks.
- Batch processing for dict/list/pandas DataFrame payloads.
- Stage-level metrics, lineage, audit events and structured errors.
- Rollback/compensation hooks for side-effect-aware transformations.
- Extensible registry of reusable transformations.
- Dependency-light design with optional pandas integration.

Typical usage
-------------
>>> pipeline = TransformationPipeline(
...     PipelineSpec(
...         name="customer_cleaning_pipeline",
...         stages=[
...             StageSpec(name="trim_strings", transformer="trim_strings"),
...             StageSpec(name="lower_email", transformer=lambda x, ctx: {**x, "email": x["email"].lower()}),
...         ],
...     )
... )
>>> result = pipeline.run({"name": " Ana ", "email": "ANA@EXAMPLE.COM"})
>>> result.output
{'name': 'Ana', 'email': 'ana@example.com'}
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import enum
import hashlib
import inspect
import json
import logging
import math
import time
import traceback
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
Payload = Any
PipelineContext = MutableMapping[str, Any]
TransformerFn = Callable[[Payload, PipelineContext], Union[Payload, Awaitable[Payload]]]
ValidatorFn = Callable[[Payload, PipelineContext], Union[None, str, Sequence[str], Awaitable[Union[None, str, Sequence[str]]]]]
ConditionFn = Callable[[Payload, PipelineContext], Union[bool, Awaitable[bool]]]
RollbackFn = Callable[[Payload, PipelineContext], Union[None, Awaitable[None]]]


class PipelineError(Exception):
    """Base exception for transformation pipeline failures."""


class StageExecutionError(PipelineError):
    """Raised when a pipeline stage fails."""

    def __init__(self, stage_name: str, message: str, *, original: Optional[BaseException] = None) -> None:
        super().__init__(f"Stage '{stage_name}' failed: {message}")
        self.stage_name = stage_name
        self.original = original


class ContractValidationError(PipelineError):
    """Raised when a pipeline contract fails."""


class PipelineMode(str, enum.Enum):
    STRICT = "strict"
    TOLERANT = "tolerant"


class StageStatus(str, enum.Enum):
    PENDING = "pending"
    SKIPPED = "skipped"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class FailurePolicy(str, enum.Enum):
    FAIL_FAST = "fail_fast"
    CONTINUE = "continue"
    SKIP_REMAINING = "skip_remaining"


class CachePolicy(str, enum.Enum):
    DISABLED = "disabled"
    READ_WRITE = "read_write"
    READ_ONLY = "read_only"
    WRITE_ONLY = "write_only"


class ErrorSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 1
    initial_delay_seconds: float = 0.25
    multiplier: float = 2.0
    max_delay_seconds: float = 10.0
    jitter_seconds: float = 0.1
    retry_exceptions: Tuple[type, ...] = (TimeoutError, ConnectionError, RuntimeError)

    def delay(self, attempt: int) -> float:
        base = self.initial_delay_seconds * (self.multiplier ** max(attempt - 1, 0))
        wait = min(base, self.max_delay_seconds)
        if self.jitter_seconds:
            wait += (hash((attempt, time.time_ns())) % 1000) / 1000 * self.jitter_seconds
        return wait

    def should_retry(self, exc: BaseException, attempt: int) -> bool:
        return attempt < self.max_attempts and isinstance(exc, self.retry_exceptions)


@dataclass(frozen=True)
class StageSpec:
    """Definition of a single transformation stage."""

    name: str
    transformer: Union[str, TransformerFn]
    description: str = ""
    enabled: bool = True
    condition: Optional[ConditionFn] = None
    input_validators: Sequence[ValidatorFn] = field(default_factory=tuple)
    output_validators: Sequence[ValidatorFn] = field(default_factory=tuple)
    rollback: Optional[RollbackFn] = None
    retry: RetryConfig = field(default_factory=RetryConfig)
    timeout_seconds: Optional[float] = None
    failure_policy: FailurePolicy = FailurePolicy.FAIL_FAST
    cache_policy: CachePolicy = CachePolicy.DISABLED
    materialize_output: bool = False
    metadata: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("StageSpec.name is required")


@dataclass(frozen=True)
class PipelineSpec:
    name: str
    stages: Sequence[StageSpec]
    version: str = "1.0.0"
    mode: PipelineMode = PipelineMode.STRICT
    global_input_validators: Sequence[ValidatorFn] = field(default_factory=tuple)
    global_output_validators: Sequence[ValidatorFn] = field(default_factory=tuple)
    enable_lineage: bool = True
    enable_audit_hash: bool = True
    metadata: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("PipelineSpec.name is required")
        if not self.stages:
            raise ValueError("PipelineSpec.stages cannot be empty")
        names = [stage.name for stage in self.stages]
        duplicates = [name for name, count in Counter(names).items() if count > 1]
        if duplicates:
            raise ValueError(f"Duplicate stage names are not allowed: {duplicates}")


@dataclass
class PipelineIssue:
    code: str
    message: str
    severity: ErrorSeverity = ErrorSeverity.ERROR
    stage: Optional[str] = None
    attempt: Optional[int] = None
    context: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "stage": self.stage,
            "attempt": self.attempt,
            "context": dict(self.context),
        }


@dataclass
class StageExecutionRecord:
    name: str
    status: StageStatus = StageStatus.PENDING
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    attempts: int = 0
    input_hash: Optional[str] = None
    output_hash: Optional[str] = None
    cache_hit: bool = False
    row_count_before: Optional[int] = None
    row_count_after: Optional[int] = None
    issues: List[PipelineIssue] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)

    @property
    def duration_ms(self) -> Optional[float]:
        if self.started_at is None or self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at) * 1000, 3)

    def start(self, payload: Payload) -> None:
        self.status = StageStatus.RUNNING
        self.started_at = time.time()
        self.row_count_before = estimate_row_count(payload)
        self.input_hash = stable_hash(payload)

    def finish(self, payload: Payload, status: StageStatus) -> None:
        self.status = status
        self.finished_at = time.time()
        self.row_count_after = estimate_row_count(payload)
        self.output_hash = stable_hash(payload)

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "status": self.status.value,
            "duration_ms": self.duration_ms,
            "attempts": self.attempts,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "cache_hit": self.cache_hit,
            "row_count_before": self.row_count_before,
            "row_count_after": self.row_count_after,
            "issues": [issue.to_dict() for issue in self.issues],
            "metadata": dict(self.metadata),
        }


@dataclass
class PipelineResult:
    output: Payload
    success: bool
    run_id: str
    pipeline_name: str
    pipeline_version: str
    issues: List[PipelineIssue] = field(default_factory=list)
    stages: List[StageExecutionRecord] = field(default_factory=list)
    context: JsonDict = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def duration_ms(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at) * 1000, 3)

    def to_dict(self) -> JsonDict:
        return {
            "success": self.success,
            "run_id": self.run_id,
            "pipeline_name": self.pipeline_name,
            "pipeline_version": self.pipeline_version,
            "duration_ms": self.duration_ms,
            "issues": [issue.to_dict() for issue in self.issues],
            "stages": [stage.to_dict() for stage in self.stages],
            "context": safe_json(self.context),
        }


class TransformationRegistry:
    """Registry of reusable named transformations."""

    def __init__(self) -> None:
        self._items: Dict[str, TransformerFn] = {}
        self.register_defaults()

    def register(self, name: str, fn: TransformerFn, *, replace: bool = False) -> None:
        key = normalize_name(name)
        if key in self._items and not replace:
            raise ValueError(f"Transformer already registered: {name}")
        self._items[key] = fn

    def get(self, name: str) -> TransformerFn:
        key = normalize_name(name)
        if key not in self._items:
            raise KeyError(f"Transformer not found: {name}")
        return self._items[key]

    def names(self) -> List[str]:
        return sorted(self._items.keys())

    def register_defaults(self) -> None:
        self._items.update(
            {
                "identity": lambda payload, ctx: payload,
                "trim_strings": trim_strings,
                "lowercase_keys": lowercase_keys,
                "drop_null_records": drop_null_records,
                "deduplicate_records": deduplicate_records,
                "flatten_dict": flatten_dict_transform,
                "normalize_column_names": normalize_column_names,
                "drop_empty_columns": drop_empty_columns,
            }
        )


class LRUCache:
    """Small in-memory LRU cache for deterministic transformation stages."""

    def __init__(self, max_items: int = 512) -> None:
        self.max_items = max_items
        self._data: OrderedDict[str, Payload] = OrderedDict()

    def get(self, key: str) -> Optional[Payload]:
        if key not in self._data:
            return None
        value = self._data.pop(key)
        self._data[key] = value
        return copy.deepcopy(value)

    def set(self, key: str, value: Payload) -> None:
        if key in self._data:
            self._data.pop(key)
        self._data[key] = copy.deepcopy(value)
        while len(self._data) > self.max_items:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()


class AuditSink:
    """Structured audit sink. Replace with OpenTelemetry/SIEM/warehouse sink if needed."""

    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger

    async def emit(self, event_type: str, payload: JsonDict) -> None:
        self.log.info("transformation_pipeline_audit", extra={"event_type": event_type, "payload": payload})


class TransformationPipeline:
    """Enterprise transformation pipeline executor."""

    def __init__(
        self,
        spec: PipelineSpec,
        *,
        registry: Optional[TransformationRegistry] = None,
        cache: Optional[LRUCache] = None,
        audit_sink: Optional[AuditSink] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.spec = spec
        self.registry = registry or TransformationRegistry()
        self.cache = cache or LRUCache()
        self.audit = audit_sink or AuditSink()
        self.log = log or logger

    def run(self, payload: Payload, *, context: Optional[JsonDict] = None) -> PipelineResult:
        """Synchronous wrapper around run_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run_async(payload, context=context))
        raise RuntimeError("run() cannot be called from an active event loop. Use await run_async(...).") from None

    async def run_async(self, payload: Payload, *, context: Optional[JsonDict] = None) -> PipelineResult:
        run_id = str(uuid.uuid4())
        ctx: PipelineContext = {
            "run_id": run_id,
            "pipeline_name": self.spec.name,
            "pipeline_version": self.spec.version,
            "metadata": dict(self.spec.metadata),
        }
        if context:
            ctx.update(context)

        result = PipelineResult(
            output=payload,
            success=True,
            run_id=run_id,
            pipeline_name=self.spec.name,
            pipeline_version=self.spec.version,
            context=dict(ctx),
        )
        await self.audit.emit("pipeline_started", {"run_id": run_id, "name": self.spec.name, "version": self.spec.version})

        try:
            global_issues = await self._validate_many(self.spec.global_input_validators, payload, ctx, stage=None)
            result.issues.extend(global_issues)
            if self._has_errors(global_issues):
                raise ContractValidationError("Global input contract failed")

            current = payload
            executed_stages: List[Tuple[StageSpec, Payload]] = []

            for stage in self.spec.stages:
                record = StageExecutionRecord(name=stage.name, metadata=dict(stage.metadata))
                result.stages.append(record)

                if not stage.enabled:
                    record.finish(current, StageStatus.SKIPPED)
                    continue

                should_run = await self._should_run(stage, current, ctx)
                if not should_run:
                    record.finish(current, StageStatus.SKIPPED)
                    continue

                try:
                    current = await self._execute_stage(stage, current, ctx, record)
                    executed_stages.append((stage, current))
                except Exception as exc:
                    issue = self._issue_from_exception(stage.name, exc, record.attempts)
                    record.issues.append(issue)
                    result.issues.append(issue)
                    record.finish(current, StageStatus.FAILED)
                    result.success = False

                    await self.audit.emit("stage_failed", {"run_id": run_id, "stage": stage.name, "error": issue.to_dict()})

                    if stage.rollback:
                        await self._rollback_stage(stage, current, ctx, record)

                    if stage.failure_policy == FailurePolicy.CONTINUE or self.spec.mode == PipelineMode.TOLERANT:
                        continue
                    if stage.failure_policy == FailurePolicy.SKIP_REMAINING:
                        break
                    raise

            output_issues = await self._validate_many(self.spec.global_output_validators, current, ctx, stage=None)
            result.issues.extend(output_issues)
            if self._has_errors(output_issues):
                result.success = False
                if self.spec.mode == PipelineMode.STRICT:
                    raise ContractValidationError("Global output contract failed")

            if self.spec.enable_audit_hash:
                ctx["audit_hash"] = stable_hash({"input": payload, "output": current, "run_id": run_id})

            result.output = current
            result.context = safe_json(dict(ctx))
        except Exception as exc:
            result.success = False
            if not any(issue.message == str(exc) for issue in result.issues):
                result.issues.append(
                    PipelineIssue(
                        code="PIPELINE_FAILED",
                        message=str(exc),
                        severity=ErrorSeverity.ERROR,
                        context={"traceback": traceback.format_exc(limit=20)},
                    )
                )
            if self.spec.mode == PipelineMode.STRICT:
                self.log.debug("Pipeline failed in strict mode", exc_info=True)
        finally:
            result.finished_at = time.time()
            await self.audit.emit("pipeline_finished", result.to_dict())

        return result

    async def _execute_stage(
        self,
        stage: StageSpec,
        payload: Payload,
        ctx: PipelineContext,
        record: StageExecutionRecord,
    ) -> Payload:
        record.start(payload)

        input_issues = await self._validate_many(stage.input_validators, payload, ctx, stage=stage.name)
        record.issues.extend(input_issues)
        if self._has_errors(input_issues):
            raise ContractValidationError(f"Input contract failed for stage {stage.name}")

        cache_key = self._cache_key(stage, payload, ctx)
        if stage.cache_policy in {CachePolicy.READ_WRITE, CachePolicy.READ_ONLY}:
            cached = self.cache.get(cache_key)
            if cached is not None:
                record.cache_hit = True
                record.finish(cached, StageStatus.SUCCEEDED)
                await self.audit.emit("stage_cache_hit", {"run_id": ctx["run_id"], "stage": stage.name})
                return cached

        transformer = self._resolve_transformer(stage.transformer)
        output: Payload = payload
        attempt = 1
        while True:
            record.attempts = attempt
            try:
                if stage.timeout_seconds:
                    output = await asyncio.wait_for(maybe_await(transformer(payload, ctx)), timeout=stage.timeout_seconds)
                else:
                    output = await maybe_await(transformer(payload, ctx))
                break
            except Exception as exc:
                if stage.retry.should_retry(exc, attempt):
                    await self.audit.emit(
                        "stage_retry",
                        {"run_id": ctx["run_id"], "stage": stage.name, "attempt": attempt, "error": str(exc)},
                    )
                    await asyncio.sleep(stage.retry.delay(attempt))
                    attempt += 1
                    continue
                raise StageExecutionError(stage.name, str(exc), original=exc) from exc

        output_issues = await self._validate_many(stage.output_validators, output, ctx, stage=stage.name)
        record.issues.extend(output_issues)
        if self._has_errors(output_issues):
            raise ContractValidationError(f"Output contract failed for stage {stage.name}")

        if stage.cache_policy in {CachePolicy.READ_WRITE, CachePolicy.WRITE_ONLY}:
            self.cache.set(cache_key, output)

        if stage.materialize_output:
            ctx.setdefault("materialized_outputs", {})[stage.name] = copy.deepcopy(output)

        if self.spec.enable_lineage:
            ctx.setdefault("lineage", []).append(
                {
                    "stage": stage.name,
                    "input_hash": record.input_hash,
                    "output_hash": stable_hash(output),
                    "rows_before": estimate_row_count(payload),
                    "rows_after": estimate_row_count(output),
                }
            )

        record.finish(output, StageStatus.SUCCEEDED)
        await self.audit.emit("stage_succeeded", {"run_id": ctx["run_id"], "stage": stage.name, "record": record.to_dict()})
        return output

    async def _rollback_stage(self, stage: StageSpec, payload: Payload, ctx: PipelineContext, record: StageExecutionRecord) -> None:
        if not stage.rollback:
            return
        try:
            await maybe_await(stage.rollback(payload, ctx))
            record.status = StageStatus.ROLLED_BACK
            await self.audit.emit("stage_rolled_back", {"run_id": ctx["run_id"], "stage": stage.name})
        except Exception as exc:
            record.issues.append(
                PipelineIssue(
                    code="ROLLBACK_FAILED",
                    message=str(exc),
                    severity=ErrorSeverity.CRITICAL,
                    stage=stage.name,
                    context={"traceback": traceback.format_exc(limit=20)},
                )
            )

    async def _should_run(self, stage: StageSpec, payload: Payload, ctx: PipelineContext) -> bool:
        if stage.condition is None:
            return True
        return bool(await maybe_await(stage.condition(payload, ctx)))

    def _resolve_transformer(self, transformer: Union[str, TransformerFn]) -> TransformerFn:
        if isinstance(transformer, str):
            return self.registry.get(transformer)
        return transformer

    def _cache_key(self, stage: StageSpec, payload: Payload, ctx: PipelineContext) -> str:
        return stable_hash(
            {
                "pipeline": self.spec.name,
                "version": self.spec.version,
                "stage": stage.name,
                "payload": payload,
                "stage_metadata": stage.metadata,
            }
        )

    async def _validate_many(
        self,
        validators: Sequence[ValidatorFn],
        payload: Payload,
        ctx: PipelineContext,
        stage: Optional[str],
    ) -> List[PipelineIssue]:
        issues: List[PipelineIssue] = []
        for validator in validators:
            try:
                validation_result = await maybe_await(validator(payload, ctx))
                messages: List[str] = []
                if isinstance(validation_result, str):
                    messages = [validation_result]
                elif validation_result:
                    messages = [str(item) for item in validation_result]
                for message in messages:
                    issues.append(PipelineIssue(code="VALIDATION_FAILED", message=message, stage=stage))
            except Exception as exc:
                issues.append(
                    PipelineIssue(
                        code="VALIDATOR_EXCEPTION",
                        message=str(exc),
                        stage=stage,
                        context={"traceback": traceback.format_exc(limit=20)},
                    )
                )
        return issues

    @staticmethod
    def _has_errors(issues: Sequence[PipelineIssue]) -> bool:
        return any(issue.severity in {ErrorSeverity.ERROR, ErrorSeverity.CRITICAL} for issue in issues)

    @staticmethod
    def _issue_from_exception(stage_name: str, exc: BaseException, attempt: int) -> PipelineIssue:
        return PipelineIssue(
            code=type(exc).__name__.upper(),
            message=str(exc),
            severity=ErrorSeverity.ERROR,
            stage=stage_name,
            attempt=attempt,
            context={"traceback": traceback.format_exc(limit=20)},
        )

    def describe(self) -> JsonDict:
        return {
            "name": self.spec.name,
            "version": self.spec.version,
            "mode": self.spec.mode.value,
            "stages": [
                {
                    "name": stage.name,
                    "description": stage.description,
                    "enabled": stage.enabled,
                    "transformer": stage.transformer if isinstance(stage.transformer, str) else getattr(stage.transformer, "__name__", "callable"),
                    "failure_policy": stage.failure_policy.value,
                    "cache_policy": stage.cache_policy.value,
                    "timeout_seconds": stage.timeout_seconds,
                    "retry_max_attempts": stage.retry.max_attempts,
                    "metadata": dict(stage.metadata),
                }
                for stage in self.spec.stages
            ],
            "metadata": dict(self.spec.metadata),
        }


# -----------------------------------------------------------------------------
# Batch execution helpers
# -----------------------------------------------------------------------------


@dataclass
class BatchPipelineResult:
    outputs: List[Payload]
    results: List[PipelineResult]
    success_count: int
    failure_count: int
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def duration_ms(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at) * 1000, 3)

    def to_dict(self) -> JsonDict:
        return {
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "duration_ms": self.duration_ms,
            "results": [result.to_dict() for result in self.results],
        }


async def run_pipeline_batch_async(
    pipeline: TransformationPipeline,
    payloads: Iterable[Payload],
    *,
    concurrency: int = 10,
    context_factory: Optional[Callable[[Payload, int], JsonDict]] = None,
) -> BatchPipelineResult:
    semaphore = asyncio.Semaphore(concurrency)
    started = time.time()

    async def _run_one(index: int, payload: Payload) -> PipelineResult:
        async with semaphore:
            context = context_factory(payload, index) if context_factory else {"batch_index": index}
            return await pipeline.run_async(payload, context=context)

    tasks = [_run_one(index, payload) for index, payload in enumerate(payloads)]
    results = await asyncio.gather(*tasks)
    outputs = [result.output for result in results]
    batch = BatchPipelineResult(
        outputs=outputs,
        results=list(results),
        success_count=sum(1 for result in results if result.success),
        failure_count=sum(1 for result in results if not result.success),
        started_at=started,
        finished_at=time.time(),
    )
    return batch


def run_pipeline_batch(
    pipeline: TransformationPipeline,
    payloads: Iterable[Payload],
    *,
    concurrency: int = 10,
    context_factory: Optional[Callable[[Payload, int], JsonDict]] = None,
) -> BatchPipelineResult:
    return asyncio.run(run_pipeline_batch_async(pipeline, payloads, concurrency=concurrency, context_factory=context_factory))


# -----------------------------------------------------------------------------
# Validators
# -----------------------------------------------------------------------------


def require_type(expected_type: type) -> ValidatorFn:
    def _validator(payload: Payload, ctx: PipelineContext) -> Optional[str]:
        if not isinstance(payload, expected_type):
            return f"Expected payload type {expected_type.__name__}, got {type(payload).__name__}"
        return None

    return _validator


def require_keys(keys: Sequence[str]) -> ValidatorFn:
    required = set(keys)

    def _validator(payload: Payload, ctx: PipelineContext) -> Optional[str]:
        if not isinstance(payload, Mapping):
            return "Payload must be a mapping/dict"
        missing = sorted(required - set(payload.keys()))
        if missing:
            return f"Missing required keys: {missing}"
        return None

    return _validator


def require_dataframe_columns(columns: Sequence[str]) -> ValidatorFn:
    required = set(columns)

    def _validator(payload: Payload, ctx: PipelineContext) -> Optional[str]:
        if pd is None or not isinstance(payload, pd.DataFrame):
            return "Payload must be a pandas DataFrame"
        missing = sorted(required - set(payload.columns))
        if missing:
            return f"Missing DataFrame columns: {missing}"
        return None

    return _validator


def max_rows(limit: int) -> ValidatorFn:
    def _validator(payload: Payload, ctx: PipelineContext) -> Optional[str]:
        rows = estimate_row_count(payload)
        if rows is not None and rows > limit:
            return f"Row count {rows} exceeds limit {limit}"
        return None

    return _validator


# -----------------------------------------------------------------------------
# Built-in transformations
# -----------------------------------------------------------------------------


async def maybe_await(value: Union[Any, Awaitable[Any]]) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def trim_strings(payload: Payload, ctx: PipelineContext) -> Payload:
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, Mapping):
        return {key: trim_strings(value, ctx) for key, value in payload.items()}
    if isinstance(payload, list):
        return [trim_strings(item, ctx) for item in payload]
    if pd is not None and isinstance(payload, pd.DataFrame):
        df = payload.copy()
        object_cols = df.select_dtypes(include=["object", "string"]).columns
        for col in object_cols:
            df[col] = df[col].map(lambda value: value.strip() if isinstance(value, str) else value)
        return df
    return payload


def lowercase_keys(payload: Payload, ctx: PipelineContext) -> Payload:
    if isinstance(payload, Mapping):
        return {str(key).lower(): lowercase_keys(value, ctx) for key, value in payload.items()}
    if isinstance(payload, list):
        return [lowercase_keys(item, ctx) for item in payload]
    return payload


def drop_null_records(payload: Payload, ctx: PipelineContext) -> Payload:
    if isinstance(payload, list):
        return [item for item in payload if item is not None]
    if pd is not None and isinstance(payload, pd.DataFrame):
        return payload.dropna(how="all").copy()
    return payload


def deduplicate_records(payload: Payload, ctx: PipelineContext) -> Payload:
    if isinstance(payload, list):
        seen = set()
        output = []
        for item in payload:
            key = stable_hash(item)
            if key not in seen:
                seen.add(key)
                output.append(item)
        return output
    if pd is not None and isinstance(payload, pd.DataFrame):
        return payload.drop_duplicates().copy()
    return payload


def flatten_dict_transform(payload: Payload, ctx: PipelineContext) -> Payload:
    if isinstance(payload, Mapping):
        return flatten_dict(payload)
    if isinstance(payload, list):
        return [flatten_dict(item) if isinstance(item, Mapping) else item for item in payload]
    if pd is not None and isinstance(payload, pd.DataFrame):
        return payload.copy()
    return payload


def normalize_column_names(payload: Payload, ctx: PipelineContext) -> Payload:
    if pd is not None and isinstance(payload, pd.DataFrame):
        df = payload.copy()
        df.columns = [normalize_name(str(col)) for col in df.columns]
        return df
    return payload


def drop_empty_columns(payload: Payload, ctx: PipelineContext) -> Payload:
    if pd is not None and isinstance(payload, pd.DataFrame):
        return payload.dropna(axis=1, how="all").copy()
    return payload


def select_keys(keys: Sequence[str]) -> TransformerFn:
    selected = list(keys)

    def _transform(payload: Payload, ctx: PipelineContext) -> Payload:
        if isinstance(payload, Mapping):
            return {key: payload.get(key) for key in selected if key in payload}
        if isinstance(payload, list):
            return [{key: item.get(key) for key in selected if isinstance(item, Mapping) and key in item} for item in payload]
        if pd is not None and isinstance(payload, pd.DataFrame):
            return payload[[key for key in selected if key in payload.columns]].copy()
        return payload

    return _transform


def rename_keys(mapping: Mapping[str, str]) -> TransformerFn:
    rename_map = dict(mapping)

    def _transform(payload: Payload, ctx: PipelineContext) -> Payload:
        if isinstance(payload, Mapping):
            return {rename_map.get(key, key): value for key, value in payload.items()}
        if isinstance(payload, list):
            return [_transform(item, ctx) if isinstance(item, Mapping) else item for item in payload]
        if pd is not None and isinstance(payload, pd.DataFrame):
            return payload.rename(columns=rename_map).copy()
        return payload

    return _transform


def add_constant_fields(fields: Mapping[str, Any]) -> TransformerFn:
    constants = dict(fields)

    def _transform(payload: Payload, ctx: PipelineContext) -> Payload:
        if isinstance(payload, Mapping):
            return {**payload, **constants}
        if isinstance(payload, list):
            return [{**item, **constants} if isinstance(item, Mapping) else item for item in payload]
        if pd is not None and isinstance(payload, pd.DataFrame):
            df = payload.copy()
            for key, value in constants.items():
                df[key] = value
            return df
        return payload

    return _transform


# -----------------------------------------------------------------------------
# Utility functions
# -----------------------------------------------------------------------------


def normalize_name(value: str) -> str:
    text = value.strip().lower()
    output = []
    previous_underscore = False
    for char in text:
        if char.isalnum():
            output.append(char)
            previous_underscore = False
        else:
            if not previous_underscore:
                output.append("_")
                previous_underscore = True
    return "".join(output).strip("_")


def flatten_dict(data: Mapping[str, Any], prefix: str = "", separator: str = ".") -> JsonDict:
    output: JsonDict = {}
    for key, value in data.items():
        full_key = f"{prefix}{separator}{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            output.update(flatten_dict(value, full_key, separator))
        else:
            output[full_key] = value
    return output


def estimate_row_count(payload: Payload) -> Optional[int]:
    if pd is not None and isinstance(payload, pd.DataFrame):
        return int(len(payload))
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, Mapping):
        return 1
    return None


def safe_json(value: Any) -> Any:
    if pd is not None and isinstance(value, pd.DataFrame):
        return {"type": "DataFrame", "rows": int(len(value)), "columns": list(value.columns)}
    if isinstance(value, Mapping):
        return {str(k): safe_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [safe_json(item) for item in value]
    if isinstance(value, tuple):
        return [safe_json(item) for item in value]
    try:
        json.dumps(value, default=str)
        return value
    except Exception:
        return str(value)


def stable_hash(value: Any) -> str:
    if pd is not None and isinstance(value, pd.DataFrame):
        payload = {
            "type": "DataFrame",
            "columns": list(value.columns),
            "rows": len(value),
            "hash": str(pd.util.hash_pandas_object(value, index=True).sum()) if len(value) else "0",
        }
    else:
        payload = safe_json(value)
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# -----------------------------------------------------------------------------
# Example factory
# -----------------------------------------------------------------------------


def build_customer_transformation_pipeline() -> TransformationPipeline:
    spec = PipelineSpec(
        name="customer_transformation_pipeline",
        version="2.0.0",
        mode=PipelineMode.STRICT,
        global_input_validators=[require_type(dict), require_keys(["name", "email"])],
        stages=[
            StageSpec(
                name="trim_all_strings",
                transformer="trim_strings",
                description="Remove leading/trailing whitespace from all string fields.",
                cache_policy=CachePolicy.READ_WRITE,
            ),
            StageSpec(
                name="normalize_email",
                transformer=lambda payload, ctx: {**payload, "email": str(payload["email"]).lower()},
                description="Normalize customer email to lowercase.",
                output_validators=[require_keys(["email"])],
            ),
            StageSpec(
                name="add_processing_metadata",
                transformer=add_constant_fields({"processed_by": "transformation_pipeline"}),
                description="Add lineage metadata field.",
            ),
        ],
        enable_lineage=True,
        enable_audit_hash=True,
        metadata={"domain": "customer", "owner": "data-platform"},
    )
    return TransformationPipeline(spec)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    pipeline = build_customer_transformation_pipeline()
    sample = {"name": " Ana Silva ", "email": "ANA@EXAMPLE.COM"}
    pipeline_result = pipeline.run(sample)
    print(json.dumps(pipeline_result.to_dict(), indent=2, ensure_ascii=False, default=str))
    print(json.dumps(pipeline_result.output, indent=2, ensure_ascii=False, default=str))

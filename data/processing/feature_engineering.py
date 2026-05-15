"""
data/processing/feature_engineering.py

Enterprise-grade feature engineering engine for data platforms and ML pipelines.

Purpose
-------
Provides a dependency-light feature engineering layer for batch, micro-batch,
streaming and offline ML feature pipelines. It supports configurable feature
specifications, reusable transformers, feature lineage, validation, safe
serialization and optional telemetry integration.

Core capabilities
-----------------
- Row-wise feature generation for dictionaries, dataclasses and objects.
- Numeric transforms: scale, normalize, log, sqrt, clipping, binning, ratios.
- Categorical transforms: one-hot, label mapping, hashing, frequency encoding.
- Text transforms: length, token count, contains, regex extraction, hashing.
- Datetime transforms: date parts, age, recency, cyclical encoding.
- Cross/features: concatenation, interaction, arithmetic expressions.
- Aggregated/windowed features from supplied reference rows.
- Custom feature functions.
- Feature registry and lineage metadata.
- Validation and missing-value handling.
- JSON reports and optional feature manifest export.
- Optional telemetry integration.
- Standard library only.

Example
-------
engine = FeatureEngineeringEngine()
result = engine.transform(
    rows,
    specs=[
        FeatureSpec(name="amount_log", transform=FeatureTransform.LOG, input_fields=("amount",)),
        FeatureSpec(name="weekday", transform=FeatureTransform.DATETIME_PART, input_fields=("created_at",), params={"part": "weekday"}),
    ],
)
print(result.to_json())
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import logging
import math
import os
import re
import statistics
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|session|jwt|bearer)",
    re.IGNORECASE,
)
WORD_PATTERN = re.compile(r"\w+", re.UNICODE)
MAX_TEXT_LENGTH = 50_000
MAX_OUTPUT_ROWS = 1_000_000


class FeatureTransform(str, Enum):
    IDENTITY = "identity"
    CONSTANT = "constant"
    COALESCE = "coalesce"
    CAST_STRING = "cast_string"
    CAST_INT = "cast_int"
    CAST_FLOAT = "cast_float"
    CAST_BOOL = "cast_bool"
    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"
    RATIO = "ratio"
    ABS = "abs"
    LOG = "log"
    SQRT = "sqrt"
    POWER = "power"
    CLIP = "clip"
    MIN_MAX_SCALE = "min_max_scale"
    ZSCORE = "zscore"
    BIN = "bin"
    BOOLEAN_FLAG = "boolean_flag"
    ONE_HOT = "one_hot"
    LABEL_MAP = "label_map"
    HASH_BUCKET = "hash_bucket"
    FREQUENCY_ENCODE = "frequency_encode"
    CONCAT = "concat"
    TEXT_LENGTH = "text_length"
    TOKEN_COUNT = "token_count"
    CONTAINS = "contains"
    REGEX_EXTRACT = "regex_extract"
    DATETIME_PART = "datetime_part"
    AGE_SECONDS = "age_seconds"
    RECENCY_SECONDS = "recency_seconds"
    CYCLICAL_DATETIME = "cyclical_datetime"
    GROUP_AGGREGATE = "group_aggregate"
    WINDOW_AGGREGATE = "window_aggregate"
    CUSTOM = "custom"


class FeatureStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    EMPTY = "empty"


class MissingStrategy(str, Enum):
    NULL = "null"
    DEFAULT = "default"
    ZERO = "zero"
    DROP_FEATURE = "drop_feature"
    ERROR = "error"


class AggregationOperation(str, Enum):
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    STDDEV = "stddev"
    DISTINCT_COUNT = "distinct_count"


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    transform: FeatureTransform
    input_fields: Tuple[str, ...] = field(default_factory=tuple)
    output_type: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    missing_strategy: MissingStrategy = MissingStrategy.NULL
    default: Any = None
    enabled: bool = True
    overwrite: bool = True
    custom_function: Optional[Callable[[Mapping[str, Any], "FeatureContext"], Any]] = None
    description: str = ""
    tags: Tuple[str, ...] = field(default_factory=tuple)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.name:
            raise FeatureConfigError("FeatureSpec.name is required")
        if self.transform != FeatureTransform.CONSTANT and self.transform != FeatureTransform.CUSTOM and not self.input_fields:
            raise FeatureConfigError(f"Feature {self.name} requires input_fields")
        if self.transform == FeatureTransform.CUSTOM and not self.custom_function:
            raise FeatureConfigError(f"Feature {self.name} custom transform requires custom_function")


@dataclass(frozen=True)
class FeatureContext:
    now: datetime
    reference_rows: Tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    registry: Dict[str, FeatureSpec] = field(default_factory=dict)
    global_stats: Dict[str, Dict[str, float]] = field(default_factory=dict)
    category_frequencies: Dict[str, Counter[Any]] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeatureLineage:
    feature_name: str
    transform: FeatureTransform
    input_fields: Tuple[str, ...]
    params: Dict[str, Any]
    tags: Tuple[str, ...]
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["transform"] = self.transform.value
        return sanitize_mapping(data)


@dataclass(frozen=True)
class FeatureErrorRecord:
    id: str
    timestamp: str
    row_index: int
    feature_name: str
    error_type: str
    error_message: str
    row: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))


@dataclass(frozen=True)
class FeatureEngineeringConfig:
    telemetry_enabled: bool = True
    include_rows: bool = True
    max_output_rows: int = MAX_OUTPUT_ROWS
    fail_fast: bool = False
    include_lineage: bool = True
    include_errors: bool = True
    report_path: Optional[str] = None
    manifest_path: Optional[str] = None

    @classmethod
    def from_env(cls) -> "FeatureEngineeringConfig":
        return cls(
            telemetry_enabled=bool_env("FEATURE_TELEMETRY_ENABLED", True),
            include_rows=bool_env("FEATURE_INCLUDE_ROWS", True),
            max_output_rows=int_env("FEATURE_MAX_OUTPUT_ROWS", MAX_OUTPUT_ROWS),
            fail_fast=bool_env("FEATURE_FAIL_FAST", False),
            include_lineage=bool_env("FEATURE_INCLUDE_LINEAGE", True),
            include_errors=bool_env("FEATURE_INCLUDE_ERRORS", True),
            report_path=os.getenv("FEATURE_REPORT_PATH"),
            manifest_path=os.getenv("FEATURE_MANIFEST_PATH"),
        )


@dataclass(frozen=True)
class FeatureEngineeringResult:
    id: str
    status: FeatureStatus
    started_at: str
    finished_at: str
    duration_ms: float
    input_count: int
    output_count: int
    feature_count: int
    generated_value_count: int
    error_count: int
    rows: List[Dict[str, Any]] = field(default_factory=list)
    lineage: List[FeatureLineage] = field(default_factory=list)
    errors: List[FeatureErrorRecord] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["lineage"] = [item.to_dict() for item in self.lineage]
        data["errors"] = [item.to_dict() for item in self.errors]
        return sanitize_mapping(data)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True, default=safe_json_default)


class FeatureEngineeringError(Exception):
    """Base feature engineering error."""


class FeatureConfigError(FeatureEngineeringError):
    """Invalid feature configuration."""


class FeatureTransformError(FeatureEngineeringError):
    """Feature transform failed."""


class FeatureTransformer(Protocol):
    def transform(self, row: Mapping[str, Any], spec: FeatureSpec, context: FeatureContext) -> Any:
        ...


class FeatureRegistry:
    def __init__(self) -> None:
        self._specs: Dict[str, FeatureSpec] = {}

    def register(self, spec: FeatureSpec) -> None:
        spec.validate()
        self._specs[spec.name] = spec

    def get(self, name: str) -> Optional[FeatureSpec]:
        return self._specs.get(name)

    def list(self) -> List[FeatureSpec]:
        return list(self._specs.values())

    def to_manifest(self) -> Dict[str, Any]:
        return {
            "created_at": utc_now_iso(),
            "features": [spec_to_dict(spec) for spec in self.list()],
        }


class FeatureEngineeringEngine:
    """Enterprise feature engineering engine."""

    def __init__(self, config: Optional[FeatureEngineeringConfig] = None, registry: Optional[FeatureRegistry] = None) -> None:
        self.config = config or FeatureEngineeringConfig.from_env()
        self.registry = registry or FeatureRegistry()

    def register(self, spec: FeatureSpec) -> None:
        self.registry.register(spec)

    def transform(
        self,
        rows: Iterable[Any],
        *,
        specs: Optional[Sequence[FeatureSpec]] = None,
        reference_rows: Optional[Sequence[Any]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> FeatureEngineeringResult:
        feature_specs = list(specs or self.registry.list())
        for spec in feature_specs:
            spec.validate()

        started = monotonic_ms()
        started_iso = utc_now_iso()
        materialized_rows = [dict(to_mapping(row)) for row in rows]
        ref_rows = tuple(dict(to_mapping(row)) for row in (reference_rows or materialized_rows))
        context = build_context(feature_specs, ref_rows, metadata or {})

        output_rows: List[Dict[str, Any]] = []
        errors: List[FeatureErrorRecord] = []
        generated_count = 0

        with telemetry_operation("feature_engineering.transform", self.config.telemetry_enabled, attributes={"feature_count": len(feature_specs)}):
            for index, row in enumerate(materialized_rows):
                enriched = dict(row)
                for spec in feature_specs:
                    if not spec.enabled:
                        continue
                    if not spec.overwrite and spec.name in enriched:
                        continue
                    try:
                        value = compute_feature(enriched, spec, context)
                        if value is _DROP_FEATURE:
                            continue
                        enriched[spec.name] = sanitize_value(value)
                        generated_count += 1
                    except Exception as exc:
                        error = FeatureErrorRecord(
                            id=str(uuid.uuid4()),
                            timestamp=utc_now_iso(),
                            row_index=index,
                            feature_name=spec.name,
                            error_type=exc.__class__.__name__,
                            error_message=str(exc),
                            row=sanitize_mapping(row),
                        )
                        errors.append(error)
                        if self.config.fail_fast:
                            raise
                if self.config.include_rows and len(output_rows) < self.config.max_output_rows:
                    output_rows.append(enriched)

        duration_ms = monotonic_ms() - started
        status = determine_status(len(materialized_rows), output_rows, errors)
        lineage = [build_lineage(spec) for spec in feature_specs] if self.config.include_lineage else []
        result = FeatureEngineeringResult(
            id=str(uuid.uuid4()),
            status=status,
            started_at=started_iso,
            finished_at=utc_now_iso(),
            duration_ms=round(duration_ms, 3),
            input_count=len(materialized_rows),
            output_count=len(output_rows),
            feature_count=len(feature_specs),
            generated_value_count=generated_count,
            error_count=len(errors),
            rows=output_rows if self.config.include_rows else [],
            lineage=lineage,
            errors=errors if self.config.include_errors else [],
            metadata=sanitize_mapping(dict(metadata or {})),
        )
        self._save_report(result)
        self._save_manifest(lineage)
        telemetry_metric("feature_engineering.input_count", len(materialized_rows), self.config.telemetry_enabled)
        telemetry_metric("feature_engineering.generated_count", generated_count, self.config.telemetry_enabled)
        telemetry_metric("feature_engineering.error_count", len(errors), self.config.telemetry_enabled)
        telemetry_metric("feature_engineering.duration_ms", duration_ms, self.config.telemetry_enabled)
        return result

    def transform_one(self, row: Any, *, specs: Optional[Sequence[FeatureSpec]] = None) -> Dict[str, Any]:
        result = self.transform([row], specs=specs)
        if result.errors:
            raise FeatureTransformError(result.errors[0].error_message)
        return result.rows[0] if result.rows else {}

    def export_manifest(self, path: str | os.PathLike[str]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.registry.to_manifest(), ensure_ascii=False, indent=2, sort_keys=True, default=safe_json_default), encoding="utf-8")
        return target

    def _save_report(self, result: FeatureEngineeringResult) -> None:
        if not self.config.report_path:
            return
        target = Path(self.config.report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(result.to_json(indent=2), encoding="utf-8")
        tmp.replace(target)

    def _save_manifest(self, lineage: Sequence[FeatureLineage]) -> None:
        if not self.config.manifest_path:
            return
        target = Path(self.config.manifest_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"created_at": utc_now_iso(), "lineage": [item.to_dict() for item in lineage]}
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=safe_json_default), encoding="utf-8")
        tmp.replace(target)


class _DropFeature:
    pass


_DROP_FEATURE = _DropFeature()


def compute_feature(row: Mapping[str, Any], spec: FeatureSpec, context: FeatureContext) -> Any:
    values = [get_field(row, field) for field in spec.input_fields]
    if any(value is None for value in values) and spec.transform not in {FeatureTransform.COALESCE, FeatureTransform.CONSTANT}:
        return handle_missing(spec)

    try:
        t = spec.transform
        params = spec.params
        if t == FeatureTransform.IDENTITY:
            return values[0]
        if t == FeatureTransform.CONSTANT:
            return params.get("value", spec.default)
        if t == FeatureTransform.COALESCE:
            for value in values:
                if value not in (None, ""):
                    return value
            return handle_missing(spec)
        if t == FeatureTransform.CAST_STRING:
            return str(values[0])
        if t == FeatureTransform.CAST_INT:
            return int(float(values[0]))
        if t == FeatureTransform.CAST_FLOAT:
            return float(values[0])
        if t == FeatureTransform.CAST_BOOL:
            return coerce_bool(values[0])
        if t == FeatureTransform.ADD:
            return sum(to_number(v) for v in values)
        if t == FeatureTransform.SUBTRACT:
            return to_number(values[0]) - sum(to_number(v) for v in values[1:])
        if t == FeatureTransform.MULTIPLY:
            result = 1.0
            for value in values:
                result *= to_number(value)
            return result
        if t in {FeatureTransform.DIVIDE, FeatureTransform.RATIO}:
            denominator = to_number(values[1]) if len(values) > 1 else params.get("denominator")
            if denominator in (0, None):
                return spec.default
            return to_number(values[0]) / float(denominator)
        if t == FeatureTransform.ABS:
            return abs(to_number(values[0]))
        if t == FeatureTransform.LOG:
            base = float(params.get("base", math.e))
            value = to_number(values[0])
            return math.log(value, base) if value > 0 else spec.default
        if t == FeatureTransform.SQRT:
            value = to_number(values[0])
            return math.sqrt(value) if value >= 0 else spec.default
        if t == FeatureTransform.POWER:
            return to_number(values[0]) ** float(params.get("exponent", 2))
        if t == FeatureTransform.CLIP:
            value = to_number(values[0])
            if "min" in params:
                value = max(value, float(params["min"]))
            if "max" in params:
                value = min(value, float(params["max"]))
            return value
        if t == FeatureTransform.MIN_MAX_SCALE:
            field = spec.input_fields[0]
            stats = context.global_stats.get(field, {})
            minimum = float(params.get("min", stats.get("min", 0.0)))
            maximum = float(params.get("max", stats.get("max", 1.0)))
            return (to_number(values[0]) - minimum) / (maximum - minimum) if maximum != minimum else 0.0
        if t == FeatureTransform.ZSCORE:
            field = spec.input_fields[0]
            stats = context.global_stats.get(field, {})
            mean = float(params.get("mean", stats.get("mean", 0.0)))
            std = float(params.get("std", stats.get("std", 1.0))) or 1.0
            return (to_number(values[0]) - mean) / std
        if t == FeatureTransform.BIN:
            return bin_value(to_number(values[0]), params.get("bins", []), labels=params.get("labels"))
        if t == FeatureTransform.BOOLEAN_FLAG:
            return bool(values[0])
        if t == FeatureTransform.ONE_HOT:
            expected = params.get("value")
            return 1 if values[0] == expected else 0
        if t == FeatureTransform.LABEL_MAP:
            return dict(params.get("mapping", {})).get(values[0], params.get("unknown", spec.default))
        if t == FeatureTransform.HASH_BUCKET:
            buckets = int(params.get("buckets", 100))
            return stable_hash(values[0]) % max(1, buckets)
        if t == FeatureTransform.FREQUENCY_ENCODE:
            field = spec.input_fields[0]
            counts = context.category_frequencies.get(field, Counter())
            total = sum(counts.values())
            return counts.get(values[0], 0) / total if total else 0.0
        if t == FeatureTransform.CONCAT:
            sep = str(params.get("separator", ""))
            return sep.join(str(v) for v in values if v is not None)
        if t == FeatureTransform.TEXT_LENGTH:
            return len(str(values[0]))
        if t == FeatureTransform.TOKEN_COUNT:
            return len(WORD_PATTERN.findall(str(values[0])))
        if t == FeatureTransform.CONTAINS:
            needle = str(params.get("value", ""))
            haystack = str(values[0])
            case_sensitive = bool(params.get("case_sensitive", False))
            return (needle in haystack) if case_sensitive else (needle.lower() in haystack.lower())
        if t == FeatureTransform.REGEX_EXTRACT:
            pattern = re.compile(str(params["pattern"]), int(params.get("flags", 0)))
            match = pattern.search(str(values[0]))
            if not match:
                return spec.default
            group = params.get("group", 1)
            return match.group(group)
        if t == FeatureTransform.DATETIME_PART:
            dt = coerce_datetime(values[0])
            return datetime_part(dt, str(params.get("part", "day")))
        if t == FeatureTransform.AGE_SECONDS:
            dt = coerce_datetime(values[0])
            reference = coerce_datetime(params.get("reference")) if params.get("reference") else context.now
            return max(0.0, (reference - dt).total_seconds())
        if t == FeatureTransform.RECENCY_SECONDS:
            dt = coerce_datetime(values[0])
            return max(0.0, (context.now - dt).total_seconds())
        if t == FeatureTransform.CYCLICAL_DATETIME:
            dt = coerce_datetime(values[0])
            part = str(params.get("part", "hour"))
            period, value = cyclical_period_value(dt, part)
            angle = 2 * math.pi * value / period
            mode = params.get("mode", "sin")
            return math.sin(angle) if mode == "sin" else math.cos(angle)
        if t == FeatureTransform.GROUP_AGGREGATE:
            return group_aggregate(row, context.reference_rows, spec)
        if t == FeatureTransform.WINDOW_AGGREGATE:
            return window_aggregate(row, context.reference_rows, spec)
        if t == FeatureTransform.CUSTOM and spec.custom_function:
            return spec.custom_function(row, context)
    except Exception as exc:
        raise FeatureTransformError(f"Feature {spec.name} failed: {exc}") from exc
    raise FeatureTransformError(f"Unsupported transform: {spec.transform}")


def handle_missing(spec: FeatureSpec) -> Any:
    if spec.missing_strategy == MissingStrategy.NULL:
        return None
    if spec.missing_strategy == MissingStrategy.DEFAULT:
        return spec.default
    if spec.missing_strategy == MissingStrategy.ZERO:
        return 0
    if spec.missing_strategy == MissingStrategy.DROP_FEATURE:
        return _DROP_FEATURE
    if spec.missing_strategy == MissingStrategy.ERROR:
        raise FeatureTransformError(f"Missing required inputs for feature {spec.name}")
    return None


def build_context(specs: Sequence[FeatureSpec], rows: Sequence[Mapping[str, Any]], metadata: Mapping[str, Any]) -> FeatureContext:
    numeric_by_field: Dict[str, List[float]] = defaultdict(list)
    category_counts: Dict[str, Counter[Any]] = defaultdict(Counter)
    fields = {field for spec in specs for field in spec.input_fields}
    for row in rows:
        for field in fields:
            value = get_field(row, field)
            if value is None:
                continue
            try:
                numeric_by_field[field].append(to_number(value))
            except Exception:
                category_counts[field][value] += 1
    stats = {}
    for field, values in numeric_by_field.items():
        if values:
            stats[field] = {
                "min": min(values),
                "max": max(values),
                "mean": statistics.fmean(values),
                "std": statistics.pstdev(values) or 1.0,
            }
    return FeatureContext(
        now=datetime.now(timezone.utc),
        reference_rows=tuple(rows),
        registry={spec.name: spec for spec in specs},
        global_stats=stats,
        category_frequencies=category_counts,
        metadata=sanitize_mapping(metadata),
    )


def group_aggregate(row: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], spec: FeatureSpec) -> Any:
    params = spec.params
    group_fields = tuple(params.get("group_by", []))
    value_field = params.get("value_field") or (spec.input_fields[0] if spec.input_fields else None)
    operation = AggregationOperation(params.get("operation", AggregationOperation.AVG.value))
    group_key = tuple(get_field(row, field) for field in group_fields)
    values = []
    for ref in rows:
        if tuple(get_field(ref, field) for field in group_fields) == group_key:
            values.append(get_field(ref, value_field))
    return aggregate_values(values, operation)


def window_aggregate(row: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], spec: FeatureSpec) -> Any:
    params = spec.params
    timestamp_field = params.get("timestamp_field", "timestamp")
    value_field = params.get("value_field") or (spec.input_fields[0] if spec.input_fields else None)
    operation = AggregationOperation(params.get("operation", AggregationOperation.AVG.value))
    window_seconds = float(params.get("window_seconds", 3600))
    current_ts = coerce_datetime(get_field(row, timestamp_field)).timestamp()
    group_fields = tuple(params.get("group_by", []))
    group_key = tuple(get_field(row, field) for field in group_fields)
    values = []
    for ref in rows:
        try:
            ref_ts = coerce_datetime(get_field(ref, timestamp_field)).timestamp()
        except Exception:
            continue
        if ref_ts > current_ts or ref_ts < current_ts - window_seconds:
            continue
        if group_fields and tuple(get_field(ref, field) for field in group_fields) != group_key:
            continue
        values.append(get_field(ref, value_field))
    return aggregate_values(values, operation)


def aggregate_values(values: Sequence[Any], operation: AggregationOperation) -> Any:
    clean = [v for v in values if v is not None]
    if operation == AggregationOperation.COUNT:
        return len(clean)
    if operation == AggregationOperation.DISTINCT_COUNT:
        return len({json_hashable(v) for v in clean})
    nums = [to_number(v) for v in clean]
    if not nums:
        return None
    if operation == AggregationOperation.SUM:
        return sum(nums)
    if operation == AggregationOperation.AVG:
        return statistics.fmean(nums)
    if operation == AggregationOperation.MIN:
        return min(nums)
    if operation == AggregationOperation.MAX:
        return max(nums)
    if operation == AggregationOperation.STDDEV:
        return statistics.pstdev(nums) if len(nums) > 1 else 0.0
    return None


def bin_value(value: float, bins: Sequence[float], labels: Optional[Sequence[Any]] = None) -> Any:
    sorted_bins = sorted(float(b) for b in bins)
    for index, boundary in enumerate(sorted_bins):
        if value <= boundary:
            return labels[index] if labels and index < len(labels) else index
    last_index = len(sorted_bins)
    return labels[last_index] if labels and last_index < len(labels) else last_index


def datetime_part(dt: datetime, part: str) -> Any:
    if part == "year":
        return dt.year
    if part == "month":
        return dt.month
    if part == "day":
        return dt.day
    if part == "hour":
        return dt.hour
    if part == "minute":
        return dt.minute
    if part == "weekday":
        return dt.weekday()
    if part == "dayofyear":
        return dt.timetuple().tm_yday
    if part == "week":
        return int(dt.strftime("%V"))
    if part == "is_weekend":
        return dt.weekday() >= 5
    return getattr(dt, part)


def cyclical_period_value(dt: datetime, part: str) -> Tuple[int, int]:
    if part == "hour":
        return 24, dt.hour
    if part == "weekday":
        return 7, dt.weekday()
    if part == "month":
        return 12, dt.month - 1
    if part == "dayofyear":
        return 366, dt.timetuple().tm_yday - 1
    return 24, dt.hour


def build_lineage(spec: FeatureSpec) -> FeatureLineage:
    return FeatureLineage(
        feature_name=spec.name,
        transform=spec.transform,
        input_fields=spec.input_fields,
        params=sanitize_mapping(spec.params),
        tags=spec.tags,
        description=spec.description,
    )


def spec_to_dict(spec: FeatureSpec) -> Dict[str, Any]:
    data = asdict(spec)
    data["transform"] = spec.transform.value
    data["missing_strategy"] = spec.missing_strategy.value
    data["custom_function"] = None if spec.custom_function else None
    return sanitize_mapping(data)


def determine_status(input_count: int, rows: Sequence[Mapping[str, Any]], errors: Sequence[FeatureErrorRecord]) -> FeatureStatus:
    if input_count == 0:
        return FeatureStatus.EMPTY
    if errors and not rows:
        return FeatureStatus.FAILED
    if errors:
        return FeatureStatus.PARTIAL
    return FeatureStatus.SUCCEEDED


def to_mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if dataclasses.is_dataclass(row):
        return asdict(row)
    if hasattr(row, "_asdict"):
        return row._asdict()
    if hasattr(row, "__dict__"):
        return vars(row)
    raise FeatureTransformError(f"Unsupported row type: {type(row)!r}")


def get_field(row: Mapping[str, Any], field_path: Optional[str]) -> Any:
    if not field_path:
        return None
    current: Any = row
    for part in field_path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def to_number(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, Decimal):
        return float(value)
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise FeatureTransformError(f"Invalid numeric value: {value!r}")
    return number


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "sim", "s", "on"}:
        return True
    if text in {"0", "false", "f", "no", "n", "nao", "não", "off"}:
        return False
    raise FeatureTransformError(f"Cannot coerce to bool: {value!r}")


def coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    elif isinstance(value, (int, float)):
        raw = float(value)
        dt = datetime.fromtimestamp(raw / 1000.0 if raw > 10_000_000_000 else raw, timezone.utc)
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def stable_hash(value: Any) -> int:
    raw = json.dumps(sanitize_value(value), ensure_ascii=False, sort_keys=True, default=safe_json_default)
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest(), 16)


def json_hashable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=safe_json_default)
    except Exception:
        return str(value)


def sanitize_mapping(values: Mapping[str, Any], *, depth: int = 0) -> Dict[str, Any]:
    if depth > 6:
        return {"_truncated": "max_depth_exceeded"}
    output: Dict[str, Any] = {}
    for key, value in values.items():
        key_str = str(key)
        if SENSITIVE_KEY_PATTERN.search(key_str):
            output[key_str] = "[REDACTED]"
        elif isinstance(value, Mapping):
            output[key_str] = sanitize_mapping(value, depth=depth + 1)
        elif isinstance(value, (list, tuple, set, deque)):
            output[key_str] = [sanitize_value(item, depth=depth + 1) for item in list(value)[:10_000]]
        else:
            output[key_str] = sanitize_value(value, depth=depth)
    return output


def sanitize_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return sanitize_mapping(asdict(value), depth=depth + 1)
    if isinstance(value, Mapping):
        return sanitize_mapping(value, depth=depth + 1)
    if isinstance(value, (list, tuple, set, deque)):
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
        logger.debug("Feature engineering telemetry metric failed", exc_info=True)


def monotonic_ms() -> float:
    import time
    return time.perf_counter() * 1000.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return asdict(value)
    if isinstance(value, (set, tuple, deque)):
        return list(value)
    return str(value)


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "AggregationOperation",
    "FeatureConfigError",
    "FeatureContext",
    "FeatureEngineeringConfig",
    "FeatureEngineeringEngine",
    "FeatureEngineeringError",
    "FeatureEngineeringResult",
    "FeatureErrorRecord",
    "FeatureLineage",
    "FeatureRegistry",
    "FeatureSpec",
    "FeatureStatus",
    "FeatureTransform",
    "FeatureTransformError",
    "FeatureTransformer",
    "MissingStrategy",
    "compute_feature",
    "build_context",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    rows = [
        {"id": 1, "amount": 10.0, "category": "food", "created_at": "2026-01-01T10:00:00Z"},
        {"id": 2, "amount": 20.0, "category": "tech", "created_at": "2026-01-02T12:00:00Z"},
    ]
    engine = FeatureEngineeringEngine(FeatureEngineeringConfig(telemetry_enabled=False))
    result = engine.transform(
        rows,
        specs=[
            FeatureSpec("amount_log", FeatureTransform.LOG, ("amount",)),
            FeatureSpec("amount_z", FeatureTransform.ZSCORE, ("amount",)),
            FeatureSpec("weekday", FeatureTransform.DATETIME_PART, ("created_at",), params={"part": "weekday"}),
            FeatureSpec("category_hash", FeatureTransform.HASH_BUCKET, ("category",), params={"buckets": 10}),
        ],
    )
    print(result.to_json())

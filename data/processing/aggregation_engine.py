"""
data/processing/aggregation_engine.py

Enterprise-grade aggregation engine for data platforms.

Purpose
-------
Provides a robust, dependency-light aggregation engine for batch, micro-batch
and streaming workloads. It supports group-by aggregations, time windows,
incremental state, custom aggregators, validation, safe serialization and
optional telemetry integration.

Core capabilities
-----------------
- Batch aggregation over dictionaries, dataclasses or arbitrary objects.
- Group-by one or multiple dimensions.
- Tumbling, sliding and session-like window assignment.
- Built-in aggregations: count, sum, min, max, avg, first, last, distinct_count,
  list, set, variance, stddev, percentile, weighted_avg.
- Custom aggregation functions through a clean protocol.
- Incremental stateful aggregation for streaming/micro-batch flows.
- Watermark-aware late-event handling.
- Null handling strategies and type coercion helpers.
- Cardinality protection.
- JSON snapshot export/import for stateful processors.
- Optional telemetry hooks without hard dependency.
- Thread-safe state updates.

Example
-------
engine = AggregationEngine()
result = engine.aggregate(
    rows,
    group_by=["country", "category"],
    aggregations=[
        AggregationSpec(name="orders", operation=AggregationOperation.COUNT),
        AggregationSpec(name="revenue", operation=AggregationOperation.SUM, field="amount"),
        AggregationSpec(name="avg_ticket", operation=AggregationOperation.AVG, field="amount"),
    ],
)
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import os
import re
import statistics
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|session|jwt|bearer)",
    re.IGNORECASE,
)

MAX_GROUP_KEY_LENGTH = 512
MAX_LIST_AGG_ITEMS = 10_000
DEFAULT_MAX_GROUPS = 250_000
DEFAULT_PERCENTILE = 95.0


class AggregationOperation(str, Enum):
    COUNT = "count"
    SUM = "sum"
    MIN = "min"
    MAX = "max"
    AVG = "avg"
    FIRST = "first"
    LAST = "last"
    DISTINCT_COUNT = "distinct_count"
    LIST = "list"
    SET = "set"
    VARIANCE = "variance"
    STDDEV = "stddev"
    PERCENTILE = "percentile"
    WEIGHTED_AVG = "weighted_avg"
    CUSTOM = "custom"


class WindowType(str, Enum):
    NONE = "none"
    TUMBLING = "tumbling"
    SLIDING = "sliding"
    SESSION = "session"


class NullStrategy(str, Enum):
    IGNORE = "ignore"
    ZERO = "zero"
    INCLUDE = "include"
    ERROR = "error"


class LateEventStrategy(str, Enum):
    DROP = "drop"
    KEEP = "keep"
    SIDE_OUTPUT = "side_output"
    ERROR = "error"


class AggregationStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    EMPTY = "empty"


@dataclass(frozen=True)
class WindowSpec:
    window_type: WindowType = WindowType.NONE
    size_seconds: Optional[int] = None
    slide_seconds: Optional[int] = None
    session_gap_seconds: Optional[int] = None
    timestamp_field: str = "timestamp"
    timezone: str = "UTC"
    allowed_lateness_seconds: int = 0

    def validate(self) -> None:
        if self.window_type == WindowType.NONE:
            return
        if self.window_type in {WindowType.TUMBLING, WindowType.SLIDING}:
            if not self.size_seconds or self.size_seconds <= 0:
                raise AggregationConfigError("window size_seconds must be positive")
        if self.window_type == WindowType.SLIDING:
            if not self.slide_seconds or self.slide_seconds <= 0:
                raise AggregationConfigError("sliding window slide_seconds must be positive")
        if self.window_type == WindowType.SESSION:
            if not self.session_gap_seconds or self.session_gap_seconds <= 0:
                raise AggregationConfigError("session_gap_seconds must be positive")


@dataclass(frozen=True)
class AggregationSpec:
    name: str
    operation: AggregationOperation
    field: Optional[str] = None
    weight_field: Optional[str] = None
    percentile: float = DEFAULT_PERCENTILE
    null_strategy: NullStrategy = NullStrategy.IGNORE
    default_value: Any = None
    custom_function: Optional[Callable[[Sequence[Any]], Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.name:
            raise AggregationConfigError("AggregationSpec.name is required")
        if self.operation not in {AggregationOperation.COUNT, AggregationOperation.CUSTOM} and not self.field:
            raise AggregationConfigError(f"Aggregation {self.name} requires field for operation {self.operation.value}")
        if self.operation == AggregationOperation.WEIGHTED_AVG and not self.weight_field:
            raise AggregationConfigError("weighted_avg requires weight_field")
        if self.operation == AggregationOperation.PERCENTILE and not (0 <= self.percentile <= 100):
            raise AggregationConfigError("percentile must be between 0 and 100")
        if self.operation == AggregationOperation.CUSTOM and not self.custom_function:
            raise AggregationConfigError("custom aggregation requires custom_function")


@dataclass(frozen=True)
class AggregationConfig:
    max_groups: int = DEFAULT_MAX_GROUPS
    max_list_items: int = MAX_LIST_AGG_ITEMS
    fail_on_cardinality_limit: bool = True
    include_empty_groups: bool = False
    emit_metadata: bool = True
    telemetry_enabled: bool = True
    late_event_strategy: LateEventStrategy = LateEventStrategy.DROP
    state_snapshot_path: Optional[str] = None

    @classmethod
    def from_env(cls) -> "AggregationConfig":
        return cls(
            max_groups=int_env("AGGREGATION_MAX_GROUPS", DEFAULT_MAX_GROUPS),
            max_list_items=int_env("AGGREGATION_MAX_LIST_ITEMS", MAX_LIST_AGG_ITEMS),
            fail_on_cardinality_limit=bool_env("AGGREGATION_FAIL_ON_CARDINALITY_LIMIT", True),
            include_empty_groups=bool_env("AGGREGATION_INCLUDE_EMPTY_GROUPS", False),
            emit_metadata=bool_env("AGGREGATION_EMIT_METADATA", True),
            telemetry_enabled=bool_env("AGGREGATION_TELEMETRY_ENABLED", True),
            late_event_strategy=LateEventStrategy(os.getenv("AGGREGATION_LATE_EVENT_STRATEGY", LateEventStrategy.DROP.value)),
            state_snapshot_path=os.getenv("AGGREGATION_STATE_SNAPSHOT_PATH"),
        )


@dataclass(frozen=True)
class AggregationResult:
    id: str
    status: AggregationStatus
    rows: List[Dict[str, Any]]
    group_count: int
    input_count: int
    output_count: int
    dropped_count: int
    late_count: int
    started_at: str
    finished_at: str
    duration_ms: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return sanitize_mapping(data)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True, default=safe_json_default)


@dataclass
class AggregationState:
    group_key: Tuple[Any, ...]
    group_values: Dict[str, Any] = field(default_factory=dict)
    raw_values: Dict[str, List[Any]] = field(default_factory=lambda: defaultdict(list))
    weights: Dict[str, List[Any]] = field(default_factory=lambda: defaultdict(list))
    counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    first_seen_at: Optional[float] = None
    last_seen_at: Optional[float] = None
    window_start: Optional[float] = None
    window_end: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "group_key": list(self.group_key),
            "group_values": sanitize_mapping(self.group_values),
            "raw_values": {k: sanitize_value(v) for k, v in self.raw_values.items()},
            "weights": {k: sanitize_value(v) for k, v in self.weights.items()},
            "counts": dict(self.counts),
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "window_start": self.window_start,
            "window_end": self.window_end,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AggregationState":
        state = cls(group_key=tuple(data.get("group_key", [])))
        state.group_values = dict(data.get("group_values", {}))
        state.raw_values = defaultdict(list, {k: list(v) for k, v in dict(data.get("raw_values", {})).items()})
        state.weights = defaultdict(list, {k: list(v) for k, v in dict(data.get("weights", {})).items()})
        state.counts = defaultdict(int, {k: int(v) for k, v in dict(data.get("counts", {})).items()})
        state.first_seen_at = data.get("first_seen_at")
        state.last_seen_at = data.get("last_seen_at")
        state.window_start = data.get("window_start")
        state.window_end = data.get("window_end")
        return state


class AggregationError(Exception):
    """Base aggregation error."""


class AggregationConfigError(AggregationError):
    """Invalid aggregation configuration."""


class AggregationCardinalityError(AggregationError):
    """Too many groups generated."""


class AggregationValidationError(AggregationError):
    """Invalid input data."""


class CustomAggregator(Protocol):
    def update(self, state: AggregationState, row: Mapping[str, Any], spec: AggregationSpec) -> None:
        ...

    def finalize(self, state: AggregationState, spec: AggregationSpec) -> Any:
        ...


class AggregationEngine:
    """Enterprise aggregation engine."""

    def __init__(self, config: Optional[AggregationConfig] = None) -> None:
        self.config = config or AggregationConfig.from_env()
        self._states: Dict[str, AggregationState] = {}
        self._lock = threading.RLock()
        self._watermark: Optional[float] = None
        if self.config.state_snapshot_path:
            self.restore_state(self.config.state_snapshot_path)

    def aggregate(
        self,
        rows: Iterable[Any],
        *,
        group_by: Optional[Sequence[str]] = None,
        aggregations: Sequence[AggregationSpec],
        window: Optional[WindowSpec] = None,
        incremental: bool = False,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> AggregationResult:
        """Aggregate rows and return finalized grouped result."""
        started = time.perf_counter()
        started_iso = utc_now_iso()
        group_fields = list(group_by or [])
        window_spec = window or WindowSpec()
        window_spec.validate()
        for spec in aggregations:
            spec.validate()

        input_count = 0
        dropped_count = 0
        late_count = 0
        local_states: Dict[str, AggregationState] = {} if not incremental else self._states

        try:
            with telemetry_operation("aggregation.aggregate", self.config.telemetry_enabled, attributes={"group_by": group_fields, "aggregations": [s.name for s in aggregations]}):
                for raw_row in rows:
                    input_count += 1
                    row = to_mapping(raw_row)
                    event_ts = extract_timestamp(row, window_spec) if window_spec.window_type != WindowType.NONE else None
                    if event_ts is not None and self._is_late(event_ts, window_spec):
                        late_count += 1
                        if self.config.late_event_strategy == LateEventStrategy.DROP:
                            dropped_count += 1
                            continue
                        if self.config.late_event_strategy == LateEventStrategy.ERROR:
                            raise AggregationValidationError(f"Late event detected: timestamp={event_ts}")

                    window_keys = assign_windows(event_ts, window_spec) if event_ts is not None else [(None, None)]
                    for window_start, window_end in window_keys:
                        group_key = build_group_key(row, group_fields, window_start, window_end)
                        state_key = encode_group_key(group_key)
                        if state_key not in local_states:
                            if len(local_states) >= self.config.max_groups:
                                if self.config.fail_on_cardinality_limit:
                                    raise AggregationCardinalityError(f"max_groups exceeded: {self.config.max_groups}")
                                dropped_count += 1
                                continue
                            local_states[state_key] = AggregationState(
                                group_key=group_key,
                                group_values=extract_group_values(row, group_fields, window_start, window_end),
                                window_start=window_start,
                                window_end=window_end,
                            )
                        update_state(local_states[state_key], row, aggregations, self.config)
                        local_states[state_key].last_seen_at = event_ts or time.time()
                        local_states[state_key].first_seen_at = local_states[state_key].first_seen_at or local_states[state_key].last_seen_at

                if incremental:
                    with self._lock:
                        self._states = local_states
                    states_to_finalize = self._states
                else:
                    states_to_finalize = local_states

                output_rows = [finalize_state(state, aggregations) for state in states_to_finalize.values()]
                finished_iso = utc_now_iso()
                duration_ms = (time.perf_counter() - started) * 1000.0
                status = AggregationStatus.EMPTY if input_count == 0 else AggregationStatus.PARTIAL if dropped_count else AggregationStatus.SUCCEEDED
                result = AggregationResult(
                    id=str(uuid.uuid4()),
                    status=status,
                    rows=output_rows,
                    group_count=len(states_to_finalize),
                    input_count=input_count,
                    output_count=len(output_rows),
                    dropped_count=dropped_count,
                    late_count=late_count,
                    started_at=started_iso,
                    finished_at=finished_iso,
                    duration_ms=round(duration_ms, 3),
                    metadata=sanitize_mapping({
                        "group_by": group_fields,
                        "window": asdict(window_spec),
                        "incremental": incremental,
                        **dict(metadata or {}),
                    }) if self.config.emit_metadata else {},
                )
                telemetry_metric("aggregation.input_count", input_count, self.config.telemetry_enabled)
                telemetry_metric("aggregation.output_count", len(output_rows), self.config.telemetry_enabled)
                telemetry_metric("aggregation.duration_ms", duration_ms, self.config.telemetry_enabled)
                return result
        except Exception:
            telemetry_metric("aggregation.errors_total", 1, self.config.telemetry_enabled)
            raise

    def update(
        self,
        row: Any,
        *,
        group_by: Optional[Sequence[str]],
        aggregations: Sequence[AggregationSpec],
        window: Optional[WindowSpec] = None,
    ) -> None:
        """Incrementally update internal state with a single row."""
        self.aggregate([row], group_by=group_by, aggregations=aggregations, window=window, incremental=True)

    def finalize(self, aggregations: Sequence[AggregationSpec]) -> List[Dict[str, Any]]:
        """Finalize current internal state without clearing it."""
        with self._lock:
            return [finalize_state(state, aggregations) for state in self._states.values()]

    def clear_state(self) -> None:
        with self._lock:
            self._states.clear()
            self._watermark = None

    def set_watermark(self, timestamp: float | datetime | str) -> None:
        self._watermark = normalize_timestamp(timestamp)

    def snapshot_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "created_at": utc_now_iso(),
                "watermark": self._watermark,
                "states": {key: state.to_dict() for key, state in self._states.items()},
            }

    def save_state(self, path: str | os.PathLike[str]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(self.snapshot_state(), ensure_ascii=False, indent=2, sort_keys=True, default=safe_json_default), encoding="utf-8")
        tmp.replace(target)
        return target

    def restore_state(self, path: str | os.PathLike[str]) -> None:
        target = Path(path)
        if not target.exists():
            return
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            states = payload.get("states") or {}
            with self._lock:
                self._watermark = payload.get("watermark")
                self._states = {str(key): AggregationState.from_dict(value) for key, value in states.items()}
        except Exception as exc:
            logger.warning("Failed to restore aggregation state from %s: %s", target, exc)

    def _is_late(self, event_ts: float, window: WindowSpec) -> bool:
        if self._watermark is None:
            return False
        return event_ts < self._watermark - window.allowed_lateness_seconds


def update_state(state: AggregationState, row: Mapping[str, Any], aggregations: Sequence[AggregationSpec], config: AggregationConfig) -> None:
    for spec in aggregations:
        value = get_field(row, spec.field) if spec.field else None
        if value is None:
            if spec.null_strategy == NullStrategy.ERROR:
                raise AggregationValidationError(f"Null value for aggregation {spec.name}")
            if spec.null_strategy == NullStrategy.IGNORE and spec.operation != AggregationOperation.COUNT:
                continue
            if spec.null_strategy == NullStrategy.ZERO:
                value = 0
            if spec.null_strategy == NullStrategy.INCLUDE:
                value = None

        if spec.operation == AggregationOperation.COUNT:
            state.counts[spec.name] += 1
        elif spec.operation == AggregationOperation.SUM:
            state.raw_values[spec.name].append(to_number(value))
        elif spec.operation in {AggregationOperation.MIN, AggregationOperation.MAX, AggregationOperation.AVG, AggregationOperation.VARIANCE, AggregationOperation.STDDEV, AggregationOperation.PERCENTILE}:
            state.raw_values[spec.name].append(to_number(value))
        elif spec.operation == AggregationOperation.FIRST:
            if spec.name not in state.group_values:
                state.group_values[spec.name] = value
        elif spec.operation == AggregationOperation.LAST:
            state.group_values[spec.name] = value
        elif spec.operation == AggregationOperation.DISTINCT_COUNT:
            if len(state.raw_values[spec.name]) < config.max_list_items:
                state.raw_values[spec.name].append(value)
        elif spec.operation == AggregationOperation.LIST:
            if len(state.raw_values[spec.name]) < config.max_list_items:
                state.raw_values[spec.name].append(value)
        elif spec.operation == AggregationOperation.SET:
            if len(state.raw_values[spec.name]) < config.max_list_items:
                state.raw_values[spec.name].append(value)
        elif spec.operation == AggregationOperation.WEIGHTED_AVG:
            weight = get_field(row, spec.weight_field) if spec.weight_field else None
            if weight is None:
                continue
            state.raw_values[spec.name].append(to_number(value))
            state.weights[spec.name].append(to_number(weight))
        elif spec.operation == AggregationOperation.CUSTOM:
            state.raw_values[spec.name].append(value)


def finalize_state(state: AggregationState, aggregations: Sequence[AggregationSpec]) -> Dict[str, Any]:
    output = dict(state.group_values)
    if state.window_start is not None:
        output["window_start"] = datetime.fromtimestamp(state.window_start, timezone.utc).isoformat()
    if state.window_end is not None:
        output["window_end"] = datetime.fromtimestamp(state.window_end, timezone.utc).isoformat()

    for spec in aggregations:
        values = state.raw_values.get(spec.name, [])
        if spec.operation == AggregationOperation.COUNT:
            output[spec.name] = state.counts.get(spec.name, 0)
        elif spec.operation == AggregationOperation.SUM:
            output[spec.name] = sum(values) if values else spec.default_value or 0
        elif spec.operation == AggregationOperation.MIN:
            output[spec.name] = min(values) if values else spec.default_value
        elif spec.operation == AggregationOperation.MAX:
            output[spec.name] = max(values) if values else spec.default_value
        elif spec.operation == AggregationOperation.AVG:
            output[spec.name] = statistics.fmean(values) if values else spec.default_value
        elif spec.operation == AggregationOperation.FIRST:
            output.setdefault(spec.name, spec.default_value)
        elif spec.operation == AggregationOperation.LAST:
            output.setdefault(spec.name, spec.default_value)
        elif spec.operation == AggregationOperation.DISTINCT_COUNT:
            output[spec.name] = len({json_safe_hashable(v) for v in values})
        elif spec.operation == AggregationOperation.LIST:
            output[spec.name] = list(values)
        elif spec.operation == AggregationOperation.SET:
            output[spec.name] = sorted({json_safe_hashable(v) for v in values}, key=str)
        elif spec.operation == AggregationOperation.VARIANCE:
            output[spec.name] = statistics.variance(values) if len(values) >= 2 else 0.0
        elif spec.operation == AggregationOperation.STDDEV:
            output[spec.name] = statistics.stdev(values) if len(values) >= 2 else 0.0
        elif spec.operation == AggregationOperation.PERCENTILE:
            output[spec.name] = percentile(sorted(values), spec.percentile) if values else spec.default_value
        elif spec.operation == AggregationOperation.WEIGHTED_AVG:
            weights = state.weights.get(spec.name, [])
            total_weight = sum(weights)
            output[spec.name] = sum(v * w for v, w in zip(values, weights)) / total_weight if total_weight else spec.default_value
        elif spec.operation == AggregationOperation.CUSTOM:
            output[spec.name] = spec.custom_function(values) if spec.custom_function else spec.default_value
    return sanitize_mapping(output)


def assign_windows(event_ts: Optional[float], window: WindowSpec) -> List[Tuple[Optional[float], Optional[float]]]:
    if event_ts is None or window.window_type == WindowType.NONE:
        return [(None, None)]
    if window.window_type == WindowType.TUMBLING:
        size = float(window.size_seconds or 1)
        start = math.floor(event_ts / size) * size
        return [(start, start + size)]
    if window.window_type == WindowType.SLIDING:
        size = float(window.size_seconds or 1)
        slide = float(window.slide_seconds or size)
        latest_start = math.floor(event_ts / slide) * slide
        windows = []
        start = latest_start
        while start + size > event_ts and start >= event_ts - size:
            windows.append((start, start + size))
            start -= slide
        return windows or [(latest_start, latest_start + size)]
    if window.window_type == WindowType.SESSION:
        # Stateless session approximation: creates a bucket anchored by session gap.
        gap = float(window.session_gap_seconds or 1)
        start = math.floor(event_ts / gap) * gap
        return [(start, start + gap)]
    return [(None, None)]


def build_group_key(row: Mapping[str, Any], group_by: Sequence[str], window_start: Optional[float], window_end: Optional[float]) -> Tuple[Any, ...]:
    values = [sanitize_group_value(get_field(row, field_name)) for field_name in group_by]
    if window_start is not None or window_end is not None:
        values.extend([window_start, window_end])
    return tuple(values)


def extract_group_values(row: Mapping[str, Any], group_by: Sequence[str], window_start: Optional[float], window_end: Optional[float]) -> Dict[str, Any]:
    values = {field_name: sanitize_group_value(get_field(row, field_name)) for field_name in group_by}
    if window_start is not None:
        values["window_start_epoch"] = window_start
    if window_end is not None:
        values["window_end_epoch"] = window_end
    return values


def encode_group_key(group_key: Tuple[Any, ...]) -> str:
    encoded = json.dumps(group_key, ensure_ascii=False, sort_keys=True, default=safe_json_default)
    if len(encoded) > MAX_GROUP_KEY_LENGTH:
        import hashlib
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return encoded


def to_mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if dataclasses.is_dataclass(row):
        return asdict(row)
    if hasattr(row, "_asdict"):
        return row._asdict()
    if hasattr(row, "__dict__"):
        return vars(row)
    raise AggregationValidationError(f"Unsupported row type: {type(row)!r}")


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


def extract_timestamp(row: Mapping[str, Any], window: WindowSpec) -> float:
    value = get_field(row, window.timestamp_field)
    if value is None:
        raise AggregationValidationError(f"timestamp field not found: {window.timestamp_field}")
    return normalize_timestamp(value)


def normalize_timestamp(value: float | int | str | datetime) -> float:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    if isinstance(value, (int, float)):
        # Heuristic: milliseconds epoch if very large.
        return float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    raise AggregationValidationError(f"Unsupported timestamp value: {value!r}")


def to_number(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise AggregationValidationError(f"Value is not numeric: {value!r}") from exc


def percentile(sorted_values: Sequence[float], percentile_value: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    k = (len(sorted_values) - 1) * percentile_value / 100.0
    lower = math.floor(k)
    upper = math.ceil(k)
    if lower == upper:
        return float(sorted_values[int(k)])
    return float(sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (k - lower))


def sanitize_group_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value)
    if SENSITIVE_KEY_PATTERN.search(text):
        return "[REDACTED]"
    return text[:MAX_GROUP_KEY_LENGTH]


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
            result[key_str] = [sanitize_value(item, depth=depth + 1) for item in list(value)[:MAX_LIST_AGG_ITEMS]]
        else:
            result[key_str] = sanitize_value(value, depth=depth)
    return result


def sanitize_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return sanitize_mapping(value, depth=depth + 1)
    if isinstance(value, (list, tuple, set)):
        return [sanitize_value(item, depth=depth + 1) for item in list(value)[:MAX_LIST_AGG_ITEMS]]
    text = str(value)
    if len(text) > MAX_TEXT_LENGTH:
        return text[:MAX_TEXT_LENGTH - 15] + "...[truncated]"
    return text


def json_safe_hashable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=safe_json_default)
    except Exception:
        return str(value)


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
        logger.debug("Aggregation telemetry metric failed", exc_info=True)


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
    "AggregationCardinalityError",
    "AggregationConfig",
    "AggregationConfigError",
    "AggregationEngine",
    "AggregationError",
    "AggregationOperation",
    "AggregationResult",
    "AggregationSpec",
    "AggregationState",
    "AggregationStatus",
    "AggregationValidationError",
    "CustomAggregator",
    "LateEventStrategy",
    "NullStrategy",
    "WindowSpec",
    "WindowType",
    "assign_windows",
    "finalize_state",
    "update_state",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    rows = [
        {"country": "BR", "category": "food", "amount": 10.5, "timestamp": "2026-01-01T00:00:01Z"},
        {"country": "BR", "category": "food", "amount": 20.0, "timestamp": "2026-01-01T00:00:30Z"},
        {"country": "US", "category": "tech", "amount": 50.0, "timestamp": "2026-01-01T00:01:05Z"},
    ]
    engine = AggregationEngine()
    result = engine.aggregate(
        rows,
        group_by=["country", "category"],
        window=WindowSpec(window_type=WindowType.TUMBLING, size_seconds=60),
        aggregations=[
            AggregationSpec(name="orders", operation=AggregationOperation.COUNT),
            AggregationSpec(name="revenue", operation=AggregationOperation.SUM, field="amount"),
            AggregationSpec(name="avg_ticket", operation=AggregationOperation.AVG, field="amount"),
            AggregationSpec(name="p95_ticket", operation=AggregationOperation.PERCENTILE, field="amount", percentile=95),
        ],
    )
    print(result.to_json())

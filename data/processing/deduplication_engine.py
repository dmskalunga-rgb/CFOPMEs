"""
data/processing/deduplication_engine.py

Enterprise-grade deduplication engine for data platforms.

Purpose
-------
Provides a dependency-light engine for exact and fuzzy deduplication in batch,
micro-batch and streaming workloads. It supports configurable keys, hashing,
normalization, retention windows, state snapshots, duplicate policies and merge
strategies.

Core capabilities
-----------------
- Exact deduplication by one or more fields.
- Composite/fingerprint keys with stable hashing.
- Optional fuzzy matching using normalized string similarity.
- Stateful incremental deduplication for streaming/micro-batch jobs.
- Time-window-aware deduplication with TTL cleanup.
- Duplicate policies: drop, keep first, keep last, mark, merge, error.
- Merge strategies per field.
- Duplicate audit records and summary reports.
- JSON snapshot/restore for state.
- Safe metadata sanitization.
- Optional telemetry integration.
- Standard library only.

Example
-------
engine = DeduplicationEngine()
result = engine.deduplicate(
    rows,
    spec=DeduplicationSpec(
        keys=("customer_id", "order_id"),
        policy=DuplicatePolicy.KEEP_FIRST,
    ),
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
import threading
import time
import uuid
from collections import defaultdict
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
NON_ALNUM_PATTERN = re.compile(r"[^a-zA-Z0-9]+")
MAX_TEXT_LENGTH = 16_384
DEFAULT_MAX_STATE_KEYS = 1_000_000


class DuplicatePolicy(str, Enum):
    KEEP_FIRST = "keep_first"
    KEEP_LAST = "keep_last"
    DROP_DUPLICATES = "drop_duplicates"
    MARK = "mark"
    MERGE = "merge"
    ERROR = "error"


class DuplicateStatus(str, Enum):
    UNIQUE = "unique"
    DUPLICATE = "duplicate"
    MERGED = "merged"
    DROPPED = "dropped"
    ERROR = "error"


class SimilarityMethod(str, Enum):
    EXACT = "exact"
    NORMALIZED = "normalized"
    TOKEN_SET = "token_set"
    LEVENSHTEIN = "levenshtein"


class MergeStrategy(str, Enum):
    FIRST_NON_NULL = "first_non_null"
    LAST_NON_NULL = "last_non_null"
    MAX = "max"
    MIN = "min"
    SUM = "sum"
    LIST = "list"
    SET = "set"
    CONCAT = "concat"
    CUSTOM = "custom"


class DeduplicationResultStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    EMPTY = "empty"


@dataclass(frozen=True)
class MergeRule:
    field: str
    strategy: MergeStrategy = MergeStrategy.LAST_NON_NULL
    separator: str = " "
    custom_function: Optional[Callable[[Any, Any], Any]] = None

    def validate(self) -> None:
        if not self.field:
            raise DeduplicationConfigError("MergeRule.field is required")
        if self.strategy == MergeStrategy.CUSTOM and not self.custom_function:
            raise DeduplicationConfigError("custom merge strategy requires custom_function")


@dataclass(frozen=True)
class FuzzySpec:
    enabled: bool = False
    fields: Tuple[str, ...] = field(default_factory=tuple)
    method: SimilarityMethod = SimilarityMethod.NORMALIZED
    threshold: float = 0.92
    block_keys: Tuple[str, ...] = field(default_factory=tuple)
    max_candidates_per_block: int = 500

    def validate(self) -> None:
        if self.enabled and not self.fields:
            raise DeduplicationConfigError("fuzzy fields are required when fuzzy matching is enabled")
        if not (0.0 <= self.threshold <= 1.0):
            raise DeduplicationConfigError("fuzzy threshold must be between 0 and 1")


@dataclass(frozen=True)
class DeduplicationSpec:
    keys: Tuple[str, ...]
    policy: DuplicatePolicy = DuplicatePolicy.KEEP_FIRST
    fuzzy: FuzzySpec = field(default_factory=FuzzySpec)
    merge_rules: Tuple[MergeRule, ...] = field(default_factory=tuple)
    timestamp_field: Optional[str] = None
    ttl_seconds: Optional[int] = None
    mark_field: str = "_duplicate"
    duplicate_key_field: str = "_duplicate_key"
    include_duplicate_audit: bool = True
    case_sensitive: bool = False
    trim_strings: bool = True
    normalize_whitespace: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.keys and not self.fuzzy.enabled:
            raise DeduplicationConfigError("keys or fuzzy matching must be configured")
        self.fuzzy.validate()
        for rule in self.merge_rules:
            rule.validate()


@dataclass(frozen=True)
class DeduplicationConfig:
    max_state_keys: int = DEFAULT_MAX_STATE_KEYS
    fail_on_state_limit: bool = True
    telemetry_enabled: bool = True
    state_snapshot_path: Optional[str] = None
    include_rows: bool = True
    max_output_rows: int = 1_000_000
    report_path: Optional[str] = None

    @classmethod
    def from_env(cls) -> "DeduplicationConfig":
        return cls(
            max_state_keys=int_env("DEDUP_MAX_STATE_KEYS", DEFAULT_MAX_STATE_KEYS),
            fail_on_state_limit=bool_env("DEDUP_FAIL_ON_STATE_LIMIT", True),
            telemetry_enabled=bool_env("DEDUP_TELEMETRY_ENABLED", True),
            state_snapshot_path=os.getenv("DEDUP_STATE_SNAPSHOT_PATH"),
            include_rows=bool_env("DEDUP_INCLUDE_ROWS", True),
            max_output_rows=int_env("DEDUP_MAX_OUTPUT_ROWS", 1_000_000),
            report_path=os.getenv("DEDUP_REPORT_PATH"),
        )


@dataclass(frozen=True)
class DuplicateAuditRecord:
    id: str
    timestamp: str
    duplicate_key: str
    status: DuplicateStatus
    original_index: Optional[int]
    duplicate_index: int
    similarity: Optional[float] = None
    reason: str = ""
    original_record: Optional[Dict[str, Any]] = None
    duplicate_record: Optional[Dict[str, Any]] = None
    merged_record: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return sanitize_mapping(data)


@dataclass(frozen=True)
class DeduplicationResult:
    id: str
    status: DeduplicationResultStatus
    started_at: str
    finished_at: str
    duration_ms: float
    input_count: int
    output_count: int
    unique_count: int
    duplicate_count: int
    merged_count: int
    dropped_count: int
    error_count: int
    rows: List[Dict[str, Any]] = field(default_factory=list)
    duplicates: List[DuplicateAuditRecord] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["duplicates"] = [item.to_dict() for item in self.duplicates]
        return sanitize_mapping(data)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True, default=safe_json_default)


@dataclass
class SeenRecord:
    duplicate_key: str
    row_index: int
    first_seen_at: float
    last_seen_at: float
    record: Dict[str, Any]
    count: int = 1
    fuzzy_text: Optional[str] = None
    block_key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SeenRecord":
        return cls(
            duplicate_key=str(data["duplicate_key"]),
            row_index=int(data.get("row_index", 0)),
            first_seen_at=float(data.get("first_seen_at", time.time())),
            last_seen_at=float(data.get("last_seen_at", time.time())),
            record=dict(data.get("record", {})),
            count=int(data.get("count", 1)),
            fuzzy_text=data.get("fuzzy_text"),
            block_key=data.get("block_key"),
        )


class DeduplicationError(Exception):
    """Base deduplication error."""


class DeduplicationConfigError(DeduplicationError):
    """Invalid deduplication configuration."""


class DeduplicationStateError(DeduplicationError):
    """Deduplication state error."""


class DuplicateFoundError(DeduplicationError):
    """Raised when duplicate policy is ERROR."""


class DeduplicationEngine:
    """Enterprise deduplication engine."""

    def __init__(self, config: Optional[DeduplicationConfig] = None) -> None:
        self.config = config or DeduplicationConfig.from_env()
        self._seen: Dict[str, SeenRecord] = {}
        self._blocks: Dict[str, List[str]] = defaultdict(list)
        self._lock = threading.RLock()
        if self.config.state_snapshot_path:
            self.restore_state(self.config.state_snapshot_path)

    def deduplicate(
        self,
        rows: Iterable[Any],
        *,
        spec: DeduplicationSpec,
        incremental: bool = False,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> DeduplicationResult:
        spec.validate()
        started = time.perf_counter()
        started_iso = utc_now_iso()
        input_count = 0
        duplicate_count = 0
        merged_count = 0
        dropped_count = 0
        error_count = 0
        output_rows: List[Dict[str, Any]] = []
        duplicates: List[DuplicateAuditRecord] = []
        local_seen: Dict[str, SeenRecord] = self._seen if incremental else {}
        local_blocks: Dict[str, List[str]] = self._blocks if incremental else defaultdict(list)

        with telemetry_operation("deduplication_engine.deduplicate", self.config.telemetry_enabled, attributes={"keys": spec.keys, "policy": spec.policy.value}):
            for index, raw in enumerate(rows):
                input_count += 1
                try:
                    row = dict(to_mapping(raw))
                    cleanup_expired(local_seen, local_blocks, spec)
                    exact_key = build_duplicate_key(row, spec)
                    duplicate_key, original, similarity, reason = self._find_duplicate(row, exact_key, spec, local_seen, local_blocks)

                    if original is None:
                        seen = build_seen_record(row, duplicate_key, index, spec)
                        self._ensure_capacity(local_seen)
                        local_seen[duplicate_key] = seen
                        if seen.block_key:
                            local_blocks[seen.block_key].append(duplicate_key)
                        output_rows.append(row)
                        continue

                    duplicate_count += 1
                    original.count += 1
                    original.last_seen_at = extract_event_time(row, spec) or time.time()

                    if spec.policy == DuplicatePolicy.ERROR:
                        error_count += 1
                        raise DuplicateFoundError(f"Duplicate detected for key={duplicate_key}")

                    if spec.policy in {DuplicatePolicy.KEEP_FIRST, DuplicatePolicy.DROP_DUPLICATES}:
                        dropped_count += 1
                        status = DuplicateStatus.DROPPED
                        audit = build_audit(duplicate_key, status, original.row_index, index, similarity, reason, original.record, row)
                        duplicates.append(audit)
                        continue

                    if spec.policy == DuplicatePolicy.KEEP_LAST:
                        replaced = replace_output_row(output_rows, original.record, row)
                        original.record = row
                        original.row_index = index
                        status = DuplicateStatus.DUPLICATE
                        audit = build_audit(duplicate_key, status, original.row_index, index, similarity, "keep_last", replaced, row)
                        duplicates.append(audit)
                        continue

                    if spec.policy == DuplicatePolicy.MARK:
                        marked = dict(row)
                        marked[spec.mark_field] = True
                        marked[spec.duplicate_key_field] = duplicate_key
                        output_rows.append(marked)
                        audit = build_audit(duplicate_key, DuplicateStatus.DUPLICATE, original.row_index, index, similarity, reason, original.record, marked)
                        duplicates.append(audit)
                        continue

                    if spec.policy == DuplicatePolicy.MERGE:
                        merged = merge_records(original.record, row, spec.merge_rules)
                        replace_output_row(output_rows, original.record, merged)
                        original.record = merged
                        merged_count += 1
                        audit = build_audit(duplicate_key, DuplicateStatus.MERGED, original.row_index, index, similarity, reason, original.record, row, merged)
                        duplicates.append(audit)
                        continue
                except Exception as exc:
                    error_count += 1
                    logger.debug("Deduplication row failed: %s", exc, exc_info=True)
                    if spec.policy == DuplicatePolicy.ERROR:
                        raise

        if incremental:
            with self._lock:
                self._seen = local_seen
                self._blocks = local_blocks

        if not self.config.include_rows:
            output_rows = []
        elif len(output_rows) > self.config.max_output_rows:
            output_rows = output_rows[: self.config.max_output_rows]

        duration_ms = (time.perf_counter() - started) * 1000.0
        status = DeduplicationResultStatus.EMPTY if input_count == 0 else DeduplicationResultStatus.PARTIAL if error_count else DeduplicationResultStatus.SUCCEEDED
        result = DeduplicationResult(
            id=str(uuid.uuid4()),
            status=status,
            started_at=started_iso,
            finished_at=utc_now_iso(),
            duration_ms=round(duration_ms, 3),
            input_count=input_count,
            output_count=len(output_rows),
            unique_count=max(0, input_count - duplicate_count - error_count),
            duplicate_count=duplicate_count,
            merged_count=merged_count,
            dropped_count=dropped_count,
            error_count=error_count,
            rows=output_rows,
            duplicates=duplicates if spec.include_duplicate_audit else [],
            metadata=sanitize_mapping({"spec": spec_to_dict(spec), **dict(metadata or {})}),
        )
        self._save_report(result)
        telemetry_metric("deduplication.input_count", input_count, self.config.telemetry_enabled)
        telemetry_metric("deduplication.duplicate_count", duplicate_count, self.config.telemetry_enabled)
        telemetry_metric("deduplication.output_count", len(output_rows), self.config.telemetry_enabled)
        telemetry_metric("deduplication.duration_ms", duration_ms, self.config.telemetry_enabled)
        return result

    def update_one(self, row: Any, *, spec: DeduplicationSpec) -> DeduplicationResult:
        return self.deduplicate([row], spec=spec, incremental=True)

    def snapshot_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "created_at": utc_now_iso(),
                "seen_count": len(self._seen),
                "seen": {key: value.to_dict() for key, value in self._seen.items()},
                "blocks": {key: list(values) for key, values in self._blocks.items()},
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
            with self._lock:
                self._seen = {key: SeenRecord.from_dict(value) for key, value in dict(payload.get("seen", {})).items()}
                self._blocks = defaultdict(list, {key: list(values) for key, values in dict(payload.get("blocks", {})).items()})
        except Exception as exc:
            logger.warning("Failed to restore deduplication state from %s: %s", target, exc)

    def clear_state(self) -> None:
        with self._lock:
            self._seen.clear()
            self._blocks.clear()

    def _find_duplicate(
        self,
        row: Mapping[str, Any],
        exact_key: str,
        spec: DeduplicationSpec,
        seen: Mapping[str, SeenRecord],
        blocks: Mapping[str, List[str]],
    ) -> Tuple[str, Optional[SeenRecord], Optional[float], str]:
        if exact_key in seen:
            return exact_key, seen[exact_key], 1.0, "exact_key_match"
        if not spec.fuzzy.enabled:
            return exact_key, None, None, "unique"

        block_key = build_block_key(row, spec)
        candidate_keys = blocks.get(block_key, [])[: spec.fuzzy.max_candidates_per_block] if block_key else list(seen.keys())[: spec.fuzzy.max_candidates_per_block]
        current_text = build_fuzzy_text(row, spec)
        best: Tuple[Optional[str], Optional[SeenRecord], float] = (None, None, 0.0)
        for candidate_key in candidate_keys:
            candidate = seen.get(candidate_key)
            if not candidate or not candidate.fuzzy_text:
                continue
            sim = similarity(current_text, candidate.fuzzy_text, spec.fuzzy.method)
            if sim > best[2]:
                best = (candidate_key, candidate, sim)
        if best[1] is not None and best[2] >= spec.fuzzy.threshold:
            return best[0] or exact_key, best[1], best[2], f"fuzzy_match:{spec.fuzzy.method.value}"
        return exact_key, None, best[2] if best[2] else None, "unique"

    def _ensure_capacity(self, seen: Mapping[str, SeenRecord]) -> None:
        if len(seen) < self.config.max_state_keys:
            return
        if self.config.fail_on_state_limit:
            raise DeduplicationStateError(f"max_state_keys exceeded: {self.config.max_state_keys}")

    def _save_report(self, result: DeduplicationResult) -> None:
        if not self.config.report_path:
            return
        target = Path(self.config.report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(result.to_json(indent=2), encoding="utf-8")
        tmp.replace(target)


def build_duplicate_key(row: Mapping[str, Any], spec: DeduplicationSpec) -> str:
    values = [normalize_key_value(get_field(row, key), spec) for key in spec.keys]
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True, default=safe_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_seen_record(row: Mapping[str, Any], duplicate_key: str, index: int, spec: DeduplicationSpec) -> SeenRecord:
    now = extract_event_time(row, spec) or time.time()
    return SeenRecord(
        duplicate_key=duplicate_key,
        row_index=index,
        first_seen_at=now,
        last_seen_at=now,
        record=dict(row),
        fuzzy_text=build_fuzzy_text(row, spec) if spec.fuzzy.enabled else None,
        block_key=build_block_key(row, spec) if spec.fuzzy.enabled else None,
    )


def build_fuzzy_text(row: Mapping[str, Any], spec: DeduplicationSpec) -> str:
    values = [normalize_text(get_field(row, field), case_sensitive=False) for field in spec.fuzzy.fields]
    return " ".join(v for v in values if v)


def build_block_key(row: Mapping[str, Any], spec: DeduplicationSpec) -> Optional[str]:
    if not spec.fuzzy.block_keys:
        return None
    values = [normalize_key_value(get_field(row, field), spec) for field in spec.fuzzy.block_keys]
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True, default=safe_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def normalize_key_value(value: Any, spec: DeduplicationSpec) -> Any:
    if isinstance(value, str):
        text = value.strip() if spec.trim_strings else value
        if spec.normalize_whitespace:
            text = re.sub(r"\s+", " ", text)
        if not spec.case_sensitive:
            text = text.lower()
        return text
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def normalize_text(value: Any, *, case_sensitive: bool = False) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not case_sensitive:
        text = text.lower()
    text = NON_ALNUM_PATTERN.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(left: str, right: str, method: SimilarityMethod) -> float:
    if method == SimilarityMethod.EXACT:
        return 1.0 if left == right else 0.0
    if method == SimilarityMethod.NORMALIZED:
        return 1.0 if left == right else levenshtein_similarity(left, right)
    if method == SimilarityMethod.TOKEN_SET:
        return token_set_similarity(left, right)
    if method == SimilarityMethod.LEVENSHTEIN:
        return levenshtein_similarity(left, right)
    return 0.0


def token_set_similarity(left: str, right: str) -> float:
    a = set(left.split())
    b = set(right.split())
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def levenshtein_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    distance = levenshtein_distance(left, right)
    return max(0.0, 1.0 - distance / max(len(left), len(right)))


def levenshtein_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, c1 in enumerate(left, start=1):
        current = [i]
        for j, c2 in enumerate(right, start=1):
            insert = current[j - 1] + 1
            delete = previous[j] + 1
            replace = previous[j - 1] + (0 if c1 == c2 else 1)
            current.append(min(insert, delete, replace))
        previous = current
    return previous[-1]


def merge_records(original: Mapping[str, Any], duplicate: Mapping[str, Any], rules: Sequence[MergeRule]) -> Dict[str, Any]:
    merged = dict(original)
    rule_by_field = {rule.field: rule for rule in rules}
    fields = set(original) | set(duplicate)
    for field in fields:
        left = original.get(field)
        right = duplicate.get(field)
        rule = rule_by_field.get(field, MergeRule(field=field, strategy=MergeStrategy.LAST_NON_NULL))
        merged[field] = merge_value(left, right, rule)
    return sanitize_mapping(merged)


def merge_value(left: Any, right: Any, rule: MergeRule) -> Any:
    if rule.strategy == MergeStrategy.FIRST_NON_NULL:
        return left if left is not None else right
    if rule.strategy == MergeStrategy.LAST_NON_NULL:
        return right if right is not None else left
    if rule.strategy == MergeStrategy.MAX:
        values = [v for v in (left, right) if v is not None]
        return max(values) if values else None
    if rule.strategy == MergeStrategy.MIN:
        values = [v for v in (left, right) if v is not None]
        return min(values) if values else None
    if rule.strategy == MergeStrategy.SUM:
        return to_number(left) + to_number(right)
    if rule.strategy == MergeStrategy.LIST:
        return flatten_list(left) + flatten_list(right)
    if rule.strategy == MergeStrategy.SET:
        return sorted({json_hashable(v) for v in flatten_list(left) + flatten_list(right)}, key=str)
    if rule.strategy == MergeStrategy.CONCAT:
        return rule.separator.join(str(v) for v in (left, right) if v not in (None, ""))
    if rule.strategy == MergeStrategy.CUSTOM and rule.custom_function:
        return rule.custom_function(left, right)
    return right if right is not None else left


def replace_output_row(rows: List[Dict[str, Any]], old: Mapping[str, Any], new: Mapping[str, Any]) -> Dict[str, Any]:
    for index, row in enumerate(rows):
        if row is old or row == old:
            previous = rows[index]
            rows[index] = dict(new)
            return previous
    rows.append(dict(new))
    return dict(old)


def build_audit(
    duplicate_key: str,
    status: DuplicateStatus,
    original_index: Optional[int],
    duplicate_index: int,
    similarity_score: Optional[float],
    reason: str,
    original: Optional[Mapping[str, Any]],
    duplicate: Optional[Mapping[str, Any]],
    merged: Optional[Mapping[str, Any]] = None,
) -> DuplicateAuditRecord:
    return DuplicateAuditRecord(
        id=str(uuid.uuid4()),
        timestamp=utc_now_iso(),
        duplicate_key=duplicate_key,
        status=status,
        original_index=original_index,
        duplicate_index=duplicate_index,
        similarity=round(similarity_score, 6) if similarity_score is not None else None,
        reason=reason,
        original_record=sanitize_mapping(dict(original or {})),
        duplicate_record=sanitize_mapping(dict(duplicate or {})),
        merged_record=sanitize_mapping(dict(merged or {})) if merged else None,
    )


def cleanup_expired(seen: Dict[str, SeenRecord], blocks: Dict[str, List[str]], spec: DeduplicationSpec) -> None:
    if not spec.ttl_seconds:
        return
    cutoff = time.time() - spec.ttl_seconds
    expired = [key for key, record in seen.items() if record.last_seen_at < cutoff]
    for key in expired:
        record = seen.pop(key, None)
        if record and record.block_key in blocks:
            blocks[record.block_key] = [item for item in blocks[record.block_key] if item != key]


def extract_event_time(row: Mapping[str, Any], spec: DeduplicationSpec) -> Optional[float]:
    if not spec.timestamp_field:
        return None
    value = get_field(row, spec.timestamp_field)
    if value is None:
        return None
    return normalize_timestamp(value)


def normalize_timestamp(value: Any) -> float:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    if isinstance(value, (int, float)):
        raw = float(value)
        return raw / 1000.0 if raw > 10_000_000_000 else raw
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    return time.time()


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


def to_mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if dataclasses.is_dataclass(row):
        return asdict(row)
    if hasattr(row, "_asdict"):
        return row._asdict()
    if hasattr(row, "__dict__"):
        return vars(row)
    raise DeduplicationConfigError(f"Unsupported row type: {type(row)!r}")


def to_number(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        number = float(value)
        return 0.0 if math.isnan(number) or math.isinf(number) else number
    except Exception:
        return 0.0


def flatten_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def json_hashable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=safe_json_default)
    except Exception:
        return str(value)


def spec_to_dict(spec: DeduplicationSpec) -> Dict[str, Any]:
    data = asdict(spec)
    data["policy"] = spec.policy.value
    data["fuzzy"]["method"] = spec.fuzzy.method.value
    data["merge_rules"] = [
        {k: (v.value if isinstance(v, Enum) else None if callable(v) else v) for k, v in asdict(rule).items()}
        for rule in spec.merge_rules
    ]
    return sanitize_mapping(data)


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
        elif isinstance(value, (list, tuple, set)):
            output[key_str] = [sanitize_value(item, depth=depth + 1) for item in list(value)[:10_000]]
        else:
            output[key_str] = sanitize_value(value, depth=depth)
    return output


def sanitize_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
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
        text = text[: MAX_TEXT_LENGTH - 15] + "...[truncated]"
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
        logger.debug("Deduplication telemetry metric failed", exc_info=True)


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


def bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "DeduplicationConfig",
    "DeduplicationConfigError",
    "DeduplicationEngine",
    "DeduplicationError",
    "DeduplicationResult",
    "DeduplicationResultStatus",
    "DeduplicationSpec",
    "DeduplicationStateError",
    "DuplicateAuditRecord",
    "DuplicateFoundError",
    "DuplicatePolicy",
    "DuplicateStatus",
    "FuzzySpec",
    "MergeRule",
    "MergeStrategy",
    "SeenRecord",
    "SimilarityMethod",
    "build_duplicate_key",
    "levenshtein_similarity",
    "similarity",
    "token_set_similarity",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    rows = [
        {"id": 1, "email": "a@example.com", "name": "João Silva", "amount": 10},
        {"id": 1, "email": "a@example.com", "name": "Joao Silva", "amount": 15},
        {"id": 2, "email": "b@example.com", "name": "Maria", "amount": 20},
    ]
    engine = DeduplicationEngine(DeduplicationConfig(telemetry_enabled=False))
    result = engine.deduplicate(
        rows,
        spec=DeduplicationSpec(
            keys=("id", "email"),
            policy=DuplicatePolicy.MERGE,
            merge_rules=(MergeRule("amount", MergeStrategy.SUM),),
        ),
    )
    print(result.to_json())

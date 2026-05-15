"""
data/processing/outlier_detection.py

Enterprise-grade outlier detection and treatment module for data platforms.

Purpose
-------
Provides a dependency-light engine for detecting, auditing and optionally
handling outliers in batch, micro-batch and data quality pipelines.

Core capabilities
-----------------
- Univariate detection: z-score, robust MAD, IQR, percentile bounds, static bounds.
- Multivariate-lite detection through distance from standardized numeric centroid.
- Field-level detection rules and treatment policies.
- Actions: mark, drop, null, clip/winsorize, cap to bounds, keep.
- Per-group baselines with configurable group keys.
- Fit/transform workflow and one-shot detect workflow.
- JSON snapshot/restore for learned baselines.
- Audit records with score, bounds, method and explanation.
- Safe metadata sanitization.
- Optional telemetry integration.
- Standard library only.

Example
-------
engine = OutlierDetectionEngine()
result = engine.detect(
    rows,
    rules=[OutlierRule(field="amount", method=OutlierMethod.IQR, threshold=1.5)],
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
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|session|jwt|bearer)",
    re.IGNORECASE,
)

MAX_TEXT_LENGTH = 16_384
EPSILON = 1e-12
DEFAULT_MIN_BASELINE_SIZE = 10


class OutlierMethod(str, Enum):
    ZSCORE = "zscore"
    ROBUST_ZSCORE = "robust_zscore"
    IQR = "iqr"
    PERCENTILE = "percentile"
    STATIC_BOUNDS = "static_bounds"
    MODIFIED_ZSCORE = "modified_zscore"
    MULTIVARIATE_DISTANCE = "multivariate_distance"
    CUSTOM = "custom"


class OutlierDirection(str, Enum):
    LOW = "low"
    HIGH = "high"
    BOTH = "both"


class OutlierAction(str, Enum):
    KEEP = "keep"
    MARK = "mark"
    DROP_ROW = "drop_row"
    NULL_FIELD = "null_field"
    CLIP = "clip"
    WINSORIZE = "winsorize"
    ERROR = "error"


class OutlierSeverity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class OutlierStatus(str, Enum):
    NORMAL = "normal"
    OUTLIER = "outlier"
    INSUFFICIENT_BASELINE = "insufficient_baseline"
    ERROR = "error"


class DetectionResultStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    EMPTY = "empty"


@dataclass(frozen=True)
class OutlierRule:
    field: str
    method: OutlierMethod = OutlierMethod.IQR
    threshold: float = 1.5
    direction: OutlierDirection = OutlierDirection.BOTH
    action: OutlierAction = OutlierAction.MARK
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    lower_percentile: float = 1.0
    upper_percentile: float = 99.0
    group_by: Tuple[str, ...] = field(default_factory=tuple)
    min_baseline_size: int = DEFAULT_MIN_BASELINE_SIZE
    output_flag_field: Optional[str] = None
    output_score_field: Optional[str] = None
    custom_function: Optional[Callable[[float, Sequence[float], Mapping[str, Any]], Tuple[bool, float, str]]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.field and self.method != OutlierMethod.MULTIVARIATE_DISTANCE:
            raise OutlierConfigError("OutlierRule.field is required")
        if self.threshold <= 0 and self.method not in {OutlierMethod.STATIC_BOUNDS, OutlierMethod.CUSTOM}:
            raise OutlierConfigError("threshold must be positive")
        if self.method == OutlierMethod.STATIC_BOUNDS and self.lower_bound is None and self.upper_bound is None:
            raise OutlierConfigError("STATIC_BOUNDS requires lower_bound and/or upper_bound")
        if self.method == OutlierMethod.PERCENTILE and not (0 <= self.lower_percentile <= self.upper_percentile <= 100):
            raise OutlierConfigError("percentiles must satisfy 0 <= lower <= upper <= 100")
        if self.method == OutlierMethod.CUSTOM and not self.custom_function:
            raise OutlierConfigError("CUSTOM method requires custom_function")


@dataclass(frozen=True)
class MultivariateRule:
    fields: Tuple[str, ...]
    threshold: float = 3.0
    action: OutlierAction = OutlierAction.MARK
    group_by: Tuple[str, ...] = field(default_factory=tuple)
    min_baseline_size: int = DEFAULT_MIN_BASELINE_SIZE
    output_flag_field: str = "_multivariate_outlier"
    output_score_field: str = "_multivariate_outlier_score"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if len(self.fields) < 2:
            raise OutlierConfigError("MultivariateRule requires at least two fields")
        if self.threshold <= 0:
            raise OutlierConfigError("multivariate threshold must be positive")


@dataclass(frozen=True)
class OutlierDetectionConfig:
    telemetry_enabled: bool = True
    include_rows: bool = True
    include_audit: bool = True
    include_errors: bool = True
    max_output_rows: int = 1_000_000
    fail_fast: bool = False
    state_snapshot_path: Optional[str] = None
    report_path: Optional[str] = None

    @classmethod
    def from_env(cls) -> "OutlierDetectionConfig":
        return cls(
            telemetry_enabled=bool_env("OUTLIER_TELEMETRY_ENABLED", True),
            include_rows=bool_env("OUTLIER_INCLUDE_ROWS", True),
            include_audit=bool_env("OUTLIER_INCLUDE_AUDIT", True),
            include_errors=bool_env("OUTLIER_INCLUDE_ERRORS", True),
            max_output_rows=int_env("OUTLIER_MAX_OUTPUT_ROWS", 1_000_000),
            fail_fast=bool_env("OUTLIER_FAIL_FAST", False),
            state_snapshot_path=os.getenv("OUTLIER_STATE_SNAPSHOT_PATH"),
            report_path=os.getenv("OUTLIER_REPORT_PATH"),
        )


@dataclass(frozen=True)
class FieldBaseline:
    group_key: Tuple[Any, ...]
    field: str
    count: int
    mean: Optional[float]
    stddev: Optional[float]
    median: Optional[float]
    mad: Optional[float]
    q1: Optional[float]
    q3: Optional[float]
    iqr: Optional[float]
    min_value: Optional[float]
    max_value: Optional[float]
    percentiles: Dict[str, float] = field(default_factory=dict)
    updated_at: str = field(default_factory=lambda: utc_now_iso())

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))

    @classmethod
    def from_values(cls, field: str, values: Sequence[float], group_key: Tuple[Any, ...] = ()) -> "FieldBaseline":
        sorted_values = sorted(v for v in values if is_valid_number(v))
        if not sorted_values:
            return cls(group_key=group_key, field=field, count=0, mean=None, stddev=None, median=None, mad=None, q1=None, q3=None, iqr=None, min_value=None, max_value=None)
        median = statistics.median(sorted_values)
        mad = statistics.median([abs(v - median) for v in sorted_values]) if sorted_values else 0.0
        q1 = percentile(sorted_values, 25)
        q3 = percentile(sorted_values, 75)
        return cls(
            group_key=group_key,
            field=field,
            count=len(sorted_values),
            mean=statistics.fmean(sorted_values),
            stddev=statistics.pstdev(sorted_values) if len(sorted_values) > 1 else 0.0,
            median=median,
            mad=mad,
            q1=q1,
            q3=q3,
            iqr=q3 - q1,
            min_value=min(sorted_values),
            max_value=max(sorted_values),
            percentiles={
                "p01": percentile(sorted_values, 1),
                "p05": percentile(sorted_values, 5),
                "p95": percentile(sorted_values, 95),
                "p99": percentile(sorted_values, 99),
            },
        )


@dataclass(frozen=True)
class OutlierAuditRecord:
    id: str
    timestamp: str
    row_index: int
    field: str
    value: Optional[float]
    status: OutlierStatus
    severity: OutlierSeverity
    method: OutlierMethod
    score: float
    lower_bound: Optional[float]
    upper_bound: Optional[float]
    action: OutlierAction
    explanation: str
    group_key: Tuple[Any, ...] = field(default_factory=tuple)
    original_value: Any = None
    treated_value: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["severity"] = self.severity.value
        data["method"] = self.method.value
        data["action"] = self.action.value
        data["group_key"] = list(self.group_key)
        return sanitize_mapping(data)


@dataclass(frozen=True)
class OutlierErrorRecord:
    id: str
    timestamp: str
    row_index: int
    field: Optional[str]
    error_type: str
    error_message: str
    row: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))


@dataclass(frozen=True)
class OutlierDetectionResult:
    id: str
    status: DetectionResultStatus
    started_at: str
    finished_at: str
    duration_ms: float
    input_count: int
    output_count: int
    evaluated_count: int
    outlier_count: int
    treated_count: int
    dropped_count: int
    error_count: int
    rows: List[Dict[str, Any]] = field(default_factory=list)
    audit: List[OutlierAuditRecord] = field(default_factory=list)
    errors: List[OutlierErrorRecord] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["audit"] = [item.to_dict() for item in self.audit]
        data["errors"] = [item.to_dict() for item in self.errors]
        return sanitize_mapping(data)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True, default=safe_json_default)


class OutlierDetectionError(Exception):
    """Base outlier detection error."""


class OutlierConfigError(OutlierDetectionError):
    """Invalid outlier configuration."""


class OutlierInputError(OutlierDetectionError):
    """Invalid outlier input."""


class OutlierTreatmentError(OutlierDetectionError):
    """Outlier treatment failed."""


class OutlierDetector(Protocol):
    def evaluate(self, value: float, baseline: FieldBaseline, rule: OutlierRule, row: Mapping[str, Any]) -> Tuple[bool, float, Optional[float], Optional[float], str]:
        ...


class OutlierDetectionEngine:
    """Enterprise outlier detection and treatment engine."""

    def __init__(self, config: Optional[OutlierDetectionConfig] = None) -> None:
        self.config = config or OutlierDetectionConfig.from_env()
        self._baselines: Dict[str, FieldBaseline] = {}
        if self.config.state_snapshot_path:
            self.restore_state(self.config.state_snapshot_path)

    def fit(
        self,
        rows: Iterable[Any],
        *,
        rules: Sequence[OutlierRule],
        multivariate_rules: Optional[Sequence[MultivariateRule]] = None,
    ) -> Dict[str, FieldBaseline]:
        materialized = [dict(to_mapping(row)) for row in rows]
        baselines = build_baselines(materialized, rules, multivariate_rules or [])
        self._baselines.update(baselines)
        return baselines

    def detect(
        self,
        rows: Iterable[Any],
        *,
        rules: Sequence[OutlierRule],
        multivariate_rules: Optional[Sequence[MultivariateRule]] = None,
        fit_baseline: bool = True,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> OutlierDetectionResult:
        for rule in rules:
            rule.validate()
        for rule in multivariate_rules or []:
            rule.validate()

        started = monotonic_ms()
        started_iso = utc_now_iso()
        materialized = [dict(to_mapping(row)) for row in rows]
        if fit_baseline:
            self.fit(materialized, rules=rules, multivariate_rules=multivariate_rules or [])

        output_rows: List[Dict[str, Any]] = []
        audit: List[OutlierAuditRecord] = []
        errors: List[OutlierErrorRecord] = []
        evaluated_count = 0
        outlier_count = 0
        treated_count = 0
        dropped_count = 0

        with telemetry_operation("outlier_detection.detect", self.config.telemetry_enabled, attributes={"rules": [r.field for r in rules]}):
            for row_index, row in enumerate(materialized):
                current = dict(row)
                drop_row = False
                try:
                    for rule in rules:
                        group_key = build_group_key(current, rule.group_by)
                        baseline = self._baselines.get(baseline_key(rule.field, group_key))
                        evaluated_count += 1
                        record, current, dropped, treated = evaluate_and_apply_rule(current, row_index, rule, baseline)
                        audit.append(record)
                        if record.status == OutlierStatus.OUTLIER:
                            outlier_count += 1
                        if treated:
                            treated_count += 1
                        if dropped:
                            drop_row = True
                            dropped_count += 1
                            break

                    if not drop_row:
                        for mv_rule in multivariate_rules or []:
                            evaluated_count += 1
                            record, current, dropped, treated = evaluate_and_apply_multivariate(current, row_index, mv_rule, self._baselines)
                            audit.append(record)
                            if record.status == OutlierStatus.OUTLIER:
                                outlier_count += 1
                            if treated:
                                treated_count += 1
                            if dropped:
                                drop_row = True
                                dropped_count += 1
                                break

                    if not drop_row and self.config.include_rows and len(output_rows) < self.config.max_output_rows:
                        output_rows.append(current)
                except Exception as exc:
                    errors.append(OutlierErrorRecord(str(uuid.uuid4()), utc_now_iso(), row_index, None, exc.__class__.__name__, str(exc), sanitize_mapping(row)))
                    if self.config.fail_fast:
                        raise

        duration_ms = monotonic_ms() - started
        status = determine_status(len(materialized), output_rows, errors)
        result = OutlierDetectionResult(
            id=str(uuid.uuid4()),
            status=status,
            started_at=started_iso,
            finished_at=utc_now_iso(),
            duration_ms=round(duration_ms, 3),
            input_count=len(materialized),
            output_count=len(output_rows),
            evaluated_count=evaluated_count,
            outlier_count=outlier_count,
            treated_count=treated_count,
            dropped_count=dropped_count,
            error_count=len(errors),
            rows=output_rows if self.config.include_rows else [],
            audit=audit if self.config.include_audit else [],
            errors=errors if self.config.include_errors else [],
            metadata=sanitize_mapping(dict(metadata or {})),
        )
        self._save_report(result)
        telemetry_metric("outlier_detection.input_count", len(materialized), self.config.telemetry_enabled)
        telemetry_metric("outlier_detection.outlier_count", outlier_count, self.config.telemetry_enabled)
        telemetry_metric("outlier_detection.duration_ms", duration_ms, self.config.telemetry_enabled)
        return result

    def snapshot_state(self) -> Dict[str, Any]:
        return {
            "created_at": utc_now_iso(),
            "baseline_count": len(self._baselines),
            "baselines": {key: baseline.to_dict() for key, baseline in self._baselines.items()},
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
        payload = json.loads(target.read_text(encoding="utf-8"))
        restored = {}
        for key, raw in dict(payload.get("baselines", {})).items():
            restored[str(key)] = FieldBaseline(
                group_key=tuple(raw.get("group_key", [])),
                field=str(raw.get("field", "")),
                count=int(raw.get("count", 0)),
                mean=to_optional_float(raw.get("mean")),
                stddev=to_optional_float(raw.get("stddev")),
                median=to_optional_float(raw.get("median")),
                mad=to_optional_float(raw.get("mad")),
                q1=to_optional_float(raw.get("q1")),
                q3=to_optional_float(raw.get("q3")),
                iqr=to_optional_float(raw.get("iqr")),
                min_value=to_optional_float(raw.get("min_value")),
                max_value=to_optional_float(raw.get("max_value")),
                percentiles={str(k): float(v) for k, v in dict(raw.get("percentiles", {})).items()},
                updated_at=str(raw.get("updated_at", utc_now_iso())),
            )
        self._baselines = restored

    def clear_state(self) -> None:
        self._baselines.clear()

    def _save_report(self, result: OutlierDetectionResult) -> None:
        if not self.config.report_path:
            return
        target = Path(self.config.report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(result.to_json(indent=2), encoding="utf-8")
        tmp.replace(target)


def build_baselines(rows: Sequence[Mapping[str, Any]], rules: Sequence[OutlierRule], multivariate_rules: Sequence[MultivariateRule]) -> Dict[str, FieldBaseline]:
    grouped: Dict[Tuple[str, Tuple[Any, ...]], List[float]] = defaultdict(list)
    for row in rows:
        for rule in rules:
            value = get_field(row, rule.field)
            if value is None:
                continue
            try:
                grouped[(rule.field, build_group_key(row, rule.group_by))].append(to_number(value))
            except Exception:
                continue
        for rule in multivariate_rules:
            group_key = build_group_key(row, rule.group_by)
            for field_name in rule.fields:
                value = get_field(row, field_name)
                if value is None:
                    continue
                try:
                    grouped[(field_name, group_key)].append(to_number(value))
                except Exception:
                    continue
    return {baseline_key(field, group): FieldBaseline.from_values(field, values, group) for (field, group), values in grouped.items()}


def evaluate_and_apply_rule(row: Dict[str, Any], row_index: int, rule: OutlierRule, baseline: Optional[FieldBaseline]) -> Tuple[OutlierAuditRecord, Dict[str, Any], bool, bool]:
    group_key = build_group_key(row, rule.group_by)
    original = get_field(row, rule.field)
    value = to_number(original)
    if baseline is None or baseline.count < rule.min_baseline_size:
        record = build_audit(row_index, rule.field, value, OutlierStatus.INSUFFICIENT_BASELINE, OutlierSeverity.NONE, rule.method, 0.0, None, None, rule.action, "Insufficient baseline", group_key, original, original, rule.metadata)
        return record, row, False, False
    is_outlier, score, lower, upper, explanation = evaluate_value(value, baseline, rule, row)
    severity = classify_severity(score, is_outlier)
    treated_value = original
    dropped = False
    treated = False
    if is_outlier:
        row, treated_value, dropped, treated = apply_action(row, rule.field, value, lower, upper, rule)
    record = build_audit(row_index, rule.field, value, OutlierStatus.OUTLIER if is_outlier else OutlierStatus.NORMAL, severity, rule.method, score, lower, upper, rule.action, explanation, group_key, original, treated_value, rule.metadata)
    return record, row, dropped, treated


def evaluate_and_apply_multivariate(row: Dict[str, Any], row_index: int, rule: MultivariateRule, baselines: Mapping[str, FieldBaseline]) -> Tuple[OutlierAuditRecord, Dict[str, Any], bool, bool]:
    group_key = build_group_key(row, rule.group_by)
    z_values = []
    for field_name in rule.fields:
        baseline = baselines.get(baseline_key(field_name, group_key))
        if baseline is None or baseline.count < rule.min_baseline_size:
            record = build_audit(row_index, ",".join(rule.fields), None, OutlierStatus.INSUFFICIENT_BASELINE, OutlierSeverity.NONE, OutlierMethod.MULTIVARIATE_DISTANCE, 0.0, None, None, rule.action, "Insufficient multivariate baseline", group_key, None, None, rule.metadata)
            return record, row, False, False
        value = to_number(get_field(row, field_name))
        z_values.append((value - (baseline.mean or 0.0)) / max(baseline.stddev or 0.0, EPSILON))
    distance = math.sqrt(sum(z * z for z in z_values))
    is_outlier = distance > rule.threshold
    severity = classify_severity(distance, is_outlier)
    dropped = False
    treated = False
    if is_outlier:
        if rule.action == OutlierAction.DROP_ROW:
            dropped = True
            treated = True
        elif rule.action == OutlierAction.MARK:
            row[rule.output_flag_field] = True
            row[rule.output_score_field] = round(distance, 6)
            treated = True
        elif rule.action == OutlierAction.ERROR:
            raise OutlierTreatmentError("multivariate outlier detected")
    record = build_audit(row_index, ",".join(rule.fields), distance, OutlierStatus.OUTLIER if is_outlier else OutlierStatus.NORMAL, severity, OutlierMethod.MULTIVARIATE_DISTANCE, distance, None, rule.threshold, rule.action, f"multivariate standardized distance={distance:.4f}", group_key, None, None, rule.metadata)
    return record, row, dropped, treated


def evaluate_value(value: float, baseline: FieldBaseline, rule: OutlierRule, row: Mapping[str, Any]) -> Tuple[bool, float, Optional[float], Optional[float], str]:
    if rule.method == OutlierMethod.STATIC_BOUNDS:
        lower = rule.lower_bound
        upper = rule.upper_bound
        score = bound_score(value, lower, upper)
        return directional_outside(value, lower, upper, rule.direction), score, lower, upper, f"static bounds lower={lower}, upper={upper}"
    if rule.method == OutlierMethod.ZSCORE:
        mean = baseline.mean or 0.0
        std = max(baseline.stddev or 0.0, EPSILON)
        z = (value - mean) / std
        lower = mean - rule.threshold * std
        upper = mean + rule.threshold * std
        return directional_score(z, rule), abs(z), lower, upper, f"zscore={z:.4f}, mean={mean:.4f}, std={std:.4f}"
    if rule.method in {OutlierMethod.ROBUST_ZSCORE, OutlierMethod.MODIFIED_ZSCORE}:
        median = baseline.median or 0.0
        mad = max(baseline.mad or 0.0, EPSILON)
        modified_z = 0.6745 * (value - median) / mad
        lower = median - (rule.threshold / 0.6745) * mad
        upper = median + (rule.threshold / 0.6745) * mad
        return directional_score(modified_z, rule), abs(modified_z), lower, upper, f"modified_z={modified_z:.4f}, median={median:.4f}, mad={mad:.4f}"
    if rule.method == OutlierMethod.IQR:
        q1 = baseline.q1 or 0.0
        q3 = baseline.q3 or 0.0
        iqr = baseline.iqr or 0.0
        lower = q1 - rule.threshold * iqr
        upper = q3 + rule.threshold * iqr
        score = bound_score(value, lower, upper)
        return directional_outside(value, lower, upper, rule.direction), score, lower, upper, f"IQR lower={lower:.4f}, upper={upper:.4f}"
    if rule.method == OutlierMethod.PERCENTILE:
        lower = percentile_from_baseline(baseline, rule.lower_percentile)
        upper = percentile_from_baseline(baseline, rule.upper_percentile)
        score = bound_score(value, lower, upper)
        return directional_outside(value, lower, upper, rule.direction), score, lower, upper, f"percentile bounds p{rule.lower_percentile}=lower, p{rule.upper_percentile}=upper"
    if rule.method == OutlierMethod.CUSTOM and rule.custom_function:
        outlier, score, explanation = rule.custom_function(value, [], row)
        return outlier, score, None, None, explanation
    return False, 0.0, None, None, "No method matched"


def apply_action(row: Dict[str, Any], field: str, value: float, lower: Optional[float], upper: Optional[float], rule: OutlierRule) -> Tuple[Dict[str, Any], Any, bool, bool]:
    if rule.action == OutlierAction.KEEP:
        return row, get_field(row, field), False, False
    if rule.action == OutlierAction.MARK:
        row[rule.output_flag_field or f"_{field}_outlier"] = True
        row[rule.output_score_field or f"_{field}_outlier_score"] = True
        return row, get_field(row, field), False, True
    if rule.action == OutlierAction.DROP_ROW:
        return row, get_field(row, field), True, True
    if rule.action == OutlierAction.NULL_FIELD:
        set_field(row, field, None)
        return row, None, False, True
    if rule.action in {OutlierAction.CLIP, OutlierAction.WINSORIZE}:
        clipped = value
        if lower is not None:
            clipped = max(clipped, lower)
        if upper is not None:
            clipped = min(clipped, upper)
        set_field(row, field, clipped)
        return row, clipped, False, True
    if rule.action == OutlierAction.ERROR:
        raise OutlierTreatmentError(f"outlier detected for field={field}")
    return row, get_field(row, field), False, False


def build_audit(row_index: int, field: str, value: Optional[float], status: OutlierStatus, severity: OutlierSeverity, method: OutlierMethod, score: float, lower: Optional[float], upper: Optional[float], action: OutlierAction, explanation: str, group_key: Tuple[Any, ...], original: Any, treated: Any, metadata: Mapping[str, Any]) -> OutlierAuditRecord:
    return OutlierAuditRecord(
        id=str(uuid.uuid4()),
        timestamp=utc_now_iso(),
        row_index=row_index,
        field=field,
        value=round(value, 6) if value is not None and is_valid_number(value) else None,
        status=status,
        severity=severity,
        method=method,
        score=round(score, 6),
        lower_bound=round(lower, 6) if lower is not None and is_valid_number(lower) else None,
        upper_bound=round(upper, 6) if upper is not None and is_valid_number(upper) else None,
        action=action,
        explanation=truncate_text(explanation, 2048),
        group_key=group_key,
        original_value=sanitize_value(original),
        treated_value=sanitize_value(treated),
        metadata=sanitize_mapping(metadata),
    )


def classify_severity(score: float, is_outlier: bool) -> OutlierSeverity:
    if not is_outlier:
        return OutlierSeverity.NONE
    if score >= 10:
        return OutlierSeverity.CRITICAL
    if score >= 6:
        return OutlierSeverity.HIGH
    if score >= 3:
        return OutlierSeverity.MEDIUM
    return OutlierSeverity.LOW


def directional_score(score: float, rule: OutlierRule) -> bool:
    if rule.direction == OutlierDirection.HIGH:
        return score > rule.threshold
    if rule.direction == OutlierDirection.LOW:
        return score < -rule.threshold
    return abs(score) > rule.threshold


def directional_outside(value: float, lower: Optional[float], upper: Optional[float], direction: OutlierDirection) -> bool:
    low = lower is not None and value < lower
    high = upper is not None and value > upper
    if direction == OutlierDirection.LOW:
        return low
    if direction == OutlierDirection.HIGH:
        return high
    return low or high


def bound_score(value: float, lower: Optional[float], upper: Optional[float]) -> float:
    if upper is not None and value > upper:
        return abs(value - upper) / max(abs(upper), 1.0)
    if lower is not None and value < lower:
        return abs(lower - value) / max(abs(lower), 1.0)
    return 0.0


def percentile_from_baseline(baseline: FieldBaseline, p: float) -> Optional[float]:
    if p == 1:
        return baseline.percentiles.get("p01")
    if p == 5:
        return baseline.percentiles.get("p05")
    if p == 95:
        return baseline.percentiles.get("p95")
    if p == 99:
        return baseline.percentiles.get("p99")
    if p <= 0:
        return baseline.min_value
    if p >= 100:
        return baseline.max_value
    return None


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


def build_group_key(row: Mapping[str, Any], group_by: Sequence[str]) -> Tuple[Any, ...]:
    return tuple(sanitize_group_value(get_field(row, field)) for field in group_by)


def baseline_key(field: str, group_key: Tuple[Any, ...]) -> str:
    raw = json.dumps({"field": field, "group": group_key}, ensure_ascii=False, sort_keys=True, default=safe_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_field(row: Mapping[str, Any], field_path: str) -> Any:
    current: Any = row
    for part in field_path.split(".") if field_path else []:
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def set_field(row: Dict[str, Any], field_path: str, value: Any) -> None:
    parts = field_path.split(".")
    current: Dict[str, Any] = row
    for part in parts[:-1]:
        if not isinstance(current.get(part), dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def to_mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if dataclasses.is_dataclass(row):
        return asdict(row)
    if hasattr(row, "_asdict"):
        return row._asdict()
    if hasattr(row, "__dict__"):
        return vars(row)
    raise OutlierInputError(f"Unsupported row type: {type(row)!r}")


def to_number(value: Any) -> float:
    if value is None:
        raise OutlierInputError("missing numeric value")
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
    number = float(value)
    if not is_valid_number(number):
        raise OutlierInputError(f"invalid numeric value: {value!r}")
    return number


def is_valid_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isnan(float(value)) and not math.isinf(float(value))


def to_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def sanitize_group_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return truncate_text(str(value), 512)


def determine_status(input_count: int, rows: Sequence[Mapping[str, Any]], errors: Sequence[OutlierErrorRecord]) -> DetectionResultStatus:
    if input_count == 0:
        return DetectionResultStatus.EMPTY
    if errors and not rows:
        return DetectionResultStatus.FAILED
    if errors:
        return DetectionResultStatus.PARTIAL
    return DetectionResultStatus.SUCCEEDED


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
        if isinstance(value, float) and not is_valid_number(value):
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
    if isinstance(value, (list, tuple, set)):
        return [sanitize_value(item, depth=depth + 1) for item in list(value)[:10_000]]
    text = str(value)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)=([^\s&]+)", r"\1=[REDACTED]", text)
    return truncate_text(text, MAX_TEXT_LENGTH)


def truncate_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 15] + "...[truncated]"


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
        logger.debug("Outlier detection telemetry metric failed", exc_info=True)


def monotonic_ms() -> float:
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
    "DetectionResultStatus",
    "FieldBaseline",
    "MultivariateRule",
    "OutlierAction",
    "OutlierAuditRecord",
    "OutlierConfigError",
    "OutlierDetectionConfig",
    "OutlierDetectionEngine",
    "OutlierDetectionError",
    "OutlierDetectionResult",
    "OutlierDetector",
    "OutlierDirection",
    "OutlierErrorRecord",
    "OutlierInputError",
    "OutlierMethod",
    "OutlierRule",
    "OutlierSeverity",
    "OutlierStatus",
    "OutlierTreatmentError",
    "build_baselines",
    "evaluate_value",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    rows = [{"store": "A", "amount": value} for value in [10, 11, 12, 10, 9, 10, 11, 12, 10, 200]]
    engine = OutlierDetectionEngine(OutlierDetectionConfig(telemetry_enabled=False))
    result = engine.detect(
        rows,
        rules=[OutlierRule(field="amount", method=OutlierMethod.IQR, threshold=1.5, action=OutlierAction.MARK, min_baseline_size=5)],
    )
    print(result.to_json())

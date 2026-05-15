"""
data/processing/anomaly_detection.py

Enterprise-grade anomaly detection engine for data platforms.

Purpose
-------
Provides a robust, dependency-light anomaly detection engine for time series,
metrics, tabular data, pipelines, data quality signals, throughput, latency,
fraud-like signals and operational telemetry.

Core capabilities
-----------------
- Batch and incremental anomaly detection.
- Multiple detectors: z-score, robust MAD, IQR, EWMA, rolling mean/std,
  static thresholds, rate-of-change and simple seasonal baseline.
- Multivariate/tabular scoring through configurable feature specs.
- Per-entity baselines using group keys.
- Stateful online updates and JSON snapshot/restore.
- Severity classification and explanation messages.
- Low-dependency design with standard library only.
- Optional telemetry integration.
- Safe metadata sanitization.

Example
-------
engine = AnomalyDetectionEngine()
result = engine.detect(
    rows,
    value_field="amount",
    timestamp_field="timestamp",
    group_by=["store_id"],
    detectors=[DetectorSpec(method=DetectionMethod.ROBUST_ZSCORE, threshold=3.5)],
)

for anomaly in result.anomalies:
    print(anomaly.to_dict())
"""

from __future__ import annotations

import contextlib
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
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, Iterator, List, Mapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|session|jwt|bearer)",
    re.IGNORECASE,
)

MAX_HISTORY_PER_SERIES = 100_000
DEFAULT_HISTORY_SIZE = 10_000
DEFAULT_MIN_BASELINE_POINTS = 20
DEFAULT_MAX_GROUPS = 100_000
MAX_TEXT_LENGTH = 16_384
EPSILON = 1e-12


class DetectionMethod(str, Enum):
    STATIC_THRESHOLD = "static_threshold"
    ZSCORE = "zscore"
    ROBUST_ZSCORE = "robust_zscore"
    IQR = "iqr"
    EWMA = "ewma"
    ROLLING_STDDEV = "rolling_stddev"
    RATE_OF_CHANGE = "rate_of_change"
    SEASONAL_BASELINE = "seasonal_baseline"
    CUSTOM = "custom"


class AnomalySeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AnomalyDirection(str, Enum):
    HIGH = "high"
    LOW = "low"
    BOTH = "both"


class AnomalyStatus(str, Enum):
    NORMAL = "normal"
    ANOMALOUS = "anomalous"
    INSUFFICIENT_BASELINE = "insufficient_baseline"
    ERROR = "error"


class MissingValueStrategy(str, Enum):
    DROP = "drop"
    ZERO = "zero"
    MEAN = "mean"
    ERROR = "error"


@dataclass(frozen=True)
class DetectorSpec:
    method: DetectionMethod
    name: Optional[str] = None
    threshold: float = 3.0
    lower_threshold: Optional[float] = None
    upper_threshold: Optional[float] = None
    direction: AnomalyDirection = AnomalyDirection.BOTH
    window_size: int = 100
    min_baseline_points: int = DEFAULT_MIN_BASELINE_POINTS
    ewma_alpha: float = 0.3
    seasonal_period_seconds: Optional[int] = None
    seasonal_tolerance_seconds: int = 300
    rate_threshold_percent: Optional[float] = None
    custom_function: Optional[Callable[[float, Sequence[float], Mapping[str, Any]], Tuple[bool, float, str]]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def detector_name(self) -> str:
        return self.name or self.method.value

    def validate(self) -> None:
        if self.threshold <= 0 and self.method not in {DetectionMethod.STATIC_THRESHOLD, DetectionMethod.CUSTOM}:
            raise AnomalyConfigError("threshold must be positive")
        if self.window_size <= 0:
            raise AnomalyConfigError("window_size must be positive")
        if self.min_baseline_points < 1:
            raise AnomalyConfigError("min_baseline_points must be at least 1")
        if self.method == DetectionMethod.CUSTOM and not self.custom_function:
            raise AnomalyConfigError("custom detector requires custom_function")
        if self.method == DetectionMethod.STATIC_THRESHOLD and self.lower_threshold is None and self.upper_threshold is None:
            raise AnomalyConfigError("static threshold requires lower_threshold and/or upper_threshold")
        if self.method == DetectionMethod.SEASONAL_BASELINE and not self.seasonal_period_seconds:
            raise AnomalyConfigError("seasonal baseline requires seasonal_period_seconds")


@dataclass(frozen=True)
class FeatureSpec:
    field: str
    detectors: Tuple[DetectorSpec, ...]
    weight: float = 1.0
    missing_strategy: MissingValueStrategy = MissingValueStrategy.DROP
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.field:
            raise AnomalyConfigError("FeatureSpec.field is required")
        if not self.detectors:
            raise AnomalyConfigError("FeatureSpec.detectors is required")
        for detector in self.detectors:
            detector.validate()


@dataclass(frozen=True)
class AnomalyConfig:
    history_size: int = DEFAULT_HISTORY_SIZE
    max_groups: int = DEFAULT_MAX_GROUPS
    fail_on_group_limit: bool = True
    default_min_baseline_points: int = DEFAULT_MIN_BASELINE_POINTS
    telemetry_enabled: bool = True
    state_snapshot_path: Optional[str] = None
    emit_normal_records: bool = False
    severity_medium_score: float = 3.0
    severity_high_score: float = 5.0
    severity_critical_score: float = 8.0

    @classmethod
    def from_env(cls) -> "AnomalyConfig":
        return cls(
            history_size=int_env("ANOMALY_HISTORY_SIZE", DEFAULT_HISTORY_SIZE),
            max_groups=int_env("ANOMALY_MAX_GROUPS", DEFAULT_MAX_GROUPS),
            fail_on_group_limit=bool_env("ANOMALY_FAIL_ON_GROUP_LIMIT", True),
            default_min_baseline_points=int_env("ANOMALY_MIN_BASELINE_POINTS", DEFAULT_MIN_BASELINE_POINTS),
            telemetry_enabled=bool_env("ANOMALY_TELEMETRY_ENABLED", True),
            state_snapshot_path=os.getenv("ANOMALY_STATE_SNAPSHOT_PATH"),
            emit_normal_records=bool_env("ANOMALY_EMIT_NORMAL_RECORDS", False),
            severity_medium_score=float_env("ANOMALY_SEVERITY_MEDIUM_SCORE", 3.0),
            severity_high_score=float_env("ANOMALY_SEVERITY_HIGH_SCORE", 5.0),
            severity_critical_score=float_env("ANOMALY_SEVERITY_CRITICAL_SCORE", 8.0),
        )


@dataclass(frozen=True)
class AnomalyRecord:
    id: str
    timestamp: str
    group_key: Tuple[Any, ...]
    field: str
    value: float
    status: AnomalyStatus
    severity: AnomalySeverity
    score: float
    method: DetectionMethod
    detector_name: str
    explanation: str
    baseline_size: int
    expected_value: Optional[float] = None
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    row: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["severity"] = self.severity.value
        data["method"] = self.method.value
        data["group_key"] = list(self.group_key)
        return sanitize_mapping(data)


@dataclass(frozen=True)
class DetectionResult:
    id: str
    status: AnomalyStatus
    started_at: str
    finished_at: str
    duration_ms: float
    input_count: int
    evaluated_count: int
    anomaly_count: int
    skipped_count: int
    group_count: int
    anomalies: List[AnomalyRecord]
    records: List[AnomalyRecord] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["anomalies"] = [item.to_dict() for item in self.anomalies]
        data["records"] = [item.to_dict() for item in self.records]
        return sanitize_mapping(data)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True, default=safe_json_default)


@dataclass
class SeriesState:
    key: Tuple[Any, ...]
    values: Deque[float]
    timestamps: Deque[float]
    ewma: Optional[float] = None
    updated_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, key: Tuple[Any, ...], history_size: int) -> "SeriesState":
        return cls(key=key, values=deque(maxlen=history_size), timestamps=deque(maxlen=history_size))

    def update(self, value: float, timestamp: float, alpha: Optional[float] = None) -> None:
        self.values.append(value)
        self.timestamps.append(timestamp)
        if alpha is not None:
            if self.ewma is None:
                self.ewma = value
            else:
                self.ewma = alpha * value + (1.0 - alpha) * self.ewma
        self.updated_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": list(self.key),
            "values": list(self.values),
            "timestamps": list(self.timestamps),
            "ewma": self.ewma,
            "updated_at": self.updated_at,
            "metadata": sanitize_mapping(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], history_size: int) -> "SeriesState":
        state = cls.create(tuple(data.get("key", [])), history_size)
        for value in list(data.get("values", []))[-history_size:]:
            state.values.append(float(value))
        for ts in list(data.get("timestamps", []))[-history_size:]:
            state.timestamps.append(float(ts))
        state.ewma = float(data["ewma"]) if data.get("ewma") is not None else None
        state.updated_at = float(data["updated_at"]) if data.get("updated_at") is not None else None
        state.metadata = dict(data.get("metadata", {}))
        return state


class AnomalyError(Exception):
    """Base anomaly detection error."""


class AnomalyConfigError(AnomalyError):
    """Invalid anomaly detector configuration."""


class AnomalyInputError(AnomalyError):
    """Invalid input row or value."""


class AnomalyCardinalityError(AnomalyError):
    """Too many groups for anomaly state."""


class AnomalyDetector(Protocol):
    def evaluate(self, value: float, baseline: Sequence[float], spec: DetectorSpec, state: SeriesState, row: Mapping[str, Any]) -> Tuple[bool, float, str, Optional[float], Optional[float], Optional[float]]:
        ...


class AnomalyDetectionEngine:
    """Enterprise anomaly detection engine."""

    def __init__(self, config: Optional[AnomalyConfig] = None) -> None:
        self.config = config or AnomalyConfig.from_env()
        self._states: Dict[str, SeriesState] = {}
        self._lock = threading.RLock()
        if self.config.state_snapshot_path:
            self.restore_state(self.config.state_snapshot_path)

    def detect(
        self,
        rows: Iterable[Any],
        *,
        value_field: Optional[str] = None,
        timestamp_field: str = "timestamp",
        group_by: Optional[Sequence[str]] = None,
        detectors: Optional[Sequence[DetectorSpec]] = None,
        features: Optional[Sequence[FeatureSpec]] = None,
        update_state: bool = True,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> DetectionResult:
        started = time.perf_counter()
        started_iso = utc_now_iso()
        group_fields = list(group_by or [])
        skipped_count = 0
        evaluated_count = 0
        input_count = 0
        anomalies: List[AnomalyRecord] = []
        all_records: List[AnomalyRecord] = []

        feature_specs = normalize_features(value_field=value_field, detectors=detectors, features=features)
        for feature in feature_specs:
            feature.validate()

        with telemetry_operation("anomaly_detection.detect", self.config.telemetry_enabled, attributes={"group_by": group_fields, "features": [f.field for f in feature_specs]}):
            for raw in rows:
                input_count += 1
                try:
                    row = to_mapping(raw)
                    timestamp = normalize_timestamp(get_field(row, timestamp_field) or time.time())
                    base_group_key = tuple(sanitize_group_value(get_field(row, field_name)) for field_name in group_fields)

                    for feature in feature_specs:
                        raw_value = get_field(row, feature.field)
                        value = self._prepare_value(raw_value, feature)
                        if value is None:
                            skipped_count += 1
                            continue
                        group_key = (*base_group_key, feature.field)
                        state = self._get_or_create_state(group_key)
                        baseline = list(state.values)

                        for detector in feature.detectors:
                            record = self._evaluate_detector(
                                value=value,
                                timestamp=timestamp,
                                group_key=group_key,
                                feature=feature,
                                detector=detector,
                                state=state,
                                baseline=baseline,
                                row=row,
                            )
                            evaluated_count += 1
                            if record.status == AnomalyStatus.ANOMALOUS:
                                anomalies.append(record)
                            if self.config.emit_normal_records or record.status == AnomalyStatus.ANOMALOUS:
                                all_records.append(record)

                        if update_state:
                            alpha = first_ewma_alpha(feature.detectors)
                            state.update(value, timestamp, alpha=alpha)
                except Exception as exc:
                    skipped_count += 1
                    logger.debug("Anomaly detection skipped row: %s", exc, exc_info=True)

        finished_iso = utc_now_iso()
        duration_ms = (time.perf_counter() - started) * 1000.0
        status = AnomalyStatus.ANOMALOUS if anomalies else AnomalyStatus.NORMAL if evaluated_count else AnomalyStatus.INSUFFICIENT_BASELINE
        result = DetectionResult(
            id=str(uuid.uuid4()),
            status=status,
            started_at=started_iso,
            finished_at=finished_iso,
            duration_ms=round(duration_ms, 3),
            input_count=input_count,
            evaluated_count=evaluated_count,
            anomaly_count=len(anomalies),
            skipped_count=skipped_count,
            group_count=len(self._states),
            anomalies=anomalies,
            records=all_records,
            metadata=sanitize_mapping(dict(metadata or {})),
        )
        telemetry_metric("anomaly_detection.input_count", input_count, self.config.telemetry_enabled)
        telemetry_metric("anomaly_detection.anomaly_count", len(anomalies), self.config.telemetry_enabled)
        telemetry_metric("anomaly_detection.duration_ms", duration_ms, self.config.telemetry_enabled)
        return result

    def detect_one(
        self,
        row: Any,
        *,
        value_field: str,
        timestamp_field: str = "timestamp",
        group_by: Optional[Sequence[str]] = None,
        detectors: Optional[Sequence[DetectorSpec]] = None,
        update_state: bool = True,
    ) -> DetectionResult:
        return self.detect(
            [row],
            value_field=value_field,
            timestamp_field=timestamp_field,
            group_by=group_by,
            detectors=detectors,
            update_state=update_state,
        )

    def snapshot_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "created_at": utc_now_iso(),
                "history_size": self.config.history_size,
                "state_count": len(self._states),
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
                self._states = {str(key): SeriesState.from_dict(value, self.config.history_size) for key, value in states.items()}
        except Exception as exc:
            logger.warning("Failed to restore anomaly state from %s: %s", target, exc)

    def clear_state(self) -> None:
        with self._lock:
            self._states.clear()

    def _get_or_create_state(self, group_key: Tuple[Any, ...]) -> SeriesState:
        state_key = encode_group_key(group_key)
        with self._lock:
            if state_key not in self._states:
                if len(self._states) >= self.config.max_groups:
                    if self.config.fail_on_group_limit:
                        raise AnomalyCardinalityError(f"max_groups exceeded: {self.config.max_groups}")
                    # Reuse a deterministic overflow group if fail-open cardinality is desired.
                    state_key = "__overflow__"
                    group_key = ("__overflow__",)
                self._states[state_key] = SeriesState.create(group_key, self.config.history_size)
            return self._states[state_key]

    def _prepare_value(self, value: Any, feature: FeatureSpec) -> Optional[float]:
        if value is None:
            if feature.missing_strategy == MissingValueStrategy.DROP:
                return None
            if feature.missing_strategy == MissingValueStrategy.ZERO:
                return 0.0
            if feature.missing_strategy == MissingValueStrategy.ERROR:
                raise AnomalyInputError(f"Missing value for field {feature.field}")
            return None
        return to_number(value)

    def _evaluate_detector(
        self,
        *,
        value: float,
        timestamp: float,
        group_key: Tuple[Any, ...],
        feature: FeatureSpec,
        detector: DetectorSpec,
        state: SeriesState,
        baseline: Sequence[float],
        row: Mapping[str, Any],
    ) -> AnomalyRecord:
        if len(baseline) < detector.min_baseline_points and detector.method not in {DetectionMethod.STATIC_THRESHOLD, DetectionMethod.CUSTOM}:
            return build_record(
                timestamp=timestamp,
                group_key=group_key,
                field=feature.field,
                value=value,
                status=AnomalyStatus.INSUFFICIENT_BASELINE,
                severity=AnomalySeverity.INFO,
                score=0.0,
                method=detector.method,
                detector_name=detector.detector_name,
                explanation=f"Insufficient baseline: {len(baseline)} < {detector.min_baseline_points}",
                baseline_size=len(baseline),
                row=row,
            )

        anomalous, score, explanation, expected, lower, upper = evaluate_detector(value, baseline, detector, state, row)
        severity = classify_severity(score, anomalous, self.config)
        return build_record(
            timestamp=timestamp,
            group_key=group_key,
            field=feature.field,
            value=value,
            status=AnomalyStatus.ANOMALOUS if anomalous else AnomalyStatus.NORMAL,
            severity=severity,
            score=round(score, 6),
            method=detector.method,
            detector_name=detector.detector_name,
            explanation=explanation,
            baseline_size=len(baseline),
            expected_value=expected,
            lower_bound=lower,
            upper_bound=upper,
            row=row,
            metadata={"feature_weight": feature.weight, **detector.metadata},
        )


def evaluate_detector(value: float, baseline: Sequence[float], spec: DetectorSpec, state: SeriesState, row: Mapping[str, Any]) -> Tuple[bool, float, str, Optional[float], Optional[float], Optional[float]]:
    values = list(baseline)[-spec.window_size:]
    if spec.method == DetectionMethod.STATIC_THRESHOLD:
        lower = spec.lower_threshold
        upper = spec.upper_threshold
        anomalous = False
        if lower is not None and value < lower and spec.direction in {AnomalyDirection.LOW, AnomalyDirection.BOTH}:
            anomalous = True
        if upper is not None and value > upper and spec.direction in {AnomalyDirection.HIGH, AnomalyDirection.BOTH}:
            anomalous = True
        score = threshold_score(value, lower, upper)
        return anomalous, score, f"Static threshold lower={lower}, upper={upper}, value={value}", None, lower, upper

    if spec.method == DetectionMethod.ZSCORE:
        mean = statistics.fmean(values)
        std = statistics.pstdev(values) or EPSILON
        z = (value - mean) / std
        return directional_abs_check(z, spec), abs(z), f"z-score={z:.4f}, mean={mean:.4f}, std={std:.4f}", mean, mean - spec.threshold * std, mean + spec.threshold * std

    if spec.method == DetectionMethod.ROBUST_ZSCORE:
        median = statistics.median(values)
        mad = statistics.median([abs(v - median) for v in values]) or EPSILON
        robust_z = 0.6745 * (value - median) / mad
        lower = median - (spec.threshold / 0.6745) * mad
        upper = median + (spec.threshold / 0.6745) * mad
        return directional_abs_check(robust_z, spec), abs(robust_z), f"robust_z={robust_z:.4f}, median={median:.4f}, mad={mad:.4f}", median, lower, upper

    if spec.method == DetectionMethod.IQR:
        q1 = percentile(sorted(values), 25)
        q3 = percentile(sorted(values), 75)
        iqr = q3 - q1
        lower = q1 - spec.threshold * iqr
        upper = q3 + spec.threshold * iqr
        anomalous = (value < lower and spec.direction in {AnomalyDirection.LOW, AnomalyDirection.BOTH}) or (value > upper and spec.direction in {AnomalyDirection.HIGH, AnomalyDirection.BOTH})
        score = threshold_score(value, lower, upper)
        return anomalous, score, f"IQR bounds lower={lower:.4f}, upper={upper:.4f}, q1={q1:.4f}, q3={q3:.4f}", statistics.median(values), lower, upper

    if spec.method == DetectionMethod.EWMA:
        expected = state.ewma if state.ewma is not None else statistics.fmean(values)
        residuals = [abs(v - expected) for v in values]
        scale = statistics.fmean(residuals) if residuals else EPSILON
        score = abs(value - expected) / max(scale, EPSILON)
        lower = expected - spec.threshold * scale
        upper = expected + spec.threshold * scale
        anomalous = (value < lower and spec.direction in {AnomalyDirection.LOW, AnomalyDirection.BOTH}) or (value > upper and spec.direction in {AnomalyDirection.HIGH, AnomalyDirection.BOTH})
        return anomalous, score, f"EWMA expected={expected:.4f}, residual_scale={scale:.4f}, score={score:.4f}", expected, lower, upper

    if spec.method == DetectionMethod.ROLLING_STDDEV:
        mean = statistics.fmean(values)
        std = statistics.pstdev(values) or EPSILON
        lower = mean - spec.threshold * std
        upper = mean + spec.threshold * std
        anomalous = (value < lower and spec.direction in {AnomalyDirection.LOW, AnomalyDirection.BOTH}) or (value > upper and spec.direction in {AnomalyDirection.HIGH, AnomalyDirection.BOTH})
        score = abs(value - mean) / std
        return anomalous, score, f"Rolling bounds lower={lower:.4f}, upper={upper:.4f}", mean, lower, upper

    if spec.method == DetectionMethod.RATE_OF_CHANGE:
        previous = values[-1]
        change_percent = ((value - previous) / max(abs(previous), EPSILON)) * 100.0
        threshold = spec.rate_threshold_percent if spec.rate_threshold_percent is not None else spec.threshold * 100.0
        anomalous = abs(change_percent) > threshold
        if spec.direction == AnomalyDirection.HIGH:
            anomalous = change_percent > threshold
        elif spec.direction == AnomalyDirection.LOW:
            anomalous = change_percent < -threshold
        score = abs(change_percent) / max(threshold, EPSILON)
        return anomalous, score, f"rate_change={change_percent:.4f}%, threshold={threshold:.4f}%", previous, None, None

    if spec.method == DetectionMethod.SEASONAL_BASELINE:
        seasonal_values = seasonal_baseline_values(state, current_ts=state.timestamps[-1] if state.timestamps else time.time(), period=spec.seasonal_period_seconds or 1, tolerance=spec.seasonal_tolerance_seconds)
        if len(seasonal_values) < spec.min_baseline_points:
            seasonal_values = values
        mean = statistics.fmean(seasonal_values)
        std = statistics.pstdev(seasonal_values) or EPSILON
        score = abs(value - mean) / std
        lower = mean - spec.threshold * std
        upper = mean + spec.threshold * std
        anomalous = (value < lower and spec.direction in {AnomalyDirection.LOW, AnomalyDirection.BOTH}) or (value > upper and spec.direction in {AnomalyDirection.HIGH, AnomalyDirection.BOTH})
        return anomalous, score, f"seasonal mean={mean:.4f}, std={std:.4f}, sample_size={len(seasonal_values)}", mean, lower, upper

    if spec.method == DetectionMethod.CUSTOM and spec.custom_function:
        anomalous, score, explanation = spec.custom_function(value, values, row)
        return anomalous, score, explanation, None, None, None

    return False, 0.0, "No detector matched", None, None, None


def directional_abs_check(raw_score: float, spec: DetectorSpec) -> bool:
    if spec.direction == AnomalyDirection.HIGH:
        return raw_score > spec.threshold
    if spec.direction == AnomalyDirection.LOW:
        return raw_score < -spec.threshold
    return abs(raw_score) > spec.threshold


def threshold_score(value: float, lower: Optional[float], upper: Optional[float]) -> float:
    if upper is not None and value > upper:
        return abs(value - upper) / max(abs(upper), 1.0)
    if lower is not None and value < lower:
        return abs(lower - value) / max(abs(lower), 1.0)
    return 0.0


def seasonal_baseline_values(state: SeriesState, *, current_ts: float, period: int, tolerance: int) -> List[float]:
    output = []
    for value, ts in zip(state.values, state.timestamps):
        phase_diff = abs(((current_ts - ts) % period))
        if phase_diff <= tolerance or abs(phase_diff - period) <= tolerance:
            output.append(value)
    return output


def classify_severity(score: float, anomalous: bool, config: AnomalyConfig) -> AnomalySeverity:
    if not anomalous:
        return AnomalySeverity.INFO
    if score >= config.severity_critical_score:
        return AnomalySeverity.CRITICAL
    if score >= config.severity_high_score:
        return AnomalySeverity.HIGH
    if score >= config.severity_medium_score:
        return AnomalySeverity.MEDIUM
    return AnomalySeverity.LOW


def build_record(
    *,
    timestamp: float,
    group_key: Tuple[Any, ...],
    field: str,
    value: float,
    status: AnomalyStatus,
    severity: AnomalySeverity,
    score: float,
    method: DetectionMethod,
    detector_name: str,
    explanation: str,
    baseline_size: int,
    expected_value: Optional[float] = None,
    lower_bound: Optional[float] = None,
    upper_bound: Optional[float] = None,
    row: Optional[Mapping[str, Any]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> AnomalyRecord:
    return AnomalyRecord(
        id=str(uuid.uuid4()),
        timestamp=datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
        group_key=group_key,
        field=field,
        value=round(value, 6),
        status=status,
        severity=severity,
        score=round(score, 6),
        method=method,
        detector_name=detector_name,
        explanation=truncate_text(explanation, 2048),
        baseline_size=baseline_size,
        expected_value=round(expected_value, 6) if expected_value is not None else None,
        lower_bound=round(lower_bound, 6) if lower_bound is not None else None,
        upper_bound=round(upper_bound, 6) if upper_bound is not None else None,
        row=sanitize_mapping(dict(row or {})),
        metadata=sanitize_mapping(dict(metadata or {})),
    )


def normalize_features(
    *,
    value_field: Optional[str],
    detectors: Optional[Sequence[DetectorSpec]],
    features: Optional[Sequence[FeatureSpec]],
) -> List[FeatureSpec]:
    if features:
        return list(features)
    if not value_field:
        raise AnomalyConfigError("value_field is required when features are not provided")
    detector_specs = tuple(detectors or [DetectorSpec(method=DetectionMethod.ROBUST_ZSCORE, threshold=3.5)])
    return [FeatureSpec(field=value_field, detectors=detector_specs)]


def first_ewma_alpha(detectors: Sequence[DetectorSpec]) -> Optional[float]:
    for detector in detectors:
        if detector.method == DetectionMethod.EWMA:
            return detector.ewma_alpha
    return None


def to_mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if dataclasses.is_dataclass(row):
        return asdict(row)
    if hasattr(row, "_asdict"):
        return row._asdict()
    if hasattr(row, "__dict__"):
        return vars(row)
    raise AnomalyInputError(f"Unsupported row type: {type(row)!r}")


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
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AnomalyInputError(f"Value is not numeric: {value!r}") from exc
    if math.isnan(number) or math.isinf(number):
        raise AnomalyInputError(f"Invalid numeric value: {value!r}")
    return number


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
    return truncate_text(text, 512)


def encode_group_key(group_key: Tuple[Any, ...]) -> str:
    encoded = json.dumps(group_key, ensure_ascii=False, sort_keys=True, default=safe_json_default)
    if len(encoded) > 512:
        import hashlib
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return encoded


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
            output[key_str] = [sanitize_value(item, depth=depth + 1) for item in list(value)[:1000]]
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
    if isinstance(value, Mapping):
        return sanitize_mapping(value, depth=depth + 1)
    text = str(value)
    return truncate_text(text, MAX_TEXT_LENGTH)


def truncate_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 15] + "...[truncated]"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return asdict(value)
    if isinstance(value, (set, tuple, deque)):
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
        logger.debug("Anomaly telemetry metric failed", exc_info=True)


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
    "AnomalyCardinalityError",
    "AnomalyConfig",
    "AnomalyConfigError",
    "AnomalyDetectionEngine",
    "AnomalyDetector",
    "AnomalyDirection",
    "AnomalyError",
    "AnomalyInputError",
    "AnomalyRecord",
    "AnomalySeverity",
    "AnomalyStatus",
    "DetectionMethod",
    "DetectionResult",
    "DetectorSpec",
    "FeatureSpec",
    "MissingValueStrategy",
    "SeriesState",
    "evaluate_detector",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    rows = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    for i in range(60):
        rows.append({"store_id": "A", "timestamp": base + i * 60, "amount": 100 + (i % 5)})
    rows.append({"store_id": "A", "timestamp": base + 61 * 60, "amount": 300})

    engine = AnomalyDetectionEngine()
    result = engine.detect(
        rows,
        value_field="amount",
        timestamp_field="timestamp",
        group_by=["store_id"],
        detectors=[DetectorSpec(method=DetectionMethod.ROBUST_ZSCORE, threshold=3.5, min_baseline_points=20)],
    )
    print(result.to_json())

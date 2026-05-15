"""
data/validation/drift_validator.py

Enterprise-grade data drift validation engine.

This module detects and validates drift between a baseline/reference dataset and
current/observed data. It is designed for data platforms, ML pipelines, feature
stores, analytics products and AI governance systems where distribution changes
must be detected, explained and audited before downstream use.

Core capabilities:

- Numeric drift detection with PSI, KS statistic, mean/std shift
- Categorical drift detection with PSI, frequency shift and unseen categories
- Schema drift detection
- Missingness/null-rate drift
- Volume/row-count drift
- Time-window-aware validation metadata
- Feature-level severity and risk scoring
- Dataset-level decision: allow/review/block
- Custom drift rule hooks
- Batch validation
- Audit and metrics hooks
- Dependency-light defaults, optional SciPy-free implementation

Python:
    3.10+
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class DriftValidationError(Exception):
    """Base exception for drift validation."""


class DriftConfigurationError(DriftValidationError):
    """Raised when drift configuration is invalid."""


class DriftInputError(DriftValidationError):
    """Raised when drift validation input is invalid."""


class DriftRuleExecutionError(DriftValidationError):
    """Raised when a custom drift rule fails."""


# =============================================================================
# Enums
# =============================================================================


class DriftStatus(str, Enum):
    STABLE = "stable"
    WARNING = "warning"
    DRIFTED = "drifted"
    ERROR = "error"
    SKIPPED = "skipped"


class DriftDecision(str, Enum):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


class DriftSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class FeatureType(str, Enum):
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    TEXT = "text"
    UNKNOWN = "unknown"


class DriftTestType(str, Enum):
    PSI = "psi"
    KS = "ks"
    MEAN_SHIFT = "mean_shift"
    STD_SHIFT = "std_shift"
    NULL_RATE = "null_rate"
    CATEGORY_FREQUENCY = "category_frequency"
    UNSEEN_CATEGORY = "unseen_category"
    SCHEMA = "schema"
    ROW_COUNT = "row_count"
    CUSTOM = "custom"


class DriftScope(str, Enum):
    FEATURE = "feature"
    DATASET = "dataset"
    SCHEMA = "schema"
    PIPELINE = "pipeline"
    MODEL = "model"


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class DriftValidatorConfig:
    """Drift validator configuration."""

    psi_warning_threshold: float = 0.10
    psi_error_threshold: float = 0.25
    ks_warning_threshold: float = 0.10
    ks_error_threshold: float = 0.20
    mean_shift_warning_z: float = 2.0
    mean_shift_error_z: float = 4.0
    null_rate_warning_delta: float = 0.05
    null_rate_error_delta: float = 0.15
    category_frequency_warning_delta: float = 0.10
    category_frequency_error_delta: float = 0.25
    unseen_category_warning_rate: float = 0.01
    unseen_category_error_rate: float = 0.05
    row_count_warning_delta_ratio: float = 0.20
    row_count_error_delta_ratio: float = 0.50
    numeric_bins: int = 10
    min_samples: int = 20
    fail_fast: bool = False
    audit_enabled: bool = True
    metrics_enabled: bool = True
    include_passed_checks: bool = False
    block_on_critical: bool = True
    review_on_error: bool = True
    max_evidence_chars: int = 2_000
    version: str = "1.0.0"

    def validate(self) -> None:
        thresholds = {
            "psi_warning_threshold": self.psi_warning_threshold,
            "psi_error_threshold": self.psi_error_threshold,
            "ks_warning_threshold": self.ks_warning_threshold,
            "ks_error_threshold": self.ks_error_threshold,
            "null_rate_warning_delta": self.null_rate_warning_delta,
            "null_rate_error_delta": self.null_rate_error_delta,
            "category_frequency_warning_delta": self.category_frequency_warning_delta,
            "category_frequency_error_delta": self.category_frequency_error_delta,
            "unseen_category_warning_rate": self.unseen_category_warning_rate,
            "unseen_category_error_rate": self.unseen_category_error_rate,
            "row_count_warning_delta_ratio": self.row_count_warning_delta_ratio,
            "row_count_error_delta_ratio": self.row_count_error_delta_ratio,
        }
        for name, value in thresholds.items():
            if value < 0:
                raise DriftConfigurationError(f"{name} must be >= 0")
        if self.psi_warning_threshold > self.psi_error_threshold:
            raise DriftConfigurationError("psi_warning_threshold cannot exceed psi_error_threshold")
        if self.ks_warning_threshold > self.ks_error_threshold:
            raise DriftConfigurationError("ks_warning_threshold cannot exceed ks_error_threshold")
        if self.numeric_bins < 2:
            raise DriftConfigurationError("numeric_bins must be >= 2")
        if self.min_samples < 1:
            raise DriftConfigurationError("min_samples must be >= 1")
        if self.max_evidence_chars < 0:
            raise DriftConfigurationError("max_evidence_chars must be >= 0")


@dataclass(frozen=True)
class DriftContext:
    """Execution context for drift validation."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    application: Optional[str] = None
    pipeline_id: Optional[str] = None
    dataset_id: Optional[str] = None
    model_id: Optional[str] = None
    baseline_window: Optional[str] = None
    current_window: Optional[str] = None
    environment: Optional[str] = None
    trace_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeatureSpec:
    """Feature configuration for drift checks."""

    name: str
    feature_type: FeatureType = FeatureType.UNKNOWN
    enabled: bool = True
    required: bool = False
    tests: Sequence[DriftTestType] = field(default_factory=tuple)
    warning_threshold: Optional[float] = None
    error_threshold: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.name:
            raise DriftConfigurationError("feature name is required")


@dataclass(frozen=True)
class DriftEvidence:
    """Evidence attached to a drift finding."""

    key: str
    value: Any
    baseline: Optional[Any] = None
    current: Optional[Any] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DriftFinding:
    """One drift validation finding."""

    finding_id: str
    check_id: str
    test_type: DriftTestType
    scope: DriftScope
    status: DriftStatus
    severity: DriftSeverity
    message: str
    feature: Optional[str] = None
    score: Optional[float] = None
    threshold: Optional[float] = None
    evidence: Sequence[DriftEvidence] = field(default_factory=tuple)
    remediation: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DriftReport:
    """Final drift validation report."""

    report_id: str
    request_id: str
    created_at: str
    status: DriftStatus
    decision: DriftDecision
    risk_score: float
    features_evaluated: int
    checks_evaluated: int
    stable_checks: int
    warning_checks: int
    drifted_checks: int
    skipped_checks: int
    baseline_rows: int
    current_rows: int
    findings: Sequence[DriftFinding]
    recommendations: Sequence[str]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.decision == DriftDecision.ALLOW

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class DriftRuleResult:
    """Result returned by custom drift rule."""

    drifted: bool
    message: str
    severity: DriftSeverity = DriftSeverity.ERROR
    score: Optional[float] = None
    threshold: Optional[float] = None
    evidence: Sequence[DriftEvidence] = field(default_factory=tuple)
    remediation: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class CustomDriftRule(Protocol):
    """Custom drift rule protocol."""

    async def evaluate(
        self,
        baseline: Sequence[Mapping[str, Any]],
        current: Sequence[Mapping[str, Any]],
        *,
        feature: FeatureSpec,
        context: DriftContext,
    ) -> DriftRuleResult:
        """Evaluate drift for a feature or dataset."""


class AuditSink(Protocol):
    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        """Emit audit event."""


class MetricsSink(Protocol):
    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Increment metric."""

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Observe metric."""


# =============================================================================
# Utility Functions
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def truncate(value: Any, max_chars: int) -> str:
    text = str(value)
    if max_chars and len(text) > max_chars:
        return text[: max_chars - 15] + "...[TRUNCATED]"
    return text


def normalize_records(data: Any) -> Sequence[Mapping[str, Any]]:
    if isinstance(data, Mapping):
        return (data,)
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        records: List[Mapping[str, Any]] = []
        for item in data:
            if not isinstance(item, Mapping):
                raise DriftInputError("all records must be mappings")
            records.append(item)
        return tuple(records)
    raise DriftInputError("data must be a mapping or a sequence of mappings")


def get_value(record: Mapping[str, Any], feature: str) -> Any:
    current: Any = record
    for part in feature.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def values_for(records: Sequence[Mapping[str, Any]], feature: str) -> List[Any]:
    return [get_value(record, feature) for record in records]


def non_null(values: Sequence[Any]) -> List[Any]:
    return [value for value in values if value is not None and value != ""]


def numeric_values(values: Sequence[Any]) -> List[float]:
    output: List[float] = []
    for value in values:
        if value is None or value == "" or isinstance(value, bool):
            continue
        try:
            number = float(value)
            if math.isfinite(number):
                output.append(number)
        except (TypeError, ValueError):
            continue
    return output


def null_rate(values: Sequence[Any]) -> float:
    if not values:
        return 0.0
    return sum(1 for value in values if value is None or value == "") / len(values)


def infer_feature_type(values: Sequence[Any]) -> FeatureType:
    sample = non_null(values)[:100]
    if not sample:
        return FeatureType.UNKNOWN
    bool_count = sum(1 for value in sample if isinstance(value, bool))
    numeric_count = 0
    datetime_count = 0
    text_count = 0
    for value in sample:
        if isinstance(value, bool):
            continue
        try:
            float(value)
            numeric_count += 1
            continue
        except (TypeError, ValueError):
            pass
        if parse_datetime(value) is not None:
            datetime_count += 1
        elif isinstance(value, str) and len(value) > 80:
            text_count += 1
    total = len(sample)
    if bool_count / total >= 0.9:
        return FeatureType.BOOLEAN
    if numeric_count / total >= 0.9:
        return FeatureType.NUMERIC
    if datetime_count / total >= 0.9:
        return FeatureType.DATETIME
    if text_count / total >= 0.5:
        return FeatureType.TEXT
    return FeatureType.CATEGORICAL


def parse_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def schema_keys(records: Sequence[Mapping[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for record in records:
        keys.update(record.keys())
    return keys


# =============================================================================
# Drift Statistics
# =============================================================================


def population_stability_index(baseline: Sequence[float], current: Sequence[float], *, bins: int = 10) -> float:
    """Calculate PSI using baseline quantile-like bins.

    PSI interpretation commonly used in risk/data monitoring:
    < 0.1 stable, 0.1-0.25 moderate shift, > 0.25 significant shift.
    """

    if not baseline or not current:
        return 0.0
    sorted_base = sorted(baseline)
    if len(set(sorted_base)) == 1:
        base_value = sorted_base[0]
        base_dist = [1.0 if index == 0 else 0.0 for index in range(bins)]
        current_dist = [sum(1 for x in current if x == base_value) / len(current)] + [sum(1 for x in current if x != base_value) / len(current)] + [0.0] * max(0, bins - 2)
    else:
        boundaries = []
        for i in range(1, bins):
            idx = min(len(sorted_base) - 1, max(0, int(len(sorted_base) * i / bins)))
            boundaries.append(sorted_base[idx])
        base_dist = _bin_distribution(baseline, boundaries)
        current_dist = _bin_distribution(current, boundaries)
    eps = 1e-8
    psi = 0.0
    for expected, actual in zip(base_dist, current_dist):
        expected = max(expected, eps)
        actual = max(actual, eps)
        psi += (actual - expected) * math.log(actual / expected)
    return max(0.0, psi)


def _bin_distribution(values: Sequence[float], boundaries: Sequence[float]) -> List[float]:
    counts = [0] * (len(boundaries) + 1)
    for value in values:
        index = 0
        while index < len(boundaries) and value > boundaries[index]:
            index += 1
        counts[index] += 1
    total = max(len(values), 1)
    return [count / total for count in counts]


def categorical_psi(baseline: Sequence[Any], current: Sequence[Any]) -> float:
    base_counts = Counter(str(value) for value in non_null(baseline))
    current_counts = Counter(str(value) for value in non_null(current))
    categories = set(base_counts) | set(current_counts)
    base_total = sum(base_counts.values()) or 1
    current_total = sum(current_counts.values()) or 1
    eps = 1e-8
    psi = 0.0
    for category in categories:
        expected = max(base_counts.get(category, 0) / base_total, eps)
        actual = max(current_counts.get(category, 0) / current_total, eps)
        psi += (actual - expected) * math.log(actual / expected)
    return max(0.0, psi)


def ks_statistic(baseline: Sequence[float], current: Sequence[float]) -> float:
    if not baseline or not current:
        return 0.0
    base_sorted = sorted(baseline)
    current_sorted = sorted(current)
    values = sorted(set(base_sorted + current_sorted))
    i = j = 0
    max_diff = 0.0
    n = len(base_sorted)
    m = len(current_sorted)
    for value in values:
        while i < n and base_sorted[i] <= value:
            i += 1
        while j < m and current_sorted[j] <= value:
            j += 1
        max_diff = max(max_diff, abs((i / n) - (j / m)))
    return clamp(max_diff)


def relative_delta(baseline_value: float, current_value: float) -> float:
    denom = max(abs(baseline_value), 1e-8)
    return abs(current_value - baseline_value) / denom


# =============================================================================
# Default sinks
# =============================================================================


class LoggingAuditSink:
    """Logging-based audit sink."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        self.logger.info("drift_audit=%s payload=%s", event_name, safe_json(payload))


class LoggingMetricsSink:
    """Logging-based metrics sink."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("drift_metric_counter=%s value=%s tags=%s", name, value, dict(tags or {}))

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("drift_metric_observe=%s value=%s tags=%s", name, value, dict(tags or {}))


class CallableDriftRule:
    """Adapter for custom sync/async drift rule callables."""

    def __init__(self, func: Callable[..., DriftRuleResult]) -> None:
        self.func = func

    async def evaluate(
        self,
        baseline: Sequence[Mapping[str, Any]],
        current: Sequence[Mapping[str, Any]],
        *,
        feature: FeatureSpec,
        context: DriftContext,
    ) -> DriftRuleResult:
        result = self.func(baseline, current, feature=feature, context=context)
        if asyncio.iscoroutine(result):
            result = await result
        if not isinstance(result, DriftRuleResult):
            raise DriftRuleExecutionError("custom drift rule must return DriftRuleResult")
        return result


# =============================================================================
# Validator
# =============================================================================


class DriftValidator:
    """Enterprise drift validator."""

    def __init__(
        self,
        *,
        features: Optional[Sequence[FeatureSpec]] = None,
        config: Optional[DriftValidatorConfig] = None,
        custom_rules: Optional[Mapping[str, CustomDriftRule]] = None,
        audit_sink: Optional[AuditSink] = None,
        metrics_sink: Optional[MetricsSink] = None,
    ) -> None:
        self.config = config or DriftValidatorConfig()
        self.config.validate()
        self.features = tuple(features or ())
        for feature in self.features:
            feature.validate()
        self.custom_rules = dict(custom_rules or {})
        self.audit_sink = audit_sink or LoggingAuditSink()
        self.metrics_sink = metrics_sink or LoggingMetricsSink()

    async def validate(
        self,
        baseline: Any,
        current: Any,
        *,
        context: Optional[DriftContext] = None,
    ) -> DriftReport:
        """Validate drift between baseline and current datasets."""

        context = context or DriftContext()
        started = time.perf_counter()
        findings: List[DriftFinding] = []
        checks_evaluated = stable = warnings = drifted = skipped = 0

        try:
            baseline_records = normalize_records(baseline)
            current_records = normalize_records(current)
            self._validate_input_sizes(baseline_records, current_records)

            schema_findings = self._validate_schema_drift(baseline_records, current_records)
            for finding in schema_findings:
                checks_evaluated += 1
                if finding.status == DriftStatus.STABLE:
                    stable += 1
                    if self.config.include_passed_checks:
                        findings.append(finding)
                else:
                    findings.append(finding)
                    if finding.severity == DriftSeverity.WARNING:
                        warnings += 1
                    else:
                        drifted += 1

            row_count_finding = self._validate_row_count_drift(baseline_records, current_records)
            checks_evaluated += 1
            if row_count_finding.status == DriftStatus.STABLE:
                stable += 1
                if self.config.include_passed_checks:
                    findings.append(row_count_finding)
            else:
                findings.append(row_count_finding)
                if row_count_finding.severity == DriftSeverity.WARNING:
                    warnings += 1
                else:
                    drifted += 1

            feature_specs = self._resolve_features(baseline_records, current_records)
            for feature in feature_specs:
                if not feature.enabled:
                    skipped += 1
                    continue
                feature_findings = await self._validate_feature(baseline_records, current_records, feature, context)
                for finding in feature_findings:
                    checks_evaluated += 1
                    if finding.status == DriftStatus.STABLE:
                        stable += 1
                        if self.config.include_passed_checks:
                            findings.append(finding)
                    elif finding.status == DriftStatus.SKIPPED:
                        skipped += 1
                        if self.config.include_passed_checks:
                            findings.append(finding)
                    else:
                        findings.append(finding)
                        if finding.severity == DriftSeverity.WARNING:
                            warnings += 1
                        else:
                            drifted += 1
                    if self.config.fail_fast and finding.severity in {DriftSeverity.ERROR, DriftSeverity.CRITICAL} and finding.status == DriftStatus.DRIFTED:
                        break
                if self.config.fail_fast and any(f.severity in {DriftSeverity.ERROR, DriftSeverity.CRITICAL} and f.status == DriftStatus.DRIFTED for f in findings):
                    break

            report = self._build_report(
                context=context,
                baseline_records=baseline_records,
                current_records=current_records,
                feature_count=len(feature_specs),
                checks_evaluated=checks_evaluated,
                stable_checks=stable,
                warning_checks=warnings,
                drifted_checks=drifted,
                skipped_checks=skipped,
                findings=findings,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            await self._record_success(context, report)
            await self._audit_completed(context, report)
            return report

        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            await self._record_failure(context, exc, latency_ms)
            await self._audit_failure(context, exc, latency_ms)
            raise

    def validate_sync(self, baseline: Any, current: Any, *, context: Optional[DriftContext] = None) -> DriftReport:
        return asyncio.run(self.validate(baseline, current, context=context))

    async def validate_many(
        self,
        pairs: Sequence[Tuple[Any, Any]],
        *,
        context: Optional[DriftContext] = None,
        concurrency: int = 5,
    ) -> Sequence[DriftReport]:
        if concurrency <= 0:
            raise DriftConfigurationError("concurrency must be positive")
        semaphore = asyncio.Semaphore(concurrency)

        async def run_one(pair: Tuple[Any, Any]) -> DriftReport:
            async with semaphore:
                return await self.validate(pair[0], pair[1], context=context)

        return tuple(await asyncio.gather(*(run_one(pair) for pair in pairs)))

    def _validate_input_sizes(self, baseline: Sequence[Mapping[str, Any]], current: Sequence[Mapping[str, Any]]) -> None:
        if not baseline:
            raise DriftInputError("baseline dataset must not be empty")
        if not current:
            raise DriftInputError("current dataset must not be empty")

    def _resolve_features(self, baseline: Sequence[Mapping[str, Any]], current: Sequence[Mapping[str, Any]]) -> Sequence[FeatureSpec]:
        if self.features:
            return self.features
        keys = sorted(schema_keys(baseline) | schema_keys(current))
        specs: List[FeatureSpec] = []
        for key in keys:
            values = values_for(baseline, key) + values_for(current, key)
            inferred = infer_feature_type(values)
            if inferred in {FeatureType.UNKNOWN, FeatureType.TEXT}:
                continue
            specs.append(FeatureSpec(name=key, feature_type=inferred))
        return tuple(specs)

    def _validate_schema_drift(self, baseline: Sequence[Mapping[str, Any]], current: Sequence[Mapping[str, Any]]) -> Sequence[DriftFinding]:
        baseline_keys = schema_keys(baseline)
        current_keys = schema_keys(current)
        missing = sorted(baseline_keys - current_keys)
        added = sorted(current_keys - baseline_keys)
        findings: List[DriftFinding] = []
        if missing:
            findings.append(
                self._finding(
                    check_id="schema.missing_fields",
                    test_type=DriftTestType.SCHEMA,
                    scope=DriftScope.SCHEMA,
                    status=DriftStatus.DRIFTED,
                    severity=DriftSeverity.ERROR,
                    message="Schema drift detected: fields missing from current dataset.",
                    evidence=(DriftEvidence(key="missing_fields", value=missing, baseline=sorted(baseline_keys), current=sorted(current_keys)),),
                    remediation="Restore missing fields or update downstream contracts and consumers.",
                )
            )
        else:
            findings.append(self._passed("schema.missing_fields", DriftTestType.SCHEMA, DriftScope.SCHEMA, "No missing fields detected."))
        if added:
            findings.append(
                self._finding(
                    check_id="schema.added_fields",
                    test_type=DriftTestType.SCHEMA,
                    scope=DriftScope.SCHEMA,
                    status=DriftStatus.WARNING,
                    severity=DriftSeverity.WARNING,
                    message="Schema drift detected: new fields added to current dataset.",
                    evidence=(DriftEvidence(key="added_fields", value=added, baseline=sorted(baseline_keys), current=sorted(current_keys)),),
                    remediation="Review new fields and update schema registry/contracts if intentional.",
                )
            )
        else:
            findings.append(self._passed("schema.added_fields", DriftTestType.SCHEMA, DriftScope.SCHEMA, "No added fields detected."))
        return tuple(findings)

    def _validate_row_count_drift(self, baseline: Sequence[Mapping[str, Any]], current: Sequence[Mapping[str, Any]]) -> DriftFinding:
        base_count = len(baseline)
        current_count = len(current)
        delta = relative_delta(float(base_count), float(current_count))
        if delta >= self.config.row_count_error_delta_ratio:
            severity = DriftSeverity.ERROR
            status = DriftStatus.DRIFTED
            threshold = self.config.row_count_error_delta_ratio
        elif delta >= self.config.row_count_warning_delta_ratio:
            severity = DriftSeverity.WARNING
            status = DriftStatus.WARNING
            threshold = self.config.row_count_warning_delta_ratio
        else:
            return self._passed(
                "dataset.row_count",
                DriftTestType.ROW_COUNT,
                DriftScope.DATASET,
                "Row count is stable within configured threshold.",
                score=delta,
                threshold=self.config.row_count_warning_delta_ratio,
            )
        return self._finding(
            check_id="dataset.row_count",
            test_type=DriftTestType.ROW_COUNT,
            scope=DriftScope.DATASET,
            status=status,
            severity=severity,
            message="Row count drift detected.",
            score=delta,
            threshold=threshold,
            evidence=(DriftEvidence(key="row_count", value={"baseline": base_count, "current": current_count}, baseline=base_count, current=current_count),),
            remediation="Investigate upstream volume changes, filtering logic or missing ingestion partitions.",
        )

    async def _validate_feature(
        self,
        baseline: Sequence[Mapping[str, Any]],
        current: Sequence[Mapping[str, Any]],
        feature: FeatureSpec,
        context: DriftContext,
    ) -> Sequence[DriftFinding]:
        base_values = values_for(baseline, feature.name)
        current_values = values_for(current, feature.name)
        feature_type = feature.feature_type if feature.feature_type != FeatureType.UNKNOWN else infer_feature_type(base_values + current_values)
        tests = tuple(feature.tests) if feature.tests else self._default_tests(feature_type)
        findings: List[DriftFinding] = []

        if feature.required and all(value is None for value in current_values):
            findings.append(
                self._finding(
                    check_id=f"{feature.name}.required",
                    test_type=DriftTestType.SCHEMA,
                    scope=DriftScope.FEATURE,
                    status=DriftStatus.DRIFTED,
                    severity=DriftSeverity.ERROR,
                    message=f"Required feature '{feature.name}' is missing or null in current dataset.",
                    feature=feature.name,
                    remediation="Restore required feature in current dataset.",
                )
            )
            return tuple(findings)

        for test in tests:
            if test == DriftTestType.NULL_RATE:
                findings.append(self._null_rate_finding(feature, base_values, current_values))
            elif test == DriftTestType.PSI:
                if feature_type == FeatureType.NUMERIC:
                    findings.append(self._numeric_psi_finding(feature, base_values, current_values))
                elif feature_type in {FeatureType.CATEGORICAL, FeatureType.BOOLEAN}:
                    findings.append(self._categorical_psi_finding(feature, base_values, current_values))
            elif test == DriftTestType.KS and feature_type == FeatureType.NUMERIC:
                findings.append(self._ks_finding(feature, base_values, current_values))
            elif test == DriftTestType.MEAN_SHIFT and feature_type == FeatureType.NUMERIC:
                findings.append(self._mean_shift_finding(feature, base_values, current_values))
            elif test == DriftTestType.STD_SHIFT and feature_type == FeatureType.NUMERIC:
                findings.append(self._std_shift_finding(feature, base_values, current_values))
            elif test == DriftTestType.CATEGORY_FREQUENCY and feature_type in {FeatureType.CATEGORICAL, FeatureType.BOOLEAN}:
                findings.append(self._category_frequency_finding(feature, base_values, current_values))
            elif test == DriftTestType.UNSEEN_CATEGORY and feature_type in {FeatureType.CATEGORICAL, FeatureType.BOOLEAN}:
                findings.append(self._unseen_category_finding(feature, base_values, current_values))
            elif test == DriftTestType.CUSTOM:
                custom = self.custom_rules.get(feature.name)
                if custom is None:
                    findings.append(
                        self._finding(
                            check_id=f"{feature.name}.custom",
                            test_type=DriftTestType.CUSTOM,
                            scope=DriftScope.FEATURE,
                            status=DriftStatus.ERROR,
                            severity=DriftSeverity.ERROR,
                            message=f"No custom drift rule registered for feature '{feature.name}'.",
                            feature=feature.name,
                            remediation="Register a custom drift rule for this feature.",
                        )
                    )
                else:
                    result = await custom.evaluate(baseline, current, feature=feature, context=context)
                    findings.append(self._custom_finding(feature, result))
        return tuple(findings)

    def _default_tests(self, feature_type: FeatureType) -> Sequence[DriftTestType]:
        if feature_type == FeatureType.NUMERIC:
            return (DriftTestType.NULL_RATE, DriftTestType.PSI, DriftTestType.KS, DriftTestType.MEAN_SHIFT, DriftTestType.STD_SHIFT)
        if feature_type in {FeatureType.CATEGORICAL, FeatureType.BOOLEAN}:
            return (DriftTestType.NULL_RATE, DriftTestType.PSI, DriftTestType.CATEGORY_FREQUENCY, DriftTestType.UNSEEN_CATEGORY)
        return (DriftTestType.NULL_RATE,)

    def _null_rate_finding(self, feature: FeatureSpec, baseline_values: Sequence[Any], current_values: Sequence[Any]) -> DriftFinding:
        base_rate = null_rate(baseline_values)
        current_rate = null_rate(current_values)
        delta = abs(current_rate - base_rate)
        if delta >= self.config.null_rate_error_delta:
            return self._drift_finding(feature, DriftTestType.NULL_RATE, delta, self.config.null_rate_error_delta, DriftSeverity.ERROR, "Null-rate drift detected.", base_rate, current_rate, "Investigate missing values introduced upstream.")
        if delta >= self.config.null_rate_warning_delta:
            return self._drift_finding(feature, DriftTestType.NULL_RATE, delta, self.config.null_rate_warning_delta, DriftSeverity.WARNING, "Null-rate drift warning detected.", base_rate, current_rate, "Review missingness changes for this feature.")
        return self._passed(f"{feature.name}.null_rate", DriftTestType.NULL_RATE, DriftScope.FEATURE, "Null-rate is stable.", feature=feature.name, score=delta, threshold=self.config.null_rate_warning_delta)

    def _numeric_psi_finding(self, feature: FeatureSpec, baseline_values: Sequence[Any], current_values: Sequence[Any]) -> DriftFinding:
        base = numeric_values(baseline_values)
        cur = numeric_values(current_values)
        if len(base) < self.config.min_samples or len(cur) < self.config.min_samples:
            return self._skipped(feature, DriftTestType.PSI, "Not enough numeric samples for PSI.")
        score = population_stability_index(base, cur, bins=self.config.numeric_bins)
        return self._score_to_finding(feature, DriftTestType.PSI, score, self.config.psi_warning_threshold, self.config.psi_error_threshold, "Numeric PSI drift detected.")

    def _categorical_psi_finding(self, feature: FeatureSpec, baseline_values: Sequence[Any], current_values: Sequence[Any]) -> DriftFinding:
        base = non_null(baseline_values)
        cur = non_null(current_values)
        if len(base) < self.config.min_samples or len(cur) < self.config.min_samples:
            return self._skipped(feature, DriftTestType.PSI, "Not enough categorical samples for PSI.")
        score = categorical_psi(base, cur)
        return self._score_to_finding(feature, DriftTestType.PSI, score, self.config.psi_warning_threshold, self.config.psi_error_threshold, "Categorical PSI drift detected.")

    def _ks_finding(self, feature: FeatureSpec, baseline_values: Sequence[Any], current_values: Sequence[Any]) -> DriftFinding:
        base = numeric_values(baseline_values)
        cur = numeric_values(current_values)
        if len(base) < self.config.min_samples or len(cur) < self.config.min_samples:
            return self._skipped(feature, DriftTestType.KS, "Not enough numeric samples for KS statistic.")
        score = ks_statistic(base, cur)
        return self._score_to_finding(feature, DriftTestType.KS, score, self.config.ks_warning_threshold, self.config.ks_error_threshold, "KS distribution drift detected.")

    def _mean_shift_finding(self, feature: FeatureSpec, baseline_values: Sequence[Any], current_values: Sequence[Any]) -> DriftFinding:
        base = numeric_values(baseline_values)
        cur = numeric_values(current_values)
        if len(base) < self.config.min_samples or len(cur) < self.config.min_samples:
            return self._skipped(feature, DriftTestType.MEAN_SHIFT, "Not enough numeric samples for mean shift.")
        base_mean = statistics.mean(base)
        cur_mean = statistics.mean(cur)
        base_std = statistics.pstdev(base) or 1e-8
        z_shift = abs(cur_mean - base_mean) / base_std
        if z_shift >= self.config.mean_shift_error_z:
            return self._drift_finding(feature, DriftTestType.MEAN_SHIFT, z_shift, self.config.mean_shift_error_z, DriftSeverity.ERROR, "Mean shift drift detected.", base_mean, cur_mean, "Investigate upstream numeric distribution shift.")
        if z_shift >= self.config.mean_shift_warning_z:
            return self._drift_finding(feature, DriftTestType.MEAN_SHIFT, z_shift, self.config.mean_shift_warning_z, DriftSeverity.WARNING, "Mean shift warning detected.", base_mean, cur_mean, "Review feature mean shift.")
        return self._passed(f"{feature.name}.mean_shift", DriftTestType.MEAN_SHIFT, DriftScope.FEATURE, "Mean is stable.", feature=feature.name, score=z_shift, threshold=self.config.mean_shift_warning_z)

    def _std_shift_finding(self, feature: FeatureSpec, baseline_values: Sequence[Any], current_values: Sequence[Any]) -> DriftFinding:
        base = numeric_values(baseline_values)
        cur = numeric_values(current_values)
        if len(base) < self.config.min_samples or len(cur) < self.config.min_samples:
            return self._skipped(feature, DriftTestType.STD_SHIFT, "Not enough numeric samples for std shift.")
        base_std = statistics.pstdev(base)
        cur_std = statistics.pstdev(cur)
        delta = relative_delta(base_std or 1e-8, cur_std)
        warning = 0.25
        error = 0.60
        return self._score_to_finding(feature, DriftTestType.STD_SHIFT, delta, warning, error, "Standard-deviation drift detected.", baseline=base_std, current=cur_std)

    def _category_frequency_finding(self, feature: FeatureSpec, baseline_values: Sequence[Any], current_values: Sequence[Any]) -> DriftFinding:
        base_counts = Counter(str(v) for v in non_null(baseline_values))
        cur_counts = Counter(str(v) for v in non_null(current_values))
        categories = set(base_counts) | set(cur_counts)
        base_total = sum(base_counts.values()) or 1
        cur_total = sum(cur_counts.values()) or 1
        max_delta = 0.0
        worst_category = None
        for category in categories:
            delta = abs((cur_counts.get(category, 0) / cur_total) - (base_counts.get(category, 0) / base_total))
            if delta > max_delta:
                max_delta = delta
                worst_category = category
        if max_delta >= self.config.category_frequency_error_delta:
            return self._drift_finding(feature, DriftTestType.CATEGORY_FREQUENCY, max_delta, self.config.category_frequency_error_delta, DriftSeverity.ERROR, "Category frequency drift detected.", dict(base_counts), dict(cur_counts), "Investigate category distribution shift.", extra={"worst_category": worst_category})
        if max_delta >= self.config.category_frequency_warning_delta:
            return self._drift_finding(feature, DriftTestType.CATEGORY_FREQUENCY, max_delta, self.config.category_frequency_warning_delta, DriftSeverity.WARNING, "Category frequency drift warning detected.", dict(base_counts), dict(cur_counts), "Review category distribution shift.", extra={"worst_category": worst_category})
        return self._passed(f"{feature.name}.category_frequency", DriftTestType.CATEGORY_FREQUENCY, DriftScope.FEATURE, "Category frequencies are stable.", feature=feature.name, score=max_delta, threshold=self.config.category_frequency_warning_delta)

    def _unseen_category_finding(self, feature: FeatureSpec, baseline_values: Sequence[Any], current_values: Sequence[Any]) -> DriftFinding:
        base_categories = set(str(v) for v in non_null(baseline_values))
        cur_values = [str(v) for v in non_null(current_values)]
        if not cur_values:
            return self._skipped(feature, DriftTestType.UNSEEN_CATEGORY, "No current non-null categorical values.")
        unseen = [v for v in cur_values if v not in base_categories]
        rate = len(unseen) / len(cur_values)
        unseen_set = sorted(set(unseen))[:50]
        if rate >= self.config.unseen_category_error_rate:
            return self._drift_finding(feature, DriftTestType.UNSEEN_CATEGORY, rate, self.config.unseen_category_error_rate, DriftSeverity.ERROR, "Unseen category drift detected.", sorted(base_categories), unseen_set, "Review new categories or update allowed category registry.")
        if rate >= self.config.unseen_category_warning_rate:
            return self._drift_finding(feature, DriftTestType.UNSEEN_CATEGORY, rate, self.config.unseen_category_warning_rate, DriftSeverity.WARNING, "Unseen category warning detected.", sorted(base_categories), unseen_set, "Review new categories.")
        return self._passed(f"{feature.name}.unseen_category", DriftTestType.UNSEEN_CATEGORY, DriftScope.FEATURE, "No significant unseen category rate detected.", feature=feature.name, score=rate, threshold=self.config.unseen_category_warning_rate)

    def _score_to_finding(
        self,
        feature: FeatureSpec,
        test_type: DriftTestType,
        score: float,
        warning_threshold: float,
        error_threshold: float,
        message: str,
        *,
        baseline: Optional[Any] = None,
        current: Optional[Any] = None,
    ) -> DriftFinding:
        warning = feature.warning_threshold if feature.warning_threshold is not None else warning_threshold
        error = feature.error_threshold if feature.error_threshold is not None else error_threshold
        if score >= error:
            return self._drift_finding(feature, test_type, score, error, DriftSeverity.ERROR, message, baseline, current, "Investigate feature distribution shift.")
        if score >= warning:
            return self._drift_finding(feature, test_type, score, warning, DriftSeverity.WARNING, message.replace("detected", "warning detected"), baseline, current, "Review feature distribution shift.")
        return self._passed(f"{feature.name}.{test_type.value}", test_type, DriftScope.FEATURE, f"{test_type.value} is stable.", feature=feature.name, score=score, threshold=warning)

    def _drift_finding(
        self,
        feature: FeatureSpec,
        test_type: DriftTestType,
        score: float,
        threshold: float,
        severity: DriftSeverity,
        message: str,
        baseline: Any,
        current: Any,
        remediation: str,
        *,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> DriftFinding:
        return self._finding(
            check_id=f"{feature.name}.{test_type.value}",
            test_type=test_type,
            scope=DriftScope.FEATURE,
            status=DriftStatus.DRIFTED if severity in {DriftSeverity.ERROR, DriftSeverity.CRITICAL} else DriftStatus.WARNING,
            severity=severity,
            message=message,
            feature=feature.name,
            score=score,
            threshold=threshold,
            evidence=(DriftEvidence(key=test_type.value, value=score, baseline=baseline, current=current, metadata=dict(extra or {})),),
            remediation=remediation,
        )

    def _custom_finding(self, feature: FeatureSpec, result: DriftRuleResult) -> DriftFinding:
        return self._finding(
            check_id=f"{feature.name}.custom",
            test_type=DriftTestType.CUSTOM,
            scope=DriftScope.FEATURE,
            status=DriftStatus.DRIFTED if result.drifted else DriftStatus.STABLE,
            severity=result.severity if result.drifted else DriftSeverity.INFO,
            message=result.message,
            feature=feature.name,
            score=result.score,
            threshold=result.threshold,
            evidence=result.evidence,
            remediation=result.remediation,
            metadata=result.metadata,
        )

    def _skipped(self, feature: FeatureSpec, test_type: DriftTestType, message: str) -> DriftFinding:
        return self._finding(
            check_id=f"{feature.name}.{test_type.value}",
            test_type=test_type,
            scope=DriftScope.FEATURE,
            status=DriftStatus.SKIPPED,
            severity=DriftSeverity.INFO,
            message=message,
            feature=feature.name,
        )

    def _passed(
        self,
        check_id: str,
        test_type: DriftTestType,
        scope: DriftScope,
        message: str,
        *,
        feature: Optional[str] = None,
        score: Optional[float] = None,
        threshold: Optional[float] = None,
    ) -> DriftFinding:
        return self._finding(
            check_id=check_id,
            test_type=test_type,
            scope=scope,
            status=DriftStatus.STABLE,
            severity=DriftSeverity.INFO,
            message=message,
            feature=feature,
            score=score,
            threshold=threshold,
        )

    def _finding(
        self,
        *,
        check_id: str,
        test_type: DriftTestType,
        scope: DriftScope,
        status: DriftStatus,
        severity: DriftSeverity,
        message: str,
        feature: Optional[str] = None,
        score: Optional[float] = None,
        threshold: Optional[float] = None,
        evidence: Sequence[DriftEvidence] = (),
        remediation: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> DriftFinding:
        ev = tuple(
            DriftEvidence(
                key=item.key,
                value=truncate(item.value, self.config.max_evidence_chars),
                baseline=truncate(item.baseline, self.config.max_evidence_chars) if item.baseline is not None else None,
                current=truncate(item.current, self.config.max_evidence_chars) if item.current is not None else None,
                metadata=item.metadata,
            )
            for item in evidence
        )
        return DriftFinding(
            finding_id=str(uuid.uuid4()),
            check_id=check_id,
            test_type=test_type,
            scope=scope,
            status=status,
            severity=severity,
            message=message,
            feature=feature,
            score=score,
            threshold=threshold,
            evidence=ev,
            remediation=remediation,
            metadata=metadata or {},
        )

    def _build_report(
        self,
        *,
        context: DriftContext,
        baseline_records: Sequence[Mapping[str, Any]],
        current_records: Sequence[Mapping[str, Any]],
        feature_count: int,
        checks_evaluated: int,
        stable_checks: int,
        warning_checks: int,
        drifted_checks: int,
        skipped_checks: int,
        findings: Sequence[DriftFinding],
        latency_ms: float,
    ) -> DriftReport:
        risk_score = self._risk_score(findings)
        decision = self._decision(findings, risk_score)
        status = self._status(decision, findings)
        return DriftReport(
            report_id=str(uuid.uuid4()),
            request_id=context.request_id,
            created_at=utc_now_iso(),
            status=status,
            decision=decision,
            risk_score=risk_score,
            features_evaluated=feature_count,
            checks_evaluated=checks_evaluated,
            stable_checks=stable_checks,
            warning_checks=warning_checks,
            drifted_checks=drifted_checks,
            skipped_checks=skipped_checks,
            baseline_rows=len(baseline_records),
            current_rows=len(current_records),
            findings=tuple(findings),
            recommendations=tuple(self._recommendations(findings, decision)),
            metadata={
                "tenant_id": context.tenant_id,
                "application": context.application,
                "pipeline_id": context.pipeline_id,
                "dataset_id": context.dataset_id,
                "model_id": context.model_id,
                "baseline_window": context.baseline_window,
                "current_window": context.current_window,
                "environment": context.environment,
                "latency_ms": round(latency_ms, 3),
                "validator_version": self.config.version,
            },
        )

    def _risk_score(self, findings: Sequence[DriftFinding]) -> float:
        weights = {
            DriftSeverity.INFO: 0.0,
            DriftSeverity.WARNING: 0.25,
            DriftSeverity.ERROR: 0.70,
            DriftSeverity.CRITICAL: 1.0,
        }
        active = [finding for finding in findings if finding.status not in {DriftStatus.STABLE, DriftStatus.SKIPPED}]
        if not active:
            return 0.0
        max_weight = max(weights[finding.severity] for finding in active)
        avg_weight = sum(weights[finding.severity] for finding in active) / len(active)
        score_component = max((finding.score or 0.0) for finding in active)
        score_component = min(score_component, 1.0)
        return clamp((max_weight * 0.60) + (avg_weight * 0.25) + (score_component * 0.15))

    def _decision(self, findings: Sequence[DriftFinding], risk_score: float) -> DriftDecision:
        severities = {finding.severity for finding in findings if finding.status not in {DriftStatus.STABLE, DriftStatus.SKIPPED}}
        if self.config.block_on_critical and DriftSeverity.CRITICAL in severities:
            return DriftDecision.BLOCK
        if risk_score >= 0.85:
            return DriftDecision.BLOCK
        if self.config.review_on_error and DriftSeverity.ERROR in severities:
            return DriftDecision.REVIEW
        if risk_score >= 0.25:
            return DriftDecision.REVIEW
        return DriftDecision.ALLOW

    def _status(self, decision: DriftDecision, findings: Sequence[DriftFinding]) -> DriftStatus:
        if any(finding.status == DriftStatus.ERROR for finding in findings):
            return DriftStatus.ERROR
        if decision == DriftDecision.BLOCK:
            return DriftStatus.DRIFTED
        if decision == DriftDecision.REVIEW:
            return DriftStatus.WARNING
        return DriftStatus.STABLE

    def _recommendations(self, findings: Sequence[DriftFinding], decision: DriftDecision) -> List[str]:
        recommendations: List[str] = []
        if decision == DriftDecision.BLOCK:
            recommendations.append("Block downstream promotion or model scoring until drift is investigated and remediated.")
        elif decision == DriftDecision.REVIEW:
            recommendations.append("Route drift report to data owner/model owner for review before downstream use.")
        else:
            recommendations.append("Drift validation passed within configured thresholds.")
        for finding in findings:
            if finding.status not in {DriftStatus.STABLE, DriftStatus.SKIPPED} and finding.remediation:
                recommendations.append(finding.remediation)
        return list(dict.fromkeys(recommendations))

    async def _record_success(self, context: DriftContext, report: DriftReport) -> None:
        if not self.config.metrics_enabled:
            return
        tags = self._metric_tags(context, report.decision)
        await self.metrics_sink.increment("data.validation.drift.success", 1, tags)
        await self.metrics_sink.observe("data.validation.drift.risk_score", report.risk_score, tags)
        await self.metrics_sink.observe("data.validation.drift.findings", len(report.findings), tags)
        await self.metrics_sink.observe("data.validation.drift.current_rows", report.current_rows, tags)

    async def _record_failure(self, context: DriftContext, exc: BaseException, latency_ms: float) -> None:
        if not self.config.metrics_enabled:
            return
        tags = {**self._metric_tags(context, DriftDecision.BLOCK), "error_type": type(exc).__name__}
        await self.metrics_sink.increment("data.validation.drift.failure", 1, tags)
        await self.metrics_sink.observe("data.validation.drift.failure_latency_ms", latency_ms, tags)

    def _metric_tags(self, context: DriftContext, decision: DriftDecision) -> Mapping[str, str]:
        return {
            "tenant_id": context.tenant_id or "unknown",
            "application": context.application or "unknown",
            "environment": context.environment or "unknown",
            "dataset_id": context.dataset_id or "unknown",
            "model_id": context.model_id or "none",
            "decision": decision.value,
        }

    async def _audit_completed(self, context: DriftContext, report: DriftReport) -> None:
        if not self.config.audit_enabled:
            return
        await self.audit_sink.emit("drift_validation_completed", {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": context.request_id,
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "application": context.application,
            "pipeline_id": context.pipeline_id,
            "dataset_id": context.dataset_id,
            "model_id": context.model_id,
            "environment": context.environment,
            "trace_id": context.trace_id,
            "baseline_window": context.baseline_window,
            "current_window": context.current_window,
            "report_id": report.report_id,
            "status": report.status.value,
            "decision": report.decision.value,
            "risk_score": report.risk_score,
            "features_evaluated": report.features_evaluated,
            "checks_evaluated": report.checks_evaluated,
            "baseline_rows": report.baseline_rows,
            "current_rows": report.current_rows,
            "findings": [asdict(finding) for finding in report.findings],
        })

    async def _audit_failure(self, context: DriftContext, exc: BaseException, latency_ms: float) -> None:
        if not self.config.audit_enabled:
            return
        await self.audit_sink.emit("drift_validation_failed", {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": context.request_id,
            "tenant_id": context.tenant_id,
            "application": context.application,
            "dataset_id": context.dataset_id,
            "model_id": context.model_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "latency_ms": round(latency_ms, 3),
        })


# =============================================================================
# Factory Helpers
# =============================================================================


def build_default_feature_specs() -> Sequence[FeatureSpec]:
    """Build example feature specs for local testing."""

    return (
        FeatureSpec(name="amount", feature_type=FeatureType.NUMERIC),
        FeatureSpec(name="status", feature_type=FeatureType.CATEGORICAL),
        FeatureSpec(name="country", feature_type=FeatureType.CATEGORICAL),
    )


def build_default_drift_validator(
    *,
    features: Optional[Sequence[FeatureSpec]] = None,
    config: Optional[DriftValidatorConfig] = None,
    custom_rules: Optional[Mapping[str, CustomDriftRule]] = None,
) -> DriftValidator:
    return DriftValidator(features=features, config=config, custom_rules=custom_rules)


async def _demo_async() -> None:
    logging.basicConfig(level=logging.INFO)
    baseline = [
        {"amount": 100 + i, "status": "paid", "country": "BR"} for i in range(100)
    ]
    current = [
        {"amount": 180 + i, "status": "paid" if i < 70 else "late", "country": "BR" if i < 95 else "US"}
        for i in range(100)
    ]
    validator = build_default_drift_validator(features=build_default_feature_specs())
    report = await validator.validate(
        baseline,
        current,
        context=DriftContext(
            tenant_id="demo",
            application="data-platform",
            dataset_id="payments",
            baseline_window="2026-04-01/2026-04-30",
            current_window="2026-05-01/2026-05-12",
            environment="dev",
        ),
    )
    print(report.to_json(indent=2))


if __name__ == "__main__":
    asyncio.run(_demo_async())

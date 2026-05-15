"""
data/quality/quality_scoring.py

Enterprise-grade Data Quality Scoring Engine.

This module centralizes score calculation for enterprise data quality outputs,
including rule results, profiling reports, validation summaries, dashboard
inputs, null analysis, freshness, consistency, completeness, accuracy, and
custom dimensions.

Main capabilities:
- Weighted quality scoring by rule, dimension, dataset, domain and pipeline
- Configurable penalties by severity, status, failure rate and confidence
- Dimension-specific score aggregation
- Explainable score contributions and penalty breakdowns
- Trend comparison against previous snapshots
- Confidence-aware scoring for sampled checks
- SLO/SLA-friendly normalized scorecards
- JSON export for dashboards, audit, metrics and APIs
- Dependency-light implementation with optional pandas-friendly ingestion

Designed for enterprise lakehouse quality gates, observability dashboards,
compliance evidence, governance workflows, orchestration decisions, and
executive data quality scorecards.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple


# =============================================================================
# Logging
# =============================================================================

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# =============================================================================
# Exceptions
# =============================================================================


class QualityScoringError(Exception):
    """Base exception for quality scoring failures."""


class QualityScoringConfigurationError(QualityScoringError):
    """Raised when scoring configuration is invalid."""


class QualityScoringExecutionError(QualityScoringError):
    """Raised when scoring execution fails."""


# =============================================================================
# Enums
# =============================================================================


class QualityDimension(str, Enum):
    """Standard data quality dimensions."""

    ACCURACY = "accuracy"
    COMPLETENESS = "completeness"
    CONSISTENCY = "consistency"
    FRESHNESS = "freshness"
    VALIDITY = "validity"
    UNIQUENESS = "uniqueness"
    INTEGRITY = "integrity"
    TIMELINESS = "timeliness"
    NULL_ANALYSIS = "null_analysis"
    PROFILING = "profiling"
    GOVERNANCE = "governance"
    SECURITY = "security"
    CUSTOM = "custom"


class QualityStatus(str, Enum):
    """Normalized quality status."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    """Normalized severity."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ScoreGrade(str, Enum):
    """Human-readable score grade."""

    EXCELLENT = "excellent"
    GOOD = "good"
    ACCEPTABLE = "acceptable"
    DEGRADED = "degraded"
    POOR = "poor"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class TrendDirection(str, Enum):
    """Score trend direction."""

    IMPROVING = "improving"
    STABLE = "stable"
    DEGRADING = "degrading"
    UNKNOWN = "unknown"


class AggregationStrategy(str, Enum):
    """Score aggregation strategy."""

    WEIGHTED_AVERAGE = "weighted_average"
    ARITHMETIC_MEAN = "arithmetic_mean"
    MINIMUM = "minimum"
    HARMONIC_MEAN = "harmonic_mean"
    PENALIZED_WEIGHTED = "penalized_weighted"


class PenaltyMode(str, Enum):
    """Penalty application mode."""

    ADDITIVE = "additive"
    MULTIPLICATIVE = "multiplicative"
    CAPPED = "capped"


# =============================================================================
# Protocols
# =============================================================================


class MetricsSink(Protocol):
    """Optional sink for publishing scoring metrics."""

    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def timing(self, metric_name: str, value_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...


class AuditSink(Protocol):
    """Optional sink for scoring audit events."""

    def write_event(self, event: Mapping[str, Any]) -> None:
        ...


# =============================================================================
# Configuration
# =============================================================================


@dataclass(frozen=True)
class GradeThresholds:
    """Score thresholds used to assign grades."""

    excellent: float = 0.99
    good: float = 0.97
    acceptable: float = 0.95
    degraded: float = 0.85
    poor: float = 0.70

    def validate(self) -> None:
        values = [self.excellent, self.good, self.acceptable, self.degraded, self.poor]
        if any(value < 0 or value > 1 for value in values):
            raise QualityScoringConfigurationError("Grade thresholds must be between 0 and 1.")
        if not (self.excellent >= self.good >= self.acceptable >= self.degraded >= self.poor):
            raise QualityScoringConfigurationError(
                "Expected excellent >= good >= acceptable >= degraded >= poor."
            )

    def grade(self, score: Optional[float]) -> ScoreGrade:
        if score is None:
            return ScoreGrade.UNKNOWN
        if score >= self.excellent:
            return ScoreGrade.EXCELLENT
        if score >= self.good:
            return ScoreGrade.GOOD
        if score >= self.acceptable:
            return ScoreGrade.ACCEPTABLE
        if score >= self.degraded:
            return ScoreGrade.DEGRADED
        if score >= self.poor:
            return ScoreGrade.POOR
        return ScoreGrade.CRITICAL


@dataclass(frozen=True)
class SeverityPenaltyConfig:
    """Default penalty multipliers/amounts by severity."""

    info: float = 0.00
    low: float = 0.01
    medium: float = 0.03
    high: float = 0.08
    critical: float = 0.20

    def penalty_for(self, severity: Severity) -> float:
        return {
            Severity.INFO: self.info,
            Severity.LOW: self.low,
            Severity.MEDIUM: self.medium,
            Severity.HIGH: self.high,
            Severity.CRITICAL: self.critical,
        }[severity]

    def validate(self) -> None:
        for value in asdict(self).values():
            if value < 0 or value > 1:
                raise QualityScoringConfigurationError("Severity penalties must be between 0 and 1.")


@dataclass(frozen=True)
class StatusPenaltyConfig:
    """Default penalty by normalized status."""

    passed: float = 0.00
    warning: float = 0.03
    failed: float = 0.12
    error: float = 0.20
    skipped: float = 0.00
    unknown: float = 0.05

    def penalty_for(self, status: QualityStatus) -> float:
        return {
            QualityStatus.PASSED: self.passed,
            QualityStatus.WARNING: self.warning,
            QualityStatus.FAILED: self.failed,
            QualityStatus.ERROR: self.error,
            QualityStatus.SKIPPED: self.skipped,
            QualityStatus.UNKNOWN: self.unknown,
        }[status]

    def validate(self) -> None:
        for value in asdict(self).values():
            if value < 0 or value > 1:
                raise QualityScoringConfigurationError("Status penalties must be between 0 and 1.")


@dataclass(frozen=True)
class QualityScoringConfig:
    """Configuration for the scoring engine."""

    aggregation_strategy: AggregationStrategy = AggregationStrategy.PENALIZED_WEIGHTED
    penalty_mode: PenaltyMode = PenaltyMode.ADDITIVE
    grade_thresholds: GradeThresholds = field(default_factory=GradeThresholds)
    severity_penalties: SeverityPenaltyConfig = field(default_factory=SeverityPenaltyConfig)
    status_penalties: StatusPenaltyConfig = field(default_factory=StatusPenaltyConfig)
    dimension_weights: Dict[QualityDimension, float] = field(
        default_factory=lambda: {
            QualityDimension.ACCURACY: 1.20,
            QualityDimension.COMPLETENESS: 1.15,
            QualityDimension.CONSISTENCY: 1.10,
            QualityDimension.FRESHNESS: 1.05,
            QualityDimension.VALIDITY: 1.00,
            QualityDimension.UNIQUENESS: 1.00,
            QualityDimension.INTEGRITY: 1.20,
            QualityDimension.NULL_ANALYSIS: 0.90,
            QualityDimension.PROFILING: 0.80,
            QualityDimension.GOVERNANCE: 0.90,
            QualityDimension.SECURITY: 1.20,
            QualityDimension.CUSTOM: 1.00,
        }
    )
    minimum_confidence: float = 0.50
    low_confidence_penalty: float = 0.05
    max_total_penalty: float = 0.70
    skipped_rules_affect_score: bool = False
    unknown_score_default: float = 0.0
    trend_significant_delta: float = 0.02

    def validate(self) -> None:
        self.grade_thresholds.validate()
        self.severity_penalties.validate()
        self.status_penalties.validate()
        if self.minimum_confidence < 0 or self.minimum_confidence > 1:
            raise QualityScoringConfigurationError("minimum_confidence must be between 0 and 1.")
        if self.low_confidence_penalty < 0 or self.low_confidence_penalty > 1:
            raise QualityScoringConfigurationError("low_confidence_penalty must be between 0 and 1.")
        if self.max_total_penalty < 0 or self.max_total_penalty > 1:
            raise QualityScoringConfigurationError("max_total_penalty must be between 0 and 1.")
        if self.unknown_score_default < 0 or self.unknown_score_default > 1:
            raise QualityScoringConfigurationError("unknown_score_default must be between 0 and 1.")
        if self.trend_significant_delta < 0 or self.trend_significant_delta > 1:
            raise QualityScoringConfigurationError("trend_significant_delta must be between 0 and 1.")
        for dimension, weight in self.dimension_weights.items():
            if weight < 0:
                raise QualityScoringConfigurationError(f"Dimension weight cannot be negative: {dimension}")


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class QualityScoreInput:
    """Normalized input item for scoring."""

    name: str
    dimension: QualityDimension
    status: QualityStatus
    score: Optional[float] = None
    severity: Severity = Severity.INFO
    weight: float = 1.0
    confidence: float = 1.0
    dataset_name: Optional[str] = None
    domain: Optional[str] = None
    pipeline_name: Optional[str] = None
    rule_id: Optional[str] = None
    evaluated_records: Optional[int] = None
    failed_records: Optional[int] = None
    error_records: Optional[int] = None
    skipped_records: Optional[int] = None
    duration_ms: Optional[float] = None
    executed_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.name.strip():
            raise QualityScoringConfigurationError("QualityScoreInput.name is required.")
        if self.score is not None and (self.score < 0 or self.score > 1):
            raise QualityScoringConfigurationError(f"Score must be between 0 and 1 for input: {self.name}")
        if self.weight < 0:
            raise QualityScoringConfigurationError(f"Weight cannot be negative for input: {self.name}")
        if self.confidence < 0 or self.confidence > 1:
            raise QualityScoringConfigurationError(f"Confidence must be between 0 and 1 for input: {self.name}")

    def failure_rate(self) -> Optional[float]:
        if self.evaluated_records is None or self.evaluated_records <= 0:
            return None
        failed = (self.failed_records or 0) + (self.error_records or 0)
        return round(failed / self.evaluated_records, 8)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["dimension"] = self.dimension.value
        data["status"] = self.status.value
        data["severity"] = self.severity.value
        return _json_safe(data)


@dataclass
class ScorePenalty:
    """Explainable penalty applied to a score item or aggregate."""

    penalty_id: str
    reason: str
    amount: float
    severity: Severity = Severity.INFO
    source_name: Optional[str] = None
    dimension: Optional[QualityDimension] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        data["dimension"] = self.dimension.value if self.dimension else None
        return _json_safe(data)


@dataclass
class ScoreContribution:
    """Explainable contribution of one score input."""

    name: str
    dimension: QualityDimension
    raw_score: float
    adjusted_score: float
    weight: float
    weighted_score: float
    status: QualityStatus
    severity: Severity
    confidence: float
    penalties: List[ScorePenalty] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["dimension"] = self.dimension.value
        data["status"] = self.status.value
        data["severity"] = self.severity.value
        data["penalties"] = [p.to_dict() for p in self.penalties]
        return _json_safe(data)


@dataclass
class DimensionScore:
    """Aggregated score for one quality dimension."""

    dimension: QualityDimension
    score: float
    grade: ScoreGrade
    weight: float
    input_count: int
    failed_count: int
    warning_count: int
    error_count: int
    average_confidence: float
    penalties: List[ScorePenalty] = field(default_factory=list)
    contributions: List[ScoreContribution] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["dimension"] = self.dimension.value
        data["grade"] = self.grade.value
        data["penalties"] = [p.to_dict() for p in self.penalties]
        data["contributions"] = [c.to_dict() for c in self.contributions]
        return _json_safe(data)


@dataclass
class DatasetScorecard:
    """Dataset-level quality scorecard."""

    dataset_name: str
    score: float
    grade: ScoreGrade
    status: QualityStatus
    trend: TrendDirection = TrendDirection.UNKNOWN
    previous_score: Optional[float] = None
    delta: Optional[float] = None
    dimension_scores: Dict[str, float] = field(default_factory=dict)
    input_count: int = 0
    failed_count: int = 0
    critical_count: int = 0
    average_confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["grade"] = self.grade.value
        data["status"] = self.status.value
        data["trend"] = self.trend.value
        return _json_safe(data)


@dataclass
class QualityScoreSnapshot:
    """Complete scoring result snapshot."""

    snapshot_id: str
    generated_at: str
    overall_score: float
    overall_grade: ScoreGrade
    overall_status: QualityStatus
    trend: TrendDirection
    previous_score: Optional[float]
    delta: Optional[float]
    dimension_scores: List[DimensionScore]
    dataset_scorecards: List[DatasetScorecard]
    contributions: List[ScoreContribution]
    penalties: List[ScorePenalty]
    input_count: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(
        self,
        *,
        include_contributions: bool = True,
        include_penalties: bool = True,
    ) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "generated_at": self.generated_at,
            "overall_score": self.overall_score,
            "overall_grade": self.overall_grade.value,
            "overall_status": self.overall_status.value,
            "trend": self.trend.value,
            "previous_score": self.previous_score,
            "delta": self.delta,
            "input_count": self.input_count,
            "metadata": _json_safe(self.metadata),
            "dimension_scores": [d.to_dict() for d in self.dimension_scores],
            "dataset_scorecards": [d.to_dict() for d in self.dataset_scorecards],
            "contributions": [c.to_dict() for c in self.contributions] if include_contributions else [],
            "penalties": [p.to_dict() for p in self.penalties] if include_penalties else [],
        }

    def to_json(
        self,
        *,
        include_contributions: bool = True,
        include_penalties: bool = True,
        indent: int = 2,
    ) -> str:
        return json.dumps(
            self.to_dict(include_contributions=include_contributions, include_penalties=include_penalties),
            indent=indent,
            ensure_ascii=False,
        )

    def save_json(self, path: str | Path, indent: int = 2) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(self.to_json(indent=indent), encoding="utf-8")
        return output


# =============================================================================
# Helpers
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 8)


def _safe_mean(values: Sequence[float], default: float = 1.0) -> float:
    clean = [float(v) for v in values if not math.isnan(float(v))]
    if not clean:
        return default
    return round(sum(clean) / len(clean), 8)


def _weighted_average(values: Sequence[Tuple[float, float]], default: float = 1.0) -> float:
    filtered = [(score, weight) for score, weight in values if weight > 0]
    if not filtered:
        return default
    denominator = sum(weight for _, weight in filtered)
    if denominator <= 0:
        return default
    return _clamp_score(sum(score * weight for score, weight in filtered) / denominator)


def _harmonic_mean(values: Sequence[float], default: float = 1.0) -> float:
    clean = [float(v) for v in values if v > 0]
    if not clean:
        return default
    return _clamp_score(len(clean) / sum(1.0 / v for v in clean))


def _severity_rank(severity: Severity) -> int:
    return {
        Severity.INFO: 0,
        Severity.LOW: 1,
        Severity.MEDIUM: 2,
        Severity.HIGH: 3,
        Severity.CRITICAL: 4,
    }[severity]


def _normalize_dimension(value: Any) -> QualityDimension:
    if isinstance(value, QualityDimension):
        return value
    raw = str(value or "custom").lower()
    for dimension in QualityDimension:
        if dimension.value == raw:
            return dimension
    if "accuracy" in raw:
        return QualityDimension.ACCURACY
    if "complete" in raw:
        return QualityDimension.COMPLETENESS
    if "consistent" in raw:
        return QualityDimension.CONSISTENCY
    if "fresh" in raw:
        return QualityDimension.FRESHNESS
    if "null" in raw:
        return QualityDimension.NULL_ANALYSIS
    if "profil" in raw:
        return QualityDimension.PROFILING
    return QualityDimension.CUSTOM


def _normalize_status(value: Any) -> QualityStatus:
    if isinstance(value, QualityStatus):
        return value
    raw = str(value or "unknown").lower()
    mapping = {
        "passed": QualityStatus.PASSED,
        "pass": QualityStatus.PASSED,
        "succeeded": QualityStatus.PASSED,
        "success": QualityStatus.PASSED,
        "healthy": QualityStatus.PASSED,
        "warning": QualityStatus.WARNING,
        "warn": QualityStatus.WARNING,
        "failed": QualityStatus.FAILED,
        "fail": QualityStatus.FAILED,
        "degraded": QualityStatus.FAILED,
        "critical": QualityStatus.FAILED,
        "error": QualityStatus.ERROR,
        "skipped": QualityStatus.SKIPPED,
        "unknown": QualityStatus.UNKNOWN,
    }
    return mapping.get(raw, QualityStatus.UNKNOWN)


def _normalize_severity(value: Any) -> Severity:
    if isinstance(value, Severity):
        return value
    raw = str(value or "info").lower()
    mapping = {
        "debug": Severity.INFO,
        "info": Severity.INFO,
        "low": Severity.LOW,
        "medium": Severity.MEDIUM,
        "warning": Severity.MEDIUM,
        "warn": Severity.MEDIUM,
        "high": Severity.HIGH,
        "critical": Severity.CRITICAL,
        "severe": Severity.CRITICAL,
    }
    return mapping.get(raw, Severity.INFO)


def _first_float(payload: Mapping[str, Any], keys: Sequence[str]) -> Optional[float]:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _first_int(payload: Mapping[str, Any], keys: Sequence[str]) -> Optional[int]:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def _trend(current: Optional[float], previous: Optional[float], significant_delta: float) -> Tuple[TrendDirection, Optional[float]]:
    if current is None or previous is None:
        return TrendDirection.UNKNOWN, None
    delta = round(current - previous, 8)
    if abs(delta) < significant_delta:
        return TrendDirection.STABLE, delta
    return (TrendDirection.IMPROVING if delta > 0 else TrendDirection.DEGRADING), delta


# =============================================================================
# Sinks
# =============================================================================


class NoopMetricsSink:
    """Default metrics sink that intentionally does nothing."""

    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        return None

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        return None

    def timing(self, metric_name: str, value_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        return None


class InMemoryAuditSink:
    """Simple audit sink useful for tests, local execution, and debugging."""

    def __init__(self) -> None:
        self.events: List[Mapping[str, Any]] = []

    def write_event(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))


# =============================================================================
# Scoring Engine
# =============================================================================


class QualityScoringEngine:
    """Enterprise data quality scoring engine."""

    def __init__(
        self,
        config: Optional[QualityScoringConfig] = None,
        *,
        metrics_sink: Optional[MetricsSink] = None,
        audit_sink: Optional[AuditSink] = None,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or QualityScoringConfig()
        self.config.validate()
        self.metrics_sink = metrics_sink or NoopMetricsSink()
        self.audit_sink = audit_sink
        self.logger = logger_ or logger

    def score(
        self,
        inputs: Sequence[QualityScoreInput | Mapping[str, Any]],
        *,
        previous_snapshot: Optional[QualityScoreSnapshot | Mapping[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> QualityScoreSnapshot:
        """Calculate a complete scoring snapshot from normalized or raw inputs."""
        normalized = [self.normalize_input(item) for item in inputs]
        for item in normalized:
            item.validate()

        active_inputs = [
            item
            for item in normalized
            if self.config.skipped_rules_affect_score or item.status != QualityStatus.SKIPPED
        ]

        contributions = [self._contribution(item) for item in active_inputs]
        dimension_scores = self._dimension_scores(contributions)
        dataset_scorecards = self._dataset_scorecards(contributions, previous_snapshot)
        overall_score = self._aggregate_dimension_scores(dimension_scores)
        penalties = [penalty for contribution in contributions for penalty in contribution.penalties]
        overall_grade = self.config.grade_thresholds.grade(overall_score)
        overall_status = self._status_from_score(overall_score, contributions)
        previous_score = self._previous_overall_score(previous_snapshot)
        trend, delta = _trend(overall_score, previous_score, self.config.trend_significant_delta)

        snapshot = QualityScoreSnapshot(
            snapshot_id=str(uuid.uuid4()),
            generated_at=utc_now_iso(),
            overall_score=overall_score,
            overall_grade=overall_grade,
            overall_status=overall_status,
            trend=trend,
            previous_score=previous_score,
            delta=delta,
            dimension_scores=dimension_scores,
            dataset_scorecards=dataset_scorecards,
            contributions=contributions,
            penalties=penalties,
            input_count=len(normalized),
            metadata=metadata or {},
        )

        self._publish_metrics(snapshot)
        self._write_audit(snapshot)
        return snapshot

    def normalize_input(self, item: QualityScoreInput | Mapping[str, Any]) -> QualityScoreInput:
        """Normalize a score input from an object or raw report/result dictionary."""
        if isinstance(item, QualityScoreInput):
            return item
        payload = dict(item)
        score = _first_float(
            payload,
            [
                "score",
                "quality_score",
                "overall_score",
                "weighted_score",
                "accuracy_score",
                "completeness_score",
                "consistency_score",
                "freshness_score",
                "validity_score",
            ],
        )
        return QualityScoreInput(
            name=str(payload.get("name") or payload.get("rule_name") or payload.get("check_name") or payload.get("report_id") or "quality_score_input"),
            dimension=_normalize_dimension(payload.get("dimension") or payload.get("rule_type") or payload.get("type")),
            status=_normalize_status(payload.get("status")),
            score=score,
            severity=_normalize_severity(payload.get("severity")),
            weight=float(payload.get("weight") or payload.get("rule_weight") or 1.0),
            confidence=float(payload.get("confidence") or 1.0),
            dataset_name=payload.get("dataset_name"),
            domain=payload.get("domain"),
            pipeline_name=payload.get("pipeline_name"),
            rule_id=payload.get("rule_id"),
            evaluated_records=_first_int(payload, ["evaluated_records", "total_records", "row_count"]),
            failed_records=_first_int(payload, ["failed_records", "incomplete_records", "inconsistent_records", "stale_records", "invalid_records"]),
            error_records=_first_int(payload, ["error_records"]),
            skipped_records=_first_int(payload, ["skipped_records"]),
            duration_ms=_first_float(payload, ["duration_ms"]),
            executed_at=payload.get("executed_at") or payload.get("finished_at") or payload.get("generated_at") or payload.get("started_at"),
            metadata={k: _json_safe(v) for k, v in payload.items()},
        )

    def inputs_from_report(self, report: Mapping[str, Any]) -> List[QualityScoreInput]:
        """Extract score inputs from a quality report dictionary."""
        payload = dict(report)
        dataset_name = payload.get("dataset_name")
        dimension = _normalize_dimension(payload.get("dimension") or payload.get("report_type") or payload.get("type"))
        inputs: List[QualityScoreInput] = []

        if any(key in payload for key in ["overall_score", "weighted_score", "quality_score", "status"]):
            base = dict(payload)
            base.setdefault("name", payload.get("report_id") or "quality_report")
            base.setdefault("dataset_name", dataset_name)
            base.setdefault("dimension", dimension.value)
            inputs.append(self.normalize_input(base))

        for result in payload.get("rule_results") or []:
            item = dict(result)
            item.setdefault("dataset_name", dataset_name)
            item.setdefault("dimension", dimension.value)
            inputs.append(self.normalize_input(item))

        return inputs

    def score_reports(
        self,
        reports: Sequence[Mapping[str, Any]],
        *,
        previous_snapshot: Optional[QualityScoreSnapshot | Mapping[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> QualityScoreSnapshot:
        inputs: List[QualityScoreInput] = []
        for report in reports:
            inputs.extend(self.inputs_from_report(report))
        return self.score(inputs, previous_snapshot=previous_snapshot, metadata=metadata)

    def _contribution(self, item: QualityScoreInput) -> ScoreContribution:
        raw_score = self._raw_score(item)
        penalties = self._penalties(item, raw_score)
        adjusted_score = self._apply_penalties(raw_score, penalties)
        dimension_weight = self.config.dimension_weights.get(item.dimension, 1.0)
        effective_weight = item.weight * dimension_weight * max(item.confidence, 0.01)
        weighted_score = adjusted_score * effective_weight

        return ScoreContribution(
            name=item.name,
            dimension=item.dimension,
            raw_score=raw_score,
            adjusted_score=adjusted_score,
            weight=round(effective_weight, 8),
            weighted_score=round(weighted_score, 8),
            status=item.status,
            severity=item.severity,
            confidence=item.confidence,
            penalties=penalties,
            metadata={
                "dataset_name": item.dataset_name,
                "domain": item.domain,
                "pipeline_name": item.pipeline_name,
                "rule_id": item.rule_id,
                "failure_rate": item.failure_rate(),
                "evaluated_records": item.evaluated_records,
                "failed_records": item.failed_records,
                "error_records": item.error_records,
                "executed_at": item.executed_at,
                **item.metadata,
            },
        )

    def _raw_score(self, item: QualityScoreInput) -> float:
        if item.score is not None:
            return _clamp_score(item.score)
        failure_rate = item.failure_rate()
        if failure_rate is not None:
            return _clamp_score(1.0 - failure_rate)
        if item.status == QualityStatus.PASSED:
            return 1.0
        if item.status == QualityStatus.WARNING:
            return 0.95
        if item.status == QualityStatus.FAILED:
            return 0.80
        if item.status == QualityStatus.ERROR:
            return 0.70
        if item.status == QualityStatus.SKIPPED:
            return 1.0 if not self.config.skipped_rules_affect_score else 0.90
        return self.config.unknown_score_default

    def _penalties(self, item: QualityScoreInput, raw_score: float) -> List[ScorePenalty]:
        penalties: List[ScorePenalty] = []

        status_penalty = self.config.status_penalties.penalty_for(item.status)
        if status_penalty > 0:
            penalties.append(
                ScorePenalty(
                    penalty_id=str(uuid.uuid4()),
                    reason=f"status_{item.status.value}",
                    amount=status_penalty,
                    severity=item.severity,
                    source_name=item.name,
                    dimension=item.dimension,
                )
            )

        severity_penalty = self.config.severity_penalties.penalty_for(item.severity)
        if severity_penalty > 0 and item.status in {QualityStatus.FAILED, QualityStatus.ERROR, QualityStatus.WARNING}:
            penalties.append(
                ScorePenalty(
                    penalty_id=str(uuid.uuid4()),
                    reason=f"severity_{item.severity.value}",
                    amount=severity_penalty,
                    severity=item.severity,
                    source_name=item.name,
                    dimension=item.dimension,
                )
            )

        failure_rate = item.failure_rate()
        if failure_rate is not None and failure_rate > 0:
            penalties.append(
                ScorePenalty(
                    penalty_id=str(uuid.uuid4()),
                    reason="record_failure_rate",
                    amount=min(0.30, failure_rate * 0.50),
                    severity=item.severity,
                    source_name=item.name,
                    dimension=item.dimension,
                    metadata={"failure_rate": failure_rate},
                )
            )

        if item.confidence < self.config.minimum_confidence:
            confidence_gap = self.config.minimum_confidence - item.confidence
            penalties.append(
                ScorePenalty(
                    penalty_id=str(uuid.uuid4()),
                    reason="low_confidence",
                    amount=min(0.20, self.config.low_confidence_penalty + confidence_gap * 0.20),
                    severity=Severity.MEDIUM,
                    source_name=item.name,
                    dimension=item.dimension,
                    metadata={"confidence": item.confidence, "minimum_confidence": self.config.minimum_confidence},
                )
            )

        if raw_score <= 0:
            penalties.append(
                ScorePenalty(
                    penalty_id=str(uuid.uuid4()),
                    reason="zero_or_invalid_score",
                    amount=0.10,
                    severity=Severity.HIGH,
                    source_name=item.name,
                    dimension=item.dimension,
                )
            )

        return penalties

    def _apply_penalties(self, raw_score: float, penalties: Sequence[ScorePenalty]) -> float:
        total_penalty = min(self.config.max_total_penalty, sum(p.amount for p in penalties))
        if self.config.penalty_mode == PenaltyMode.ADDITIVE:
            return _clamp_score(raw_score - total_penalty)
        if self.config.penalty_mode == PenaltyMode.MULTIPLICATIVE:
            score = raw_score
            for penalty in penalties:
                score *= max(0.0, 1.0 - penalty.amount)
            return _clamp_score(score)
        if self.config.penalty_mode == PenaltyMode.CAPPED:
            return _clamp_score(max(raw_score - total_penalty, 1.0 - self.config.max_total_penalty))
        raise QualityScoringConfigurationError(f"Unsupported penalty mode: {self.config.penalty_mode}")

    def _dimension_scores(self, contributions: Sequence[ScoreContribution]) -> List[DimensionScore]:
        grouped: Dict[QualityDimension, List[ScoreContribution]] = defaultdict(list)
        for contribution in contributions:
            grouped[contribution.dimension].append(contribution)

        dimension_scores: List[DimensionScore] = []
        for dimension, items in grouped.items():
            score = self._aggregate_contributions(items)
            failed_count = sum(1 for item in items if item.status == QualityStatus.FAILED)
            warning_count = sum(1 for item in items if item.status == QualityStatus.WARNING)
            error_count = sum(1 for item in items if item.status == QualityStatus.ERROR)
            penalties = [p for item in items for p in item.penalties]
            dimension_scores.append(
                DimensionScore(
                    dimension=dimension,
                    score=score,
                    grade=self.config.grade_thresholds.grade(score),
                    weight=self.config.dimension_weights.get(dimension, 1.0),
                    input_count=len(items),
                    failed_count=failed_count,
                    warning_count=warning_count,
                    error_count=error_count,
                    average_confidence=_safe_mean([item.confidence for item in items], default=1.0),
                    penalties=penalties,
                    contributions=list(items),
                )
            )

        return sorted(dimension_scores, key=lambda d: d.dimension.value)

    def _aggregate_contributions(self, contributions: Sequence[ScoreContribution]) -> float:
        if not contributions:
            return 1.0
        if self.config.aggregation_strategy == AggregationStrategy.ARITHMETIC_MEAN:
            return _safe_mean([c.adjusted_score for c in contributions])
        if self.config.aggregation_strategy == AggregationStrategy.MINIMUM:
            return _clamp_score(min(c.adjusted_score for c in contributions))
        if self.config.aggregation_strategy == AggregationStrategy.HARMONIC_MEAN:
            return _harmonic_mean([c.adjusted_score for c in contributions])
        return _weighted_average([(c.adjusted_score, c.weight) for c in contributions])

    def _aggregate_dimension_scores(self, dimensions: Sequence[DimensionScore]) -> float:
        if not dimensions:
            return 1.0
        if self.config.aggregation_strategy == AggregationStrategy.ARITHMETIC_MEAN:
            return _safe_mean([d.score for d in dimensions])
        if self.config.aggregation_strategy == AggregationStrategy.MINIMUM:
            return _clamp_score(min(d.score for d in dimensions))
        if self.config.aggregation_strategy == AggregationStrategy.HARMONIC_MEAN:
            return _harmonic_mean([d.score for d in dimensions])
        return _weighted_average([(d.score, d.weight) for d in dimensions])

    def _dataset_scorecards(
        self,
        contributions: Sequence[ScoreContribution],
        previous_snapshot: Optional[QualityScoreSnapshot | Mapping[str, Any]],
    ) -> List[DatasetScorecard]:
        grouped: Dict[str, List[ScoreContribution]] = defaultdict(list)
        for contribution in contributions:
            dataset = str(contribution.metadata.get("dataset_name") or "unknown")
            grouped[dataset].append(contribution)

        previous_by_dataset = self._previous_dataset_scores(previous_snapshot)
        scorecards: List[DatasetScorecard] = []
        for dataset, items in grouped.items():
            score = self._aggregate_contributions(items)
            previous_score = previous_by_dataset.get(dataset)
            trend, delta = _trend(score, previous_score, self.config.trend_significant_delta)
            dimension_scores: Dict[str, float] = {}
            by_dim: Dict[QualityDimension, List[ScoreContribution]] = defaultdict(list)
            for item in items:
                by_dim[item.dimension].append(item)
            for dim, dim_items in by_dim.items():
                dimension_scores[dim.value] = self._aggregate_contributions(dim_items)

            failed_count = sum(1 for item in items if item.status in {QualityStatus.FAILED, QualityStatus.ERROR})
            critical_count = sum(1 for item in items if item.severity == Severity.CRITICAL)
            status = self._status_from_score(score, items)
            scorecards.append(
                DatasetScorecard(
                    dataset_name=dataset,
                    score=score,
                    grade=self.config.grade_thresholds.grade(score),
                    status=status,
                    trend=trend,
                    previous_score=previous_score,
                    delta=delta,
                    dimension_scores=dimension_scores,
                    input_count=len(items),
                    failed_count=failed_count,
                    critical_count=critical_count,
                    average_confidence=_safe_mean([item.confidence for item in items], default=1.0),
                )
            )

        return sorted(scorecards, key=lambda s: (s.score, s.dataset_name))

    def _status_from_score(self, score: float, contributions: Sequence[ScoreContribution]) -> QualityStatus:
        if any(c.status == QualityStatus.ERROR for c in contributions):
            return QualityStatus.ERROR
        if any(c.severity == Severity.CRITICAL and c.status in {QualityStatus.FAILED, QualityStatus.ERROR} for c in contributions):
            return QualityStatus.FAILED
        grade = self.config.grade_thresholds.grade(score)
        if grade in {ScoreGrade.CRITICAL, ScoreGrade.POOR}:
            return QualityStatus.FAILED
        if grade == ScoreGrade.DEGRADED or any(c.status == QualityStatus.WARNING for c in contributions):
            return QualityStatus.WARNING
        return QualityStatus.PASSED

    def _previous_overall_score(self, previous_snapshot: Optional[QualityScoreSnapshot | Mapping[str, Any]]) -> Optional[float]:
        if previous_snapshot is None:
            return None
        if isinstance(previous_snapshot, QualityScoreSnapshot):
            return previous_snapshot.overall_score
        value = previous_snapshot.get("overall_score")
        return float(value) if value is not None else None

    def _previous_dataset_scores(self, previous_snapshot: Optional[QualityScoreSnapshot | Mapping[str, Any]]) -> Dict[str, float]:
        if previous_snapshot is None:
            return {}
        if isinstance(previous_snapshot, QualityScoreSnapshot):
            return {item.dataset_name: item.score for item in previous_snapshot.dataset_scorecards}
        result: Dict[str, float] = {}
        for item in previous_snapshot.get("dataset_scorecards", []) or []:
            dataset = item.get("dataset_name")
            score = item.get("score")
            if dataset is not None and score is not None:
                result[str(dataset)] = float(score)
        return result

    def _publish_metrics(self, snapshot: QualityScoreSnapshot) -> None:
        tags = {"status": snapshot.overall_status.value, "grade": snapshot.overall_grade.value}
        self.metrics_sink.gauge("data_quality.scoring.overall_score", snapshot.overall_score, tags=tags)
        self.metrics_sink.gauge("data_quality.scoring.input_count", snapshot.input_count, tags=tags)
        self.metrics_sink.gauge("data_quality.scoring.penalty_count", len(snapshot.penalties), tags=tags)
        for dimension in snapshot.dimension_scores:
            self.metrics_sink.gauge(
                "data_quality.scoring.dimension_score",
                dimension.score,
                tags={**tags, "dimension": dimension.dimension.value, "grade": dimension.grade.value},
            )
        for dataset in snapshot.dataset_scorecards:
            self.metrics_sink.gauge(
                "data_quality.scoring.dataset_score",
                dataset.score,
                tags={**tags, "dataset": dataset.dataset_name, "grade": dataset.grade.value},
            )
        self.metrics_sink.increment("data_quality.scoring.snapshot_generated", tags=tags)

    def _write_audit(self, snapshot: QualityScoreSnapshot) -> None:
        if not self.audit_sink:
            return
        self.audit_sink.write_event(
            {
                "event_type": "quality_score_snapshot_generated",
                "snapshot_id": snapshot.snapshot_id,
                "timestamp": snapshot.generated_at,
                "overall_score": snapshot.overall_score,
                "overall_grade": snapshot.overall_grade.value,
                "overall_status": snapshot.overall_status.value,
                "input_count": snapshot.input_count,
                "penalty_count": len(snapshot.penalties),
                "dataset_count": len(snapshot.dataset_scorecards),
            }
        )


# =============================================================================
# Convenience API
# =============================================================================


def score_quality(
    inputs: Sequence[QualityScoreInput | Mapping[str, Any]],
    *,
    previous_snapshot: Optional[QualityScoreSnapshot | Mapping[str, Any]] = None,
    config: Optional[QualityScoringConfig] = None,
) -> QualityScoreSnapshot:
    """Convenience function for one-shot quality scoring."""
    return QualityScoringEngine(config).score(inputs, previous_snapshot=previous_snapshot)


# =============================================================================
# Local Smoke Example
# =============================================================================


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    raw_inputs = [
        {
            "name": "customer_id_completeness",
            "dataset_name": "customers",
            "dimension": "completeness",
            "status": "failed",
            "severity": "critical",
            "completeness_score": 0.985,
            "evaluated_records": 10000,
            "incomplete_records": 150,
            "weight": 2.0,
        },
        {
            "name": "email_accuracy",
            "dataset_name": "customers",
            "dimension": "accuracy",
            "status": "warning",
            "severity": "medium",
            "accuracy_score": 0.972,
            "evaluated_records": 10000,
            "failed_records": 280,
        },
        {
            "name": "orders_freshness",
            "dataset_name": "orders",
            "dimension": "freshness",
            "status": "passed",
            "severity": "info",
            "freshness_score": 0.998,
            "evaluated_records": 50000,
            "stale_records": 10,
        },
    ]

    engine = QualityScoringEngine()
    snapshot = engine.score(raw_inputs, metadata={"environment": "dev"})
    print(snapshot.to_json())

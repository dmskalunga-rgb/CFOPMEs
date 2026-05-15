"""
data/processing/feature_selection.py

Enterprise-grade feature selection engine for data platforms and ML pipelines.

Purpose
-------
Provides a dependency-light feature selection layer for offline/online ML
pipelines, feature stores, analytics jobs and data quality workflows. It ranks,
filters and audits features using statistical, operational and governance rules.

Core capabilities
-----------------
- Works with dictionaries, dataclasses, namedtuples and objects.
- Feature profiling: missing ratio, distinct ratio, variance, type inference.
- Selection rules: include/exclude, missingness, variance, cardinality,
  correlation, target association and custom scoring.
- Numeric and categorical target scoring using dependency-light statistics.
- Greedy correlation pruning.
- Protected/required feature support.
- Stable ranking with explanations.
- JSON manifest/report export.
- Optional telemetry integration.
- Safe metadata sanitization.
- Standard library only.

Example
-------
selector = FeatureSelectionEngine()
result = selector.select(
    rows,
    target_field="churn",
    config=FeatureSelectionConfig(max_features=50),
)
print(result.selected_features)
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
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|session|jwt|bearer)",
    re.IGNORECASE,
)
MAX_TEXT_LENGTH = 16_384
EPSILON = 1e-12


class FeatureType(str, Enum):
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    TEXT = "text"
    UNKNOWN = "unknown"


class SelectionStatus(str, Enum):
    SELECTED = "selected"
    REJECTED = "rejected"
    REQUIRED = "required"
    EXCLUDED = "excluded"
    ERROR = "error"


class SelectionMethod(str, Enum):
    RULE_BASED = "rule_based"
    VARIANCE = "variance"
    MISSINGNESS = "missingness"
    CARDINALITY = "cardinality"
    CORRELATION = "correlation"
    TARGET_ASSOCIATION = "target_association"
    CUSTOM_SCORE = "custom_score"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class FeatureSelectionConfig:
    include_features: Tuple[str, ...] = field(default_factory=tuple)
    exclude_features: Tuple[str, ...] = field(default_factory=tuple)
    required_features: Tuple[str, ...] = field(default_factory=tuple)
    ignore_fields: Tuple[str, ...] = field(default_factory=tuple)
    max_features: Optional[int] = None
    min_features: int = 0
    max_missing_ratio: float = 0.95
    min_variance: float = 0.0
    max_cardinality_ratio: float = 1.0
    min_distinct_count: int = 1
    max_correlation: float = 0.95
    min_target_score: float = 0.0
    text_as_categorical_threshold: int = 128
    sample_limit: Optional[int] = None
    custom_score_fn: Optional[Callable[[str, "FeatureProfile", Sequence[Mapping[str, Any]]], float]] = None
    telemetry_enabled: bool = True
    report_path: Optional[str] = None
    manifest_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "FeatureSelectionConfig":
        return cls(
            include_features=tuple(split_env("FEATURE_SELECTION_INCLUDE")),
            exclude_features=tuple(split_env("FEATURE_SELECTION_EXCLUDE")),
            required_features=tuple(split_env("FEATURE_SELECTION_REQUIRED")),
            ignore_fields=tuple(split_env("FEATURE_SELECTION_IGNORE_FIELDS")),
            max_features=int_env_optional("FEATURE_SELECTION_MAX_FEATURES"),
            min_features=int_env("FEATURE_SELECTION_MIN_FEATURES", 0),
            max_missing_ratio=float_env("FEATURE_SELECTION_MAX_MISSING_RATIO", 0.95),
            min_variance=float_env("FEATURE_SELECTION_MIN_VARIANCE", 0.0),
            max_cardinality_ratio=float_env("FEATURE_SELECTION_MAX_CARDINALITY_RATIO", 1.0),
            min_distinct_count=int_env("FEATURE_SELECTION_MIN_DISTINCT_COUNT", 1),
            max_correlation=float_env("FEATURE_SELECTION_MAX_CORRELATION", 0.95),
            min_target_score=float_env("FEATURE_SELECTION_MIN_TARGET_SCORE", 0.0),
            text_as_categorical_threshold=int_env("FEATURE_SELECTION_TEXT_AS_CATEGORICAL_THRESHOLD", 128),
            sample_limit=int_env_optional("FEATURE_SELECTION_SAMPLE_LIMIT"),
            telemetry_enabled=bool_env("FEATURE_SELECTION_TELEMETRY_ENABLED", True),
            report_path=os.getenv("FEATURE_SELECTION_REPORT_PATH"),
            manifest_path=os.getenv("FEATURE_SELECTION_MANIFEST_PATH"),
        )

    def validate(self) -> None:
        if not 0.0 <= self.max_missing_ratio <= 1.0:
            raise FeatureSelectionConfigError("max_missing_ratio must be between 0 and 1")
        if not 0.0 <= self.max_cardinality_ratio <= 1.0:
            raise FeatureSelectionConfigError("max_cardinality_ratio must be between 0 and 1")
        if not 0.0 <= self.max_correlation <= 1.0:
            raise FeatureSelectionConfigError("max_correlation must be between 0 and 1")
        if self.max_features is not None and self.max_features < 1:
            raise FeatureSelectionConfigError("max_features must be positive")


@dataclass(frozen=True)
class FeatureProfile:
    name: str
    feature_type: FeatureType
    count: int
    missing_count: int
    missing_ratio: float
    distinct_count: int
    distinct_ratio: float
    variance: Optional[float]
    mean: Optional[float]
    min_value: Optional[float]
    max_value: Optional[float]
    sample_values: List[Any] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["feature_type"] = self.feature_type.value
        return sanitize_mapping(data)


@dataclass(frozen=True)
class FeatureScore:
    feature: str
    status: SelectionStatus
    score: float
    rank: Optional[int]
    method: SelectionMethod
    reasons: List[str]
    profile: FeatureProfile
    target_score: float = 0.0
    custom_score: float = 0.0
    correlation_penalty: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["method"] = self.method.value
        data["profile"] = self.profile.to_dict()
        return sanitize_mapping(data)


@dataclass(frozen=True)
class FeatureSelectionResult:
    id: str
    started_at: str
    finished_at: str
    duration_ms: float
    input_count: int
    feature_count: int
    selected_count: int
    rejected_count: int
    selected_features: List[str]
    rejected_features: List[str]
    scores: List[FeatureScore]
    correlation_groups: List[List[str]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping({
            "id": self.id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "input_count": self.input_count,
            "feature_count": self.feature_count,
            "selected_count": self.selected_count,
            "rejected_count": self.rejected_count,
            "selected_features": self.selected_features,
            "rejected_features": self.rejected_features,
            "scores": [score.to_dict() for score in self.scores],
            "correlation_groups": self.correlation_groups,
            "metadata": self.metadata,
        })

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True, default=safe_json_default)

    def to_manifest(self) -> Dict[str, Any]:
        return sanitize_mapping({
            "created_at": self.finished_at,
            "selected_features": self.selected_features,
            "rejected_features": self.rejected_features,
            "feature_scores": [
                {
                    "feature": score.feature,
                    "status": score.status.value,
                    "score": score.score,
                    "rank": score.rank,
                    "reasons": score.reasons,
                    "type": score.profile.feature_type.value,
                }
                for score in self.scores
            ],
            "metadata": self.metadata,
        })


class FeatureSelectionError(Exception):
    """Base feature selection error."""


class FeatureSelectionConfigError(FeatureSelectionError):
    """Invalid feature selection configuration."""


class FeatureSelectionInputError(FeatureSelectionError):
    """Invalid feature selection input."""


class FeatureSelectionEngine:
    """Enterprise feature selection engine."""

    def __init__(self, default_config: Optional[FeatureSelectionConfig] = None) -> None:
        self.default_config = default_config or FeatureSelectionConfig.from_env()

    def select(
        self,
        rows: Iterable[Any],
        *,
        target_field: Optional[str] = None,
        config: Optional[FeatureSelectionConfig] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> FeatureSelectionResult:
        cfg = config or self.default_config
        cfg.validate()
        started = monotonic_ms()
        started_iso = utc_now_iso()
        materialized = [dict(to_mapping(row)) for _, row in zip(range(cfg.sample_limit or 10**18), rows)]
        if not materialized:
            return FeatureSelectionResult(
                id=str(uuid.uuid4()), started_at=started_iso, finished_at=utc_now_iso(), duration_ms=0.0,
                input_count=0, feature_count=0, selected_count=0, rejected_count=0,
                selected_features=[], rejected_features=[], scores=[], metadata=sanitize_mapping(dict(metadata or {})),
            )

        with telemetry_operation("feature_selection.select", cfg.telemetry_enabled, attributes={"target_field": target_field}):
            feature_names = discover_features(materialized, target_field=target_field, config=cfg)
            profiles = {name: profile_feature(name, materialized, cfg) for name in feature_names}
            target_values = [get_field(row, target_field) for row in materialized] if target_field else []
            target_scores = compute_target_scores(profiles, materialized, target_values, target_field) if target_field else {}
            custom_scores = compute_custom_scores(profiles, materialized, cfg)
            raw_scores = build_scores(profiles, target_scores, custom_scores, cfg)
            correlation_groups, penalties = compute_correlation_penalties(raw_scores, materialized, cfg)
            ranked = apply_selection(raw_scores, penalties, cfg)

        duration_ms = monotonic_ms() - started
        selected = [score.feature for score in ranked if score.status in {SelectionStatus.SELECTED, SelectionStatus.REQUIRED}]
        rejected = [score.feature for score in ranked if score.status not in {SelectionStatus.SELECTED, SelectionStatus.REQUIRED}]
        result = FeatureSelectionResult(
            id=str(uuid.uuid4()),
            started_at=started_iso,
            finished_at=utc_now_iso(),
            duration_ms=round(duration_ms, 3),
            input_count=len(materialized),
            feature_count=len(feature_names),
            selected_count=len(selected),
            rejected_count=len(rejected),
            selected_features=selected,
            rejected_features=rejected,
            scores=ranked,
            correlation_groups=correlation_groups,
            metadata=sanitize_mapping({"target_field": target_field, **dict(cfg.metadata), **dict(metadata or {})}),
        )
        save_outputs(result, cfg)
        telemetry_metric("feature_selection.input_count", len(materialized), cfg.telemetry_enabled)
        telemetry_metric("feature_selection.selected_count", len(selected), cfg.telemetry_enabled)
        telemetry_metric("feature_selection.duration_ms", duration_ms, cfg.telemetry_enabled)
        return result


def discover_features(rows: Sequence[Mapping[str, Any]], *, target_field: Optional[str], config: FeatureSelectionConfig) -> List[str]:
    fields = sorted({str(key) for row in rows for key in row.keys()})
    ignore = set(config.ignore_fields) | ({target_field} if target_field else set()) | set(config.exclude_features)
    if config.include_features:
        fields = [field for field in fields if field in set(config.include_features)]
    return [field for field in fields if field not in ignore and not SENSITIVE_KEY_PATTERN.search(field)]


def profile_feature(name: str, rows: Sequence[Mapping[str, Any]], config: FeatureSelectionConfig) -> FeatureProfile:
    values = [get_field(row, name) for row in rows]
    count = len(values)
    non_missing = [v for v in values if v not in (None, "")]
    missing_count = count - len(non_missing)
    distinct = {json_hashable(v) for v in non_missing}
    feature_type = infer_feature_type(non_missing, config)
    numeric_values = []
    if feature_type in {FeatureType.NUMERIC, FeatureType.BOOLEAN}:
        for value in non_missing:
            try:
                numeric_values.append(to_number(value))
            except Exception:
                pass
    variance = statistics.pvariance(numeric_values) if len(numeric_values) >= 2 else 0.0 if numeric_values else None
    mean = statistics.fmean(numeric_values) if numeric_values else None
    return FeatureProfile(
        name=name,
        feature_type=feature_type,
        count=count,
        missing_count=missing_count,
        missing_ratio=round(missing_count / count if count else 0.0, 6),
        distinct_count=len(distinct),
        distinct_ratio=round(len(distinct) / count if count else 0.0, 6),
        variance=round(variance, 10) if variance is not None else None,
        mean=round(mean, 10) if mean is not None else None,
        min_value=min(numeric_values) if numeric_values else None,
        max_value=max(numeric_values) if numeric_values else None,
        sample_values=list(distinct)[:10],
    )


def infer_feature_type(values: Sequence[Any], config: FeatureSelectionConfig) -> FeatureType:
    if not values:
        return FeatureType.UNKNOWN
    bool_count = sum(isinstance(v, bool) or str(v).lower() in {"true", "false", "0", "1", "yes", "no"} for v in values)
    if bool_count / len(values) > 0.95:
        return FeatureType.BOOLEAN
    numeric_count = 0
    for value in values:
        try:
            to_number(value)
            numeric_count += 1
        except Exception:
            pass
    if numeric_count / len(values) > 0.95:
        return FeatureType.NUMERIC
    datetime_count = 0
    for value in values[:1000]:
        try:
            coerce_datetime(value)
            datetime_count += 1
        except Exception:
            pass
    if values and datetime_count / min(len(values), 1000) > 0.95:
        return FeatureType.DATETIME
    avg_len = statistics.fmean(len(str(v)) for v in values) if values else 0
    distinct_count = len({json_hashable(v) for v in values})
    if avg_len <= config.text_as_categorical_threshold and distinct_count <= max(1000, len(values) * 0.5):
        return FeatureType.CATEGORICAL
    return FeatureType.TEXT


def compute_target_scores(
    profiles: Mapping[str, FeatureProfile],
    rows: Sequence[Mapping[str, Any]],
    target_values: Sequence[Any],
    target_field: Optional[str],
) -> Dict[str, float]:
    if not target_field:
        return {}
    target_numeric = safe_numeric_vector(target_values)
    target_is_numeric = len(target_numeric) == len([v for v in target_values if v not in (None, "")]) and bool(target_numeric)
    scores: Dict[str, float] = {}
    for feature, profile in profiles.items():
        values = [get_field(row, feature) for row in rows]
        if profile.feature_type in {FeatureType.NUMERIC, FeatureType.BOOLEAN} and target_is_numeric:
            scores[feature] = abs(pearson(safe_numeric_vector(values), target_numeric))
        elif profile.feature_type in {FeatureType.CATEGORICAL, FeatureType.BOOLEAN}:
            scores[feature] = categorical_association(values, target_values)
        elif profile.feature_type == FeatureType.NUMERIC:
            scores[feature] = numeric_group_separation(values, target_values)
        else:
            scores[feature] = 0.0
    return scores


def compute_custom_scores(profiles: Mapping[str, FeatureProfile], rows: Sequence[Mapping[str, Any]], config: FeatureSelectionConfig) -> Dict[str, float]:
    if not config.custom_score_fn:
        return {}
    result: Dict[str, float] = {}
    for feature, profile in profiles.items():
        try:
            result[feature] = float(config.custom_score_fn(feature, profile, rows))
        except Exception:
            logger.debug("Custom feature score failed for %s", feature, exc_info=True)
            result[feature] = 0.0
    return result


def build_scores(
    profiles: Mapping[str, FeatureProfile],
    target_scores: Mapping[str, float],
    custom_scores: Mapping[str, float],
    config: FeatureSelectionConfig,
) -> List[FeatureScore]:
    scores: List[FeatureScore] = []
    required = set(config.required_features)
    excluded = set(config.exclude_features)
    for feature, profile in profiles.items():
        reasons: List[str] = []
        status = SelectionStatus.SELECTED
        base_score = 1.0
        method = SelectionMethod.HYBRID
        if feature in excluded:
            status = SelectionStatus.EXCLUDED
            reasons.append("explicitly excluded")
            base_score = 0.0
        if profile.missing_ratio > config.max_missing_ratio:
            status = SelectionStatus.REJECTED
            reasons.append(f"missing_ratio {profile.missing_ratio:.4f} > {config.max_missing_ratio:.4f}")
        if profile.distinct_count < config.min_distinct_count:
            status = SelectionStatus.REJECTED
            reasons.append(f"distinct_count {profile.distinct_count} < {config.min_distinct_count}")
        if profile.distinct_ratio > config.max_cardinality_ratio:
            status = SelectionStatus.REJECTED
            reasons.append(f"distinct_ratio {profile.distinct_ratio:.4f} > {config.max_cardinality_ratio:.4f}")
        if profile.feature_type == FeatureType.NUMERIC and (profile.variance or 0.0) <= config.min_variance:
            status = SelectionStatus.REJECTED
            reasons.append(f"variance {profile.variance} <= {config.min_variance}")
        target_score = float(target_scores.get(feature, 0.0))
        custom_score = float(custom_scores.get(feature, 0.0))
        if target_scores and target_score < config.min_target_score:
            status = SelectionStatus.REJECTED
            reasons.append(f"target_score {target_score:.4f} < {config.min_target_score:.4f}")
        if feature in required:
            status = SelectionStatus.REQUIRED
            reasons.append("required feature")
        score = max(0.0, base_score - profile.missing_ratio + target_score + custom_score)
        scores.append(FeatureScore(feature, status, round(score, 8), None, method, reasons or ["passed selection rules"], profile, target_score, custom_score))
    return scores


def compute_correlation_penalties(scores: Sequence[FeatureScore], rows: Sequence[Mapping[str, Any]], config: FeatureSelectionConfig) -> Tuple[List[List[str]], Dict[str, float]]:
    numeric_features = [score.feature for score in scores if score.profile.feature_type in {FeatureType.NUMERIC, FeatureType.BOOLEAN}]
    penalties: Dict[str, float] = defaultdict(float)
    groups: List[List[str]] = []
    vectors = {feature: safe_numeric_vector([get_field(row, feature) for row in rows]) for feature in numeric_features}
    score_map = {score.feature: score.score for score in scores}
    for i, left in enumerate(numeric_features):
        for right in numeric_features[i + 1:]:
            corr = abs(pearson(vectors[left], vectors[right]))
            if corr >= config.max_correlation:
                groups.append([left, right])
                loser = right if score_map.get(left, 0) >= score_map.get(right, 0) else left
                penalties[loser] = max(penalties[loser], corr)
    return groups, dict(penalties)


def apply_selection(scores: Sequence[FeatureScore], penalties: Mapping[str, float], config: FeatureSelectionConfig) -> List[FeatureScore]:
    updated: List[FeatureScore] = []
    for score in scores:
        status = score.status
        reasons = list(score.reasons)
        penalty = float(penalties.get(score.feature, 0.0))
        final_score = max(0.0, score.score - penalty)
        if penalty and status == SelectionStatus.SELECTED:
            status = SelectionStatus.REJECTED
            reasons.append(f"correlation_penalty {penalty:.4f} >= max_correlation {config.max_correlation:.4f}")
        updated.append(dataclasses.replace(score, status=status, score=round(final_score, 8), correlation_penalty=penalty, reasons=reasons))
    updated.sort(key=lambda item: (item.status == SelectionStatus.REQUIRED, item.status == SelectionStatus.SELECTED, item.score), reverse=True)

    selected_seen = 0
    ranked: List[FeatureScore] = []
    for item in updated:
        status = item.status
        if status == SelectionStatus.SELECTED:
            if config.max_features is not None and selected_seen >= config.max_features:
                status = SelectionStatus.REJECTED
                item = dataclasses.replace(item, status=status, reasons=[*item.reasons, "max_features limit"])
            else:
                selected_seen += 1
        if status == SelectionStatus.REQUIRED:
            selected_seen += 1
        rank = selected_seen if status in {SelectionStatus.SELECTED, SelectionStatus.REQUIRED} else None
        ranked.append(dataclasses.replace(item, rank=rank))
    return ranked


def pearson(x: Sequence[float], y: Sequence[float]) -> float:
    n = min(len(x), len(y))
    if n < 2:
        return 0.0
    x = list(x[:n])
    y = list(y[:n])
    mean_x = statistics.fmean(x)
    mean_y = statistics.fmean(y)
    num = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    den_x = math.sqrt(sum((a - mean_x) ** 2 for a in x))
    den_y = math.sqrt(sum((b - mean_y) ** 2 for b in y))
    return num / max(den_x * den_y, EPSILON)


def categorical_association(values: Sequence[Any], target: Sequence[Any]) -> float:
    n = min(len(values), len(target))
    if n == 0:
        return 0.0
    pairs = [(json_hashable(values[i]), json_hashable(target[i])) for i in range(n) if values[i] not in (None, "") and target[i] not in (None, "")]
    if not pairs:
        return 0.0
    total = len(pairs)
    feature_counts = Counter(v for v, _ in pairs)
    target_counts = Counter(t for _, t in pairs)
    joint_counts = Counter(pairs)
    chi = 0.0
    for (feature_value, target_value), observed in joint_counts.items():
        expected = feature_counts[feature_value] * target_counts[target_value] / total
        chi += ((observed - expected) ** 2) / max(expected, EPSILON)
    return min(1.0, chi / max(total, 1))


def numeric_group_separation(values: Sequence[Any], target: Sequence[Any]) -> float:
    groups: Dict[Any, List[float]] = defaultdict(list)
    all_values: List[float] = []
    for value, target_value in zip(values, target):
        if value in (None, "") or target_value in (None, ""):
            continue
        try:
            number = to_number(value)
        except Exception:
            continue
        groups[json_hashable(target_value)].append(number)
        all_values.append(number)
    if len(groups) < 2 or not all_values:
        return 0.0
    global_mean = statistics.fmean(all_values)
    between = sum(len(vals) * (statistics.fmean(vals) - global_mean) ** 2 for vals in groups.values() if vals)
    total = sum((value - global_mean) ** 2 for value in all_values)
    return between / max(total, EPSILON)


def safe_numeric_vector(values: Sequence[Any]) -> List[float]:
    result = []
    for value in values:
        if value in (None, ""):
            continue
        try:
            result.append(to_number(value))
        except Exception:
            continue
    return result


def save_outputs(result: FeatureSelectionResult, config: FeatureSelectionConfig) -> None:
    if config.report_path:
        target = Path(config.report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result.to_json(indent=2), encoding="utf-8")
    if config.manifest_path:
        target = Path(config.manifest_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result.to_manifest(), ensure_ascii=False, indent=2, sort_keys=True, default=safe_json_default), encoding="utf-8")


def to_mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if dataclasses.is_dataclass(row):
        return asdict(row)
    if hasattr(row, "_asdict"):
        return row._asdict()
    if hasattr(row, "__dict__"):
        return vars(row)
    raise FeatureSelectionInputError(f"Unsupported row type: {type(row)!r}")


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
    if isinstance(value, Decimal):
        return float(value)
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise ValueError(f"invalid numeric value: {value!r}")
    return number


def coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        raw = float(value)
        dt = datetime.fromtimestamp(raw / 1000.0 if raw > 10_000_000_000 else raw, timezone.utc)
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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
        logger.debug("Feature selection telemetry metric failed", exc_info=True)


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
    if isinstance(value, (set, tuple)):
        return list(value)
    return str(value)


def split_env(name: str) -> List[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def int_env_optional(name: str) -> Optional[int]:
    raw = os.getenv(name)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


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
    "FeatureProfile",
    "FeatureScore",
    "FeatureSelectionConfig",
    "FeatureSelectionConfigError",
    "FeatureSelectionEngine",
    "FeatureSelectionError",
    "FeatureSelectionInputError",
    "FeatureSelectionResult",
    "FeatureType",
    "SelectionMethod",
    "SelectionStatus",
    "categorical_association",
    "compute_target_scores",
    "infer_feature_type",
    "numeric_group_separation",
    "pearson",
    "profile_feature",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    rows = [
        {"age": 20, "income": 1000, "city": "A", "constant": 1, "target": 0},
        {"age": 30, "income": 2000, "city": "B", "constant": 1, "target": 1},
        {"age": 40, "income": 3000, "city": "B", "constant": 1, "target": 1},
    ]
    selector = FeatureSelectionEngine(FeatureSelectionConfig(max_features=3, telemetry_enabled=False))
    print(selector.select(rows, target_field="target").to_json())

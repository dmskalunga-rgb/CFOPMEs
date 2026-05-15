# ml/anomaly_detection/features.py
"""
Enterprise Anomaly Detection Feature Engineering.

Recursos:
- feature engineering para detecção de anomalias
- janelas temporais por entidade
- velocity features
- estatísticas robustas
- z-score clássico e robusto
- sazonalidade temporal
- encoding categórico seguro
- agregações por grupo
- pipeline serializável
- entrada por records dict ou dataclasses

Projetado para:
- fraude
- observabilidade
- IoT
- transações
- logs
- métricas de sistema
- dados financeiros
"""

from __future__ import annotations

import hashlib
import json
import math
import pickle
import statistics
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np


class FeatureError(RuntimeError):
    pass


class FeatureType(str, Enum):
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    TEMPORAL = "temporal"
    BOOLEAN = "boolean"
    TEXT = "text"


class MissingStrategy(str, Enum):
    ZERO = "zero"
    MEAN = "mean"
    MEDIAN = "median"
    CONSTANT = "constant"
    DROP = "drop"


class ScalingStrategy(str, Enum):
    NONE = "none"
    STANDARD = "standard"
    ROBUST = "robust"
    MINMAX = "minmax"


@dataclass(frozen=True)
class FeatureConfig:
    numeric_fields: Sequence[str] = field(default_factory=list)
    categorical_fields: Sequence[str] = field(default_factory=list)
    boolean_fields: Sequence[str] = field(default_factory=list)
    timestamp_field: Optional[str] = "timestamp"
    entity_id_field: Optional[str] = "entity_id"

    rolling_windows: Sequence[int] = (3, 5, 10, 30)
    velocity_windows: Sequence[int] = (5, 10, 30)
    lag_features: Sequence[int] = (1, 2, 3)

    enable_temporal_features: bool = True
    enable_rolling_features: bool = True
    enable_velocity_features: bool = True
    enable_group_baseline_features: bool = True
    enable_categorical_frequency: bool = True
    enable_hash_features: bool = True

    scaling_strategy: ScalingStrategy = ScalingStrategy.ROBUST
    missing_strategy: MissingStrategy = MissingStrategy.MEDIAN
    missing_constant: float = 0.0

    max_categories_per_field: int = 100
    hash_buckets: int = 64
    epsilon: float = 1e-9


@dataclass(frozen=True)
class FeatureVector:
    record_id: str
    entity_id: Optional[str]
    timestamp: Optional[str]
    features: Dict[str, float]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureBuildResult:
    run_id: str
    generated_at: str
    feature_names: List[str]
    vectors: List[FeatureVector]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_matrix(self) -> np.ndarray:
        return np.asarray(
            [
                [vector.features.get(name, 0.0) for name in self.feature_names]
                for vector in self.vectors
            ],
            dtype=float,
        )

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=indent, default=str)


class RecordAccessor:
    @staticmethod
    def to_mapping(record: Any) -> Mapping[str, Any]:
        if isinstance(record, Mapping):
            return record

        if is_dataclass(record):
            return asdict(record)

        if hasattr(record, "__dict__"):
            return vars(record)

        raise FeatureError(f"Tipo de record não suportado: {type(record)!r}")

    @staticmethod
    def get(record: Mapping[str, Any], field_path: str, default: Any = None) -> Any:
        current: Any = record

        for part in field_path.split("."):
            if isinstance(current, Mapping):
                current = current.get(part, default)
            else:
                return default

        return current


class RobustStats:
    @staticmethod
    def median(values: Sequence[float]) -> float:
        return float(np.median(values)) if values else 0.0

    @staticmethod
    def mad(values: Sequence[float]) -> float:
        if not values:
            return 0.0

        arr = np.asarray(values, dtype=float)
        med = np.median(arr)
        return float(np.median(np.abs(arr - med)))

    @staticmethod
    def iqr(values: Sequence[float]) -> float:
        if not values:
            return 0.0

        arr = np.asarray(values, dtype=float)
        return float(np.percentile(arr, 75) - np.percentile(arr, 25))

    @staticmethod
    def robust_z(value: float, values: Sequence[float], epsilon: float = 1e-9) -> float:
        med = RobustStats.median(values)
        mad = RobustStats.mad(values)

        if mad <= epsilon:
            return 0.0

        return float(0.6745 * (value - med) / mad)

    @staticmethod
    def z_score(value: float, values: Sequence[float], epsilon: float = 1e-9) -> float:
        if not values:
            return 0.0

        mean = float(np.mean(values))
        std = float(np.std(values))

        if std <= epsilon:
            return 0.0

        return float((value - mean) / std)


class FeatureScaler:
    def __init__(self, strategy: ScalingStrategy = ScalingStrategy.ROBUST) -> None:
        self.strategy = strategy
        self.params: Dict[str, Dict[str, float]] = {}

    def fit(self, vectors: Sequence[FeatureVector], feature_names: Sequence[str]) -> "FeatureScaler":
        self.params.clear()

        if self.strategy == ScalingStrategy.NONE:
            return self

        for name in feature_names:
            values = [float(v.features.get(name, 0.0)) for v in vectors]
            arr = np.asarray(values, dtype=float)

            if self.strategy == ScalingStrategy.STANDARD:
                mean = float(np.mean(arr))
                std = float(np.std(arr)) or 1.0
                self.params[name] = {"mean": mean, "std": std}

            elif self.strategy == ScalingStrategy.ROBUST:
                median = float(np.median(arr))
                iqr = float(np.percentile(arr, 75) - np.percentile(arr, 25)) or 1.0
                self.params[name] = {"median": median, "iqr": iqr}

            elif self.strategy == ScalingStrategy.MINMAX:
                min_v = float(np.min(arr))
                max_v = float(np.max(arr))
                scale = max(max_v - min_v, 1e-9)
                self.params[name] = {"min": min_v, "scale": scale}

        return self

    def transform(self, vectors: Sequence[FeatureVector]) -> List[FeatureVector]:
        if self.strategy == ScalingStrategy.NONE:
            return list(vectors)

        result: List[FeatureVector] = []

        for vector in vectors:
            features = dict(vector.features)

            for name, params in self.params.items():
                value = features.get(name, 0.0)

                if self.strategy == ScalingStrategy.STANDARD:
                    features[name] = (value - params["mean"]) / params["std"]

                elif self.strategy == ScalingStrategy.ROBUST:
                    features[name] = (value - params["median"]) / params["iqr"]

                elif self.strategy == ScalingStrategy.MINMAX:
                    features[name] = (value - params["min"]) / params["scale"]

            result.append(
                FeatureVector(
                    record_id=vector.record_id,
                    entity_id=vector.entity_id,
                    timestamp=vector.timestamp,
                    features=features,
                    metadata=vector.metadata,
                )
            )

        return result

    def fit_transform(
        self,
        vectors: Sequence[FeatureVector],
        feature_names: Sequence[str],
    ) -> List[FeatureVector]:
        self.fit(vectors, feature_names)
        return self.transform(vectors)


class MissingValueImputer:
    def __init__(
        self,
        strategy: MissingStrategy = MissingStrategy.MEDIAN,
        constant: float = 0.0,
    ) -> None:
        self.strategy = strategy
        self.constant = constant
        self.values: Dict[str, float] = {}

    def fit(self, vectors: Sequence[FeatureVector], feature_names: Sequence[str]) -> "MissingValueImputer":
        self.values.clear()

        for name in feature_names:
            values = [
                float(v.features[name])
                for v in vectors
                if name in v.features and math.isfinite(float(v.features[name]))
            ]

            if self.strategy == MissingStrategy.ZERO:
                self.values[name] = 0.0
            elif self.strategy == MissingStrategy.CONSTANT:
                self.values[name] = self.constant
            elif self.strategy == MissingStrategy.MEAN:
                self.values[name] = float(np.mean(values)) if values else 0.0
            elif self.strategy == MissingStrategy.MEDIAN:
                self.values[name] = float(np.median(values)) if values else 0.0
            elif self.strategy == MissingStrategy.DROP:
                self.values[name] = 0.0

        return self

    def transform(
        self,
        vectors: Sequence[FeatureVector],
        feature_names: Sequence[str],
    ) -> List[FeatureVector]:
        result: List[FeatureVector] = []

        for vector in vectors:
            features = dict(vector.features)

            for name in feature_names:
                value = features.get(name)

                if value is None:
                    features[name] = self.values.get(name, 0.0)
                    continue

                try:
                    numeric = float(value)
                    if not math.isfinite(numeric):
                        features[name] = self.values.get(name, 0.0)
                    else:
                        features[name] = numeric
                except Exception:
                    features[name] = self.values.get(name, 0.0)

            result.append(
                FeatureVector(
                    record_id=vector.record_id,
                    entity_id=vector.entity_id,
                    timestamp=vector.timestamp,
                    features=features,
                    metadata=vector.metadata,
                )
            )

        return result

    def fit_transform(
        self,
        vectors: Sequence[FeatureVector],
        feature_names: Sequence[str],
    ) -> List[FeatureVector]:
        self.fit(vectors, feature_names)
        return self.transform(vectors, feature_names)


class CategoricalEncoder:
    def __init__(
        self,
        max_categories_per_field: int = 100,
        hash_buckets: int = 64,
    ) -> None:
        self.max_categories_per_field = max_categories_per_field
        self.hash_buckets = hash_buckets
        self.categories: Dict[str, List[str]] = {}
        self.frequencies: Dict[str, Dict[str, float]] = {}

    def fit(
        self,
        records: Sequence[Mapping[str, Any]],
        fields: Sequence[str],
    ) -> "CategoricalEncoder":
        self.categories.clear()
        self.frequencies.clear()

        total = max(len(records), 1)

        for field in fields:
            values = [str(RecordAccessor.get(r, field, "__missing__")) for r in records]
            counts = Counter(values)

            self.categories[field] = [
                value
                for value, _ in counts.most_common(self.max_categories_per_field)
            ]

            self.frequencies[field] = {
                value: count / total
                for value, count in counts.items()
            }

        return self

    def encode(self, record: Mapping[str, Any], field: str) -> Dict[str, float]:
        raw = str(RecordAccessor.get(record, field, "__missing__"))
        features: Dict[str, float] = {}

        for category in self.categories.get(field, []):
            safe = self._safe_name(category)
            features[f"cat_{field}_{safe}"] = 1.0 if raw == category else 0.0

        features[f"catfreq_{field}"] = self.frequencies.get(field, {}).get(raw, 0.0)

        bucket = self._hash_bucket(raw)
        features[f"hash_{field}_{bucket}"] = 1.0

        return features

    def encoded_feature_names(self, fields: Sequence[str]) -> List[str]:
        names: List[str] = []

        for field in fields:
            for category in self.categories.get(field, []):
                names.append(f"cat_{field}_{self._safe_name(category)}")

            names.append(f"catfreq_{field}")

            for bucket in range(self.hash_buckets):
                names.append(f"hash_{field}_{bucket}")

        return names

    def _hash_bucket(self, value: str) -> int:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % self.hash_buckets

    @staticmethod
    def _safe_name(value: str) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in value.lower())[:80]


class TemporalFeatureBuilder:
    @staticmethod
    def parse_timestamp(value: Any) -> Optional[datetime]:
        if value is None:
            return None

        if isinstance(value, datetime):
            return value

        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None

    def build(self, timestamp: Optional[datetime]) -> Dict[str, float]:
        if timestamp is None:
            return {
                "hour": 0.0,
                "day": 0.0,
                "weekday": 0.0,
                "month": 0.0,
                "is_weekend": 0.0,
                "hour_sin": 0.0,
                "hour_cos": 0.0,
                "weekday_sin": 0.0,
                "weekday_cos": 0.0,
                "month_sin": 0.0,
                "month_cos": 0.0,
            }

        hour = timestamp.hour
        weekday = timestamp.weekday()
        month = timestamp.month

        return {
            "hour": float(hour),
            "day": float(timestamp.day),
            "weekday": float(weekday),
            "month": float(month),
            "is_weekend": 1.0 if weekday >= 5 else 0.0,
            "hour_sin": math.sin(2 * math.pi * hour / 24),
            "hour_cos": math.cos(2 * math.pi * hour / 24),
            "weekday_sin": math.sin(2 * math.pi * weekday / 7),
            "weekday_cos": math.cos(2 * math.pi * weekday / 7),
            "month_sin": math.sin(2 * math.pi * month / 12),
            "month_cos": math.cos(2 * math.pi * month / 12),
        }


class RollingState:
    def __init__(self, max_window: int) -> None:
        self.values: Deque[float] = deque(maxlen=max_window)

    def snapshot(self) -> List[float]:
        return list(self.values)

    def append(self, value: float) -> None:
        self.values.append(value)


class EntityFeatureState:
    def __init__(self, config: FeatureConfig) -> None:
        max_window = max(
            list(config.rolling_windows)
            + list(config.velocity_windows)
            + list(config.lag_features)
            + [1]
        )
        self.numeric_states: Dict[str, RollingState] = {
            field: RollingState(max_window)
            for field in config.numeric_fields
        }
        self.event_count = 0
        self.first_seen: Optional[datetime] = None
        self.last_seen: Optional[datetime] = None


class AnomalyFeatureEngineer:
    def __init__(self, config: Optional[FeatureConfig] = None) -> None:
        self.config = config or FeatureConfig()
        self.categorical_encoder = CategoricalEncoder(
            max_categories_per_field=self.config.max_categories_per_field,
            hash_buckets=self.config.hash_buckets,
        )
        self.temporal_builder = TemporalFeatureBuilder()
        self.imputer = MissingValueImputer(
            strategy=self.config.missing_strategy,
            constant=self.config.missing_constant,
        )
        self.scaler = FeatureScaler(strategy=self.config.scaling_strategy)

        self.global_numeric_history: Dict[str, List[float]] = defaultdict(list)
        self.entity_states: Dict[str, EntityFeatureState] = {}
        self.feature_names: List[str] = []
        self.is_fitted = False

    def fit(self, records: Sequence[Any]) -> "AnomalyFeatureEngineer":
        normalized = [RecordAccessor.to_mapping(r) for r in records]

        self.categorical_encoder.fit(normalized, self.config.categorical_fields)

        self.global_numeric_history.clear()

        for record in normalized:
            for field in self.config.numeric_fields:
                value = self._numeric(record, field)
                if value is not None:
                    self.global_numeric_history[field].append(value)

        raw_vectors = self._build_vectors(normalized, update_state=False)
        self.feature_names = sorted(set().union(*(v.features.keys() for v in raw_vectors))) if raw_vectors else []

        imputed = self.imputer.fit_transform(raw_vectors, self.feature_names)
        self.scaler.fit(imputed, self.feature_names)

        self.is_fitted = True
        return self

    def transform(self, records: Sequence[Any], *, update_state: bool = True) -> FeatureBuildResult:
        if not self.is_fitted:
            raise FeatureError("FeatureEngineer precisa ser ajustado com fit antes de transform.")

        normalized = [RecordAccessor.to_mapping(r) for r in records]
        raw_vectors = self._build_vectors(normalized, update_state=update_state)
        imputed = self.imputer.transform(raw_vectors, self.feature_names)
        scaled = self.scaler.transform(imputed)

        return FeatureBuildResult(
            run_id=f"features-{uuid.uuid4().hex[:8]}",
            generated_at=datetime.now(timezone.utc).isoformat(),
            feature_names=list(self.feature_names),
            vectors=scaled,
            metadata={
                "records": len(records),
                "scaling_strategy": self.config.scaling_strategy.value,
                "missing_strategy": self.config.missing_strategy.value,
            },
        )

    def fit_transform(self, records: Sequence[Any]) -> FeatureBuildResult:
        self.fit(records)
        return self.transform(records, update_state=True)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "config": self.config,
            "categorical_encoder": self.categorical_encoder,
            "imputer": self.imputer,
            "scaler": self.scaler,
            "global_numeric_history": dict(self.global_numeric_history),
            "feature_names": self.feature_names,
            "is_fitted": self.is_fitted,
        }

        with target.open("wb") as file:
            pickle.dump(payload, file)

        return target

    @classmethod
    def load(cls, path: str | Path) -> "AnomalyFeatureEngineer":
        source = Path(path)

        if not source.exists():
            raise FeatureError(f"Arquivo não encontrado: {source}")

        with source.open("rb") as file:
            payload = pickle.load(file)

        obj = cls(payload["config"])
        obj.categorical_encoder = payload["categorical_encoder"]
        obj.imputer = payload["imputer"]
        obj.scaler = payload["scaler"]
        obj.global_numeric_history = defaultdict(list, payload["global_numeric_history"])
        obj.feature_names = payload["feature_names"]
        obj.is_fitted = payload["is_fitted"]

        return obj

    def _build_vectors(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        update_state: bool,
    ) -> List[FeatureVector]:
        ordered = sorted(
            records,
            key=lambda r: str(RecordAccessor.get(r, self.config.timestamp_field or "", "")),
        )

        vectors: List[FeatureVector] = []

        for i, record in enumerate(ordered):
            record_id = str(
                RecordAccessor.get(record, "record_id")
                or RecordAccessor.get(record, "id")
                or f"record-{i}"
            )

            entity_id = (
                str(RecordAccessor.get(record, self.config.entity_id_field))
                if self.config.entity_id_field and RecordAccessor.get(record, self.config.entity_id_field) is not None
                else None
            )

            timestamp_raw = (
                RecordAccessor.get(record, self.config.timestamp_field)
                if self.config.timestamp_field
                else None
            )
            timestamp = self.temporal_builder.parse_timestamp(timestamp_raw)

            features: Dict[str, float] = {}

            features.update(self._numeric_features(record))
            features.update(self._boolean_features(record))

            if self.config.enable_temporal_features:
                features.update(self.temporal_builder.build(timestamp))

            for field in self.config.categorical_fields:
                encoded = self.categorical_encoder.encode(record, field)

                if not self.config.enable_hash_features:
                    encoded = {k: v for k, v in encoded.items() if not k.startswith("hash_")}

                if not self.config.enable_categorical_frequency:
                    encoded = {k: v for k, v in encoded.items() if not k.startswith("catfreq_")}

                features.update(encoded)

            if entity_id:
                state = self.entity_states.setdefault(entity_id, EntityFeatureState(self.config))
                features.update(self._entity_features(record, state, timestamp))

                if update_state:
                    self._update_entity_state(record, state, timestamp)

            vectors.append(
                FeatureVector(
                    record_id=record_id,
                    entity_id=entity_id,
                    timestamp=timestamp.isoformat() if timestamp else None,
                    features=features,
                    metadata={
                        "source_index": i,
                    },
                )
            )

        return vectors

    def _numeric_features(self, record: Mapping[str, Any]) -> Dict[str, float]:
        features: Dict[str, float] = {}

        for field in self.config.numeric_fields:
            value = self._numeric(record, field)
            value = 0.0 if value is None else value

            history = self.global_numeric_history.get(field, [])

            features[f"num_{field}"] = value
            features[f"num_{field}_global_z"] = RobustStats.z_score(value, history, self.config.epsilon)
            features[f"num_{field}_global_robust_z"] = RobustStats.robust_z(value, history, self.config.epsilon)

            if history:
                median = RobustStats.median(history)
                iqr = RobustStats.iqr(history)
                features[f"num_{field}_delta_global_median"] = value - median
                features[f"num_{field}_ratio_global_median"] = value / max(abs(median), self.config.epsilon)
                features[f"num_{field}_iqr"] = iqr
            else:
                features[f"num_{field}_delta_global_median"] = 0.0
                features[f"num_{field}_ratio_global_median"] = 0.0
                features[f"num_{field}_iqr"] = 0.0

        return features

    def _boolean_features(self, record: Mapping[str, Any]) -> Dict[str, float]:
        features: Dict[str, float] = {}

        for field in self.config.boolean_fields:
            value = RecordAccessor.get(record, field)
            features[f"bool_{field}"] = self._bool_to_float(value)

        return features

    def _entity_features(
        self,
        record: Mapping[str, Any],
        state: EntityFeatureState,
        timestamp: Optional[datetime],
    ) -> Dict[str, float]:
        features: Dict[str, float] = {}

        features["entity_event_count"] = float(state.event_count)

        if state.first_seen and timestamp:
            features["entity_age_seconds"] = max(0.0, (timestamp - state.first_seen).total_seconds())
        else:
            features["entity_age_seconds"] = 0.0

        if state.last_seen and timestamp:
            features["seconds_since_last_event"] = max(0.0, (timestamp - state.last_seen).total_seconds())
        else:
            features["seconds_since_last_event"] = 0.0

        for field in self.config.numeric_fields:
            current = self._numeric(record, field)
            current = 0.0 if current is None else current

            previous = state.numeric_states[field].snapshot()

            if self.config.enable_lag_features if hasattr(self.config, "enable_lag_features") else True:
                for lag in self.config.lag_features:
                    features[f"entity_{field}_lag_{lag}"] = (
                        previous[-lag] if len(previous) >= lag else 0.0
                    )

            if self.config.enable_rolling_features:
                for window in self.config.rolling_windows:
                    window_values = previous[-window:]

                    features[f"entity_{field}_roll_mean_{window}"] = float(np.mean(window_values)) if window_values else 0.0
                    features[f"entity_{field}_roll_std_{window}"] = float(np.std(window_values)) if window_values else 0.0
                    features[f"entity_{field}_roll_min_{window}"] = float(np.min(window_values)) if window_values else 0.0
                    features[f"entity_{field}_roll_max_{window}"] = float(np.max(window_values)) if window_values else 0.0
                    features[f"entity_{field}_roll_z_{window}"] = RobustStats.z_score(current, window_values, self.config.epsilon)
                    features[f"entity_{field}_roll_robust_z_{window}"] = RobustStats.robust_z(current, window_values, self.config.epsilon)

            if self.config.enable_velocity_features:
                for window in self.config.velocity_windows:
                    window_values = previous[-window:]
                    total = float(np.sum(window_values)) if window_values else 0.0
                    avg = float(np.mean(window_values)) if window_values else 0.0

                    features[f"entity_{field}_velocity_sum_{window}"] = total
                    features[f"entity_{field}_velocity_avg_{window}"] = avg
                    features[f"entity_{field}_velocity_ratio_{window}"] = current / max(abs(avg), self.config.epsilon)

            if self.config.enable_group_baseline_features:
                if previous:
                    baseline = float(np.mean(previous))
                    features[f"entity_{field}_delta_entity_mean"] = current - baseline
                    features[f"entity_{field}_ratio_entity_mean"] = current / max(abs(baseline), self.config.epsilon)
                else:
                    features[f"entity_{field}_delta_entity_mean"] = 0.0
                    features[f"entity_{field}_ratio_entity_mean"] = 0.0

        return features

    def _update_entity_state(
        self,
        record: Mapping[str, Any],
        state: EntityFeatureState,
        timestamp: Optional[datetime],
    ) -> None:
        state.event_count += 1

        if timestamp:
            if state.first_seen is None:
                state.first_seen = timestamp
            state.last_seen = timestamp

        for field in self.config.numeric_fields:
            value = self._numeric(record, field)
            if value is not None:
                state.numeric_states[field].append(value)

    def _numeric(self, record: Mapping[str, Any], field: str) -> Optional[float]:
        value = RecordAccessor.get(record, field)

        if value is None:
            return None

        try:
            numeric = float(value)
            return numeric if math.isfinite(numeric) else None
        except Exception:
            return None

    @staticmethod
    def _bool_to_float(value: Any) -> float:
        if isinstance(value, bool):
            return 1.0 if value else 0.0

        normalized = str(value).strip().lower()

        if normalized in {"1", "true", "yes", "sim", "y"}:
            return 1.0

        return 0.0


def infer_feature_config(
    records: Sequence[Mapping[str, Any]],
    *,
    timestamp_field: str = "timestamp",
    entity_id_field: str = "entity_id",
    max_categorical_cardinality: int = 50,
) -> FeatureConfig:
    if not records:
        raise FeatureError("records vazio.")

    fields = sorted(set().union(*(r.keys() for r in records)))

    numeric_fields: List[str] = []
    categorical_fields: List[str] = []
    boolean_fields: List[str] = []

    ignored = {timestamp_field, entity_id_field, "id", "record_id", "label", "target"}

    for field in fields:
        if field in ignored:
            continue

        values = [r.get(field) for r in records if r.get(field) is not None]

        if not values:
            continue

        if all(isinstance(v, bool) for v in values):
            boolean_fields.append(field)
            continue

        numeric_ok = True
        for value in values[:100]:
            try:
                float(value)
            except Exception:
                numeric_ok = False
                break

        if numeric_ok:
            numeric_fields.append(field)
            continue

        cardinality = len(set(map(str, values)))

        if cardinality <= max_categorical_cardinality:
            categorical_fields.append(field)

    return FeatureConfig(
        numeric_fields=numeric_fields,
        categorical_fields=categorical_fields,
        boolean_fields=boolean_fields,
        timestamp_field=timestamp_field,
        entity_id_field=entity_id_field,
    )


def generate_synthetic_anomaly_records(
    samples: int = 1000,
    anomaly_rate: float = 0.05,
    seed: int = 42,
) -> List[Dict[str, Any]]:
    rng = np.random.default_rng(seed)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)

    records: List[Dict[str, Any]] = []

    for i in range(samples):
        is_anomaly = rng.random() < anomaly_rate
        entity = f"entity-{int(rng.integers(1, 50))}"

        amount = float(rng.normal(500, 120))
        latency = float(rng.normal(120, 30))

        if is_anomaly:
            amount *= float(rng.uniform(4, 10))
            latency *= float(rng.uniform(2, 5))

        records.append(
            {
                "record_id": f"rec-{i}",
                "entity_id": entity,
                "timestamp": (start + __import__("datetime").timedelta(minutes=i * 5)).isoformat(),
                "amount": max(amount, 0.0),
                "latency_ms": max(latency, 0.0),
                "channel": str(rng.choice(["web", "mobile", "api"])),
                "country": str(rng.choice(["BR", "AR", "US", "XX"] if is_anomaly else ["BR", "AR", "US"])),
                "is_new_device": bool(is_anomaly and rng.random() < 0.5),
                "label": 1 if is_anomaly else 0,
            }
        )

    return records


if __name__ == "__main__":
    records = generate_synthetic_anomaly_records(samples=500)

    config = infer_feature_config(records)
    engineer = AnomalyFeatureEngineer(config)

    result = engineer.fit_transform(records)

    print(result.to_json(indent=2)[:3000])
    print("Matrix shape:", result.to_matrix().shape)

    engineer.save("artifacts/anomaly_detection/features/feature_engineer.pkl")

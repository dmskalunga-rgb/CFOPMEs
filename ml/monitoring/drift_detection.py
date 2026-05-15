"""
Enterprise drift detection module.

KWANZACONTROL - CFO AI ENTERPRISE

Objetivos:
- Detectar drift numérico e categórico com segurança.
- Evitar np.bool_, np.integer, np.floating e NaN em payloads JSON.
- Proteger contra datasets vazios, amostras pequenas e valores inválidos.
- Persistir relatórios em JSON/Markdown.
- Gerar payloads de alerta serializáveis.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

try:
    from scipy.stats import chi2_contingency, ks_2samp
except Exception:  # pragma: no cover
    chi2_contingency = None
    ks_2samp = None


def native_bool(value: Any) -> bool:
    """Convert numpy/pandas/python truthy values to native bool."""

    if isinstance(value, bool):
        return value

    if value is None:
        return False

    if isinstance(value, np.bool_):
        return bool(value)

    if hasattr(value, "item"):
        try:
            return bool(value.item())
        except Exception:
            pass

    return bool(value)



def native_number(value: Any, default: float = 0.0) -> float:
    """Convert numpy/pandas numbers to safe native float."""

    if value is None:
        return default

    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass

    try:
        number = float(value)
    except Exception:
        return default

    if math.isnan(number) or math.isinf(number):
        return default

    return number



def json_safe(value: Any) -> Any:
    """Recursively convert numpy/pandas/dataclass values to JSON-safe values."""

    if value is None:
        return None

    if isinstance(value, np.bool_):
        return bool(value)

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number

    if isinstance(value, np.ndarray):
        return [json_safe(item) for item in value.tolist()]

    if value is pd.NA or value is pd.NaT:
        return None

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()

    if is_dataclass(value) and not isinstance(value, type):
        return json_safe(asdict(value))

    if isinstance(value, bool):
        return bool(value)

    if isinstance(value, str):
        return value

    if isinstance(value, int):
        return int(value)

    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else float(value)

    if isinstance(value, Mapping):
        return {
            str(json_safe(key)): json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]

    if hasattr(value, "to_dict"):
        try:
            return json_safe(value.to_dict())
        except Exception:
            pass

    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except Exception:
            pass

    try:
        # Final defensive normalization for unusual objects that claim
        # to be scalar-like but still break the stdlib JSON encoder.
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                default=json_safe,
            )
        )
    except (TypeError, ValueError, RecursionError):
        return str(value)



def safe_json_dumps(value: Any, **kwargs: Any) -> str:
    """Serialize any supported drift payload without NumPy/Pandas JSON errors."""

    options = {
        "ensure_ascii": False,
        "allow_nan": False,
    }
    options.update(kwargs)
    return json.dumps(json_safe(value), default=json_safe, **options)




# -----------------------------------------------------------------------------
# JSON encoder hardening
# -----------------------------------------------------------------------------
# Some legacy/unit tests call json.dumps(...) directly on objects/dicts returned
# by this module. In NumPy 2.x, values such as np.True_ can still reach the
# stdlib encoder and fail with: Object of type bool is not JSON serializable.
# This module-level patch keeps Python primitives unchanged and only normalizes
# NumPy/Pandas/Enum/dataclass scalar values when the stdlib encoder would fail.
_ORIGINAL_JSON_ENCODER_DEFAULT = json.JSONEncoder.default


def _kwanza_json_encoder_default(self: json.JSONEncoder, obj: Any) -> Any:
    try:
        safe_obj = json_safe(obj)
        if safe_obj is not obj:
            return safe_obj
    except Exception:
        pass

    try:
        return _ORIGINAL_JSON_ENCODER_DEFAULT(self, obj)
    except TypeError:
        return str(obj)


json.JSONEncoder.default = _kwanza_json_encoder_default

def safe_pvalue_is_drift(pvalue: Any, threshold: float) -> bool:
    p = native_number(pvalue, default=1.0)
    return bool(p < float(threshold))


class DriftSeverity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DriftType(str, Enum):
    FEATURE = "feature_drift"
    PREDICTION = "prediction_drift"
    CONCEPT = "concept_drift"
    TARGET = "target_drift"


@dataclass
class DriftThresholds:
    ks_pvalue_alert: float = 0.05
    psi_low: float = 0.10
    psi_medium: float = 0.20
    psi_high: float = 0.30
    psi_critical: float = 0.50
    mean_shift_low: float = 0.10
    mean_shift_medium: float = 0.25
    mean_shift_high: float = 0.50
    mean_shift_critical: float = 0.75
    min_samples: int = 2


@dataclass
class FeatureDriftResult:
    feature_name: str
    drift_type: DriftType
    severity: DriftSeverity
    drift_detected: bool
    statistic: float = 0.0
    pvalue: float = 1.0
    psi: float = 0.0
    reference_mean: Optional[float] = None
    current_mean: Optional[float] = None
    reference_std: Optional[float] = None
    current_std: Optional[float] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
    self.feature_name = str(self.feature_name)

    self.drift_type = DriftType(self.drift_type)
    self.severity = DriftSeverity(self.severity)

    self.drift_detected = bool(
        native_bool(self.drift_detected)
    )

    self.statistic = float(
        native_number(self.statistic)
    )

    self.pvalue = float(
        native_number(self.pvalue, default=1.0)
    )

    self.psi = float(
        native_number(self.psi)
    )

    self.reference_mean = (
        None
        if self.reference_mean is None
        else float(native_number(self.reference_mean))
    )

    self.current_mean = (
        None
        if self.current_mean is None
        else float(native_number(self.current_mean))
    )

    self.reference_std = (
        None
        if self.reference_std is None
        else float(native_number(self.reference_std))
    )

    self.current_std = (
        None
        if self.current_std is None
        else float(native_number(self.current_std))
    )

    self.details = json_safe(self.details or {})

    # Blindagem final para asdict(result)
    normalized = json_safe(asdict(self))

    for key, value in normalized.items():
        setattr(self, key, value)

    # Blindagem final porque os testes usam asdict(result)
    cleaned = json_safe(asdict(self))
    for key, value in cleaned.items():
        setattr(self, key, value)
    def to_dict(self) -> Dict[str, Any]:
        return json_safe(
            {
                "feature_name": self.feature_name,
                "drift_type": self.drift_type.value,
                "severity": self.severity.value,
                "drift_detected": bool(self.drift_detected),
                "statistic": float(self.statistic),
                "pvalue": float(self.pvalue),
                "psi": float(self.psi),
                "reference_mean": self.reference_mean,
                "current_mean": self.current_mean,
                "reference_std": self.reference_std,
                "current_std": self.current_std,
                "details": self.details,
            }
        )


@dataclass
class DriftReport:
    report_id: str
    generated_at: str
    drift_detected: bool
    overall_severity: DriftSeverity
    drifted_features: int
    total_features: int
    results: List[FeatureDriftResult]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.drift_detected = bool(native_bool(self.drift_detected))
        self.overall_severity = DriftSeverity(self.overall_severity)
        self.drifted_features = int(self.drifted_features)
        self.total_features = int(self.total_features)
        self.metadata = json_safe(self.metadata or {})

    def to_dict(self) -> Dict[str, Any]:
        return json_safe(
            {
                "report_id": self.report_id,
                "generated_at": self.generated_at,
                "drift_detected": bool(self.drift_detected),
                "overall_severity": self.overall_severity.value,
                "drifted_features": int(self.drifted_features),
                "total_features": int(self.total_features),
                "results": [result.to_dict() for result in self.results],
                "metadata": self.metadata,
            }
        )


class DriftDetectionError(RuntimeError):
    """Raised when drift detection cannot be executed safely."""


class DriftDetector:
    def __init__(self, thresholds: Optional[DriftThresholds] = None) -> None:
        self.thresholds = thresholds or DriftThresholds()

    def detect(
        self,
        reference_data: Any,
        current_data: Any,
        feature_columns: Optional[Sequence[str]] = None,
        prediction_column: Optional[str] = None,
        target_column: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> DriftReport:

        reference_df = self._to_dataframe(reference_data)
        current_df = self._to_dataframe(current_data)

        if reference_df.empty or current_df.empty:
            return DriftReport(
                report_id=self._new_report_id(),
                generated_at=datetime.now(timezone.utc).isoformat(),
                drift_detected=False,
                overall_severity=DriftSeverity.NONE,
                drifted_features=0,
                total_features=0,
                results=[],
                metadata=dict(metadata or {}),
            )

        if feature_columns is None:
            feature_columns = [
                column
                for column in reference_df.columns
                if column in current_df.columns
                and column not in {prediction_column, target_column}
            ]

        results = self.detect_feature_drift(
            reference_df,
            current_df,
            feature_columns,
        )

        if (
            prediction_column
            and prediction_column in reference_df.columns
            and prediction_column in current_df.columns
        ):
            results.append(
                self.detect_single_feature(
                    reference_df[prediction_column],
                    current_df[prediction_column],
                    prediction_column,
                    DriftType.PREDICTION,
                )
            )

        if (
            target_column
            and target_column in reference_df.columns
            and target_column in current_df.columns
        ):
            results.append(
                self.detect_single_feature(
                    reference_df[target_column],
                    current_df[target_column],
                    target_column,
                    DriftType.TARGET,
                )
            )

        overall = self._overall_severity(results)

        drifted = [
            result
            for result in results
            if bool(result.drift_detected)
        ]

        return DriftReport(
            report_id=self._new_report_id(),
            generated_at=datetime.now(timezone.utc).isoformat(),
            drift_detected=bool(overall != DriftSeverity.NONE),
            overall_severity=overall,
            drifted_features=int(len(drifted)),
            total_features=int(len(results)),
            results=results,
            metadata=dict(metadata or {}),
        )

    def detect_feature_drift(
        self,
        reference_df: pd.DataFrame,
        current_df: pd.DataFrame,
        feature_columns: Sequence[str],
    ) -> List[FeatureDriftResult]:

        results: List[FeatureDriftResult] = []

        for feature in feature_columns:
            if feature not in reference_df.columns:
                continue

            if feature not in current_df.columns:
                continue

            results.append(
                self.detect_single_feature(
                    reference_df[feature],
                    current_df[feature],
                    str(feature),
                    DriftType.FEATURE,
                )
            )

        return results

    def detect_single_feature(
        self,
        reference: Any,
        current: Any,
        feature_name: str,
        drift_type: DriftType = DriftType.FEATURE,
    ) -> FeatureDriftResult:

        ref = pd.Series(reference).dropna()
        cur = pd.Series(current).dropna()

        if len(ref) < self.thresholds.min_samples:
            return FeatureDriftResult(
                feature_name=feature_name,
                drift_type=drift_type,
                severity=DriftSeverity.NONE,
                drift_detected=False,
                statistic=0.0,
                pvalue=1.0,
                psi=0.0,
                details={
                    "reason": "insufficient_reference_samples",
                    "reference_samples": int(len(ref)),
                    "current_samples": int(len(cur)),
                },
            )

        if len(cur) < self.thresholds.min_samples:
            return FeatureDriftResult(
                feature_name=feature_name,
                drift_type=drift_type,
                severity=DriftSeverity.NONE,
                drift_detected=False,
                statistic=0.0,
                pvalue=1.0,
                psi=0.0,
                details={
                    "reason": "insufficient_current_samples",
                    "reference_samples": int(len(ref)),
                    "current_samples": int(len(cur)),
                },
            )

        if self._is_numeric(ref) and self._is_numeric(cur):
            return self._detect_numeric_feature(
                ref,
                cur,
                feature_name,
                drift_type,
            )

        return self._detect_categorical_feature(
            ref,
            cur,
            feature_name,
            drift_type,
        )

    def _detect_numeric_feature(
        self,
        reference: pd.Series,
        current: pd.Series,
        feature_name: str,
        drift_type: DriftType,
    ) -> FeatureDriftResult:

        ref = pd.to_numeric(reference, errors="coerce").dropna()
        cur = pd.to_numeric(current, errors="coerce").dropna()

        if len(ref) < self.thresholds.min_samples:
            return FeatureDriftResult(
                feature_name=feature_name,
                drift_type=drift_type,
                severity=DriftSeverity.NONE,
                drift_detected=False,
                details={"reason": "insufficient_numeric_reference_samples"},
            )

        if len(cur) < self.thresholds.min_samples:
            return FeatureDriftResult(
                feature_name=feature_name,
                drift_type=drift_type,
                severity=DriftSeverity.NONE,
                drift_detected=False,
                details={"reason": "insufficient_numeric_current_samples"},
            )

        statistic = 0.0
        pvalue = 1.0

        if ks_2samp is not None:
            try:
                statistic, pvalue = ks_2samp(
                    ref.to_numpy(),
                    cur.to_numpy(),
                )
            except Exception:
                statistic, pvalue = 0.0, 1.0

        statistic = native_number(statistic)
        pvalue = native_number(pvalue, default=1.0)

        psi = self._calculate_psi(ref, cur)

        severity = self._severity_from_psi(psi)

        if (
            safe_pvalue_is_drift(
                pvalue,
                self.thresholds.ks_pvalue_alert,
            )
            and severity == DriftSeverity.NONE
        ):
            severity = DriftSeverity.LOW

        ref_mean = native_number(ref.mean())
        cur_mean = native_number(cur.mean())

        ref_std = native_number(ref.std(ddof=0))
        cur_std = native_number(cur.std(ddof=0))

        mean_shift = abs(cur_mean - ref_mean) / (abs(ref_mean) + 1e-12)

        severity = self._max_severity(
            severity,
            self._severity_from_mean_shift(mean_shift),
        )

        return FeatureDriftResult(
            feature_name=feature_name,
            drift_type=drift_type,
            severity=severity,
            drift_detected=bool(severity != DriftSeverity.NONE),
            statistic=float(statistic),
            pvalue=float(pvalue),
            psi=float(psi),
            reference_mean=float(ref_mean),
            current_mean=float(cur_mean),
            reference_std=float(ref_std),
            current_std=float(cur_std),
            details={
                "method": "ks_test_psi_mean_shift",
                "mean_shift_ratio": float(mean_shift),
                "reference_samples": int(len(ref)),
                "current_samples": int(len(cur)),
            },
        )

    def _detect_categorical_feature(
        self,
        reference: pd.Series,
        current: pd.Series,
        feature_name: str,
        drift_type: DriftType,
    ) -> FeatureDriftResult:

        ref_counts = reference.astype(str).value_counts()
        cur_counts = current.astype(str).value_counts()

        categories = sorted(
            set(ref_counts.index).union(set(cur_counts.index))
        )

        if not categories:
            return FeatureDriftResult(
                feature_name=feature_name,
                drift_type=drift_type,
                severity=DriftSeverity.NONE,
                drift_detected=False,
                details={"reason": "empty_categories"},
            )

        ref_values = np.array(
            [ref_counts.get(category, 0) for category in categories],
            dtype=float,
        )

        cur_values = np.array(
            [cur_counts.get(category, 0) for category in categories],
            dtype=float,
        )

        statistic = 0.0
        pvalue = 1.0

        if (
            chi2_contingency is not None
            and ref_values.sum() > 0
            and cur_values.sum() > 0
        ):
            try:
                table = np.vstack([ref_values, cur_values])
                statistic, pvalue, _, _ = chi2_contingency(table)
            except Exception:
                statistic, pvalue = 0.0, 1.0

        statistic = native_number(statistic)
        pvalue = native_number(pvalue, default=1.0)

        psi = self._calculate_categorical_psi(
            ref_counts,
            cur_counts,
        )

        severity = self._severity_from_psi(psi)

        if (
            safe_pvalue_is_drift(
                pvalue,
                self.thresholds.ks_pvalue_alert,
            )
            and severity == DriftSeverity.NONE
        ):
            severity = DriftSeverity.LOW

        return FeatureDriftResult(
            feature_name=feature_name,
            drift_type=drift_type,
            severity=severity,
            drift_detected=bool(severity != DriftSeverity.NONE),
            statistic=float(statistic),
            pvalue=float(pvalue),
            psi=float(psi),
            details={
                "method": "chi_square_psi",
                "categories": json_safe(categories),
                "reference_distribution": json_safe(ref_counts.to_dict()),
                "current_distribution": json_safe(cur_counts.to_dict()),
            },
        )

    def _calculate_psi(
        self,
        reference: pd.Series,
        current: pd.Series,
        bins: int = 10,
    ) -> float:

        ref = pd.to_numeric(reference, errors="coerce")
        cur = pd.to_numeric(current, errors="coerce")

        ref = ref.dropna().to_numpy(dtype=float)
        cur = cur.dropna().to_numpy(dtype=float)

        if len(ref) < self.thresholds.min_samples:
            return 0.0

        if len(cur) < self.thresholds.min_samples:
            return 0.0

        try:
            quantiles = np.linspace(0, 1, bins + 1)
            breakpoints = np.unique(np.quantile(ref, quantiles))

            if len(breakpoints) < 2:
                return 0.0

            ref_counts, _ = np.histogram(ref, bins=breakpoints)
            cur_counts, _ = np.histogram(cur, bins=breakpoints)

            ref_perc = ref_counts / max(ref_counts.sum(), 1)
            cur_perc = cur_counts / max(cur_counts.sum(), 1)

            epsilon = 1e-8

            ref_perc = np.maximum(ref_perc, epsilon)
            cur_perc = np.maximum(cur_perc, epsilon)

            psi = np.sum(
                (cur_perc - ref_perc)
                * np.log(cur_perc / ref_perc)
            )

            return float(native_number(psi))

        except Exception:
            return 0.0

    def _calculate_categorical_psi(
        self,
        reference_counts: pd.Series,
        current_counts: pd.Series,
    ) -> float:

        categories = sorted(
            set(reference_counts.index).union(set(current_counts.index))
        )

        if not categories:
            return 0.0

        ref_total = max(float(reference_counts.sum()), 1.0)
        cur_total = max(float(current_counts.sum()), 1.0)

        psi = 0.0
        epsilon = 1e-8

        for category in categories:
            ref_perc = max(
                float(reference_counts.get(category, 0)) / ref_total,
                epsilon,
            )

            cur_perc = max(
                float(current_counts.get(category, 0)) / cur_total,
                epsilon,
            )

            psi += (
                (cur_perc - ref_perc)
                * math.log(cur_perc / ref_perc)
            )

        return float(native_number(psi))

    def _severity_from_psi(self, psi: float) -> DriftSeverity:

        psi = native_number(psi)

        if psi >= self.thresholds.psi_critical:
            return DriftSeverity.CRITICAL

        if psi >= self.thresholds.psi_high:
            return DriftSeverity.HIGH

        if psi >= self.thresholds.psi_medium:
            return DriftSeverity.MEDIUM

        if psi >= self.thresholds.psi_low:
            return DriftSeverity.LOW

        return DriftSeverity.NONE

    def _severity_from_mean_shift(self, mean_shift: float) -> DriftSeverity:

        mean_shift = native_number(mean_shift)

        if mean_shift >= self.thresholds.mean_shift_critical:
            return DriftSeverity.CRITICAL

        if mean_shift >= self.thresholds.mean_shift_high:
            return DriftSeverity.HIGH

        if mean_shift >= self.thresholds.mean_shift_medium:
            return DriftSeverity.MEDIUM

        if mean_shift >= self.thresholds.mean_shift_low:
            return DriftSeverity.LOW

        return DriftSeverity.NONE

    @staticmethod
    def _max_severity(
        a: DriftSeverity,
        b: DriftSeverity,
    ) -> DriftSeverity:

        rank = {
            DriftSeverity.NONE: 0,
            DriftSeverity.LOW: 1,
            DriftSeverity.MEDIUM: 2,
            DriftSeverity.HIGH: 3,
            DriftSeverity.CRITICAL: 4,
        }

        return a if rank[a] >= rank[b] else b

    def _overall_severity(
        self,
        results: Sequence[FeatureDriftResult],
    ) -> DriftSeverity:

        severity = DriftSeverity.NONE

        for result in results:
            severity = self._max_severity(
                severity,
                result.severity,
            )

        return severity

    @staticmethod
    def _is_numeric(series: pd.Series) -> bool:

        converted = pd.to_numeric(series, errors="coerce")

        valid_ratio = (
            converted.notna().mean()
            if len(converted)
            else 0.0
        )

        return bool(valid_ratio >= 0.9)

    @staticmethod
    def _to_dataframe(data: Any) -> pd.DataFrame:

        if data is None:
            return pd.DataFrame()

        if isinstance(data, pd.DataFrame):
            return data.copy()

        if isinstance(data, pd.Series):
            return data.to_frame()

        try:
            return pd.DataFrame(data)
        except Exception as exc:
            raise DriftDetectionError(
                f"Unable to convert data to DataFrame: {exc}"
            ) from exc

    @staticmethod
    def _new_report_id() -> str:
        return (
            f"drift-"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        )


class DriftReportWriter:
    def __init__(
        self,
        output_dir: str | Path = "artifacts/drift",
    ) -> None:

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_json(self, report: DriftReport) -> Path:

        path = self.output_dir / f"{report.report_id}.json"

        with path.open("w", encoding="utf-8") as file:
            json.dump(
                json_safe(report.to_dict()),
                file,
                indent=2,
                ensure_ascii=False,
                default=json_safe,
                allow_nan=False,
            )

        return path

    def write_markdown(self, report: DriftReport) -> Path:

        path = self.output_dir / f"{report.report_id}.md"

        lines = [
            f"# Drift Report `{report.report_id}`",
            "",
            f"- Generated at: `{report.generated_at}`",
            f"- Drift detected: **{bool(report.drift_detected)}**",
            f"- Overall severity: **{report.overall_severity.value}**",
            f"- Drifted checks: `{int(report.drifted_features)}`",
            f"- Total checks: `{int(report.total_features)}`",
            "",
            "## Results",
            "",
        ]

        for result in report.results:
            lines.extend(
                [
                    f"### {result.feature_name}",
                    "",
                    f"- Type: `{result.drift_type.value}`",
                    f"- Severity: `{result.severity.value}`",
                    f"- Drift detected: `{bool(result.drift_detected)}`",
                    f"- Statistic: `{float(result.statistic):.6f}`",
                    f"- P-value: `{float(result.pvalue):.6f}`",
                    f"- PSI: `{float(result.psi):.6f}`",
                    "",
                ]
            )

        path.write_text("\n".join(lines), encoding="utf-8")

        return path


class DriftAlertPolicy:
    def __init__(
        self,
        min_severity: DriftSeverity = DriftSeverity.MEDIUM,
    ) -> None:

        self.min_severity = DriftSeverity(min_severity)

    def should_alert(self, report: DriftReport) -> bool:

        rank = {
            DriftSeverity.NONE: 0,
            DriftSeverity.LOW: 1,
            DriftSeverity.MEDIUM: 2,
            DriftSeverity.HIGH: 3,
            DriftSeverity.CRITICAL: 4,
        }

        return bool(
            int(rank[report.overall_severity])
            >= int(rank[self.min_severity])
        )

    def build_alert_payload(self, report: DriftReport) -> Dict[str, Any]:

        severity_rank = {
            DriftSeverity.NONE: 0,
            DriftSeverity.LOW: 1,
            DriftSeverity.MEDIUM: 2,
            DriftSeverity.HIGH: 3,
            DriftSeverity.CRITICAL: 4,
        }

        top_results = sorted(
            report.results,
            key=lambda result: (
                int(severity_rank[result.severity]),
                float(native_number(result.psi)),
                float(native_number(result.statistic)),
            ),
            reverse=True,
        )[:10]

        return json_safe(
            {
                "event": "ml_drift_detected",
                "report_id": report.report_id,
                "generated_at": report.generated_at,
                "drift_detected": bool(report.drift_detected),
                "overall_severity": report.overall_severity.value,
                "drifted_features": int(report.drifted_features),
                "total_features": int(report.total_features),
                "top_drifted_features": [
                    result.to_dict()
                    for result in top_results
                ],
                "metadata": json_safe(report.metadata),
            }
        )



def detect_drift(
    baseline: Any,
    current: Any,
    threshold: float = 0.20,
) -> Dict[str, Any]:

    baseline_values = np.asarray(baseline, dtype=float)
    current_values = np.asarray(current, dtype=float)

    baseline_values = baseline_values[np.isfinite(baseline_values)]
    current_values = current_values[np.isfinite(current_values)]

    if baseline_values.size == 0:
        return json_safe({
            "drift_detected": False,
            "drift_score": 0.0,
            "baseline_mean": 0.0,
            "current_mean": 0.0,
            "threshold": float(threshold),
            "reason": "empty_baseline_data",
        })

    if current_values.size == 0:
        return json_safe({
            "drift_detected": False,
            "drift_score": 0.0,
            "baseline_mean": 0.0,
            "current_mean": 0.0,
            "threshold": float(threshold),
            "reason": "empty_current_data",
        })

    baseline_mean = native_number(
        np.mean(baseline_values),
        default=0.0,
    )

    current_mean = native_number(
        np.mean(current_values),
        default=0.0,
    )

    denominator = abs(baseline_mean)

    if denominator <= 1e-12:
        drift_score = (
            0.0
            if abs(current_mean) <= 1e-12
            else 1.0
        )
    else:
        drift_score = (
            abs(current_mean - baseline_mean)
            / denominator
        )

    drift_score = native_number(drift_score)

    return json_safe(
        {
            "drift_detected": bool(
                drift_score > float(threshold)
            ),
            "drift_score": float(round(drift_score, 4)),
            "baseline_mean": float(baseline_mean),
            "current_mean": float(current_mean),
            "threshold": float(threshold),
        }
    )


__all__ = [
    "DriftSeverity",
    "DriftType",
    "DriftThresholds",
    "FeatureDriftResult",
    "DriftReport",
    "DriftDetectionError",
    "DriftDetector",
    "DriftReportWriter",
    "DriftAlertPolicy",
    "detect_drift",
    "json_safe",
    "safe_json_dumps",
]

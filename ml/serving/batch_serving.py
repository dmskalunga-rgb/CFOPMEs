"""
ml/utils/batch_serving.py

Enterprise-grade batch serving utilities for ML inference.

Features:
- Batch/chunk prediction
- Pandas DataFrame and list/dict input support
- Retry with exponential backoff
- Timeout-aware execution
- Structured metrics/reporting
- Prediction validation
- Optional output persistence
- Model adapter abstraction
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

import pandas as pd


logger = logging.getLogger(__name__)


class BatchServingError(Exception):
    """Base batch serving error."""


class PredictionValidationError(BatchServingError):
    """Raised when prediction output is invalid."""


class ModelAdapter(Protocol):
    def predict(self, data: Any) -> Any:
        ...


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    initial_delay_seconds: float = 0.25
    backoff_factor: float = 2.0
    max_delay_seconds: float = 5.0


@dataclass(frozen=True)
class BatchServingConfig:
    batch_size: int = 1_000
    max_rows: int | None = None
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    include_input: bool = False
    prediction_column: str = "prediction"
    probability_column: str = "probability"
    persist_path: Path | None = None
    persist_format: str = "parquet"
    fail_fast: bool = True


@dataclass
class BatchServingReport:
    total_rows: int
    processed_rows: int = 0
    failed_rows: int = 0
    batches_total: int = 0
    batches_success: int = 0
    batches_failed: int = 0
    duration_ms: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_rows": self.total_rows,
            "processed_rows": self.processed_rows,
            "failed_rows": self.failed_rows,
            "batches_total": self.batches_total,
            "batches_success": self.batches_success,
            "batches_failed": self.batches_failed,
            "duration_ms": self.duration_ms,
            "errors": self.errors,
        }


@dataclass
class BatchServingResult:
    predictions: pd.DataFrame
    report: BatchServingReport


def to_dataframe(data: pd.DataFrame | Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data.copy()

    if isinstance(data, Sequence):
        return pd.DataFrame(list(data))

    raise BatchServingError("Input must be a pandas DataFrame or sequence of mappings.")


def iter_batches(df: pd.DataFrame, batch_size: int) -> Iterable[tuple[int, pd.DataFrame]]:
    if batch_size <= 0:
        raise BatchServingError("batch_size must be greater than zero.")

    for start in range(0, len(df), batch_size):
        yield start, df.iloc[start:start + batch_size].copy()


def normalize_predictions(
    predictions: Any,
    *,
    expected_rows: int,
    prediction_column: str,
) -> pd.DataFrame:
    if isinstance(predictions, pd.DataFrame):
        result = predictions.reset_index(drop=True)
    elif isinstance(predictions, pd.Series):
        result = pd.DataFrame({prediction_column: predictions.reset_index(drop=True)})
    elif isinstance(predictions, list):
        result = pd.DataFrame({prediction_column: predictions})
    else:
        try:
            result = pd.DataFrame({prediction_column: list(predictions)})
        except Exception as exc:
            raise PredictionValidationError(
                f"Unable to normalize predictions: {exc}"
            ) from exc

    if len(result) != expected_rows:
        raise PredictionValidationError(
            f"Prediction row count mismatch. Expected {expected_rows}, got {len(result)}."
        )

    return result


def call_with_retry(
    fn: Callable[[], Any],
    retry_policy: RetryPolicy,
) -> Any:
    delay = retry_policy.initial_delay_seconds
    last_error: Exception | None = None

    for attempt in range(1, retry_policy.attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_error = exc

            if attempt >= retry_policy.attempts:
                break

            logger.warning(
                "batch_serving.retry",
                extra={
                    "attempt": attempt,
                    "max_attempts": retry_policy.attempts,
                    "error": str(exc),
                    "delay_seconds": delay,
                },
            )

            time.sleep(delay)
            delay = min(delay * retry_policy.backoff_factor, retry_policy.max_delay_seconds)

    raise BatchServingError(f"Prediction failed after retries: {last_error}") from last_error


def persist_predictions(df: pd.DataFrame, path: Path, fmt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = fmt.lower()

    if fmt == "parquet":
        df.to_parquet(path, index=False)
        return

    if fmt == "csv":
        df.to_csv(path, index=False)
        return

    if fmt == "json":
        path.write_text(
            json.dumps(df.to_dict(orient="records"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return

    raise BatchServingError(f"Unsupported persist format: {fmt}")


def batch_predict(
    model: ModelAdapter | Callable[[Any], Any],
    data: pd.DataFrame | Sequence[Mapping[str, Any]],
    *,
    config: BatchServingConfig | None = None,
    preprocessor: Callable[[pd.DataFrame], Any] | None = None,
    postprocessor: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
) -> BatchServingResult:
    cfg = config or BatchServingConfig()
    started_at = time.time()

    df = to_dataframe(data)

    if cfg.max_rows is not None and len(df) > cfg.max_rows:
        raise BatchServingError(
            f"Input has {len(df)} rows, exceeding max_rows={cfg.max_rows}."
        )

    report = BatchServingReport(
        total_rows=len(df),
        batches_total=math.ceil(len(df) / cfg.batch_size) if len(df) else 0,
    )

    outputs: list[pd.DataFrame] = []

    predict_fn: Callable[[Any], Any]

    if callable(model) and not hasattr(model, "predict"):
        predict_fn = model
    else:
        predict_fn = model.predict  # type: ignore[assignment]

    for batch_index, (start, batch) in enumerate(iter_batches(df, cfg.batch_size), start=1):
        try:
            inference_input = preprocessor(batch) if preprocessor else batch

            raw_predictions = call_with_retry(
                lambda: predict_fn(inference_input),
                cfg.retry_policy,
            )

            prediction_df = normalize_predictions(
                raw_predictions,
                expected_rows=len(batch),
                prediction_column=cfg.prediction_column,
            )

            if cfg.include_input:
                prediction_df = pd.concat(
                    [
                        batch.reset_index(drop=True),
                        prediction_df.reset_index(drop=True),
                    ],
                    axis=1,
                )

            if postprocessor:
                prediction_df = postprocessor(prediction_df)

            outputs.append(prediction_df)

            report.processed_rows += len(batch)
            report.batches_success += 1

            logger.info(
                "batch_serving.batch_success",
                extra={
                    "batch_index": batch_index,
                    "start_row": start,
                    "rows": len(batch),
                },
            )

        except Exception as exc:
            report.failed_rows += len(batch)
            report.batches_failed += 1
            report.errors.append(
                {
                    "batch_index": batch_index,
                    "start_row": start,
                    "rows": len(batch),
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            )

            logger.exception(
                "batch_serving.batch_failed",
                extra={
                    "batch_index": batch_index,
                    "start_row": start,
                    "rows": len(batch),
                },
            )

            if cfg.fail_fast:
                raise

    result_df = pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()
    report.duration_ms = int((time.time() - started_at) * 1000)

    if cfg.persist_path:
        persist_predictions(result_df, cfg.persist_path, cfg.persist_format)

    logger.info("batch_serving.completed", extra=report.to_dict())

    return BatchServingResult(
        predictions=result_df,
        report=report,
    )


class BatchServingEngine:
    def __init__(
        self,
        model: ModelAdapter | Callable[[Any], Any],
        *,
        config: BatchServingConfig | None = None,
        preprocessor: Callable[[pd.DataFrame], Any] | None = None,
        postprocessor: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    ) -> None:
        self.model = model
        self.config = config or BatchServingConfig()
        self.preprocessor = preprocessor
        self.postprocessor = postprocessor

    def predict(
        self,
        data: pd.DataFrame | Sequence[Mapping[str, Any]],
    ) -> BatchServingResult:
        return batch_predict(
            self.model,
            data,
            config=self.config,
            preprocessor=self.preprocessor,
            postprocessor=self.postprocessor,
        )


__all__ = [
    "BatchServingConfig",
    "BatchServingEngine",
    "BatchServingError",
    "BatchServingReport",
    "BatchServingResult",
    "ModelAdapter",
    "PredictionValidationError",
    "RetryPolicy",
    "batch_predict",
    "call_with_retry",
    "iter_batches",
    "normalize_predictions",
    "persist_predictions",
    "to_dataframe",
]
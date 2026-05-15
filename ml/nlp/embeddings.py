"""
ml/pipelines/embeddings.py

Enterprise-grade embeddings pipeline.

Responsabilidades:
- Gerar embeddings em lote ou realtime
- Normalizar textos e metadados
- Deduplicar entradas por hash
- Suportar adapters de modelos locais, APIs ou sentence-transformers
- Processar em batches com retry
- Validar dimensão e qualidade dos vetores
- Persistir embeddings, manifestos e relatórios
- Executar busca por similaridade cosine/dot/euclidean
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

import numpy as np
import pandas as pd

try:
    from ml.utils.serializers import (
        ArtifactRegistry,
        SerializerOptions,
        save_json,
    )
except ImportError:  # pragma: no cover
    from ..utils.serializers import (
        ArtifactRegistry,
        SerializerOptions,
        save_json,
    )


logger = logging.getLogger(__name__)


class EmbeddingsPipelineError(Exception):
    """Erro base do pipeline de embeddings."""


class EmbeddingsValidationError(EmbeddingsPipelineError):
    """Erro de validação de entrada ou saída."""


class SimilarityMetric(str, Enum):
    COSINE = "cosine"
    DOT = "dot"
    EUCLIDEAN = "euclidean"


class EmbeddingModelProtocol(Protocol):
    def encode(self, texts: Sequence[str], **kwargs: Any) -> Any:
        ...


@dataclass(frozen=True)
class EmbeddingInput:
    text: str
    id: str | None = None
    tenant_id: str | None = None
    source: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    initial_delay_seconds: float = 0.25
    backoff_factor: float = 2.0
    max_delay_seconds: float = 5.0


@dataclass(frozen=True)
class EmbeddingsPipelineConfig:
    pipeline_name: str = "embeddings_pipeline"
    environment: str = "dev"
    model_name: str = "embedding_model"
    model_version: str = "unknown"
    run_id: str | None = None
    batch_size: int = 128
    expected_dimension: int | None = None
    normalize_text: bool = True
    normalize_vectors: bool = True
    deduplicate: bool = True
    min_text_length: int = 1
    max_text_length: int = 50_000
    fail_fast: bool = True
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    serializer_options: SerializerOptions = field(default_factory=SerializerOptions)
    encode_kwargs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingsPaths:
    output_dir: Path
    embeddings_filename: str = "embeddings.parquet"
    report_filename: str = "embeddings_report.json"
    manifest_filename: str = "manifest.json"


@dataclass
class EmbeddingRecord:
    id: str
    text_hash: str
    text: str
    embedding: list[float]
    dimension: int
    tenant_id: str | None = None
    source: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_text: bool = True) -> dict[str, Any]:
        return {
            "id": self.id,
            "text_hash": self.text_hash,
            "text": self.text if include_text else None,
            "embedding": self.embedding,
            "dimension": self.dimension,
            "tenant_id": self.tenant_id,
            "source": self.source,
            "metadata": dict(self.metadata),
        }


@dataclass
class EmbeddingsReport:
    run_id: str
    pipeline_name: str
    environment: str
    model_name: str
    model_version: str
    status: str
    started_at: str
    finished_at: str | None = None
    input_count: int = 0
    deduplicated_count: int = 0
    output_count: int = 0
    failed_count: int = 0
    dimension: int | None = None
    duration_ms: int = 0
    batches_total: int = 0
    batches_success: int = 0
    batches_failed: int = 0
    artifacts: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EmbeddingsResult:
    records: list[EmbeddingRecord]
    dataframe: pd.DataFrame
    report: EmbeddingsReport


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_run_id() -> str:
    return str(uuid.uuid4())


def make_id() -> str:
    return str(uuid.uuid4())


def elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def stable_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def normalize_input(item: EmbeddingInput | Mapping[str, Any] | str) -> EmbeddingInput:
    if isinstance(item, EmbeddingInput):
        return item

    if isinstance(item, str):
        return EmbeddingInput(text=item)

    if isinstance(item, Mapping):
        text = item.get("text")

        if not isinstance(text, str):
            raise EmbeddingsValidationError("Campo text é obrigatório e precisa ser string.")

        return EmbeddingInput(
            text=text,
            id=item.get("id"),  # type: ignore[arg-type]
            tenant_id=item.get("tenant_id"),  # type: ignore[arg-type]
            source=item.get("source"),  # type: ignore[arg-type]
            metadata=item.get("metadata") or {},  # type: ignore[arg-type]
        )

    raise EmbeddingsValidationError(f"Tipo de entrada não suportado: {type(item).__name__}")


def validate_input(item: EmbeddingInput, config: EmbeddingsPipelineConfig) -> None:
    size = len(item.text or "")

    if size < config.min_text_length:
        raise EmbeddingsValidationError("Texto abaixo do tamanho mínimo.")

    if size > config.max_text_length:
        raise EmbeddingsValidationError("Texto acima do tamanho máximo.")


def deduplicate_inputs(items: Sequence[EmbeddingInput]) -> list[EmbeddingInput]:
    seen: set[str] = set()
    result: list[EmbeddingInput] = []

    for item in items:
        text_hash = stable_text_hash(item.text)

        if text_hash in seen:
            continue

        seen.add(text_hash)
        result.append(item)

    return result


def iter_batches(items: Sequence[EmbeddingInput], batch_size: int) -> Iterable[list[EmbeddingInput]]:
    if batch_size <= 0:
        raise EmbeddingsValidationError("batch_size precisa ser maior que zero.")

    for start in range(0, len(items), batch_size):
        yield list(items[start:start + batch_size])


def l2_normalize(vector: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vector)

    if norm == 0:
        return vector

    return vector / norm


def normalize_embeddings_array(vectors: Any) -> np.ndarray:
    array = np.asarray(vectors, dtype=np.float32)

    if array.ndim == 1:
        array = array.reshape(1, -1)

    if array.ndim != 2:
        raise EmbeddingsValidationError(
            f"Embeddings precisam ser matriz 2D. Recebido ndim={array.ndim}."
        )

    return array


def validate_embeddings(
    vectors: np.ndarray,
    *,
    expected_rows: int,
    expected_dimension: int | None,
) -> None:
    if vectors.shape[0] != expected_rows:
        raise EmbeddingsValidationError(
            f"Quantidade de embeddings inválida. Esperado {expected_rows}, recebido {vectors.shape[0]}."
        )

    if expected_dimension is not None and vectors.shape[1] != expected_dimension:
        raise EmbeddingsValidationError(
            f"Dimensão inválida. Esperado {expected_dimension}, recebido {vectors.shape[1]}."
        )

    if not np.isfinite(vectors).all():
        raise EmbeddingsValidationError("Embeddings contêm NaN ou infinito.")


def call_with_retry(fn: Callable[[], Any], policy: RetryPolicy) -> Any:
    delay = policy.initial_delay_seconds
    last_error: Exception | None = None

    for attempt in range(1, policy.attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_error = exc

            if attempt >= policy.attempts:
                break

            logger.warning(
                "embeddings.retry",
                extra={
                    "attempt": attempt,
                    "max_attempts": policy.attempts,
                    "error": str(exc),
                    "delay_seconds": delay,
                },
            )

            time.sleep(delay)
            delay = min(delay * policy.backoff_factor, policy.max_delay_seconds)

    raise EmbeddingsPipelineError(f"Falha ao gerar embeddings após retries: {last_error}") from last_error


def records_to_dataframe(records: Sequence[EmbeddingRecord]) -> pd.DataFrame:
    return pd.DataFrame([record.to_dict() for record in records])


def save_embeddings_dataframe(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    suffix = path.suffix.lower()

    if suffix == ".parquet":
        df.to_parquet(path, index=False)
        return

    if suffix == ".csv":
        output = df.copy()
        output["embedding"] = output["embedding"].apply(json.dumps)
        output.to_csv(path, index=False)
        return

    if suffix == ".json":
        path.write_text(
            json.dumps(df.to_dict(orient="records"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return

    raise EmbeddingsPipelineError(f"Formato de persistência não suportado: {suffix}")


def cosine_similarity_matrix(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    query_norm = query / np.maximum(np.linalg.norm(query, axis=1, keepdims=True), 1e-12)
    matrix_norm = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
    return query_norm @ matrix_norm.T


def similarity_search(
    query_embedding: Sequence[float],
    records: Sequence[EmbeddingRecord],
    *,
    metric: SimilarityMetric = SimilarityMetric.COSINE,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    if top_k <= 0:
        raise EmbeddingsValidationError("top_k precisa ser maior que zero.")

    if not records:
        return []

    query = np.asarray(query_embedding, dtype=np.float32).reshape(1, -1)
    matrix = np.asarray([record.embedding for record in records], dtype=np.float32)

    if query.shape[1] != matrix.shape[1]:
        raise EmbeddingsValidationError("Dimensão da query diferente da base.")

    if metric == SimilarityMetric.COSINE:
        scores = cosine_similarity_matrix(query, matrix)[0]
        order = np.argsort(-scores)
    elif metric == SimilarityMetric.DOT:
        scores = (query @ matrix.T)[0]
        order = np.argsort(-scores)
    elif metric == SimilarityMetric.EUCLIDEAN:
        scores = np.linalg.norm(matrix - query, axis=1)
        order = np.argsort(scores)
    else:
        raise EmbeddingsValidationError(f"Métrica não suportada: {metric}")

    results: list[dict[str, Any]] = []

    for index in order[:top_k]:
        record = records[int(index)]
        score = float(scores[int(index)])

        results.append(
            {
                "id": record.id,
                "score": score,
                "text": record.text,
                "tenant_id": record.tenant_id,
                "source": record.source,
                "metadata": dict(record.metadata),
            }
        )

    return results


class EmbeddingsPipeline:
    def __init__(
        self,
        model: EmbeddingModelProtocol | Callable[[Sequence[str]], Any],
        *,
        config: EmbeddingsPipelineConfig | None = None,
        paths: EmbeddingsPaths | None = None,
        preprocessor: Callable[[str], str] | None = None,
    ) -> None:
        self.model = model
        self.config = config or EmbeddingsPipelineConfig()
        self.paths = paths
        self.preprocessor = preprocessor
        self.registry = ArtifactRegistry()

    def run(
        self,
        inputs: Sequence[EmbeddingInput | Mapping[str, Any] | str],
    ) -> EmbeddingsResult:
        started = time.perf_counter()
        run_id = self.config.run_id or make_run_id()

        report = EmbeddingsReport(
            run_id=run_id,
            pipeline_name=self.config.pipeline_name,
            environment=self.config.environment,
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            status="running",
            started_at=utc_now_iso(),
            input_count=len(inputs),
        )

        records: list[EmbeddingRecord] = []

        try:
            normalized = [normalize_input(item) for item in inputs]

            prepared: list[EmbeddingInput] = []

            for item in normalized:
                text = clean_text(item.text) if self.config.normalize_text else item.text

                if self.preprocessor:
                    text = self.preprocessor(text)

                prepared_item = EmbeddingInput(
                    id=item.id,
                    text=text,
                    tenant_id=item.tenant_id,
                    source=item.source,
                    metadata=item.metadata,
                )

                validate_input(prepared_item, self.config)
                prepared.append(prepared_item)

            if self.config.deduplicate:
                before = len(prepared)
                prepared = deduplicate_inputs(prepared)
                report.deduplicated_count = before - len(prepared)

            report.batches_total = math.ceil(len(prepared) / self.config.batch_size) if prepared else 0

            for batch in iter_batches(prepared, self.config.batch_size):
                try:
                    batch_records = self._embed_batch(batch)
                    records.extend(batch_records)
                    report.batches_success += 1
                except Exception as exc:
                    report.batches_failed += 1
                    report.failed_count += len(batch)
                    report.errors.append(
                        {
                            "type": type(exc).__name__,
                            "message": str(exc),
                            "batch_size": len(batch),
                        }
                    )

                    logger.exception("embeddings.batch_failed")

                    if self.config.fail_fast:
                        raise

            df = records_to_dataframe(records)

            report.output_count = len(records)
            report.dimension = records[0].dimension if records else None

            if self.paths:
                self._persist(df, report)

            report.status = "success"
            report.finished_at = utc_now_iso()
            report.duration_ms = elapsed_ms(started)

            if self.paths:
                self._save_report(report)

            logger.info(
                "embeddings_pipeline.completed",
                extra={
                    "run_id": run_id,
                    "input_count": report.input_count,
                    "output_count": report.output_count,
                    "duration_ms": report.duration_ms,
                },
            )

            return EmbeddingsResult(
                records=records,
                dataframe=df,
                report=report,
            )

        except Exception as exc:
            report.status = "failed"
            report.finished_at = utc_now_iso()
            report.duration_ms = elapsed_ms(started)
            report.errors.append(
                {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            )

            if self.paths:
                self._save_report(report)

            logger.exception(
                "embeddings_pipeline.failed",
                extra={"run_id": run_id},
            )

            if self.config.fail_fast:
                raise

            return EmbeddingsResult(
                records=records,
                dataframe=records_to_dataframe(records),
                report=report,
            )

    def encode_texts(self, texts: Sequence[str]) -> np.ndarray:
        if hasattr(self.model, "encode"):
            raw = self.model.encode(texts, **dict(self.config.encode_kwargs))  # type: ignore[union-attr]
        elif callable(self.model):
            raw = self.model(texts)
        else:
            raise EmbeddingsValidationError("Modelo precisa ter encode() ou ser callable.")

        vectors = normalize_embeddings_array(raw)

        validate_embeddings(
            vectors,
            expected_rows=len(texts),
            expected_dimension=self.config.expected_dimension,
        )

        if self.config.normalize_vectors:
            vectors = np.vstack([l2_normalize(row) for row in vectors])

        return vectors

    def _embed_batch(self, batch: Sequence[EmbeddingInput]) -> list[EmbeddingRecord]:
        texts = [item.text for item in batch]

        vectors = call_with_retry(
            lambda: self.encode_texts(texts),
            self.config.retry_policy,
        )

        records: list[EmbeddingRecord] = []

        for item, vector in zip(batch, vectors, strict=True):
            text_hash = stable_text_hash(item.text)

            records.append(
                EmbeddingRecord(
                    id=item.id or text_hash,
                    text_hash=text_hash,
                    text=item.text,
                    embedding=vector.astype(float).tolist(),
                    dimension=int(vector.shape[0]),
                    tenant_id=item.tenant_id,
                    source=item.source,
                    metadata={
                        **dict(item.metadata),
                        "model_name": self.config.model_name,
                        "model_version": self.config.model_version,
                        "embedded_at": utc_now_iso(),
                    },
                )
            )

        return records

    def _persist(self, df: pd.DataFrame, report: EmbeddingsReport) -> None:
        if not self.paths:
            return

        self.paths.output_dir.mkdir(parents=True, exist_ok=True)

        embeddings_path = self.paths.output_dir / self.paths.embeddings_filename
        manifest_path = self.paths.output_dir / self.paths.manifest_filename

        save_embeddings_dataframe(df, embeddings_path)

        metadata_artifact = save_json(
            {
                "embeddings_path": str(embeddings_path),
                "rows": int(df.shape[0]),
                "columns": list(df.columns),
                "dimension": report.dimension,
            },
            embeddings_path.with_suffix(embeddings_path.suffix + ".metadata.json"),
            options=self.config.serializer_options,
            metadata_extra={
                "artifact_type": "embeddings_metadata",
                "run_id": report.run_id,
            },
        )
        self.registry.register("embeddings_metadata", metadata_artifact)

        manifest_artifact = self.registry.save_manifest(
            manifest_path,
            options=self.config.serializer_options,
        )
        self.registry.register("manifest", manifest_artifact)

        report.artifacts = {
            "embeddings_path": str(embeddings_path),
            "metadata_path": str(embeddings_path.with_suffix(embeddings_path.suffix + ".metadata.json")),
            "manifest_path": str(manifest_path),
        }

    def _save_report(self, report: EmbeddingsReport) -> None:
        if not self.paths:
            return

        report_path = self.paths.output_dir / self.paths.report_filename

        save_json(
            report.to_dict(),
            report_path,
            options=self.config.serializer_options,
            metadata_extra={
                "artifact_type": "embeddings_report",
                "run_id": report.run_id,
            },
        )


def run_embeddings_pipeline(
    model: EmbeddingModelProtocol | Callable[[Sequence[str]], Any],
    inputs: Sequence[EmbeddingInput | Mapping[str, Any] | str],
    *,
    output_dir: str | Path | None = None,
    config: EmbeddingsPipelineConfig | None = None,
    preprocessor: Callable[[str], str] | None = None,
) -> EmbeddingsResult:
    paths = EmbeddingsPaths(output_dir=Path(output_dir)) if output_dir else None

    pipeline = EmbeddingsPipeline(
        model=model,
        config=config,
        paths=paths,
        preprocessor=preprocessor,
    )

    return pipeline.run(inputs)


__all__ = [
    "EmbeddingInput",
    "EmbeddingModelProtocol",
    "EmbeddingRecord",
    "EmbeddingsPaths",
    "EmbeddingsPipeline",
    "EmbeddingsPipelineConfig",
    "EmbeddingsPipelineError",
    "EmbeddingsReport",
    "EmbeddingsResult",
    "EmbeddingsValidationError",
    "RetryPolicy",
    "SimilarityMetric",
    "clean_text",
    "cosine_similarity_matrix",
    "deduplicate_inputs",
    "elapsed_ms",
    "iter_batches",
    "l2_normalize",
    "make_id",
    "make_run_id",
    "normalize_embeddings_array",
    "normalize_input",
    "records_to_dataframe",
    "run_embeddings_pipeline",
    "save_embeddings_dataframe",
    "similarity_search",
    "stable_text_hash",
    "utc_now_iso",
    "validate_embeddings",
    "validate_input",
]
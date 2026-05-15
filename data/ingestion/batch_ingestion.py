# data/ingestion/batch_ingestion.py

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from data.ingestion.file_ingestion import FileIngestionService
from data.ingestion.ingestion_pipeline import (
    IngestionContext,
    IngestionResult,
    IngestionStatus,
    SinkFn,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BatchIngestionConfig:
    patterns: Sequence[str] = (
        "*.csv",
        "*.json",
        "*.jsonl",
        "*.parquet",
        "*.xml",
    )
    recursive: bool = True
    max_files: int = 10_000
    max_concurrency: int = 4
    fail_fast: bool = False
    skip_hidden_files: bool = True
    move_processed_files: bool = False
    processed_dir_name: str = "_processed"
    failed_dir_name: str = "_failed"


@dataclass(slots=True)
class BatchIngestionRequest:
    directory: str | Path
    source: str = "batch"
    tenant_id: str | None = None
    requested_by: str | None = None
    correlation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    patterns: Sequence[str] | None = None
    recursive: bool | None = None


@dataclass(slots=True)
class BatchIngestionSummary:
    status: IngestionStatus
    correlation_id: str
    source: str
    total_files: int
    successful_files: int
    failed_files: int
    skipped_files: int
    total_records: int
    accepted_records: int
    rejected_records: int
    duration_ms: float
    results: list[IngestionResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class BatchIngestionError(Exception):
    pass


class BatchDirectoryNotFoundError(BatchIngestionError):
    pass


class BatchFileLimitExceededError(BatchIngestionError):
    pass


class BatchIngestionService:
    """
    Serviço enterprise para ingestão batch de diretórios.

    Recursos:
    - Processamento concorrente de arquivos
    - Suporte a múltiplos padrões
    - Correlação por lote
    - Fail-fast opcional
    - Resumo consolidado
    - Movimentação opcional para _processed/_failed
    - Logs estruturados
    """

    def __init__(
        self,
        *,
        file_service: FileIngestionService | None = None,
        config: BatchIngestionConfig | None = None,
    ) -> None:
        self.file_service = file_service or FileIngestionService()
        self.config = config or BatchIngestionConfig()

    async def ingest(
        self,
        *,
        request: BatchIngestionRequest,
        sink: SinkFn,
    ) -> BatchIngestionSummary:
        started = time.perf_counter()

        root = Path(request.directory).expanduser().resolve()
        correlation_id = request.correlation_id or str(uuid.uuid4())

        files = self._discover_files(
            root=root,
            patterns=request.patterns or self.config.patterns,
            recursive=(
                request.recursive
                if request.recursive is not None
                else self.config.recursive
            ),
        )

        logger.info(
            "Batch ingestion started.",
            extra={
                "source": request.source,
                "tenant_id": request.tenant_id,
                "requested_by": request.requested_by,
                "correlation_id": correlation_id,
                "directory": str(root),
                "total_files": len(files),
            },
        )

        semaphore = asyncio.Semaphore(self.config.max_concurrency)
        results: list[IngestionResult] = []
        errors: list[str] = []

        try:
            tasks = [
                asyncio.create_task(
                    self._process_file(
                        file_path=file_path,
                        root=root,
                        request=request,
                        sink=sink,
                        correlation_id=correlation_id,
                        semaphore=semaphore,
                    )
                )
                for file_path in files
            ]

            for task in asyncio.as_completed(tasks):
                try:
                    result = await task
                    results.append(result)

                    if result.status == IngestionStatus.FAILED and self.config.fail_fast:
                        for pending in tasks:
                            if not pending.done():
                                pending.cancel()
                        break

                except Exception as exc:
                    error = str(exc)
                    errors.append(error)

                    logger.exception(
                        "Batch file ingestion task failed.",
                        extra={
                            "correlation_id": correlation_id,
                            "error": error,
                        },
                    )

                    if self.config.fail_fast:
                        for pending in tasks:
                            if not pending.done():
                                pending.cancel()
                        break

            await asyncio.gather(*tasks, return_exceptions=True)

            summary = self._build_summary(
                request=request,
                correlation_id=correlation_id,
                started=started,
                total_files=len(files),
                results=results,
                errors=errors,
            )

            logger.info(
                "Batch ingestion finished.",
                extra={
                    "source": summary.source,
                    "status": summary.status,
                    "correlation_id": summary.correlation_id,
                    "total_files": summary.total_files,
                    "successful_files": summary.successful_files,
                    "failed_files": summary.failed_files,
                    "skipped_files": summary.skipped_files,
                    "total_records": summary.total_records,
                    "accepted_records": summary.accepted_records,
                    "rejected_records": summary.rejected_records,
                    "duration_ms": summary.duration_ms,
                },
            )

            return summary

        except Exception:
            logger.exception(
                "Batch ingestion failed.",
                extra={
                    "source": request.source,
                    "tenant_id": request.tenant_id,
                    "correlation_id": correlation_id,
                    "directory": str(root),
                },
            )
            raise

    async def ingest_directory(
        self,
        *,
        directory: str | Path,
        sink: SinkFn,
        patterns: Sequence[str] | None = None,
        recursive: bool | None = None,
        tenant_id: str | None = None,
        requested_by: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BatchIngestionSummary:
        request = BatchIngestionRequest(
            directory=directory,
            tenant_id=tenant_id,
            requested_by=requested_by,
            metadata=metadata or {},
            patterns=patterns,
            recursive=recursive,
        )

        return await self.ingest(request=request, sink=sink)

    def _discover_files(
        self,
        *,
        root: Path,
        patterns: Sequence[str],
        recursive: bool,
    ) -> list[Path]:
        if not root.exists() or not root.is_dir():
            raise BatchDirectoryNotFoundError(f"Directory not found: {root}")

        files: list[Path] = []

        for pattern in patterns:
            found = root.rglob(pattern) if recursive else root.glob(pattern)
            files.extend(found)

        unique_files = sorted(
            {
                file.resolve()
                for file in files
                if file.is_file() and self._is_allowed_file(file)
            }
        )

        if len(unique_files) > self.config.max_files:
            raise BatchFileLimitExceededError(
                f"Batch found {len(unique_files)} files. "
                f"Limit is {self.config.max_files}."
            )

        return unique_files

    def _is_allowed_file(self, file_path: Path) -> bool:
        if self.config.skip_hidden_files and any(
            part.startswith(".") for part in file_path.parts
        ):
            return False

        if self.config.processed_dir_name in file_path.parts:
            return False

        if self.config.failed_dir_name in file_path.parts:
            return False

        return True

    async def _process_file(
        self,
        *,
        file_path: Path,
        root: Path,
        request: BatchIngestionRequest,
        sink: SinkFn,
        correlation_id: str,
        semaphore: asyncio.Semaphore,
    ) -> IngestionResult:
        async with semaphore:
            context = IngestionContext(
                source=f"{request.source}:{file_path.name}",
                tenant_id=request.tenant_id,
                requested_by=request.requested_by,
                correlation_id=correlation_id,
                metadata={
                    **request.metadata,
                    "entrypoint": "batch",
                    "batch_source": request.source,
                    "file_path": str(file_path),
                    "relative_path": str(file_path.relative_to(root)),
                    "file_name": file_path.name,
                    "file_extension": file_path.suffix.lower(),
                    "file_size_bytes": file_path.stat().st_size,
                    "received_at": datetime.now(timezone.utc).isoformat(),
                },
            )

            try:
                result = await self.file_service.ingest_file(
                    file_path=file_path,
                    sink=sink,
                    context=context,
                )

                if self.config.move_processed_files:
                    target_dir = (
                        root / self.config.processed_dir_name
                        if result.status in {
                            IngestionStatus.SUCCESS,
                            IngestionStatus.PARTIAL,
                        }
                        else root / self.config.failed_dir_name
                    )
                    self._move_file(file_path, target_dir)

                return result

            except Exception as exc:
                logger.exception(
                    "Failed to ingest batch file.",
                    extra={
                        "file_path": str(file_path),
                        "correlation_id": correlation_id,
                    },
                )

                if self.config.move_processed_files:
                    self._move_file(file_path, root / self.config.failed_dir_name)

                return IngestionResult(
                    status=IngestionStatus.FAILED,
                    source=context.source,
                    correlation_id=correlation_id,
                    errors=[str(exc)],
                    metadata=context.metadata,
                )

    def _move_file(self, file_path: Path, target_dir: Path) -> Path:
        target_dir.mkdir(parents=True, exist_ok=True)

        target = target_dir / file_path.name

        if target.exists():
            stem = file_path.stem
            suffix = file_path.suffix
            target = target_dir / f"{stem}_{int(time.time())}{suffix}"

        file_path.rename(target)
        return target

    def _build_summary(
        self,
        *,
        request: BatchIngestionRequest,
        correlation_id: str,
        started: float,
        total_files: int,
        results: list[IngestionResult],
        errors: list[str],
    ) -> BatchIngestionSummary:
        successful = sum(
            1
            for result in results
            if result.status in {IngestionStatus.SUCCESS, IngestionStatus.PARTIAL}
        )
        failed = sum(1 for result in results if result.status == IngestionStatus.FAILED)
        skipped = sum(1 for result in results if result.status == IngestionStatus.SKIPPED)

        total_records = sum(result.total_records for result in results)
        accepted_records = sum(result.accepted_records for result in results)
        rejected_records = sum(result.rejected_records for result in results)

        if total_files == 0:
            status = IngestionStatus.SKIPPED
        elif failed == 0 and successful == total_files:
            status = IngestionStatus.SUCCESS
        elif successful > 0:
            status = IngestionStatus.PARTIAL
        else:
            status = IngestionStatus.FAILED

        return BatchIngestionSummary(
            status=status,
            correlation_id=correlation_id,
            source=request.source,
            total_files=total_files,
            successful_files=successful,
            failed_files=failed,
            skipped_files=skipped,
            total_records=total_records,
            accepted_records=accepted_records,
            rejected_records=rejected_records,
            duration_ms=(time.perf_counter() - started) * 1000,
            results=results,
            errors=errors,
        )
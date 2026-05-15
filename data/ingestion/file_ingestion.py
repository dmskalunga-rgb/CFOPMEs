#!/usr/bin/env python3
"""
data/ingestion/file_ingestion.py

Enterprise-grade file ingestion module.

Objetivo:
- Descobrir, validar e ingerir arquivos locais de forma robusta.
- Suportar CSV, JSON, JSONL, XML, Parquet e texto genérico.
- Gerar IngestionRecord padronizado com hash, manifest, metadados, tenant e idempotência.
- Aplicar quarantine/archive opcionais, limite de tamanho, deduplicação, batch sink e auditoria leve.
- Funcionar com dependências mínimas; Parquet usa pandas/pyarrow apenas quando necessário.

Uso:
    from data.ingestion.file_ingestion import FileIngestionConfig, FileIngestionHandler

    handler = FileIngestionHandler(FileIngestionConfig(path="/data/inbox", recursive=True))
    result = handler.ingest()

Integração com sink:
    def sink(records):
        ... salvar no Supabase, Kafka, warehouse, etc.

    handler = FileIngestionHandler(config, sink=sink)
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import mimetypes
import os
import shutil
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

try:
    from data.ingestion import BaseIngestionHandler, IngestionMode, IngestionRecord, IngestionResult, IngestionStatus, PayloadFormat
except Exception:  # pragma: no cover
    class IngestionMode(str, Enum):
        FILE = "file"

    class IngestionStatus(str, Enum):
        PROCESSED = "processed"
        PARTIAL = "partial"
        FAILED = "failed"
        SKIPPED = "skipped"

    class PayloadFormat(str, Enum):
        JSON = "json"
        CSV = "csv"
        PARQUET = "parquet"
        XML = "xml"
        TEXT = "text"
        BINARY = "binary"
        UNKNOWN = "unknown"

    @dataclass(frozen=True)
    class IngestionRecord:  # type: ignore
        payload: Any
        source: str
        record_id: str = field(default_factory=lambda: f"ing_{uuid.uuid4().hex[:20]}")
        tenant_id: Optional[str] = None
        correlation_id: str = field(default_factory=lambda: f"corr_{uuid.uuid4().hex[:16]}")
        format: PayloadFormat = PayloadFormat.JSON
        occurred_at: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())
        metadata: Dict[str, Any] = field(default_factory=dict)

        @property
        def idempotency_key(self) -> str:
            raw = f"{self.tenant_id}|{self.source}|{self.record_id}|{hash_payload(self.payload)}"
            return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @dataclass(frozen=True)
    class IngestionResult:  # type: ignore
        status: IngestionStatus
        accepted: int = 0
        processed: int = 0
        skipped: int = 0
        failed: int = 0
        errors: List[str] = field(default_factory=list)
        warnings: List[str] = field(default_factory=list)
        metadata: Dict[str, Any] = field(default_factory=dict)
        started_at: Optional[str] = None
        finished_at: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())

    class BaseIngestionHandler:  # type: ignore
        mode = IngestionMode.FILE


LOGGER = logging.getLogger(__name__)
FILE_INGESTION_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc


class FileStatus(str, Enum):
    DISCOVERED = "discovered"
    VALIDATED = "validated"
    LOADED = "loaded"
    INGESTED = "ingested"
    ARCHIVED = "archived"
    QUARANTINED = "quarantined"
    SKIPPED = "skipped"
    FAILED = "failed"


class FileAction(str, Enum):
    NONE = "none"
    ARCHIVE = "archive"
    DELETE = "delete"
    QUARANTINE = "quarantine"


@dataclass(frozen=True)
class FileIngestionConfig:
    path: str
    source: str = "file"
    tenant_id: Optional[str] = None
    recursive: bool = False
    pattern: str = "*"
    encoding: str = "utf-8"
    max_file_size_bytes: int = 250_000_000
    max_records_per_file: Optional[int] = None
    batch_size: int = 5000
    deduplicate_files: bool = True
    deduplicate_records: bool = True
    include_file_content_for_text: bool = True
    archive_dir: Optional[str] = None
    quarantine_dir: Optional[str] = None
    on_success: FileAction = FileAction.NONE
    on_failure: FileAction = FileAction.QUARANTINE
    allowed_extensions: Set[str] = field(default_factory=lambda: {".csv", ".json", ".jsonl", ".xml", ".parquet", ".pq", ".txt", ".log"})
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FileManifestEntry:
    file_id: str
    path: str
    name: str
    extension: str
    size_bytes: int
    modified_at: str
    content_type: Optional[str]
    sha256: str
    status: FileStatus
    record_count: int = 0
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_id": self.file_id,
            "path": self.path,
            "name": self.name,
            "extension": self.extension,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "content_type": self.content_type,
            "sha256": self.sha256,
            "status": self.status.value,
            "record_count": self.record_count,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metadata": sanitize_metadata(self.metadata),
        }


@dataclass(frozen=True)
class FileLoadResult:
    manifest: FileManifestEntry
    records: List[IngestionRecord]
    warnings: List[str] = field(default_factory=list)


class FileIngestionError(Exception):
    """Base file ingestion error."""


class FileValidationError(FileIngestionError):
    """File validation failed."""


class UnsupportedFileTypeError(FileIngestionError):
    """Unsupported file type."""


Sink = Callable[[Sequence[IngestionRecord]], IngestionResult]
ManifestSink = Callable[[Sequence[FileManifestEntry]], None]
RecordValidator = Callable[[IngestionRecord], None]
PayloadTransformer = Callable[[Any], Any]


class FileIngestionHandler(BaseIngestionHandler):
    mode = IngestionMode.FILE

    def __init__(
        self,
        config: FileIngestionConfig,
        sink: Optional[Sink] = None,
        manifest_sink: Optional[ManifestSink] = None,
        record_validator: Optional[RecordValidator] = None,
        payload_transformer: Optional[PayloadTransformer] = None,
    ) -> None:
        self.config = config
        self.sink = sink
        self.manifest_sink = manifest_sink
        self.record_validator = record_validator
        self.payload_transformer = payload_transformer
        self._seen_files: Set[str] = set()
        self._seen_records: Set[str] = set()

    def discover_files(self) -> List[Path]:
        root = Path(self.config.path)
        if not root.exists():
            raise FileValidationError(f"Path não encontrado: {root}")
        if root.is_file():
            files = [root]
        else:
            iterator = root.rglob(self.config.pattern) if self.config.recursive else root.glob(self.config.pattern)
            files = [item for item in iterator if item.is_file()]
        return sorted(files, key=lambda item: str(item))

    def ingest(self, records: Optional[Sequence[IngestionRecord]] = None) -> IngestionResult:  # type: ignore[override]
        started = utc_now_iso()
        if records is not None:
            return self._send_records(records, started, metadata={"direct_records": True})

        accepted = processed = skipped = failed = 0
        errors: List[str] = []
        warnings: List[str] = []
        manifests: List[FileManifestEntry] = []
        pending_batch: List[IngestionRecord] = []

        try:
            files = self.discover_files()
        except Exception as exc:  # noqa: BLE001
            return IngestionResult(status=IngestionStatus.FAILED, failed=1, errors=[str(exc)], started_at=started)

        for file_path in files:
            try:
                load_result = self.load_file(file_path)
                manifests.append(load_result.manifest)
                warnings.extend(load_result.warnings)
                file_records = self._prepare_records(load_result.records)
                if not file_records:
                    skipped += 1
                    continue
                accepted += len(file_records)
                pending_batch.extend(file_records)
                if len(pending_batch) >= self.config.batch_size:
                    result = self._send_records(pending_batch, started, metadata={"batch_flush": True})
                    processed += result.processed
                    failed += result.failed
                    skipped += result.skipped
                    errors.extend(result.errors)
                    pending_batch = []
                self._handle_success(file_path)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("file_ingestion_failed", extra={"file": str(file_path)})
                failed += 1
                errors.append(f"{file_path}: {exc}")
                manifests.append(self._manifest_for_failed_file(file_path, exc))
                self._handle_failure(file_path)

        if pending_batch:
            result = self._send_records(pending_batch, started, metadata={"final_flush": True})
            processed += result.processed
            failed += result.failed
            skipped += result.skipped
            errors.extend(result.errors)

        if self.manifest_sink and manifests:
            try:
                self.manifest_sink(manifests)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"manifest_sink_failed: {exc}")

        status = IngestionStatus.PROCESSED if failed == 0 else IngestionStatus.PARTIAL if processed > 0 or accepted > 0 else IngestionStatus.FAILED
        if not files:
            status = IngestionStatus.SKIPPED
            warnings.append("no_files_discovered")

        return IngestionResult(
            status=status,
            accepted=accepted,
            processed=processed if self.sink else accepted,
            skipped=skipped,
            failed=failed,
            errors=errors,
            warnings=warnings,
            started_at=started,
            metadata={
                "version": FILE_INGESTION_VERSION,
                "file_count": len(files),
                "manifest_count": len(manifests),
                "source": self.config.source,
                "tenant_id": self.config.tenant_id,
            },
        )

    def load_file(self, file_path: Path) -> FileLoadResult:
        started = utc_now_iso()
        manifest = self._build_manifest(file_path, FileStatus.DISCOVERED, started_at=started)
        self._validate_file(file_path, manifest)

        if self.config.deduplicate_files and manifest.sha256 in self._seen_files:
            skipped_manifest = self._replace_manifest(manifest, status=FileStatus.SKIPPED, record_count=0, finished_at=utc_now_iso(), metadata={"reason": "duplicate_file"})
            return FileLoadResult(skipped_manifest, [], warnings=[f"duplicate_file_skipped: {file_path}"])
        self._seen_files.add(manifest.sha256)

        records = list(self._load_records_by_type(file_path, manifest))
        loaded_manifest = self._replace_manifest(manifest, status=FileStatus.LOADED, record_count=len(records), finished_at=utc_now_iso())
        return FileLoadResult(loaded_manifest, records)

    def _prepare_records(self, records: Sequence[IngestionRecord]) -> List[IngestionRecord]:
        prepared: List[IngestionRecord] = []
        for record in records:
            current = record
            if self.payload_transformer:
                current = self._clone_record(record, payload=self.payload_transformer(record.payload))
            if self.record_validator:
                self.record_validator(current)
            if self.config.deduplicate_records and current.idempotency_key in self._seen_records:
                continue
            self._seen_records.add(current.idempotency_key)
            prepared.append(current)
        return prepared

    def _send_records(self, records: Sequence[IngestionRecord], started: str, metadata: Optional[Mapping[str, Any]] = None) -> IngestionResult:
        if not records:
            return IngestionResult(status=IngestionStatus.SKIPPED, skipped=0, started_at=started)
        if self.sink:
            return self.sink(records)
        return IngestionResult(status=IngestionStatus.PROCESSED, accepted=len(records), processed=len(records), started_at=started, metadata=dict(metadata or {}))

    def _load_records_by_type(self, file_path: Path, manifest: FileManifestEntry) -> Iterator[IngestionRecord]:
        suffix = file_path.suffix.lower()
        if suffix == ".csv":
            yield from self._load_csv(file_path, manifest)
        elif suffix in {".json", ".jsonl"}:
            yield from self._load_json(file_path, manifest, json_lines=suffix == ".jsonl")
        elif suffix == ".xml":
            yield from self._load_xml(file_path, manifest)
        elif suffix in {".parquet", ".pq"}:
            yield from self._load_parquet(file_path, manifest)
        elif suffix in {".txt", ".log"}:
            yield from self._load_text(file_path, manifest)
        else:
            raise UnsupportedFileTypeError(f"Extensão não suportada: {suffix}")

    def _load_csv(self, file_path: Path, manifest: FileManifestEntry) -> Iterator[IngestionRecord]:
        with file_path.open("r", encoding=self.config.encoding, newline="") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
            reader = csv.DictReader(fh, dialect=dialect)
            for row_number, row in enumerate(reader, start=1):
                if self.config.max_records_per_file and row_number > self.config.max_records_per_file:
                    break
                yield self._record(dict(row), manifest, PayloadFormat.CSV, row_number=row_number)

    def _load_json(self, file_path: Path, manifest: FileManifestEntry, json_lines: bool) -> Iterator[IngestionRecord]:
        if json_lines:
            with file_path.open("r", encoding=self.config.encoding) as fh:
                for row_number, line in enumerate(fh, start=1):
                    if self.config.max_records_per_file and row_number > self.config.max_records_per_file:
                        break
                    if not line.strip():
                        continue
                    yield self._record(json.loads(line), manifest, PayloadFormat.JSON, row_number=row_number)
            return
        data = json.loads(file_path.read_text(encoding=self.config.encoding))
        items = data if isinstance(data, list) else [data]
        for row_number, item in enumerate(items, start=1):
            if self.config.max_records_per_file and row_number > self.config.max_records_per_file:
                break
            yield self._record(item, manifest, PayloadFormat.JSON, row_number=row_number)

    def _load_xml(self, file_path: Path, manifest: FileManifestEntry) -> Iterator[IngestionRecord]:
        tree = ET.parse(file_path)
        root = tree.getroot()
        children = list(root)
        items = children if children else [root]
        for row_number, element in enumerate(items, start=1):
            if self.config.max_records_per_file and row_number > self.config.max_records_per_file:
                break
            yield self._record(xml_element_to_dict(element), manifest, PayloadFormat.XML, row_number=row_number)

    def _load_parquet(self, file_path: Path, manifest: FileManifestEntry) -> Iterator[IngestionRecord]:
        try:
            import pandas as pd  # type: ignore
        except ImportError as exc:
            raise FileIngestionError("Parquet requer dependências: pip install pandas pyarrow") from exc
        df = pd.read_parquet(file_path)
        if self.config.max_records_per_file:
            df = df.head(self.config.max_records_per_file)
        for row_number, item in enumerate(df.to_dict(orient="records"), start=1):
            yield self._record(item, manifest, PayloadFormat.PARQUET, row_number=row_number)

    def _load_text(self, file_path: Path, manifest: FileManifestEntry) -> Iterator[IngestionRecord]:
        if self.config.include_file_content_for_text:
            payload = {"content": file_path.read_text(encoding=self.config.encoding), "filename": file_path.name}
        else:
            payload = {"filename": file_path.name, "path": str(file_path)}
        yield self._record(payload, manifest, PayloadFormat.TEXT, row_number=1)

    def _record(self, payload: Any, manifest: FileManifestEntry, payload_format: PayloadFormat, row_number: int) -> IngestionRecord:
        record_id = f"file_{manifest.file_id}_{row_number}"
        return IngestionRecord(
            payload=payload,
            source=self.config.source,
            record_id=record_id,
            tenant_id=self.config.tenant_id,
            correlation_id=f"corr_{manifest.file_id}",
            format=payload_format,
            occurred_at=utc_now_iso(),
            metadata={
                **self.config.metadata,
                "file_id": manifest.file_id,
                "file_path": manifest.path,
                "file_name": manifest.name,
                "file_sha256": manifest.sha256,
                "row_number": row_number,
                "content_type": manifest.content_type,
            },
        )

    def _validate_file(self, file_path: Path, manifest: FileManifestEntry) -> None:
        if not file_path.exists() or not file_path.is_file():
            raise FileValidationError(f"Arquivo inválido: {file_path}")
        if manifest.size_bytes > self.config.max_file_size_bytes:
            raise FileValidationError(f"Arquivo excede limite: {manifest.size_bytes} > {self.config.max_file_size_bytes}")
        if self.config.allowed_extensions and manifest.extension not in self.config.allowed_extensions:
            raise UnsupportedFileTypeError(f"Extensão bloqueada: {manifest.extension}")

    def _build_manifest(self, file_path: Path, status: FileStatus, started_at: Optional[str] = None, error: Optional[Exception] = None) -> FileManifestEntry:
        stat = file_path.stat() if file_path.exists() else None
        sha = file_sha256(file_path) if file_path.exists() and file_path.is_file() else ""
        file_id = hashlib.sha256(f"{file_path}|{sha}".encode("utf-8")).hexdigest()[:20]
        content_type, _ = mimetypes.guess_type(str(file_path))
        return FileManifestEntry(
            file_id=file_id,
            path=str(file_path),
            name=file_path.name,
            extension=file_path.suffix.lower(),
            size_bytes=stat.st_size if stat else 0,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=DEFAULT_TIMEZONE).isoformat() if stat else utc_now_iso(),
            content_type=content_type,
            sha256=sha,
            status=status,
            error=str(error) if error else None,
            started_at=started_at,
            finished_at=utc_now_iso() if error else None,
            metadata=sanitize_metadata(self.config.metadata),
        )

    def _manifest_for_failed_file(self, file_path: Path, exc: Exception) -> FileManifestEntry:
        try:
            return self._build_manifest(file_path, FileStatus.FAILED, started_at=utc_now_iso(), error=exc)
        except Exception:
            return FileManifestEntry(
                file_id=f"file_{uuid.uuid4().hex[:16]}",
                path=str(file_path),
                name=file_path.name,
                extension=file_path.suffix.lower(),
                size_bytes=0,
                modified_at=utc_now_iso(),
                content_type=None,
                sha256="",
                status=FileStatus.FAILED,
                error=str(exc),
                started_at=utc_now_iso(),
                finished_at=utc_now_iso(),
            )

    def _replace_manifest(self, manifest: FileManifestEntry, **changes: Any) -> FileManifestEntry:
        data = manifest.__dict__.copy()
        data.update(changes)
        return FileManifestEntry(**data)

    def _clone_record(self, record: IngestionRecord, payload: Any) -> IngestionRecord:
        return IngestionRecord(
            payload=payload,
            source=record.source,
            record_id=record.record_id,
            tenant_id=record.tenant_id,
            correlation_id=record.correlation_id,
            format=record.format,
            occurred_at=record.occurred_at,
            metadata=record.metadata,
        )

    def _handle_success(self, file_path: Path) -> None:
        if self.config.on_success == FileAction.ARCHIVE and self.config.archive_dir:
            move_file(file_path, Path(self.config.archive_dir))
        elif self.config.on_success == FileAction.DELETE:
            file_path.unlink(missing_ok=True)

    def _handle_failure(self, file_path: Path) -> None:
        if self.config.on_failure == FileAction.QUARANTINE and self.config.quarantine_dir and file_path.exists():
            move_file(file_path, Path(self.config.quarantine_dir))
        elif self.config.on_failure == FileAction.DELETE and file_path.exists():
            file_path.unlink(missing_ok=True)


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_payload(payload: Any) -> str:
    try:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    except TypeError:
        raw = repr(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def xml_element_to_dict(element: ET.Element) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"tag": strip_namespace(element.tag)}
    if element.attrib:
        payload["attributes"] = {strip_namespace(key): value for key, value in element.attrib.items()}
    text = (element.text or "").strip()
    if text:
        payload["text"] = text
    children = [xml_element_to_dict(child) for child in list(element)]
    if children:
        payload["children"] = children
    return payload


def strip_namespace(value: str) -> str:
    return value.split("}", 1)[-1] if "}" in value else value


def move_file(file_path: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / file_path.name
    if target.exists():
        target = target_dir / f"{file_path.stem}_{int(time.time())}_{uuid.uuid4().hex[:8]}{file_path.suffix}"
    shutil.move(str(file_path), str(target))
    return target


def sanitize_metadata(metadata: Mapping[str, Any]) -> Dict[str, Any]:
    sensitive = {"password", "secret", "token", "api_key", "apikey", "authorization", "cookie"}
    result: Dict[str, Any] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if any(item in key_text.lower() for item in sensitive):
            result[key_text] = "[REDACTED]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key_text] = value
        else:
            result[key_text] = str(value)[:500]
    return result


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()


__all__ = [
    "FILE_INGESTION_VERSION",
    "FileStatus",
    "FileAction",
    "FileIngestionConfig",
    "FileManifestEntry",
    "FileLoadResult",
    "FileIngestionError",
    "FileValidationError",
    "UnsupportedFileTypeError",
    "FileIngestionHandler",
    "file_sha256",
    "hash_payload",
    "xml_element_to_dict",
    "move_file",
]

"""
data/utils/compression.py

Enterprise-grade compression utilities.

Este módulo centraliza operações seguras e auditáveis de compressão e
 descompressão para pipelines de dados, arquivos batch, artefatos, exports,
 backups, ingestão e processamento lakehouse.

Capacidades principais:
- Compressão/descompressão com gzip, bz2, xz/lzma, zip e tar.
- Operações streaming para arquivos grandes.
- Checksums SHA-256 para integridade.
- Proteção contra path traversal em extração de arquivos.
- Validação de tamanho máximo, número máximo de arquivos e extensões permitidas.
- API orientada a configuração, com resultado estruturado.
- Sem dependências externas obrigatórias.
"""

from __future__ import annotations

import bz2
import gzip
import hashlib
import io
import json
import logging
import lzma
import os
import shutil
import tarfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union


logger = logging.getLogger(__name__)

PathLike = Union[str, os.PathLike[str]]
JsonDict = Dict[str, Any]


class CompressionFormat(str, Enum):
    """Formatos de compressão suportados."""

    GZIP = "gzip"
    BZ2 = "bz2"
    XZ = "xz"
    ZIP = "zip"
    TAR = "tar"
    TAR_GZ = "tar.gz"
    TAR_BZ2 = "tar.bz2"
    TAR_XZ = "tar.xz"


class CompressionLevel(int, Enum):
    """Níveis semânticos de compressão."""

    FAST = 1
    BALANCED = 6
    MAX = 9


class CompressionError(Exception):
    """Erro base para operações de compressão."""


class CompressionSecurityError(CompressionError):
    """Erro de segurança durante extração/descompressão."""


class CompressionConfigurationError(CompressionError):
    """Erro de configuração inválida."""


@dataclass(frozen=True)
class CompressionPolicy:
    """Política de segurança e limites para compressão/descompressão."""

    max_file_size_bytes: Optional[int] = 5 * 1024 * 1024 * 1024
    max_total_uncompressed_bytes: Optional[int] = 20 * 1024 * 1024 * 1024
    max_members: int = 100_000
    allow_overwrite: bool = False
    preserve_permissions: bool = False
    allowed_extensions: Optional[Tuple[str, ...]] = None
    block_absolute_paths: bool = True
    block_path_traversal: bool = True

    def validate_member(self, member_name: str, destination_dir: Path) -> Path:
        """Valida e resolve caminho seguro de extração."""
        if not member_name or member_name.strip() == "":
            raise CompressionSecurityError("Archive member has empty name")

        member_path = Path(member_name)
        if self.block_absolute_paths and member_path.is_absolute():
            raise CompressionSecurityError(f"Absolute path is not allowed in archive member: {member_name}")

        resolved_destination = destination_dir.resolve()
        resolved_target = (resolved_destination / member_path).resolve()
        if self.block_path_traversal and not str(resolved_target).startswith(str(resolved_destination)):
            raise CompressionSecurityError(f"Path traversal attempt detected: {member_name}")

        if self.allowed_extensions is not None and resolved_target.is_file():
            suffix = resolved_target.suffix.lower()
            if suffix not in {ext.lower() for ext in self.allowed_extensions}:
                raise CompressionSecurityError(f"File extension is not allowed: {suffix}")

        if resolved_target.exists() and not self.allow_overwrite:
            raise CompressionSecurityError(f"Destination already exists and overwrite is disabled: {resolved_target}")

        return resolved_target


@dataclass(frozen=True)
class CompressionResult:
    """Resultado estruturado de uma operação de compressão."""

    source: Optional[str]
    destination: str
    format: CompressionFormat
    original_size_bytes: int
    compressed_size_bytes: int
    checksum_sha256: str
    duration_ms: float
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def compression_ratio(self) -> float:
        if self.original_size_bytes <= 0:
            return 0.0
        return round(self.compressed_size_bytes / self.original_size_bytes, 6)

    @property
    def saved_bytes(self) -> int:
        return max(0, self.original_size_bytes - self.compressed_size_bytes)

    def to_dict(self) -> JsonDict:
        return {
            "source": self.source,
            "destination": self.destination,
            "format": self.format.value,
            "original_size_bytes": self.original_size_bytes,
            "compressed_size_bytes": self.compressed_size_bytes,
            "compression_ratio": self.compression_ratio,
            "saved_bytes": self.saved_bytes,
            "checksum_sha256": self.checksum_sha256,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at.isoformat(),
            "metadata": dict(self.metadata),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class ExtractionResult:
    """Resultado estruturado de uma extração/descompressão."""

    source: str
    destination_dir: str
    format: CompressionFormat
    extracted_files: Tuple[str, ...]
    total_bytes: int
    checksum_sha256: str
    duration_ms: float
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def file_count(self) -> int:
        return len(self.extracted_files)

    def to_dict(self) -> JsonDict:
        return {
            "source": self.source,
            "destination_dir": self.destination_dir,
            "format": self.format.value,
            "extracted_files": list(self.extracted_files),
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "checksum_sha256": self.checksum_sha256,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at.isoformat(),
            "metadata": dict(self.metadata),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


class CompressionManager:
    """Gerenciador enterprise para compressão e extração segura."""

    def __init__(
        self,
        *,
        policy: Optional[CompressionPolicy] = None,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        if chunk_size <= 0:
            raise CompressionConfigurationError("chunk_size must be greater than zero")
        self.policy = policy or CompressionPolicy()
        self.chunk_size = chunk_size

    def compress_file(
        self,
        source: PathLike,
        destination: Optional[PathLike] = None,
        *,
        fmt: Optional[CompressionFormat] = None,
        level: Union[int, CompressionLevel] = CompressionLevel.BALANCED,
    ) -> CompressionResult:
        """Comprime um único arquivo."""
        start = time.perf_counter()
        source_path = Path(source)
        if not source_path.is_file():
            raise CompressionError(f"Source file does not exist: {source_path}")
        self._validate_source_size(source_path)

        fmt = fmt or infer_format(destination or source_path.name + ".gz")
        destination_path = Path(destination) if destination else default_compressed_path(source_path, fmt)
        self._ensure_can_write(destination_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        original_size = source_path.stat().st_size
        if fmt == CompressionFormat.GZIP:
            self._compress_stream_gzip(source_path, destination_path, int(level))
        elif fmt == CompressionFormat.BZ2:
            self._compress_stream_bz2(source_path, destination_path, int(level))
        elif fmt == CompressionFormat.XZ:
            self._compress_stream_xz(source_path, destination_path, int(level))
        elif fmt in {CompressionFormat.ZIP, CompressionFormat.TAR, CompressionFormat.TAR_GZ, CompressionFormat.TAR_BZ2, CompressionFormat.TAR_XZ}:
            return self.compress_paths([source_path], destination_path, fmt=fmt, level=level)
        else:
            raise CompressionConfigurationError(f"Unsupported single-file compression format: {fmt}")

        compressed_size = destination_path.stat().st_size
        checksum = sha256_file(destination_path, chunk_size=self.chunk_size)
        return CompressionResult(
            source=str(source_path),
            destination=str(destination_path),
            format=fmt,
            original_size_bytes=original_size,
            compressed_size_bytes=compressed_size,
            checksum_sha256=checksum,
            duration_ms=(time.perf_counter() - start) * 1000.0,
        )

    def decompress_file(
        self,
        source: PathLike,
        destination: Optional[PathLike] = None,
        *,
        fmt: Optional[CompressionFormat] = None,
    ) -> ExtractionResult:
        """Descomprime arquivo gzip/bz2/xz simples para arquivo de destino."""
        start = time.perf_counter()
        source_path = Path(source)
        if not source_path.is_file():
            raise CompressionError(f"Source file does not exist: {source_path}")
        self._validate_source_size(source_path)
        fmt = fmt or infer_format(source_path)
        destination_path = Path(destination) if destination else default_decompressed_path(source_path, fmt)
        self._ensure_can_write(destination_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        if fmt == CompressionFormat.GZIP:
            self._decompress_stream_gzip(source_path, destination_path)
        elif fmt == CompressionFormat.BZ2:
            self._decompress_stream_bz2(source_path, destination_path)
        elif fmt == CompressionFormat.XZ:
            self._decompress_stream_xz(source_path, destination_path)
        elif fmt in {CompressionFormat.ZIP, CompressionFormat.TAR, CompressionFormat.TAR_GZ, CompressionFormat.TAR_BZ2, CompressionFormat.TAR_XZ}:
            return self.extract_archive(source_path, destination_path if destination else source_path.parent, fmt=fmt)
        else:
            raise CompressionConfigurationError(f"Unsupported decompression format: {fmt}")

        total_bytes = destination_path.stat().st_size
        self._validate_total_uncompressed_size(total_bytes)
        checksum = sha256_file(destination_path, chunk_size=self.chunk_size)
        return ExtractionResult(
            source=str(source_path),
            destination_dir=str(destination_path.parent),
            format=fmt,
            extracted_files=(str(destination_path),),
            total_bytes=total_bytes,
            checksum_sha256=checksum,
            duration_ms=(time.perf_counter() - start) * 1000.0,
        )

    def compress_paths(
        self,
        sources: Sequence[PathLike],
        destination: PathLike,
        *,
        fmt: CompressionFormat = CompressionFormat.ZIP,
        level: Union[int, CompressionLevel] = CompressionLevel.BALANCED,
        root_dir: Optional[PathLike] = None,
    ) -> CompressionResult:
        """Comprime múltiplos arquivos/diretórios em ZIP ou TAR."""
        start = time.perf_counter()
        source_paths = [Path(source) for source in sources]
        if not source_paths:
            raise CompressionConfigurationError("At least one source path is required")
        for path in source_paths:
            if not path.exists():
                raise CompressionError(f"Source path does not exist: {path}")
        destination_path = Path(destination)
        self._ensure_can_write(destination_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        files = collect_files(source_paths)
        if len(files) > self.policy.max_members:
            raise CompressionSecurityError(f"Too many files to compress: {len(files)} > {self.policy.max_members}")
        original_size = sum(path.stat().st_size for path in files)
        self._validate_total_uncompressed_size(original_size)

        base = Path(root_dir).resolve() if root_dir else common_parent(source_paths)
        if fmt == CompressionFormat.ZIP:
            self._compress_zip(files, destination_path, base, int(level))
        elif fmt in {CompressionFormat.TAR, CompressionFormat.TAR_GZ, CompressionFormat.TAR_BZ2, CompressionFormat.TAR_XZ}:
            self._compress_tar(files, destination_path, base, fmt)
        else:
            raise CompressionConfigurationError(f"Archive format required for multiple paths, got: {fmt}")

        compressed_size = destination_path.stat().st_size
        checksum = sha256_file(destination_path, chunk_size=self.chunk_size)
        return CompressionResult(
            source=",".join(str(path) for path in source_paths),
            destination=str(destination_path),
            format=fmt,
            original_size_bytes=original_size,
            compressed_size_bytes=compressed_size,
            checksum_sha256=checksum,
            duration_ms=(time.perf_counter() - start) * 1000.0,
            metadata={"file_count": len(files)},
        )

    def extract_archive(
        self,
        source: PathLike,
        destination_dir: PathLike,
        *,
        fmt: Optional[CompressionFormat] = None,
    ) -> ExtractionResult:
        """Extrai ZIP/TAR de forma segura."""
        start = time.perf_counter()
        source_path = Path(source)
        destination_path = Path(destination_dir)
        if not source_path.is_file():
            raise CompressionError(f"Archive file does not exist: {source_path}")
        self._validate_source_size(source_path)
        destination_path.mkdir(parents=True, exist_ok=True)
        fmt = fmt or infer_format(source_path)
        checksum = sha256_file(source_path, chunk_size=self.chunk_size)

        if fmt == CompressionFormat.ZIP:
            extracted = self._extract_zip(source_path, destination_path)
        elif fmt in {CompressionFormat.TAR, CompressionFormat.TAR_GZ, CompressionFormat.TAR_BZ2, CompressionFormat.TAR_XZ}:
            extracted = self._extract_tar(source_path, destination_path, fmt)
        else:
            raise CompressionConfigurationError(f"Archive extraction requires ZIP/TAR format, got: {fmt}")

        total_bytes = sum(Path(path).stat().st_size for path in extracted if Path(path).is_file())
        self._validate_total_uncompressed_size(total_bytes)
        return ExtractionResult(
            source=str(source_path),
            destination_dir=str(destination_path),
            format=fmt,
            extracted_files=tuple(str(path) for path in extracted),
            total_bytes=total_bytes,
            checksum_sha256=checksum,
            duration_ms=(time.perf_counter() - start) * 1000.0,
        )

    def _compress_stream_gzip(self, source: Path, destination: Path, level: int) -> None:
        with source.open("rb") as src, gzip.open(destination, "wb", compresslevel=level) as dst:
            shutil.copyfileobj(src, dst, length=self.chunk_size)

    def _compress_stream_bz2(self, source: Path, destination: Path, level: int) -> None:
        with source.open("rb") as src, bz2.open(destination, "wb", compresslevel=level) as dst:
            shutil.copyfileobj(src, dst, length=self.chunk_size)

    def _compress_stream_xz(self, source: Path, destination: Path, level: int) -> None:
        preset = max(0, min(9, int(level)))
        with source.open("rb") as src, lzma.open(destination, "wb", preset=preset) as dst:
            shutil.copyfileobj(src, dst, length=self.chunk_size)

    def _decompress_stream_gzip(self, source: Path, destination: Path) -> None:
        with gzip.open(source, "rb") as src, destination.open("wb") as dst:
            self._copy_with_limit(src, dst)

    def _decompress_stream_bz2(self, source: Path, destination: Path) -> None:
        with bz2.open(source, "rb") as src, destination.open("wb") as dst:
            self._copy_with_limit(src, dst)

    def _decompress_stream_xz(self, source: Path, destination: Path) -> None:
        with lzma.open(source, "rb") as src, destination.open("wb") as dst:
            self._copy_with_limit(src, dst)

    def _copy_with_limit(self, src: BinaryIO, dst: BinaryIO) -> int:
        total = 0
        while True:
            chunk = src.read(self.chunk_size)
            if not chunk:
                break
            total += len(chunk)
            self._validate_total_uncompressed_size(total)
            dst.write(chunk)
        return total

    def _compress_zip(self, files: Sequence[Path], destination: Path, base: Path, level: int) -> None:
        compression = zipfile.ZIP_DEFLATED
        with zipfile.ZipFile(destination, "w", compression=compression, compresslevel=level) as archive:
            for file_path in files:
                arcname = safe_relative_name(file_path, base)
                archive.write(file_path, arcname=arcname)

    def _compress_tar(self, files: Sequence[Path], destination: Path, base: Path, fmt: CompressionFormat) -> None:
        mode = {
            CompressionFormat.TAR: "w",
            CompressionFormat.TAR_GZ: "w:gz",
            CompressionFormat.TAR_BZ2: "w:bz2",
            CompressionFormat.TAR_XZ: "w:xz",
        }[fmt]
        with tarfile.open(destination, mode) as archive:
            for file_path in files:
                arcname = safe_relative_name(file_path, base)
                archive.add(file_path, arcname=arcname, recursive=False)

    def _extract_zip(self, source: Path, destination: Path) -> List[Path]:
        extracted: List[Path] = []
        total_size = 0
        with zipfile.ZipFile(source, "r") as archive:
            members = archive.infolist()
            if len(members) > self.policy.max_members:
                raise CompressionSecurityError(f"Archive has too many members: {len(members)}")
            for member in members:
                target = self.policy.validate_member(member.filename, destination)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                total_size += int(member.file_size)
                self._validate_total_uncompressed_size(total_size)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member, "r") as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=self.chunk_size)
                extracted.append(target)
        return extracted

    def _extract_tar(self, source: Path, destination: Path, fmt: CompressionFormat) -> List[Path]:
        mode = {
            CompressionFormat.TAR: "r:",
            CompressionFormat.TAR_GZ: "r:gz",
            CompressionFormat.TAR_BZ2: "r:bz2",
            CompressionFormat.TAR_XZ: "r:xz",
        }[fmt]
        extracted: List[Path] = []
        total_size = 0
        with tarfile.open(source, mode) as archive:
            members = archive.getmembers()
            if len(members) > self.policy.max_members:
                raise CompressionSecurityError(f"Archive has too many members: {len(members)}")
            for member in members:
                target = self.policy.validate_member(member.name, destination)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if member.issym() or member.islnk():
                    raise CompressionSecurityError(f"Links are not allowed in tar archive: {member.name}")
                if not member.isfile():
                    continue
                total_size += int(member.size)
                self._validate_total_uncompressed_size(total_size)
                target.parent.mkdir(parents=True, exist_ok=True)
                src = archive.extractfile(member)
                if src is None:
                    continue
                with src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=self.chunk_size)
                if self.policy.preserve_permissions:
                    os.chmod(target, member.mode)
                extracted.append(target)
        return extracted

    def _validate_source_size(self, path: Path) -> None:
        if self.policy.max_file_size_bytes is not None and path.stat().st_size > self.policy.max_file_size_bytes:
            raise CompressionSecurityError(
                f"Source file exceeds max_file_size_bytes: {path.stat().st_size} > {self.policy.max_file_size_bytes}"
            )

    def _validate_total_uncompressed_size(self, size: int) -> None:
        if self.policy.max_total_uncompressed_bytes is not None and size > self.policy.max_total_uncompressed_bytes:
            raise CompressionSecurityError(
                f"Uncompressed data exceeds max_total_uncompressed_bytes: {size} > {self.policy.max_total_uncompressed_bytes}"
            )

    def _ensure_can_write(self, path: Path) -> None:
        if path.exists() and not self.policy.allow_overwrite:
            raise CompressionSecurityError(f"Destination already exists and overwrite is disabled: {path}")


def compress_file(
    source: PathLike,
    destination: Optional[PathLike] = None,
    *,
    fmt: Optional[CompressionFormat] = None,
    level: Union[int, CompressionLevel] = CompressionLevel.BALANCED,
    policy: Optional[CompressionPolicy] = None,
) -> CompressionResult:
    """Atalho para comprimir arquivo único."""
    return CompressionManager(policy=policy).compress_file(source, destination, fmt=fmt, level=level)


def decompress_file(
    source: PathLike,
    destination: Optional[PathLike] = None,
    *,
    fmt: Optional[CompressionFormat] = None,
    policy: Optional[CompressionPolicy] = None,
) -> ExtractionResult:
    """Atalho para descomprimir arquivo único."""
    return CompressionManager(policy=policy).decompress_file(source, destination, fmt=fmt)


def compress_paths(
    sources: Sequence[PathLike],
    destination: PathLike,
    *,
    fmt: CompressionFormat = CompressionFormat.ZIP,
    level: Union[int, CompressionLevel] = CompressionLevel.BALANCED,
    policy: Optional[CompressionPolicy] = None,
) -> CompressionResult:
    """Atalho para comprimir múltiplos caminhos."""
    return CompressionManager(policy=policy).compress_paths(sources, destination, fmt=fmt, level=level)


def extract_archive(
    source: PathLike,
    destination_dir: PathLike,
    *,
    fmt: Optional[CompressionFormat] = None,
    policy: Optional[CompressionPolicy] = None,
) -> ExtractionResult:
    """Atalho para extrair arquivo ZIP/TAR."""
    return CompressionManager(policy=policy).extract_archive(source, destination_dir, fmt=fmt)


def infer_format(path_or_name: PathLike) -> CompressionFormat:
    """Infere formato por extensão."""
    name = str(path_or_name).lower()
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return CompressionFormat.TAR_GZ
    if name.endswith(".tar.bz2") or name.endswith(".tbz2"):
        return CompressionFormat.TAR_BZ2
    if name.endswith(".tar.xz") or name.endswith(".txz"):
        return CompressionFormat.TAR_XZ
    if name.endswith(".tar"):
        return CompressionFormat.TAR
    if name.endswith(".zip"):
        return CompressionFormat.ZIP
    if name.endswith(".gz"):
        return CompressionFormat.GZIP
    if name.endswith(".bz2"):
        return CompressionFormat.BZ2
    if name.endswith(".xz") or name.endswith(".lzma"):
        return CompressionFormat.XZ
    raise CompressionConfigurationError(f"Cannot infer compression format from: {path_or_name}")


def default_compressed_path(source: Path, fmt: CompressionFormat) -> Path:
    suffix = {
        CompressionFormat.GZIP: ".gz",
        CompressionFormat.BZ2: ".bz2",
        CompressionFormat.XZ: ".xz",
        CompressionFormat.ZIP: ".zip",
        CompressionFormat.TAR: ".tar",
        CompressionFormat.TAR_GZ: ".tar.gz",
        CompressionFormat.TAR_BZ2: ".tar.bz2",
        CompressionFormat.TAR_XZ: ".tar.xz",
    }[fmt]
    return source.with_name(source.name + suffix)


def default_decompressed_path(source: Path, fmt: CompressionFormat) -> Path:
    name = source.name
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz", ".gz", ".bz2", ".xz", ".lzma", ".zip", ".tar"):
        if name.lower().endswith(suffix):
            return source.with_name(name[: -len(suffix)])
    return source.with_suffix("")


def sha256_file(path: PathLike, *, chunk_size: int = 1024 * 1024) -> str:
    """Calcula SHA-256 de arquivo em streaming."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Calcula SHA-256 de bytes."""
    return hashlib.sha256(data).hexdigest()


def collect_files(paths: Sequence[Path]) -> List[Path]:
    """Coleta arquivos a partir de arquivos e diretórios."""
    files: List[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(item for item in path.rglob("*") if item.is_file()))
        else:
            raise CompressionError(f"Unsupported source path: {path}")
    return files


def common_parent(paths: Sequence[Path]) -> Path:
    """Calcula diretório comum para nomes relativos no archive."""
    resolved = [path.resolve() for path in paths]
    if len(resolved) == 1:
        return resolved[0].parent if resolved[0].is_file() else resolved[0]
    return Path(os.path.commonpath([str(path.parent if path.is_file() else path) for path in resolved]))


def safe_relative_name(path: Path, base: Path) -> str:
    """Gera nome relativo seguro para archive."""
    resolved_base = base.resolve()
    resolved_path = path.resolve()
    try:
        relative = resolved_path.relative_to(resolved_base)
    except ValueError:
        relative = Path(path.name)
    relative_text = str(relative).replace(os.sep, "/")
    if relative_text.startswith("../") or relative_text == ".." or Path(relative_text).is_absolute():
        raise CompressionSecurityError(f"Unsafe archive relative path: {relative_text}")
    return relative_text


def is_archive(path: PathLike) -> bool:
    """Indica se o caminho parece ser arquivo compactado suportado."""
    try:
        infer_format(path)
        return True
    except CompressionConfigurationError:
        return False


def estimate_compression_ratio(original_size: int, compressed_size: int) -> float:
    """Calcula razão de compressão."""
    if original_size <= 0:
        return 0.0
    return round(compressed_size / original_size, 6)


__all__ = [
    "CompressionConfigurationError",
    "CompressionError",
    "CompressionFormat",
    "CompressionLevel",
    "CompressionManager",
    "CompressionPolicy",
    "CompressionResult",
    "CompressionSecurityError",
    "ExtractionResult",
    "collect_files",
    "common_parent",
    "compress_file",
    "compress_paths",
    "decompress_file",
    "default_compressed_path",
    "default_decompressed_path",
    "estimate_compression_ratio",
    "extract_archive",
    "infer_format",
    "is_archive",
    "safe_relative_name",
    "sha256_bytes",
    "sha256_file",
]

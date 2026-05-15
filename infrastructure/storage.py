# kwanza-ai-core/infrastructure/storage.py
from __future__ import annotations

import abc
import asyncio
import contextlib
import hashlib
import json
import logging
import mimetypes
import os
import pathlib
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, AsyncIterator, BinaryIO, Dict, Iterable, Mapping, Optional, Protocol, Sequence


class StorageBackend(str, Enum):
    LOCAL = "local"
    S3 = "s3"


class StorageVisibility(str, Enum):
    PRIVATE = "private"
    PUBLIC = "public"


class StorageObjectStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


@dataclass(frozen=True)
class StorageConfig:
    backend: StorageBackend = StorageBackend.LOCAL
    base_path: pathlib.Path = pathlib.Path("./storage")
    bucket_name: str = "kwanza-ai-core"
    namespace: str = "default"
    enable_versioning: bool = True
    enable_checksums: bool = True
    enable_audit: bool = True
    default_visibility: StorageVisibility = StorageVisibility.PRIVATE
    retention_days: Optional[int] = None

    s3_endpoint_url: Optional[str] = None
    s3_region_name: str = "us-east-1"
    s3_access_key_id: Optional[str] = None
    s3_secret_access_key: Optional[str] = None
    s3_use_ssl: bool = True

    multipart_chunk_size: int = 8 * 1024 * 1024
    max_object_size_bytes: int = 5 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class StorageObject:
    key: str
    size_bytes: int
    content_type: str
    checksum_sha256: Optional[str]
    created_at: str
    updated_at: str
    status: StorageObjectStatus = StorageObjectStatus.ACTIVE
    version_id: Optional[str] = None
    visibility: StorageVisibility = StorageVisibility.PRIVATE
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoragePutResult:
    key: str
    version_id: Optional[str]
    size_bytes: int
    checksum_sha256: Optional[str]
    content_type: str
    uri: str


@dataclass(frozen=True)
class StorageListResult:
    objects: list[StorageObject]
    prefix: str
    count: int


class MetricsSink(Protocol):
    def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None: ...

    def timing(
        self,
        name: str,
        value_ms: float,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None: ...


class NoopMetricsSink:
    def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        return None

    def timing(
        self,
        name: str,
        value_ms: float,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        return None


class StorageError(RuntimeError):
    pass


class StorageNotFoundError(StorageError):
    pass


class StorageValidationError(StorageError):
    pass


class StorageBackendError(StorageError):
    pass


class StorageBackendClient(abc.ABC):
    @abc.abstractmethod
    async def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Optional[Mapping[str, Any]] = None,
        visibility: StorageVisibility = StorageVisibility.PRIVATE,
    ) -> StoragePutResult:
        raise NotImplementedError

    @abc.abstractmethod
    async def get_bytes(self, key: str, version_id: Optional[str] = None) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    async def exists(self, key: str) -> bool:
        raise NotImplementedError

    @abc.abstractmethod
    async def stat(self, key: str, version_id: Optional[str] = None) -> StorageObject:
        raise NotImplementedError

    @abc.abstractmethod
    async def list(self, prefix: str = "", limit: int = 1000) -> StorageListResult:
        raise NotImplementedError

    @abc.abstractmethod
    async def delete(self, key: str) -> bool:
        raise NotImplementedError

    @abc.abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


class LocalStorageBackend(StorageBackendClient):
    def __init__(self, config: StorageConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.root = config.base_path / config.namespace
        self.objects_root = self.root / "objects"
        self.metadata_root = self.root / "metadata"
        self.versions_root = self.root / "versions"
        self._lock = asyncio.Lock()

        self.objects_root.mkdir(parents=True, exist_ok=True)
        self.metadata_root.mkdir(parents=True, exist_ok=True)
        self.versions_root.mkdir(parents=True, exist_ok=True)

    async def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Optional[Mapping[str, Any]] = None,
        visibility: StorageVisibility = StorageVisibility.PRIVATE,
    ) -> StoragePutResult:
        self._validate_key(key)

        if len(data) > self.config.max_object_size_bytes:
            raise StorageValidationError("Object exceeds max_object_size_bytes")

        async with self._lock:
            path = self._object_path(key)
            meta_path = self._metadata_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.parent.mkdir(parents=True, exist_ok=True)

            existing_version_id: Optional[str] = None

            if self.config.enable_versioning and path.exists():
                existing_version_id = str(uuid.uuid4())
                version_path = self._version_path(key, existing_version_id)
                version_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, version_path)

            checksum = sha256_bytes(data) if self.config.enable_checksums else None

            await asyncio.to_thread(path.write_bytes, data)

            now = now_iso()
            object_meta = StorageObject(
                key=key,
                size_bytes=len(data),
                content_type=content_type,
                checksum_sha256=checksum,
                created_at=now,
                updated_at=now,
                version_id=existing_version_id,
                visibility=visibility,
                metadata=dict(metadata or {}),
            )

            await asyncio.to_thread(
                meta_path.write_text,
                json.dumps(to_jsonable_object(object_meta), ensure_ascii=False, indent=2),
                "utf-8",
            )

            return StoragePutResult(
                key=key,
                version_id=existing_version_id,
                size_bytes=len(data),
                checksum_sha256=checksum,
                content_type=content_type,
                uri=f"local://{self.config.namespace}/{key}",
            )

    async def get_bytes(self, key: str, version_id: Optional[str] = None) -> bytes:
        self._validate_key(key)
        path = self._version_path(key, version_id) if version_id else self._object_path(key)

        if not path.exists():
            raise StorageNotFoundError(f"Object not found: {key}")

        return await asyncio.to_thread(path.read_bytes)

    async def exists(self, key: str) -> bool:
        self._validate_key(key)
        return self._object_path(key).exists()

    async def stat(self, key: str, version_id: Optional[str] = None) -> StorageObject:
        self._validate_key(key)

        meta_path = self._metadata_path(key)

        if not meta_path.exists():
            if not self._object_path(key).exists():
                raise StorageNotFoundError(f"Object not found: {key}")
            return await self._stat_from_file(key)

        raw = await asyncio.to_thread(meta_path.read_text, "utf-8")
        data = json.loads(raw)

        return StorageObject(
            key=data["key"],
            size_bytes=int(data["size_bytes"]),
            content_type=data.get("content_type", "application/octet-stream"),
            checksum_sha256=data.get("checksum_sha256"),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            status=StorageObjectStatus(data.get("status", StorageObjectStatus.ACTIVE.value)),
            version_id=data.get("version_id"),
            visibility=StorageVisibility(data.get("visibility", StorageVisibility.PRIVATE.value)),
            metadata=dict(data.get("metadata", {})),
        )

    async def list(self, prefix: str = "", limit: int = 1000) -> StorageListResult:
        clean_prefix = normalize_key(prefix)
        objects: list[StorageObject] = []

        for path in self.objects_root.rglob("*"):
            if not path.is_file():
                continue

            key = path.relative_to(self.objects_root).as_posix()

            if clean_prefix and not key.startswith(clean_prefix):
                continue

            with contextlib.suppress(Exception):
                objects.append(await self.stat(key))

            if len(objects) >= limit:
                break

        return StorageListResult(
            objects=objects,
            prefix=prefix,
            count=len(objects),
        )

    async def delete(self, key: str) -> bool:
        self._validate_key(key)

        async with self._lock:
            path = self._object_path(key)
            meta_path = self._metadata_path(key)

            existed = path.exists()

            if path.exists():
                await asyncio.to_thread(path.unlink)

            if meta_path.exists():
                await asyncio.to_thread(meta_path.unlink)

            return existed

    async def close(self) -> None:
        return None

    async def _stat_from_file(self, key: str) -> StorageObject:
        path = self._object_path(key)
        stat = await asyncio.to_thread(path.stat)

        return StorageObject(
            key=key,
            size_bytes=stat.st_size,
            content_type=guess_content_type(key),
            checksum_sha256=sha256_bytes(await asyncio.to_thread(path.read_bytes))
            if self.config.enable_checksums
            else None,
            created_at=datetime.fromtimestamp(stat.st_ctime, timezone.utc).isoformat(),
            updated_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        )

    def _object_path(self, key: str) -> pathlib.Path:
        return safe_join(self.objects_root, key)

    def _metadata_path(self, key: str) -> pathlib.Path:
        return safe_join(self.metadata_root, f"{key}.json")

    def _version_path(self, key: str, version_id: Optional[str]) -> pathlib.Path:
        if not version_id:
            raise StorageValidationError("version_id is required")
        return safe_join(self.versions_root, version_id, key)

    def _validate_key(self, key: str) -> None:
        validate_storage_key(key)


class S3StorageBackend(StorageBackendClient):
    """
    Backend S3 compatível.

    Requer:
        pip install aioboto3

    Compatível também com MinIO, Wasabi e serviços S3-like via endpoint_url.
    """

    def __init__(self, config: StorageConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self._session: Any = None
        self._client: Any = None

    async def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        try:
            import aioboto3
        except ImportError as exc:
            raise StorageBackendError("aioboto3 não está instalado. Use: pip install aioboto3") from exc

        self._session = aioboto3.Session()
        self._client = await self._session.client(
            "s3",
            endpoint_url=self.config.s3_endpoint_url,
            region_name=self.config.s3_region_name,
            aws_access_key_id=self.config.s3_access_key_id,
            aws_secret_access_key=self.config.s3_secret_access_key,
            use_ssl=self.config.s3_use_ssl,
        ).__aenter__()

        return self._client

    async def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Optional[Mapping[str, Any]] = None,
        visibility: StorageVisibility = StorageVisibility.PRIVATE,
    ) -> StoragePutResult:
        validate_storage_key(key)

        if len(data) > self.config.max_object_size_bytes:
            raise StorageValidationError("Object exceeds max_object_size_bytes")

        client = await self._get_client()
        checksum = sha256_bytes(data) if self.config.enable_checksums else None
        s3_key = self._s3_key(key)

        extra_args: Dict[str, Any] = {
            "Bucket": self.config.bucket_name,
            "Key": s3_key,
            "Body": data,
            "ContentType": content_type,
            "Metadata": stringify_metadata(
                {
                    **dict(metadata or {}),
                    "checksum_sha256": checksum or "",
                    "namespace": self.config.namespace,
                }
            ),
        }

        if visibility == StorageVisibility.PUBLIC:
            extra_args["ACL"] = "public-read"

        response = await client.put_object(**extra_args)

        return StoragePutResult(
            key=key,
            version_id=response.get("VersionId"),
            size_bytes=len(data),
            checksum_sha256=checksum,
            content_type=content_type,
            uri=f"s3://{self.config.bucket_name}/{s3_key}",
        )

    async def get_bytes(self, key: str, version_id: Optional[str] = None) -> bytes:
        validate_storage_key(key)
        client = await self._get_client()

        kwargs: Dict[str, Any] = {
            "Bucket": self.config.bucket_name,
            "Key": self._s3_key(key),
        }

        if version_id:
            kwargs["VersionId"] = version_id

        try:
            response = await client.get_object(**kwargs)
            async with response["Body"] as stream:
                return await stream.read()

        except Exception as exc:
            if "NoSuchKey" in repr(exc) or "404" in repr(exc):
                raise StorageNotFoundError(f"Object not found: {key}") from exc
            raise StorageBackendError(str(exc)) from exc

    async def exists(self, key: str) -> bool:
        try:
            await self.stat(key)
            return True
        except StorageNotFoundError:
            return False

    async def stat(self, key: str, version_id: Optional[str] = None) -> StorageObject:
        validate_storage_key(key)
        client = await self._get_client()

        kwargs: Dict[str, Any] = {
            "Bucket": self.config.bucket_name,
            "Key": self._s3_key(key),
        }

        if version_id:
            kwargs["VersionId"] = version_id

        try:
            response = await client.head_object(**kwargs)
        except Exception as exc:
            if "404" in repr(exc) or "Not Found" in repr(exc):
                raise StorageNotFoundError(f"Object not found: {key}") from exc
            raise StorageBackendError(str(exc)) from exc

        metadata = dict(response.get("Metadata", {}))
        updated_at = response.get("LastModified")

        if hasattr(updated_at, "isoformat"):
            updated_at_str = updated_at.isoformat()
        else:
            updated_at_str = now_iso()

        return StorageObject(
            key=key,
            size_bytes=int(response.get("ContentLength", 0)),
            content_type=response.get("ContentType", guess_content_type(key)),
            checksum_sha256=metadata.get("checksum_sha256"),
            created_at=metadata.get("created_at", updated_at_str),
            updated_at=updated_at_str,
            version_id=response.get("VersionId") or version_id,
            visibility=self.config.default_visibility,
            metadata=metadata,
        )

    async def list(self, prefix: str = "", limit: int = 1000) -> StorageListResult:
        client = await self._get_client()
        s3_prefix = self._s3_key(prefix) if prefix else f"{self.config.namespace}/"

        paginator = client.get_paginator("list_objects_v2")
        objects: list[StorageObject] = []

        async for page in paginator.paginate(
            Bucket=self.config.bucket_name,
            Prefix=s3_prefix,
            PaginationConfig={"MaxItems": limit},
        ):
            for item in page.get("Contents", []):
                full_key = item["Key"]
                key = full_key.removeprefix(f"{self.config.namespace}/")

                objects.append(
                    StorageObject(
                        key=key,
                        size_bytes=int(item.get("Size", 0)),
                        content_type=guess_content_type(key),
                        checksum_sha256=None,
                        created_at=item.get("LastModified", datetime.now(timezone.utc)).isoformat(),
                        updated_at=item.get("LastModified", datetime.now(timezone.utc)).isoformat(),
                    )
                )

                if len(objects) >= limit:
                    break

        return StorageListResult(objects=objects, prefix=prefix, count=len(objects))

    async def delete(self, key: str) -> bool:
        validate_storage_key(key)
        client = await self._get_client()

        existed = await self.exists(key)

        await client.delete_object(
            Bucket=self.config.bucket_name,
            Key=self._s3_key(key),
        )

        return existed

    async def close(self) -> None:
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None

    def _s3_key(self, key: str) -> str:
        return f"{self.config.namespace}/{normalize_key(key)}"


class StorageService:
    def __init__(
        self,
        config: Optional[StorageConfig] = None,
        backend: Optional[StorageBackendClient] = None,
        metrics: Optional[MetricsSink] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or StorageConfig()
        self.metrics = metrics or NoopMetricsSink()
        self.logger = logger or logging.getLogger("kwanza.infrastructure.storage")
        self.backend = backend or self._build_backend()

    async def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        visibility: Optional[StorageVisibility] = None,
    ) -> StoragePutResult:
        started = time.monotonic()

        try:
            content_type = content_type or guess_content_type(key)
            visibility = visibility or self.config.default_visibility

            result = await self.backend.put_bytes(
                normalize_key(key),
                data,
                content_type=content_type,
                metadata={
                    **dict(metadata or {}),
                    "created_at": now_iso(),
                },
                visibility=visibility,
            )

            self.metrics.increment("storage.put", tags=self._tags())
            return result

        except Exception as exc:
            self.metrics.increment("storage.put.error", tags=self._tags())
            raise StorageBackendError(str(exc)) from exc

        finally:
            self.metrics.timing(
                "storage.put.latency_ms",
                (time.monotonic() - started) * 1000,
                tags=self._tags(),
            )

    async def put_json(
        self,
        key: str,
        value: Any,
        *,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> StoragePutResult:
        return await self.put_bytes(
            ensure_extension(key, ".json"),
            json.dumps(value, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
            content_type="application/json",
            metadata=metadata,
        )

    async def put_text(
        self,
        key: str,
        text: str,
        *,
        encoding: str = "utf-8",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> StoragePutResult:
        return await self.put_bytes(
            key,
            text.encode(encoding),
            content_type="text/plain; charset=utf-8",
            metadata=metadata,
        )

    async def put_file(
        self,
        source_path: str | pathlib.Path,
        key: Optional[str] = None,
        *,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> StoragePutResult:
        path = pathlib.Path(source_path)

        if not path.exists() or not path.is_file():
            raise StorageNotFoundError(f"Source file not found: {path}")

        data = await asyncio.to_thread(path.read_bytes)
        target_key = key or path.name

        return await self.put_bytes(
            target_key,
            data,
            content_type=guess_content_type(target_key),
            metadata={
                **dict(metadata or {}),
                "source_path": str(path),
            },
        )

    async def get_bytes(self, key: str, version_id: Optional[str] = None) -> bytes:
        started = time.monotonic()

        try:
            data = await self.backend.get_bytes(normalize_key(key), version_id=version_id)
            self.metrics.increment("storage.get", tags=self._tags())
            return data

        except StorageNotFoundError:
            self.metrics.increment("storage.get.not_found", tags=self._tags())
            raise

        except Exception as exc:
            self.metrics.increment("storage.get.error", tags=self._tags())
            raise StorageBackendError(str(exc)) from exc

        finally:
            self.metrics.timing(
                "storage.get.latency_ms",
                (time.monotonic() - started) * 1000,
                tags=self._tags(),
            )

    async def get_json(self, key: str, version_id: Optional[str] = None) -> Any:
        data = await self.get_bytes(key, version_id=version_id)
        return json.loads(data.decode("utf-8"))

    async def get_text(
        self,
        key: str,
        *,
        encoding: str = "utf-8",
        version_id: Optional[str] = None,
    ) -> str:
        data = await self.get_bytes(key, version_id=version_id)
        return data.decode(encoding)

    async def download_file(
        self,
        key: str,
        destination_path: str | pathlib.Path,
        *,
        version_id: Optional[str] = None,
        overwrite: bool = False,
    ) -> pathlib.Path:
        destination = pathlib.Path(destination_path)

        if destination.exists() and not overwrite:
            raise StorageValidationError(f"Destination already exists: {destination}")

        data = await self.get_bytes(key, version_id=version_id)

        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(destination.write_bytes, data)

        return destination

    async def stat(self, key: str, version_id: Optional[str] = None) -> StorageObject:
        return await self.backend.stat(normalize_key(key), version_id=version_id)

    async def exists(self, key: str) -> bool:
        return await self.backend.exists(normalize_key(key))

    async def list(self, prefix: str = "", limit: int = 1000) -> StorageListResult:
        return await self.backend.list(normalize_key(prefix), limit=limit)

    async def delete(self, key: str) -> bool:
        started = time.monotonic()

        try:
            deleted = await self.backend.delete(normalize_key(key))
            self.metrics.increment("storage.delete", tags=self._tags())
            return deleted

        finally:
            self.metrics.timing(
                "storage.delete.latency_ms",
                (time.monotonic() - started) * 1000,
                tags=self._tags(),
            )

    async def copy(self, source_key: str, target_key: str) -> StoragePutResult:
        source = await self.get_bytes(source_key)
        source_meta = await self.stat(source_key)

        return await self.put_bytes(
            target_key,
            source,
            content_type=source_meta.content_type,
            metadata={
                **source_meta.metadata,
                "copied_from": source_key,
                "copied_at": now_iso(),
            },
            visibility=source_meta.visibility,
        )

    async def move(self, source_key: str, target_key: str) -> StoragePutResult:
        result = await self.copy(source_key, target_key)
        await self.delete(source_key)
        return result

    async def cleanup_expired(self) -> int:
        if self.config.retention_days is None:
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.config.retention_days)
        listed = await self.list("", limit=100_000)
        deleted = 0

        for obj in listed.objects:
            with contextlib.suppress(Exception):
                updated_at = datetime.fromisoformat(obj.updated_at.replace("Z", "+00:00"))
                if updated_at < cutoff:
                    if await self.delete(obj.key):
                        deleted += 1

        return deleted

    async def close(self) -> None:
        await self.backend.close()

    def _build_backend(self) -> StorageBackendClient:
        if self.config.backend == StorageBackend.LOCAL:
            return LocalStorageBackend(self.config, self.logger)

        if self.config.backend == StorageBackend.S3:
            return S3StorageBackend(self.config, self.logger)

        raise StorageBackendError(f"Unsupported storage backend: {self.config.backend}")

    def _tags(self) -> Dict[str, str]:
        return {
            "backend": self.config.backend.value,
            "namespace": self.config.namespace,
            "bucket": self.config.bucket_name,
        }


class StorageRepository:
    """
    Repositório utilitário para persistir entidades JSON versionadas.
    """

    def __init__(self, storage: StorageService, collection: str) -> None:
        self.storage = storage
        self.collection = normalize_key(collection)

    async def save(
        self,
        entity_id: str,
        data: Mapping[str, Any],
        *,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> StoragePutResult:
        key = f"{self.collection}/{entity_id}.json"
        return await self.storage.put_json(key, data, metadata=metadata)

    async def get(self, entity_id: str) -> Dict[str, Any]:
        key = f"{self.collection}/{entity_id}.json"
        return await self.storage.get_json(key)

    async def delete(self, entity_id: str) -> bool:
        key = f"{self.collection}/{entity_id}.json"
        return await self.storage.delete(key)

    async def list(self, limit: int = 1000) -> list[StorageObject]:
        result = await self.storage.list(self.collection, limit=limit)
        return result.objects


def validate_storage_key(key: str) -> None:
    if not key or not key.strip():
        raise StorageValidationError("Storage key cannot be empty")

    normalized = normalize_key(key)

    if normalized.startswith("../") or "/../" in normalized or normalized == "..":
        raise StorageValidationError("Storage key cannot contain path traversal")

    if normalized.startswith("/"):
        raise StorageValidationError("Storage key cannot be absolute")

    if "\x00" in normalized:
        raise StorageValidationError("Storage key contains null byte")


def normalize_key(key: str) -> str:
    return str(key).replace("\\", "/").strip().strip("/")


def safe_join(root: pathlib.Path, *parts: str) -> pathlib.Path:
    root_resolved = root.resolve()
    candidate = root.joinpath(*[normalize_key(part) for part in parts]).resolve()

    if root_resolved not in candidate.parents and candidate != root_resolved:
        raise StorageValidationError("Unsafe storage path")

    return candidate


def guess_content_type(key: str) -> str:
    content_type, _encoding = mimetypes.guess_type(key)
    return content_type or "application/octet-stream"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | pathlib.Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()

    with pathlib.Path(path).open("rb") as file:
        while chunk := file.read(chunk_size):
            digest.update(chunk)

    return digest.hexdigest()


def ensure_extension(key: str, extension: str) -> str:
    if key.endswith(extension):
        return key
    return f"{key}{extension}"


def stringify_metadata(metadata: Mapping[str, Any]) -> Dict[str, str]:
    return {
        str(key): str(value)
        for key, value in metadata.items()
        if value is not None
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_jsonable_object(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, StorageObject):
        return {
            "key": obj.key,
            "size_bytes": obj.size_bytes,
            "content_type": obj.content_type,
            "checksum_sha256": obj.checksum_sha256,
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
            "status": obj.status.value,
            "version_id": obj.version_id,
            "visibility": obj.visibility.value,
            "metadata": obj.metadata,
        }

    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)

    return dict(obj)


def build_storage_from_env() -> StorageService:
    backend = StorageBackend(os.getenv("STORAGE_BACKEND", StorageBackend.LOCAL.value).lower())

    config = StorageConfig(
        backend=backend,
        base_path=pathlib.Path(os.getenv("STORAGE_BASE_PATH", "./storage")),
        bucket_name=os.getenv("STORAGE_BUCKET_NAME", "kwanza-ai-core"),
        namespace=os.getenv("STORAGE_NAMESPACE", "default"),
        enable_versioning=os.getenv("STORAGE_ENABLE_VERSIONING", "true").lower() == "true",
        enable_checksums=os.getenv("STORAGE_ENABLE_CHECKSUMS", "true").lower() == "true",
        enable_audit=os.getenv("STORAGE_ENABLE_AUDIT", "true").lower() == "true",
        default_visibility=StorageVisibility(
            os.getenv("STORAGE_DEFAULT_VISIBILITY", StorageVisibility.PRIVATE.value).lower()
        ),
        retention_days=int(os.getenv("STORAGE_RETENTION_DAYS"))
        if os.getenv("STORAGE_RETENTION_DAYS")
        else None,
        s3_endpoint_url=os.getenv("S3_ENDPOINT_URL"),
        s3_region_name=os.getenv("S3_REGION_NAME", "us-east-1"),
        s3_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
        s3_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
        s3_use_ssl=os.getenv("S3_USE_SSL", "true").lower() == "true",
        multipart_chunk_size=int(os.getenv("STORAGE_MULTIPART_CHUNK_SIZE", str(8 * 1024 * 1024))),
        max_object_size_bytes=int(
            os.getenv("STORAGE_MAX_OBJECT_SIZE_BYTES", str(5 * 1024 * 1024 * 1024))
        ),
    )

    return StorageService(config=config)
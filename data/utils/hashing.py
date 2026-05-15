"""
data/utils/hashing.py

Enterprise-grade hashing and integrity utilities.

Este módulo centraliza funções robustas para hashing, HMAC, checksums,
fingerprints determinísticos, assinatura de payloads, comparação segura e
validação de integridade em pipelines de dados, ingestão, validação, auditoria,
segurança e armazenamento.

Capacidades principais:
- Hash SHA-256, SHA-512, SHA-1, MD5 opcional e BLAKE2.
- HMAC com comparação constante usando hmac.compare_digest.
- Checksum de arquivos e streams em chunks.
- Fingerprint determinístico de objetos Python/JSON-safe.
- Hash de linhas/registros para CDC, deduplicação e auditoria.
- Merkle root simples para conjuntos de registros.
- Assinatura e verificação de payloads.
- Salts e peppers opcionais.
- API estruturada com resultados serializáveis.
- Sem dependências externas obrigatórias.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import math
import os
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union


PathLike = Union[str, os.PathLike[str]]
JsonDict = Dict[str, Any]


class HashAlgorithm(str, Enum):
    """Algoritmos de hash suportados."""

    SHA256 = "sha256"
    SHA512 = "sha512"
    SHA384 = "sha384"
    SHA1 = "sha1"
    MD5 = "md5"
    BLAKE2B = "blake2b"
    BLAKE2S = "blake2s"


class DigestEncoding(str, Enum):
    """Codificações de digest suportadas."""

    HEX = "hex"
    BASE64 = "base64"
    BASE64URL = "base64url"
    BYTES = "bytes"


class HashPurpose(str, Enum):
    """Finalidade semântica do hash."""

    CHECKSUM = "CHECKSUM"
    FINGERPRINT = "FINGERPRINT"
    SIGNATURE = "SIGNATURE"
    DEDUPLICATION = "DEDUPLICATION"
    AUDIT = "AUDIT"
    SECURITY = "SECURITY"
    CACHE_KEY = "CACHE_KEY"


class HashingError(Exception):
    """Erro base do módulo de hashing."""


class UnsupportedHashAlgorithmError(HashingError):
    """Algoritmo de hash não suportado."""


class SignatureVerificationError(HashingError):
    """Assinatura inválida."""


@dataclass(frozen=True)
class HashConfig:
    """Configuração para operações de hash."""

    algorithm: HashAlgorithm = HashAlgorithm.SHA256
    encoding: DigestEncoding = DigestEncoding.HEX
    chunk_size: int = 1024 * 1024
    salt: Optional[bytes] = None
    pepper: Optional[bytes] = None
    allow_insecure: bool = False
    canonical_json: bool = True

    def __post_init__(self) -> None:
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        if self.algorithm in {HashAlgorithm.MD5, HashAlgorithm.SHA1} and not self.allow_insecure:
            raise UnsupportedHashAlgorithmError(
                f"{self.algorithm.value} is considered insecure. Set allow_insecure=True only for legacy checksums."
            )


@dataclass(frozen=True)
class HashResult:
    """Resultado estruturado de uma operação de hash."""

    digest: Union[str, bytes]
    algorithm: HashAlgorithm
    encoding: DigestEncoding
    purpose: HashPurpose = HashPurpose.CHECKSUM
    source: Optional[str] = None
    size_bytes: Optional[int] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        digest_value = self.digest if isinstance(self.digest, str) else base64.b64encode(self.digest).decode("ascii")
        return {
            "digest": digest_value,
            "algorithm": self.algorithm.value,
            "encoding": self.encoding.value,
            "purpose": self.purpose.value,
            "source": self.source,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at.isoformat(),
            "metadata": safe_json_value(dict(self.metadata)),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class Signature:
    """Assinatura HMAC serializável."""

    value: str
    algorithm: HashAlgorithm = HashAlgorithm.SHA256
    encoding: DigestEncoding = DigestEncoding.BASE64URL
    key_id: Optional[str] = None
    signed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "value": self.value,
            "algorithm": self.algorithm.value,
            "encoding": self.encoding.value,
            "key_id": self.key_id,
            "signed_at": self.signed_at.isoformat(),
            "metadata": safe_json_value(dict(self.metadata)),
        }

    def to_header(self, prefix: str = "sha256") -> str:
        return f"{prefix}={self.value}"


class HashingService:
    """Serviço enterprise para hashing, HMAC e integridade."""

    def __init__(self, config: Optional[HashConfig] = None) -> None:
        self.config = config or HashConfig()

    def hash_bytes(
        self,
        data: bytes,
        *,
        purpose: HashPurpose = HashPurpose.CHECKSUM,
        config: Optional[HashConfig] = None,
    ) -> HashResult:
        cfg = config or self.config
        digest = compute_hash_bytes(data, config=cfg)
        return HashResult(
            digest=digest,
            algorithm=cfg.algorithm,
            encoding=cfg.encoding,
            purpose=purpose,
            size_bytes=len(data),
        )

    def hash_text(
        self,
        text: str,
        *,
        encoding: str = "utf-8",
        purpose: HashPurpose = HashPurpose.FINGERPRINT,
        config: Optional[HashConfig] = None,
    ) -> HashResult:
        return self.hash_bytes(text.encode(encoding), purpose=purpose, config=config)

    def hash_object(
        self,
        value: Any,
        *,
        purpose: HashPurpose = HashPurpose.FINGERPRINT,
        config: Optional[HashConfig] = None,
    ) -> HashResult:
        cfg = config or self.config
        payload = canonical_dumps(value, canonical=cfg.canonical_json).encode("utf-8")
        return self.hash_bytes(payload, purpose=purpose, config=cfg)

    def hash_file(
        self,
        path: PathLike,
        *,
        purpose: HashPurpose = HashPurpose.CHECKSUM,
        config: Optional[HashConfig] = None,
    ) -> HashResult:
        cfg = config or self.config
        path_obj = Path(path)
        digest = compute_hash_file(path_obj, config=cfg)
        return HashResult(
            digest=digest,
            algorithm=cfg.algorithm,
            encoding=cfg.encoding,
            purpose=purpose,
            source=str(path_obj),
            size_bytes=path_obj.stat().st_size,
        )

    def hmac_bytes(
        self,
        data: bytes,
        secret: Union[str, bytes],
        *,
        config: Optional[HashConfig] = None,
    ) -> HashResult:
        cfg = config or self.config
        digest = compute_hmac(data, secret, config=cfg)
        return HashResult(
            digest=digest,
            algorithm=cfg.algorithm,
            encoding=cfg.encoding,
            purpose=HashPurpose.SIGNATURE,
            size_bytes=len(data),
        )

    def sign_payload(
        self,
        payload: Any,
        secret: Union[str, bytes],
        *,
        key_id: Optional[str] = None,
        config: Optional[HashConfig] = None,
    ) -> Signature:
        cfg = config or HashConfig(algorithm=self.config.algorithm, encoding=DigestEncoding.BASE64URL)
        data = canonical_dumps(payload, canonical=cfg.canonical_json).encode("utf-8")
        value = compute_hmac(data, secret, config=cfg)
        if not isinstance(value, str):
            value = base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
        return Signature(value=value, algorithm=cfg.algorithm, encoding=cfg.encoding, key_id=key_id)

    def verify_payload_signature(
        self,
        payload: Any,
        secret: Union[str, bytes],
        signature: Union[str, Signature],
        *,
        config: Optional[HashConfig] = None,
        raise_on_failure: bool = False,
    ) -> bool:
        expected = self.sign_payload(payload, secret, config=config).value
        provided = signature.value if isinstance(signature, Signature) else signature
        ok = constant_time_compare(expected, provided)
        if not ok and raise_on_failure:
            raise SignatureVerificationError("Invalid payload signature")
        return ok


# =============================================================================
# Core hashing functions
# =============================================================================

def get_hash_constructor(algorithm: HashAlgorithm, *, allow_insecure: bool = False) -> Any:
    """Retorna construtor hashlib para algoritmo informado."""
    if algorithm in {HashAlgorithm.MD5, HashAlgorithm.SHA1} and not allow_insecure:
        raise UnsupportedHashAlgorithmError(
            f"{algorithm.value} is considered insecure. Set allow_insecure=True only for legacy checksums."
        )
    mapping = {
        HashAlgorithm.SHA256: hashlib.sha256,
        HashAlgorithm.SHA512: hashlib.sha512,
        HashAlgorithm.SHA384: hashlib.sha384,
        HashAlgorithm.SHA1: hashlib.sha1,
        HashAlgorithm.MD5: hashlib.md5,
        HashAlgorithm.BLAKE2B: hashlib.blake2b,
        HashAlgorithm.BLAKE2S: hashlib.blake2s,
    }
    try:
        return mapping[algorithm]
    except KeyError as exc:
        raise UnsupportedHashAlgorithmError(f"Unsupported hash algorithm: {algorithm}") from exc


def new_hash(config: Optional[HashConfig] = None) -> Any:
    cfg = config or HashConfig()
    constructor = get_hash_constructor(cfg.algorithm, allow_insecure=cfg.allow_insecure)
    digest = constructor()
    if cfg.salt:
        digest.update(cfg.salt)
    if cfg.pepper:
        digest.update(cfg.pepper)
    return digest


def encode_digest(raw_digest: bytes, encoding: DigestEncoding) -> Union[str, bytes]:
    if encoding == DigestEncoding.HEX:
        return raw_digest.hex()
    if encoding == DigestEncoding.BASE64:
        return base64.b64encode(raw_digest).decode("ascii")
    if encoding == DigestEncoding.BASE64URL:
        return base64.urlsafe_b64encode(raw_digest).decode("ascii").rstrip("=")
    if encoding == DigestEncoding.BYTES:
        return raw_digest
    raise ValueError(f"Unsupported digest encoding: {encoding}")


def compute_hash_bytes(data: bytes, *, config: Optional[HashConfig] = None) -> Union[str, bytes]:
    cfg = config or HashConfig()
    digest = new_hash(cfg)
    digest.update(data)
    return encode_digest(digest.digest(), cfg.encoding)


def compute_hash_text(text: str, *, encoding: str = "utf-8", config: Optional[HashConfig] = None) -> Union[str, bytes]:
    return compute_hash_bytes(text.encode(encoding), config=config)


def compute_hash_object(value: Any, *, config: Optional[HashConfig] = None) -> Union[str, bytes]:
    cfg = config or HashConfig()
    data = canonical_dumps(value, canonical=cfg.canonical_json).encode("utf-8")
    return compute_hash_bytes(data, config=cfg)


def compute_hash_file(path: PathLike, *, config: Optional[HashConfig] = None) -> Union[str, bytes]:
    cfg = config or HashConfig()
    path_obj = Path(path)
    if not path_obj.is_file():
        raise FileNotFoundError(f"File not found: {path_obj}")
    digest = new_hash(cfg)
    with path_obj.open("rb") as file:
        for chunk in iter(lambda: file.read(cfg.chunk_size), b""):
            digest.update(chunk)
    return encode_digest(digest.digest(), cfg.encoding)


def compute_hash_stream(stream: BinaryIO, *, config: Optional[HashConfig] = None) -> Union[str, bytes]:
    cfg = config or HashConfig()
    digest = new_hash(cfg)
    while True:
        chunk = stream.read(cfg.chunk_size)
        if not chunk:
            break
        digest.update(chunk)
    return encode_digest(digest.digest(), cfg.encoding)


def compute_hmac(data: bytes, secret: Union[str, bytes], *, config: Optional[HashConfig] = None) -> Union[str, bytes]:
    cfg = config or HashConfig()
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    constructor = get_hash_constructor(cfg.algorithm, allow_insecure=cfg.allow_insecure)
    mac = hmac.new(key, digestmod=constructor)
    if cfg.salt:
        mac.update(cfg.salt)
    mac.update(data)
    if cfg.pepper:
        mac.update(cfg.pepper)
    return encode_digest(mac.digest(), cfg.encoding)


def compute_hmac_text(text: str, secret: Union[str, bytes], *, encoding: str = "utf-8", config: Optional[HashConfig] = None) -> Union[str, bytes]:
    return compute_hmac(text.encode(encoding), secret, config=config)


def constant_time_compare(left: Union[str, bytes], right: Union[str, bytes]) -> bool:
    """Compara valores em tempo constante."""
    if isinstance(left, str) and isinstance(right, str):
        return hmac.compare_digest(left, right)
    left_bytes = left.encode("utf-8") if isinstance(left, str) else left
    right_bytes = right.encode("utf-8") if isinstance(right, str) else right
    return hmac.compare_digest(left_bytes, right_bytes)


# =============================================================================
# Fingerprint, records and Merkle helpers
# =============================================================================

def canonical_dumps(value: Any, *, canonical: bool = True) -> str:
    """Serializa valor em JSON determinístico."""
    safe = safe_json_value(value)
    if canonical:
        return json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return json.dumps(safe, ensure_ascii=False, default=str)


def fingerprint(value: Any, *, algorithm: HashAlgorithm = HashAlgorithm.SHA256, length: Optional[int] = None) -> str:
    """Gera fingerprint determinístico para qualquer valor JSON-safe."""
    digest = compute_hash_object(value, config=HashConfig(algorithm=algorithm, encoding=DigestEncoding.HEX))
    assert isinstance(digest, str)
    return digest[:length] if length else digest


def cache_key(*parts: Any, prefix: Optional[str] = None, length: int = 32) -> str:
    """Gera chave curta e determinística para cache."""
    digest = fingerprint(parts, length=length)
    return f"{prefix}:{digest}" if prefix else digest


def record_hash(record: Mapping[str, Any], *, exclude_fields: Optional[Iterable[str]] = None, config: Optional[HashConfig] = None) -> str:
    """Gera hash determinístico de um registro, ignorando campos opcionais."""
    excluded = set(exclude_fields or ())
    normalized = {str(k): v for k, v in record.items() if str(k) not in excluded}
    digest = compute_hash_object(normalized, config=config or HashConfig(encoding=DigestEncoding.HEX))
    assert isinstance(digest, str)
    return digest


def records_hash(records: Iterable[Mapping[str, Any]], *, exclude_fields: Optional[Iterable[str]] = None, config: Optional[HashConfig] = None) -> str:
    """Gera hash determinístico de uma coleção ordenada de registros."""
    row_hashes = [record_hash(record, exclude_fields=exclude_fields, config=config) for record in records]
    digest = compute_hash_object(row_hashes, config=config or HashConfig(encoding=DigestEncoding.HEX))
    assert isinstance(digest, str)
    return digest


def merkle_root(values: Iterable[Any], *, config: Optional[HashConfig] = None) -> str:
    """Calcula Merkle root simples para lista de valores."""
    cfg = config or HashConfig(encoding=DigestEncoding.HEX)
    leaves = [str(compute_hash_object(value, config=cfg)) for value in values]
    if not leaves:
        return str(compute_hash_object([], config=cfg))
    level = leaves
    while len(level) > 1:
        next_level: List[str] = []
        for index in range(0, len(level), 2):
            left = level[index]
            right = level[index + 1] if index + 1 < len(level) else left
            next_level.append(str(compute_hash_object([left, right], config=cfg)))
        level = next_level
    return level[0]


def verify_checksum(path: PathLike, expected: Union[str, bytes], *, config: Optional[HashConfig] = None) -> bool:
    """Verifica checksum de arquivo."""
    actual = compute_hash_file(path, config=config)
    return constant_time_compare(actual, expected)


def random_salt(size: int = 32, *, encoding: DigestEncoding = DigestEncoding.BASE64URL) -> Union[str, bytes]:
    """Gera salt criptograficamente seguro."""
    if size <= 0:
        raise ValueError("salt size must be positive")
    data = secrets.token_bytes(size)
    return encode_digest(data, encoding)


def derive_key_from_secret(secret: Union[str, bytes], *, context: str = "data-platform", config: Optional[HashConfig] = None) -> bytes:
    """Deriva chave estável simples a partir de segredo e contexto.

    Para senhas humanas, prefira KDFs dedicados como PBKDF2/Argon2/bcrypt.
    Esta função é adequada para derivação interna de chave de assinatura a
    partir de segredo já forte.
    """
    raw = secret.encode("utf-8") if isinstance(secret, str) else secret
    digest = compute_hmac(context.encode("utf-8"), raw, config=config or HashConfig(encoding=DigestEncoding.BYTES))
    assert isinstance(digest, bytes)
    return digest


# =============================================================================
# Safe JSON helpers
# =============================================================================

def safe_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): safe_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [safe_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


# =============================================================================
# Compatibility aliases
# =============================================================================

sha256_bytes = lambda data: compute_hash_bytes(data, config=HashConfig(algorithm=HashAlgorithm.SHA256, encoding=DigestEncoding.HEX))
sha256_text = lambda text: compute_hash_text(text, config=HashConfig(algorithm=HashAlgorithm.SHA256, encoding=DigestEncoding.HEX))
sha256_file = lambda path: compute_hash_file(path, config=HashConfig(algorithm=HashAlgorithm.SHA256, encoding=DigestEncoding.HEX))


__all__ = [
    "DigestEncoding",
    "HashAlgorithm",
    "HashConfig",
    "HashPurpose",
    "HashResult",
    "HashingError",
    "HashingService",
    "Signature",
    "SignatureVerificationError",
    "UnsupportedHashAlgorithmError",
    "cache_key",
    "canonical_dumps",
    "compute_hash_bytes",
    "compute_hash_file",
    "compute_hash_object",
    "compute_hash_stream",
    "compute_hash_text",
    "compute_hmac",
    "compute_hmac_text",
    "constant_time_compare",
    "derive_key_from_secret",
    "encode_digest",
    "fingerprint",
    "get_hash_constructor",
    "merkle_root",
    "new_hash",
    "random_salt",
    "record_hash",
    "records_hash",
    "safe_json_value",
    "sha256_bytes",
    "sha256_file",
    "sha256_text",
    "verify_checksum",
]

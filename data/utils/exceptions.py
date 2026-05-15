"""
data/utils/exceptions.py

Enterprise-grade exception hierarchy and error utilities.

Este módulo centraliza exceções padronizadas para a plataforma de dados,
incluindo ingestão, validação, IA, storage, segurança, configuração,
observabilidade, integração e pipelines.

Capacidades principais:
- Hierarquia rica de exceções com error_code, severity, retryable e contexto.
- Serialização segura para JSON/logs/auditoria/APIs.
- Agregação de múltiplos erros.
- Helpers para wrapping, redaction e criação consistente de erros.
- Suporte a causa original sem vazamento acidental de segredos.
- Sem dependências externas obrigatórias.
"""

from __future__ import annotations

import json
import math
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Type


JsonDict = Dict[str, Any]


class ErrorSeverity(str, Enum):
    """Severidade de erro padronizada."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    FATAL = "FATAL"


class ErrorCategory(str, Enum):
    """Categoria funcional do erro."""

    CONFIGURATION = "CONFIGURATION"
    VALIDATION = "VALIDATION"
    INGESTION = "INGESTION"
    TRANSFORMATION = "TRANSFORMATION"
    SERIALIZATION = "SERIALIZATION"
    DESERIALIZATION = "DESERIALIZATION"
    STORAGE = "STORAGE"
    DATABASE = "DATABASE"
    NETWORK = "NETWORK"
    SECURITY = "SECURITY"
    PRIVACY = "PRIVACY"
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    OBSERVABILITY = "OBSERVABILITY"
    AI = "AI"
    RAG = "RAG"
    PIPELINE = "PIPELINE"
    CONCURRENCY = "CONCURRENCY"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    EXTERNAL_SERVICE = "EXTERNAL_SERVICE"
    UNKNOWN = "UNKNOWN"


class ErrorCode(str, Enum):
    """Códigos de erro enterprise."""

    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    MISSING_CONFIGURATION = "MISSING_CONFIGURATION"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    SCHEMA_VALIDATION_ERROR = "SCHEMA_VALIDATION_ERROR"
    QUALITY_VALIDATION_ERROR = "QUALITY_VALIDATION_ERROR"
    INTEGRITY_VALIDATION_ERROR = "INTEGRITY_VALIDATION_ERROR"
    PII_VALIDATION_ERROR = "PII_VALIDATION_ERROR"
    COMPLIANCE_ERROR = "COMPLIANCE_ERROR"
    INGESTION_ERROR = "INGESTION_ERROR"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    RECORD_PARSE_ERROR = "RECORD_PARSE_ERROR"
    TRANSFORMATION_ERROR = "TRANSFORMATION_ERROR"
    SERIALIZATION_ERROR = "SERIALIZATION_ERROR"
    DESERIALIZATION_ERROR = "DESERIALIZATION_ERROR"
    STORAGE_ERROR = "STORAGE_ERROR"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSAFE_PATH = "UNSAFE_PATH"
    DATABASE_ERROR = "DATABASE_ERROR"
    QUERY_ERROR = "QUERY_ERROR"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    CIRCUIT_BREAKER_OPEN = "CIRCUIT_BREAKER_OPEN"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"
    SECURITY_ERROR = "SECURITY_ERROR"
    SECRET_LEAK_PREVENTED = "SECRET_LEAK_PREVENTED"
    PRIVACY_ERROR = "PRIVACY_ERROR"
    PII_DETECTED = "PII_DETECTED"
    AI_ERROR = "AI_ERROR"
    RAG_ERROR = "RAG_ERROR"
    MODEL_ERROR = "MODEL_ERROR"
    EMBEDDING_ERROR = "EMBEDDING_ERROR"
    PIPELINE_ERROR = "PIPELINE_ERROR"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    CONCURRENCY_ERROR = "CONCURRENCY_ERROR"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


SECRET_KEYS = {
    "password",
    "passwd",
    "pwd",
    "senha",
    "secret",
    "secret_key",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "private_key",
    "credential",
    "credentials",
}


@dataclass(frozen=True)
class ErrorContext:
    """Contexto seguro associado a uma exceção."""

    operation: Optional[str] = None
    component: Optional[str] = None
    dataset_name: Optional[str] = None
    pipeline_name: Optional[str] = None
    run_id: Optional[str] = None
    correlation_id: Optional[str] = None
    tenant_id: Optional[str] = None
    source_system: Optional[str] = None
    resource: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return sanitize_payload(
            {
                "operation": self.operation,
                "component": self.component,
                "dataset_name": self.dataset_name,
                "pipeline_name": self.pipeline_name,
                "run_id": self.run_id,
                "correlation_id": self.correlation_id,
                "tenant_id": self.tenant_id,
                "source_system": self.source_system,
                "resource": self.resource,
                "metadata": dict(self.metadata),
            }
        )


class EnterpriseError(Exception):
    """Exceção base enterprise com metadados, severidade e contexto."""

    error_code: ErrorCode = ErrorCode.UNKNOWN_ERROR
    category: ErrorCategory = ErrorCategory.UNKNOWN
    severity: ErrorSeverity = ErrorSeverity.ERROR
    retryable: bool = False
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        error_code: Optional[ErrorCode] = None,
        category: Optional[ErrorCategory] = None,
        severity: Optional[ErrorSeverity] = None,
        retryable: Optional[bool] = None,
        context: Optional[ErrorContext] = None,
        details: Optional[Mapping[str, Any]] = None,
        cause: Optional[BaseException] = None,
        error_id: Optional[str] = None,
        http_status: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self.error_code
        self.category = category or self.category
        self.severity = severity or self.severity
        self.retryable = self.retryable if retryable is None else retryable
        self.context = context or ErrorContext()
        self.details = dict(details or {})
        self.cause = cause
        self.error_id = error_id or str(uuid.uuid4())
        self.occurred_at = datetime.now(timezone.utc)
        self.http_status = http_status or self.http_status
        if cause is not None:
            self.__cause__ = cause

    def to_dict(self, *, include_traceback: bool = False, include_cause: bool = True) -> JsonDict:
        payload: JsonDict = {
            "error_id": self.error_id,
            "error_code": self.error_code.value,
            "category": self.category.value,
            "severity": self.severity.value,
            "message": self.message,
            "retryable": self.retryable,
            "http_status": self.http_status,
            "context": self.context.to_dict(),
            "details": sanitize_payload(self.details),
            "occurred_at": self.occurred_at.isoformat(),
            "type": self.__class__.__name__,
        }
        if include_cause and self.cause is not None:
            payload["cause"] = {
                "type": type(self.cause).__name__,
                "message": redact_text(str(self.cause)),
            }
        if include_traceback:
            payload["traceback"] = traceback.format_exception(type(self), self, self.__traceback__)
        return payload

    def to_json(self, *, indent: int = 2, include_traceback: bool = False) -> str:
        return json.dumps(self.to_dict(include_traceback=include_traceback), ensure_ascii=False, indent=indent, default=str)

    def public_message(self) -> str:
        return f"{self.error_code.value}: {self.message}"

    def __str__(self) -> str:
        return self.public_message()


class ConfigurationError(EnterpriseError):
    error_code = ErrorCode.CONFIGURATION_ERROR
    category = ErrorCategory.CONFIGURATION
    severity = ErrorSeverity.ERROR
    retryable = False
    http_status = 500


class MissingConfigurationError(ConfigurationError):
    error_code = ErrorCode.MISSING_CONFIGURATION


class InvalidConfigurationError(ConfigurationError):
    error_code = ErrorCode.INVALID_CONFIGURATION


class ValidationError(EnterpriseError):
    error_code = ErrorCode.VALIDATION_ERROR
    category = ErrorCategory.VALIDATION
    severity = ErrorSeverity.ERROR
    retryable = False
    http_status = 422


class SchemaValidationError(ValidationError):
    error_code = ErrorCode.SCHEMA_VALIDATION_ERROR


class QualityValidationError(ValidationError):
    error_code = ErrorCode.QUALITY_VALIDATION_ERROR


class IntegrityValidationError(ValidationError):
    error_code = ErrorCode.INTEGRITY_VALIDATION_ERROR


class PIIValidationError(ValidationError):
    error_code = ErrorCode.PII_VALIDATION_ERROR
    category = ErrorCategory.PRIVACY
    severity = ErrorSeverity.CRITICAL


class ComplianceError(EnterpriseError):
    error_code = ErrorCode.COMPLIANCE_ERROR
    category = ErrorCategory.SECURITY
    severity = ErrorSeverity.ERROR
    retryable = False
    http_status = 403


class IngestionError(EnterpriseError):
    error_code = ErrorCode.INGESTION_ERROR
    category = ErrorCategory.INGESTION
    severity = ErrorSeverity.ERROR
    retryable = True
    http_status = 500


class SourceUnavailableError(IngestionError):
    error_code = ErrorCode.SOURCE_UNAVAILABLE
    retryable = True
    http_status = 503


class RecordParseError(IngestionError):
    error_code = ErrorCode.RECORD_PARSE_ERROR
    retryable = False
    http_status = 400


class TransformationError(EnterpriseError):
    error_code = ErrorCode.TRANSFORMATION_ERROR
    category = ErrorCategory.TRANSFORMATION
    severity = ErrorSeverity.ERROR
    retryable = False
    http_status = 500


class SerializationError(EnterpriseError):
    error_code = ErrorCode.SERIALIZATION_ERROR
    category = ErrorCategory.SERIALIZATION
    retryable = False
    http_status = 500


class DeserializationError(EnterpriseError):
    error_code = ErrorCode.DESERIALIZATION_ERROR
    category = ErrorCategory.DESERIALIZATION
    retryable = False
    http_status = 400


class StorageError(EnterpriseError):
    error_code = ErrorCode.STORAGE_ERROR
    category = ErrorCategory.STORAGE
    retryable = True
    http_status = 500


class FileNotFoundEnterpriseError(StorageError):
    error_code = ErrorCode.FILE_NOT_FOUND
    retryable = False
    http_status = 404


class FileTooLargeError(StorageError):
    error_code = ErrorCode.FILE_TOO_LARGE
    category = ErrorCategory.SECURITY
    retryable = False
    http_status = 413


class UnsafePathError(StorageError):
    error_code = ErrorCode.UNSAFE_PATH
    category = ErrorCategory.SECURITY
    severity = ErrorSeverity.CRITICAL
    retryable = False
    http_status = 400


class DatabaseError(EnterpriseError):
    error_code = ErrorCode.DATABASE_ERROR
    category = ErrorCategory.DATABASE
    retryable = True
    http_status = 500


class QueryError(DatabaseError):
    error_code = ErrorCode.QUERY_ERROR
    retryable = False


class ConnectionErrorEnterprise(EnterpriseError):
    error_code = ErrorCode.CONNECTION_ERROR
    category = ErrorCategory.NETWORK
    retryable = True
    http_status = 503


class NetworkError(EnterpriseError):
    error_code = ErrorCode.NETWORK_ERROR
    category = ErrorCategory.NETWORK
    retryable = True
    http_status = 503


class TimeoutEnterpriseError(EnterpriseError):
    error_code = ErrorCode.TIMEOUT_ERROR
    category = ErrorCategory.TIMEOUT
    retryable = True
    http_status = 504


class RateLimitExceededError(EnterpriseError):
    error_code = ErrorCode.RATE_LIMIT_EXCEEDED
    category = ErrorCategory.RATE_LIMIT
    severity = ErrorSeverity.WARNING
    retryable = True
    http_status = 429


class CircuitBreakerOpenError(EnterpriseError):
    error_code = ErrorCode.CIRCUIT_BREAKER_OPEN
    category = ErrorCategory.EXTERNAL_SERVICE
    severity = ErrorSeverity.WARNING
    retryable = True
    http_status = 503


class AuthenticationError(EnterpriseError):
    error_code = ErrorCode.AUTHENTICATION_ERROR
    category = ErrorCategory.AUTHENTICATION
    severity = ErrorSeverity.ERROR
    retryable = False
    http_status = 401


class AuthorizationError(EnterpriseError):
    error_code = ErrorCode.AUTHORIZATION_ERROR
    category = ErrorCategory.AUTHORIZATION
    severity = ErrorSeverity.ERROR
    retryable = False
    http_status = 403


class SecurityError(EnterpriseError):
    error_code = ErrorCode.SECURITY_ERROR
    category = ErrorCategory.SECURITY
    severity = ErrorSeverity.CRITICAL
    retryable = False
    http_status = 403


class SecretLeakPreventedError(SecurityError):
    error_code = ErrorCode.SECRET_LEAK_PREVENTED


class PrivacyError(EnterpriseError):
    error_code = ErrorCode.PRIVACY_ERROR
    category = ErrorCategory.PRIVACY
    severity = ErrorSeverity.CRITICAL
    retryable = False
    http_status = 403


class PIIDetectedError(PrivacyError):
    error_code = ErrorCode.PII_DETECTED


class AIError(EnterpriseError):
    error_code = ErrorCode.AI_ERROR
    category = ErrorCategory.AI
    retryable = True
    http_status = 500


class RAGError(AIError):
    error_code = ErrorCode.RAG_ERROR
    category = ErrorCategory.RAG


class ModelError(AIError):
    error_code = ErrorCode.MODEL_ERROR


class EmbeddingError(AIError):
    error_code = ErrorCode.EMBEDDING_ERROR


class PipelineError(EnterpriseError):
    error_code = ErrorCode.PIPELINE_ERROR
    category = ErrorCategory.PIPELINE
    severity = ErrorSeverity.ERROR
    retryable = False
    http_status = 500


class DependencyError(EnterpriseError):
    error_code = ErrorCode.DEPENDENCY_ERROR
    category = ErrorCategory.PIPELINE
    retryable = False


class ConcurrencyError(EnterpriseError):
    error_code = ErrorCode.CONCURRENCY_ERROR
    category = ErrorCategory.CONCURRENCY
    retryable = True


class NotImplementedEnterpriseError(EnterpriseError):
    error_code = ErrorCode.NOT_IMPLEMENTED
    severity = ErrorSeverity.WARNING
    retryable = False
    http_status = 501


@dataclass(frozen=True)
class ErrorSummary:
    """Resumo de múltiplos erros."""

    total: int
    by_code: Mapping[str, int]
    by_category: Mapping[str, int]
    by_severity: Mapping[str, int]
    retryable_count: int
    critical_count: int

    def to_dict(self) -> JsonDict:
        return {
            "total": self.total,
            "by_code": dict(self.by_code),
            "by_category": dict(self.by_category),
            "by_severity": dict(self.by_severity),
            "retryable_count": self.retryable_count,
            "critical_count": self.critical_count,
        }


class ErrorGroup(EnterpriseError):
    """Agrega múltiplas exceções enterprise."""

    error_code = ErrorCode.UNKNOWN_ERROR
    category = ErrorCategory.UNKNOWN
    severity = ErrorSeverity.ERROR
    retryable = False
    http_status = 500

    def __init__(
        self,
        message: str,
        errors: Sequence[BaseException],
        *,
        context: Optional[ErrorContext] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.errors = tuple(errors)
        super().__init__(message, context=context, details=details)
        self.severity = max_error_severity(self.errors)
        self.retryable = all(is_retryable_error(error) for error in self.errors) if self.errors else False

    def summary(self) -> ErrorSummary:
        enterprise_errors = [to_enterprise_error(error) for error in self.errors]
        return ErrorSummary(
            total=len(enterprise_errors),
            by_code=count_by(lambda err: err.error_code.value, enterprise_errors),
            by_category=count_by(lambda err: err.category.value, enterprise_errors),
            by_severity=count_by(lambda err: err.severity.value, enterprise_errors),
            retryable_count=sum(1 for err in enterprise_errors if err.retryable),
            critical_count=sum(1 for err in enterprise_errors if err.severity in {ErrorSeverity.CRITICAL, ErrorSeverity.FATAL}),
        )

    def to_dict(self, *, include_traceback: bool = False, include_cause: bool = True) -> JsonDict:
        payload = super().to_dict(include_traceback=include_traceback, include_cause=include_cause)
        payload["summary"] = self.summary().to_dict()
        payload["errors"] = [to_error_dict(error, include_traceback=include_traceback) for error in self.errors]
        return payload


def to_enterprise_error(error: BaseException) -> EnterpriseError:
    """Converte qualquer exceção para EnterpriseError."""
    if isinstance(error, EnterpriseError):
        return error
    return EnterpriseError(
        str(error),
        error_code=ErrorCode.UNKNOWN_ERROR,
        category=ErrorCategory.UNKNOWN,
        severity=ErrorSeverity.ERROR,
        retryable=False,
        cause=error,
    )


def wrap_error(
    error: BaseException,
    *,
    message: Optional[str] = None,
    error_cls: Type[EnterpriseError] = EnterpriseError,
    context: Optional[ErrorContext] = None,
    details: Optional[Mapping[str, Any]] = None,
) -> EnterpriseError:
    """Encapsula exceção em uma exceção enterprise."""
    if isinstance(error, error_cls) and context is None and details is None and message is None:
        return error
    return error_cls(
        message or str(error),
        context=context,
        details=details,
        cause=error,
    )


def raise_with_context(
    error: BaseException,
    *,
    context: ErrorContext,
    error_cls: Type[EnterpriseError] = EnterpriseError,
    message: Optional[str] = None,
    details: Optional[Mapping[str, Any]] = None,
) -> None:
    """Lança exceção enterprise com contexto preservando causa."""
    raise wrap_error(error, message=message, error_cls=error_cls, context=context, details=details) from error


def to_error_dict(error: BaseException, *, include_traceback: bool = False) -> JsonDict:
    """Serializa exceção em dicionário seguro."""
    enterprise_error = to_enterprise_error(error)
    return enterprise_error.to_dict(include_traceback=include_traceback)


def is_retryable_error(error: BaseException) -> bool:
    """Indica se erro é retryable."""
    if isinstance(error, EnterpriseError):
        return error.retryable
    return isinstance(error, (TimeoutError, ConnectionError, OSError))


def max_error_severity(errors: Iterable[BaseException]) -> ErrorSeverity:
    """Retorna maior severidade entre erros."""
    order = {
        ErrorSeverity.DEBUG: 0,
        ErrorSeverity.INFO: 1,
        ErrorSeverity.WARNING: 2,
        ErrorSeverity.ERROR: 3,
        ErrorSeverity.CRITICAL: 4,
        ErrorSeverity.FATAL: 5,
    }
    severities = [to_enterprise_error(error).severity for error in errors]
    return max(severities, key=lambda item: order[item]) if severities else ErrorSeverity.ERROR


def count_by(func: Any, values: Iterable[Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        key = str(func(value))
        counts[key] = counts.get(key, 0) + 1
    return counts


def sanitize_payload(payload: Mapping[str, Any]) -> JsonDict:
    """Sanitiza payload para evitar vazamento de segredo."""
    return safe_json_value(_sanitize_value(payload))


def _sanitize_value(value: Any, key: Optional[str] = None) -> Any:
    if key is not None and key.lower() in SECRET_KEYS:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(k): _sanitize_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(value: str) -> str:
    """Mascara padrões comuns de segredos em texto."""
    text = value
    for key in SECRET_KEYS:
        text = __import__("re").sub(
            rf"({key}\s*[=:]\s*)([^\s,;]+)",
            rf"\1[REDACTED]",
            text,
            flags=__import__("re").IGNORECASE,
        )
    return text


def safe_json_value(value: Any) -> Any:
    """Converte valor arbitrário para estrutura JSON-safe."""
    if isinstance(value, Mapping):
        return {str(key): safe_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [safe_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def error_response(error: BaseException, *, include_traceback: bool = False) -> JsonDict:
    """Cria payload de resposta de erro para APIs/CLI."""
    enterprise_error = to_enterprise_error(error)
    return {
        "success": False,
        "error": enterprise_error.to_dict(include_traceback=include_traceback),
    }


def ensure(condition: bool, message: str, *, error_cls: Type[EnterpriseError] = ValidationError, **kwargs: Any) -> None:
    """Lança erro se condição for falsa."""
    if not condition:
        raise error_cls(message, **kwargs)


def fail(message: str, *, error_cls: Type[EnterpriseError] = EnterpriseError, **kwargs: Any) -> None:
    """Lança erro enterprise diretamente."""
    raise error_cls(message, **kwargs)


__all__ = [
    "AIError",
    "AuthenticationError",
    "AuthorizationError",
    "CircuitBreakerOpenError",
    "ComplianceError",
    "ConcurrencyError",
    "ConfigurationError",
    "ConnectionErrorEnterprise",
    "DatabaseError",
    "DependencyError",
    "DeserializationError",
    "EmbeddingError",
    "EnterpriseError",
    "ErrorCategory",
    "ErrorCode",
    "ErrorContext",
    "ErrorGroup",
    "ErrorSeverity",
    "ErrorSummary",
    "FileNotFoundEnterpriseError",
    "FileTooLargeError",
    "IngestionError",
    "IntegrityValidationError",
    "InvalidConfigurationError",
    "MissingConfigurationError",
    "ModelError",
    "NetworkError",
    "NotImplementedEnterpriseError",
    "PIIDetectedError",
    "PIIValidationError",
    "PipelineError",
    "PrivacyError",
    "QualityValidationError",
    "QueryError",
    "RAGError",
    "RateLimitExceededError",
    "RecordParseError",
    "SchemaValidationError",
    "SecretLeakPreventedError",
    "SecurityError",
    "SerializationError",
    "SourceUnavailableError",
    "StorageError",
    "TimeoutEnterpriseError",
    "TransformationError",
    "UnsafePathError",
    "ValidationError",
    "count_by",
    "ensure",
    "error_response",
    "fail",
    "is_retryable_error",
    "max_error_severity",
    "raise_with_context",
    "redact_text",
    "safe_json_value",
    "sanitize_payload",
    "to_enterprise_error",
    "to_error_dict",
    "wrap_error",
]

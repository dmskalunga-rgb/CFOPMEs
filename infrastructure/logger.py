# kwanza-ai-core/infrastructure/logger.py
from __future__ import annotations

import contextvars
import dataclasses
import json
import logging
import logging.handlers
import os
import pathlib
import re
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Optional, Protocol


class LogFormat(str, Enum):
    TEXT = "text"
    JSON = "json"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class LoggerConfig:
    service_name: str = "kwanza-ai-core"
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    instance_id: str = field(default_factory=lambda: os.getenv("INSTANCE_ID", str(uuid.uuid4())))
    level: LogLevel = LogLevel.INFO
    log_format: LogFormat = LogFormat.JSON

    enable_console: bool = True
    enable_file: bool = False
    file_path: pathlib.Path = pathlib.Path("./logs/kwanza-ai-core.log")
    file_max_bytes: int = 50 * 1024 * 1024
    file_backup_count: int = 10

    enable_audit_file: bool = True
    audit_file_path: pathlib.Path = pathlib.Path("./logs/audit.log")

    include_stacktrace: bool = True
    include_process_info: bool = True
    include_thread_info: bool = True

    redact_fields: tuple[str, ...] = (
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "access_token",
        "refresh_token",
        "private_key",
        "client_secret",
        "db_password",
        "redis_url",
    )


class MetricsSink(Protocol):
    def increment(
        self,
        name: str,
        value: float = 1.0,
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


request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_id",
    default=None,
)

correlation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "correlation_id",
    default=None,
)

tenant_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "tenant_id",
    default=None,
)

user_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "user_id",
    default=None,
)

operation_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "operation",
    default=None,
)


class LoggerSetupError(RuntimeError):
    pass


class SensitiveDataRedactor:
    def __init__(self, fields: Iterable[str]) -> None:
        self.fields = {field.lower() for field in fields}

    def redact(self, value: Any) -> Any:
        if dataclasses.is_dataclass(value):
            value = dataclasses.asdict(value)

        if isinstance(value, Mapping):
            result: Dict[str, Any] = {}

            for key, item in value.items():
                key_text = str(key)
                lowered = key_text.lower()

                if self._is_sensitive_key(lowered):
                    result[key_text] = "***"
                else:
                    result[key_text] = self.redact(item)

            return result

        if isinstance(value, list):
            return [self.redact(item) for item in value]

        if isinstance(value, tuple):
            return tuple(self.redact(item) for item in value)

        if isinstance(value, str):
            return self._redact_inline_secrets(value)

        return value

    def _is_sensitive_key(self, key: str) -> bool:
        return key in self.fields or any(field in key for field in self.fields)

    def _redact_inline_secrets(self, value: str) -> str:
        patterns = [
            r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._\-]+",
            r"(?i)(api[_-]?key\s*[=:]\s*)[A-Za-z0-9._\-]+",
            r"(?i)(password\s*[=:]\s*)[^&\s]+",
            r"(?i)(token\s*[=:]\s*)[A-Za-z0-9._\-]+",
        ]

        cleaned = value

        for pattern in patterns:
            cleaned = re.sub(pattern, r"\1***", cleaned)

        return cleaned


class JsonLogFormatter(logging.Formatter):
    def __init__(self, config: LoggerConfig) -> None:
        super().__init__()
        self.config = config
        self.redactor = SensitiveDataRedactor(config.redact_fields)

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": self.redactor.redact(record.getMessage()),
            "service": self.config.service_name,
            "environment": self.config.environment,
            "instance_id": self.config.instance_id,
            "request_id": request_id_var.get(),
            "correlation_id": correlation_id_var.get(),
            "tenant_id": tenant_id_var.get(),
            "user_id": user_id_var.get(),
            "operation": operation_var.get(),
        }

        if self.config.include_process_info:
            payload["process"] = {
                "pid": record.process,
                "process_name": record.processName,
            }

        if self.config.include_thread_info:
            payload["thread"] = {
                "thread_id": record.thread,
                "thread_name": record.threadName,
            }

        extra = self._extract_extra(record)
        if extra:
            payload["extra"] = self.redactor.redact(extra)

        if record.exc_info and self.config.include_stacktrace:
            payload["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]),
                "stacktrace": self.formatException(record.exc_info),
            }

        return json.dumps(payload, ensure_ascii=False, default=str)

    def _extract_extra(self, record: logging.LogRecord) -> Dict[str, Any]:
        reserved = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys())
        reserved.update({"message", "asctime"})

        return {
            key: value
            for key, value in record.__dict__.items()
            if key not in reserved and not key.startswith("_")
        }


class TextLogFormatter(logging.Formatter):
    def __init__(self, config: LoggerConfig) -> None:
        super().__init__(
            fmt=(
                "%(asctime)s | %(levelname)s | %(name)s | "
                "request_id=%(request_id)s | correlation_id=%(correlation_id)s | "
                "%(message)s"
            )
        )
        self.config = config
        self.redactor = SensitiveDataRedactor(config.redact_fields)

    def format(self, record: logging.LogRecord) -> str:
        record.request_id = request_id_var.get() or "-"
        record.correlation_id = correlation_id_var.get() or "-"
        record.msg = self.redactor.redact(str(record.msg))
        return super().format(record)


class MetricsLogHandler(logging.Handler):
    def __init__(self, metrics: MetricsSink) -> None:
        super().__init__()
        self.metrics = metrics

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.metrics.increment(
                "logger.records",
                tags={
                    "level": record.levelname.lower(),
                    "logger": record.name,
                },
            )
        except Exception:
            return None


class LoggingManager:
    def __init__(
        self,
        config: Optional[LoggerConfig] = None,
        metrics: Optional[MetricsSink] = None,
    ) -> None:
        self.config = config or LoggerConfig()
        self.metrics = metrics or NoopMetricsSink()

    def setup(self) -> None:
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(self.config.level.value)

        handlers = self._build_handlers()

        for handler in handlers:
            root.addHandler(handler)

        logging.captureWarnings(True)

    def get_logger(self, name: str) -> logging.Logger:
        return logging.getLogger(name)

    def audit_logger(self) -> logging.Logger:
        return logging.getLogger("kwanza.audit")

    def _build_handlers(self) -> list[logging.Handler]:
        handlers: list[logging.Handler] = []

        formatter = self._formatter()

        if self.config.enable_console:
            console = logging.StreamHandler(sys.stdout)
            console.setLevel(self.config.level.value)
            console.setFormatter(formatter)
            handlers.append(console)

        if self.config.enable_file:
            self.config.file_path.parent.mkdir(parents=True, exist_ok=True)

            file_handler = logging.handlers.RotatingFileHandler(
                filename=self.config.file_path,
                maxBytes=self.config.file_max_bytes,
                backupCount=self.config.file_backup_count,
                encoding="utf-8",
            )
            file_handler.setLevel(self.config.level.value)
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)

        if self.config.enable_audit_file:
            self.config.audit_file_path.parent.mkdir(parents=True, exist_ok=True)

            audit_handler = logging.handlers.RotatingFileHandler(
                filename=self.config.audit_file_path,
                maxBytes=self.config.file_max_bytes,
                backupCount=self.config.file_backup_count,
                encoding="utf-8",
            )
            audit_handler.setLevel(logging.INFO)
            audit_handler.setFormatter(formatter)

            audit_logger = logging.getLogger("kwanza.audit")
            audit_logger.handlers.clear()
            audit_logger.addHandler(audit_handler)
            audit_logger.propagate = False
            audit_logger.setLevel(logging.INFO)

        handlers.append(MetricsLogHandler(self.metrics))
        return handlers

    def _formatter(self) -> logging.Formatter:
        if self.config.log_format == LogFormat.JSON:
            return JsonLogFormatter(self.config)

        return TextLogFormatter(self.config)


@dataclass(frozen=True)
class LogContext:
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    operation: Optional[str] = None


class log_context:
    def __init__(
        self,
        *,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        operation: Optional[str] = None,
    ) -> None:
        self.context = LogContext(
            request_id=request_id,
            correlation_id=correlation_id,
            tenant_id=tenant_id,
            user_id=user_id,
            operation=operation,
        )
        self.tokens: list[tuple[contextvars.ContextVar[Any], contextvars.Token[Any]]] = []

    def __enter__(self) -> "log_context":
        if self.context.request_id is not None:
            self.tokens.append((request_id_var, request_id_var.set(self.context.request_id)))

        if self.context.correlation_id is not None:
            self.tokens.append((correlation_id_var, correlation_id_var.set(self.context.correlation_id)))

        if self.context.tenant_id is not None:
            self.tokens.append((tenant_id_var, tenant_id_var.set(self.context.tenant_id)))

        if self.context.user_id is not None:
            self.tokens.append((user_id_var, user_id_var.set(self.context.user_id)))

        if self.context.operation is not None:
            self.tokens.append((operation_var, operation_var.set(self.context.operation)))

        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for variable, token in reversed(self.tokens):
            variable.reset(token)


class OperationLogger:
    def __init__(
        self,
        logger: logging.Logger,
        operation: str,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.logger = logger
        self.operation = operation
        self.metadata = dict(metadata or {})
        self.started = 0.0

    def __enter__(self) -> "OperationLogger":
        self.started = time.monotonic()

        self.logger.info(
            "Operation started",
            extra={
                "operation_name": self.operation,
                "metadata": self.metadata,
            },
        )

        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        elapsed_ms = round((time.monotonic() - self.started) * 1000, 2)

        if exc is None:
            self.logger.info(
                "Operation completed",
                extra={
                    "operation_name": self.operation,
                    "elapsed_ms": elapsed_ms,
                },
            )
        else:
            self.logger.exception(
                "Operation failed",
                extra={
                    "operation_name": self.operation,
                    "elapsed_ms": elapsed_ms,
                    "error": repr(exc),
                },
            )


class AuditLogger:
    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger("kwanza.audit")

    def record(
        self,
        action: str,
        *,
        actor_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        resource: Optional[str] = None,
        resource_id: Optional[str] = None,
        outcome: str = "success",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.logger.info(
            "Audit event",
            extra={
                "audit": {
                    "event_id": str(uuid.uuid4()),
                    "action": action,
                    "actor_id": actor_id or user_id_var.get(),
                    "tenant_id": tenant_id or tenant_id_var.get(),
                    "resource": resource,
                    "resource_id": resource_id,
                    "outcome": outcome,
                    "metadata": dict(metadata or {}),
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def configure_logging(
    config: Optional[LoggerConfig] = None,
    metrics: Optional[MetricsSink] = None,
) -> LoggingManager:
    manager = LoggingManager(config=config, metrics=metrics)
    manager.setup()
    return manager


def build_logger_config_from_env() -> LoggerConfig:
    return LoggerConfig(
        service_name=os.getenv("LOG_SERVICE_NAME", os.getenv("APP_NAME", "kwanza-ai-core")),
        environment=os.getenv("APP_ENV", "development"),
        instance_id=os.getenv("INSTANCE_ID", str(uuid.uuid4())),
        level=LogLevel(os.getenv("LOG_LEVEL", "INFO").upper()),
        log_format=LogFormat(os.getenv("LOG_FORMAT", "json").lower()),
        enable_console=os.getenv("LOG_ENABLE_CONSOLE", "true").lower() == "true",
        enable_file=os.getenv("LOG_ENABLE_FILE", "false").lower() == "true",
        file_path=pathlib.Path(os.getenv("LOG_FILE_PATH", "./logs/kwanza-ai-core.log")),
        file_max_bytes=int(os.getenv("LOG_FILE_MAX_BYTES", str(50 * 1024 * 1024))),
        file_backup_count=int(os.getenv("LOG_FILE_BACKUP_COUNT", "10")),
        enable_audit_file=os.getenv("LOG_ENABLE_AUDIT_FILE", "true").lower() == "true",
        audit_file_path=pathlib.Path(os.getenv("LOG_AUDIT_FILE_PATH", "./logs/audit.log")),
        include_stacktrace=os.getenv("LOG_INCLUDE_STACKTRACE", "true").lower() == "true",
        include_process_info=os.getenv("LOG_INCLUDE_PROCESS_INFO", "true").lower() == "true",
        include_thread_info=os.getenv("LOG_INCLUDE_THREAD_INFO", "true").lower() == "true",
    )


def configure_logging_from_env() -> LoggingManager:
    return configure_logging(build_logger_config_from_env())


def current_log_context() -> Dict[str, Optional[str]]:
    return {
        "request_id": request_id_var.get(),
        "correlation_id": correlation_id_var.get(),
        "tenant_id": tenant_id_var.get(),
        "user_id": user_id_var.get(),
        "operation": operation_var.get(),
    }


def new_request_id() -> str:
    return str(uuid.uuid4())


def new_correlation_id() -> str:
    return str(uuid.uuid4())


def exception_to_dict(exc: BaseException) -> Dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exception(type(exc), exc, exc.__traceback__),
    }


configure_logging_from_env()
#!/usr/bin/env python3
"""
core/config/logging.py

Enterprise-grade logging configuration.

Objetivo:
- Centralizar configuração de logging para API, workers, jobs, modelos e serviços.
- Suportar logs JSON estruturados, logs human-readable, request-id/correlation-id,
  contexto por thread/async, redaction de dados sensíveis, rotação de arquivo e configuração por ambiente.
- Evitar vazamento de tokens, senhas, API keys, cookies e secrets.

Variáveis de ambiente:
    LOG_LEVEL=INFO
    LOG_FORMAT=json              # json | text
    LOG_FILE=                    # opcional: /var/log/app/app.log
    LOG_ROTATION_BYTES=10485760
    LOG_BACKUP_COUNT=5
    LOG_SERVICE_NAME=enterprise-ai
    LOG_ENVIRONMENT=development
    LOG_REDACT=true
    LOG_INCLUDE_SOURCE=false

Uso:
    from core.config.logging import configure_logging, get_logger, bind_log_context

    configure_logging()
    logger = get_logger(__name__)
    bind_log_context(request_id="req_123", tenant_id="tenant_a")
    logger.info("process_started", extra={"operation": "score"})
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.config
import os
import re
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional


LOGGING_CONFIG_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc

_log_context: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar("log_context", default={})
_configured = False


SENSITIVE_KEY_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"password",
        r"passwd",
        r"pwd",
        r"secret",
        r"token",
        r"api[_-]?key",
        r"apikey",
        r"authorization",
        r"cookie",
        r"set-cookie",
        r"jwt",
        r"bearer",
        r"client[_-]?secret",
        r"private[_-]?key",
        r"connection[_-]?string",
        r"dsn",
    )
]

SENSITIVE_VALUE_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"ApiKey\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"(password|secret|token|api_key|apikey)=([^\s&]+)", re.IGNORECASE),
]


@dataclass(frozen=True)
class LoggingSettings:
    level: str = "INFO"
    log_format: str = "json"
    service_name: str = "enterprise-ai"
    environment: str = "development"
    log_file: Optional[str] = None
    rotation_bytes: int = 10_485_760
    backup_count: int = 5
    redact: bool = True
    include_source: bool = False
    propagate: bool = False

    @staticmethod
    def from_env() -> "LoggingSettings":
        return LoggingSettings(
            level=os.getenv("LOG_LEVEL", "INFO").upper(),
            log_format=os.getenv("LOG_FORMAT", "json").lower(),
            service_name=os.getenv("LOG_SERVICE_NAME", os.getenv("API_NAME", "enterprise-ai")),
            environment=os.getenv("LOG_ENVIRONMENT", os.getenv("API_ENV", "development")),
            log_file=os.getenv("LOG_FILE") or None,
            rotation_bytes=int(os.getenv("LOG_ROTATION_BYTES", "10485760")),
            backup_count=int(os.getenv("LOG_BACKUP_COUNT", "5")),
            redact=os.getenv("LOG_REDACT", "true").lower() in {"1", "true", "yes", "sim"},
            include_source=os.getenv("LOG_INCLUDE_SOURCE", "false").lower() in {"1", "true", "yes", "sim"},
            propagate=os.getenv("LOG_PROPAGATE", "false").lower() in {"1", "true", "yes", "sim"},
        )


class RedactionFilter(logging.Filter):
    def __init__(self, enabled: bool = True) -> None:
        super().__init__()
        self.enabled = enabled

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.enabled:
            return True
        if isinstance(record.msg, str):
            record.msg = redact_value(record.msg)
        if isinstance(record.args, dict):
            record.args = redact_mapping(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(redact_value(item) for item in record.args)
        return True


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = _log_context.get({})
        for key, value in context.items():
            setattr(record, key, value)
        record.request_id = getattr(record, "request_id", context.get("request_id", "-"))
        record.correlation_id = getattr(record, "correlation_id", context.get("correlation_id", "-"))
        record.tenant_id = getattr(record, "tenant_id", context.get("tenant_id", "-"))
        record.user_id = getattr(record, "user_id", context.get("user_id", "-"))
        return True


class JsonFormatter(logging.Formatter):
    RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module", "exc_info",
        "exc_text", "stack_info", "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "message", "asctime",
    }

    def __init__(self, settings: LoggingSettings) -> None:
        super().__init__()
        self.settings = settings

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=DEFAULT_TIMEZONE).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self.settings.service_name,
            "environment": self.settings.environment,
            "request_id": getattr(record, "request_id", "-"),
            "correlation_id": getattr(record, "correlation_id", "-"),
            "tenant_id": getattr(record, "tenant_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
        }
        if self.settings.include_source:
            payload["source"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
                "module": record.module,
            }
        extras = self._extract_extras(record)
        if extras:
            payload["extra"] = redact_mapping(extras) if self.settings.redact else extras
        if record.exc_info:
            payload["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info and record.exc_info[1] else None,
                "traceback": self.formatException(record.exc_info),
            }
        if record.stack_info:
            payload["stack"] = record.stack_info
        if self.settings.redact:
            payload = redact_mapping(payload)
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))

    def _extract_extras(self, record: logging.LogRecord) -> Dict[str, Any]:
        extras: Dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in self.RESERVED:
                continue
            if key in {"request_id", "correlation_id", "tenant_id", "user_id"}:
                continue
            if key.startswith("_"):
                continue
            extras[key] = value
        return extras


class TextFormatter(logging.Formatter):
    def __init__(self, settings: LoggingSettings) -> None:
        fmt = "%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s correlation_id=%(correlation_id)s tenant_id=%(tenant_id)s] %(message)s"
        if settings.include_source:
            fmt += " (%(pathname)s:%(lineno)d)"
        super().__init__(fmt=fmt, datefmt="%Y-%m-%dT%H:%M:%S%z")
        self.settings = settings

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        return redact_value(rendered) if self.settings.redact else rendered


def configure_logging(settings: Optional[LoggingSettings] = None, force: bool = False) -> None:
    global _configured
    if _configured and not force:
        return

    settings = settings or LoggingSettings.from_env()
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(resolve_level(settings.level))
    root.propagate = settings.propagate

    formatter: logging.Formatter
    if settings.log_format == "text":
        formatter = TextFormatter(settings)
    else:
        formatter = JsonFormatter(settings)

    filters = [ContextFilter(), RedactionFilter(settings.redact)]
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(resolve_level(settings.level))
    console_handler.setFormatter(formatter)
    for item in filters:
        console_handler.addFilter(item)
    root.addHandler(console_handler)

    if settings.log_file:
        file_path = Path(settings.log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=str(file_path),
            maxBytes=settings.rotation_bytes,
            backupCount=settings.backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(resolve_level(settings.level))
        file_handler.setFormatter(formatter)
        for item in filters:
            file_handler.addFilter(item)
        root.addHandler(file_handler)

    tune_noisy_loggers()
    _configured = True
    logging.getLogger(__name__).info(
        "logging_configured",
        extra={
            "logging_version": LOGGING_CONFIG_VERSION,
            "log_format": settings.log_format,
            "service_name": settings.service_name,
            "environment": settings.environment,
            "log_file_enabled": bool(settings.log_file),
        },
    )


def get_logger(name: str) -> logging.Logger:
    if not _configured:
        configure_logging()
    return logging.getLogger(name)


def bind_log_context(**kwargs: Any) -> Dict[str, Any]:
    context = dict(_log_context.get({}))
    for key, value in kwargs.items():
        if value is not None:
            context[key] = value
    _log_context.set(context)
    return context


def clear_log_context() -> None:
    _log_context.set({})


def get_log_context() -> Dict[str, Any]:
    return dict(_log_context.get({}))


def log_context_metadata() -> Dict[str, Any]:
    return {
        "configured": _configured,
        "context": redact_mapping(get_log_context()),
        "logging_config_version": LOGGING_CONFIG_VERSION,
    }


def redact_mapping(payload: Mapping[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in payload.items():
        key_text = str(key)
        if is_sensitive_key(key_text):
            result[key_text] = "[REDACTED]"
        else:
            result[key_text] = redact_value(value)
    return result


def redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, str):
        redacted = value
        for pattern in SENSITIVE_VALUE_PATTERNS:
            redacted = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]" if match.lastindex and match.lastindex >= 1 else "[REDACTED]", redacted)
        return redacted
    return value


def is_sensitive_key(key: str) -> bool:
    return any(pattern.search(key) for pattern in SENSITIVE_KEY_PATTERNS)


def resolve_level(level: str) -> int:
    return getattr(logging, str(level).upper(), logging.INFO)


def tune_noisy_loggers() -> None:
    noisy = {
        "uvicorn.access": os.getenv("LOG_UVICORN_ACCESS_LEVEL", "WARNING"),
        "uvicorn.error": os.getenv("LOG_UVICORN_ERROR_LEVEL", "INFO"),
        "urllib3": "WARNING",
        "httpx": "WARNING",
        "asyncio": "WARNING",
    }
    for name, level in noisy.items():
        logging.getLogger(name).setLevel(resolve_level(level))


def logging_health() -> Dict[str, Any]:
    root = logging.getLogger()
    return {
        "status": "ok",
        "configured": _configured,
        "level": logging.getLevelName(root.level),
        "handler_count": len(root.handlers),
        "handlers": [handler.__class__.__name__ for handler in root.handlers],
        "context_keys": sorted(get_log_context().keys()),
        "version": LOGGING_CONFIG_VERSION,
        "timestamp": datetime.now(tz=DEFAULT_TIMEZONE).isoformat(),
    }


__all__ = [
    "LOGGING_CONFIG_VERSION",
    "LoggingSettings",
    "RedactionFilter",
    "ContextFilter",
    "JsonFormatter",
    "TextFormatter",
    "configure_logging",
    "get_logger",
    "bind_log_context",
    "clear_log_context",
    "get_log_context",
    "log_context_metadata",
    "redact_mapping",
    "redact_value",
    "logging_health",
]

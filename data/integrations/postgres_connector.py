"""
data/integrations/postgres_connector.py

Enterprise-grade PostgreSQL connector.

This module provides a production-ready PostgreSQL integration layer for data
platform workloads, including transactional commands, safe parameterized
queries, bulk insert/upsert, streaming reads, health checks, schema inspection,
retry policies, circuit breaker protection, audit events, metrics, and JSON
friendly results.

Main capabilities:
- SQLAlchemy engine/session management
- Connection pooling and configurable timeouts
- Parameterized query execution
- Transaction context manager
- Bulk insert and PostgreSQL ON CONFLICT upsert
- Streaming/chunked reads for large datasets
- Optional pandas DataFrame integration
- Schema/table/column introspection
- Retry with exponential backoff and jitter
- Circuit breaker for repeated failures
- Metrics and audit sink hooks
- Safe logging with secret redaction

Environment variables commonly used:
- POSTGRES_HOST / DB_HOST
- POSTGRES_PORT / DB_PORT
- POSTGRES_DB / DB_NAME
- POSTGRES_USER / DB_USER
- POSTGRES_PASSWORD / DB_PASSWORD
- POSTGRES_SCHEMA / DB_SCHEMA
- DATABASE_URL
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, Iterator, List, Mapping, Optional, Protocol, Sequence, Tuple
from urllib.parse import quote_plus

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None  # type: ignore

try:
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.engine import Connection, Engine, Result
    from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError, TimeoutError
except Exception:  # pragma: no cover
    create_engine = None  # type: ignore
    inspect = None  # type: ignore
    text = None  # type: ignore
    Connection = Any  # type: ignore
    Engine = Any  # type: ignore
    Result = Any  # type: ignore
    DBAPIError = Exception  # type: ignore
    OperationalError = Exception  # type: ignore
    SQLAlchemyError = Exception  # type: ignore
    TimeoutError = Exception  # type: ignore


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# =============================================================================
# Exceptions
# =============================================================================


class PostgresConnectorError(Exception):
    """Base exception for PostgreSQL connector failures."""


class PostgresConfigurationError(PostgresConnectorError):
    """Raised when connector configuration is invalid."""


class PostgresDependencyError(PostgresConnectorError):
    """Raised when required dependencies are missing."""


class PostgresExecutionError(PostgresConnectorError):
    """Raised when query execution fails."""


class PostgresCircuitOpenError(PostgresConnectorError):
    """Raised when circuit breaker is open."""


# =============================================================================
# Protocols
# =============================================================================


class MetricsSink(Protocol):
    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def timing(self, metric_name: str, value_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...


class AuditSink(Protocol):
    def write_event(self, event: Mapping[str, Any]) -> None:
        ...


# =============================================================================
# Enums / config
# =============================================================================


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class FetchMode(str, Enum):
    ALL = "all"
    ONE = "one"
    MANY = "many"
    NONE = "none"


class ConflictAction(str, Enum):
    DO_NOTHING = "do_nothing"
    DO_UPDATE = "do_update"


class IsolationLevel(str, Enum):
    AUTOCOMMIT = "AUTOCOMMIT"
    READ_COMMITTED = "READ COMMITTED"
    REPEATABLE_READ = "REPEATABLE READ"
    SERIALIZABLE = "SERIALIZABLE"


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 10.0
    backoff_multiplier: float = 2.0
    jitter: bool = True
    retry_on_operational_errors: bool = True

    def validate(self) -> None:
        if self.max_attempts < 1:
            raise PostgresConfigurationError("max_attempts must be at least 1.")
        if self.base_delay_seconds < 0:
            raise PostgresConfigurationError("base_delay_seconds cannot be negative.")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise PostgresConfigurationError("max_delay_seconds must be >= base_delay_seconds.")
        if self.backoff_multiplier < 1:
            raise PostgresConfigurationError("backoff_multiplier must be >= 1.")


@dataclass(frozen=True)
class CircuitBreakerConfig:
    enabled: bool = True
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0
    half_open_max_calls: int = 1

    def validate(self) -> None:
        if self.failure_threshold < 1:
            raise PostgresConfigurationError("failure_threshold must be at least 1.")
        if self.recovery_timeout_seconds <= 0:
            raise PostgresConfigurationError("recovery_timeout_seconds must be positive.")
        if self.half_open_max_calls < 1:
            raise PostgresConfigurationError("half_open_max_calls must be at least 1.")


@dataclass(frozen=True)
class PostgresConfig:
    host: str = "localhost"
    port: int = 5432
    database: str = "postgres"
    username: str = "postgres"
    password: str = "postgres"
    schema: str = "public"
    database_url: Optional[str] = None
    driver: str = "psycopg2"
    ssl_mode: str = "prefer"
    connect_timeout_seconds: int = 10
    statement_timeout_ms: Optional[int] = 300_000
    application_name: str = "data-platform-postgres-connector"
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout_seconds: int = 30
    pool_recycle_seconds: int = 1800
    pool_pre_ping: bool = True
    echo_sql: bool = False
    isolation_level: Optional[IsolationLevel] = None
    retry: RetryConfig = field(default_factory=RetryConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)

    @staticmethod
    def from_env(prefix: str = "POSTGRES") -> "PostgresConfig":
        database_url = os.getenv("DATABASE_URL") or os.getenv(f"{prefix}_DATABASE_URL")
        return PostgresConfig(
            host=os.getenv(f"{prefix}_HOST") or os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv(f"{prefix}_PORT") or os.getenv("DB_PORT", "5432")),
            database=os.getenv(f"{prefix}_DB") or os.getenv(f"{prefix}_DATABASE") or os.getenv("DB_NAME", "postgres"),
            username=os.getenv(f"{prefix}_USER") or os.getenv("DB_USER", "postgres"),
            password=os.getenv(f"{prefix}_PASSWORD") or os.getenv("DB_PASSWORD", "postgres"),
            schema=os.getenv(f"{prefix}_SCHEMA") or os.getenv("DB_SCHEMA", "public"),
            database_url=database_url,
            driver=os.getenv(f"{prefix}_DRIVER", "psycopg2"),
            ssl_mode=os.getenv(f"{prefix}_SSL_MODE") or os.getenv("DB_SSL_MODE", "prefer"),
            connect_timeout_seconds=int(os.getenv(f"{prefix}_CONNECT_TIMEOUT_SECONDS") or os.getenv("DB_CONNECT_TIMEOUT_SECONDS", "10")),
            statement_timeout_ms=_optional_int(os.getenv(f"{prefix}_STATEMENT_TIMEOUT_MS") or os.getenv("DB_STATEMENT_TIMEOUT_MS"), 300_000),
            application_name=os.getenv(f"{prefix}_APPLICATION_NAME", "data-platform-postgres-connector"),
            pool_size=int(os.getenv(f"{prefix}_POOL_SIZE") or os.getenv("DB_POOL_MAX_SIZE", "5")),
            max_overflow=int(os.getenv(f"{prefix}_MAX_OVERFLOW", "10")),
            pool_timeout_seconds=int(os.getenv(f"{prefix}_POOL_TIMEOUT_SECONDS") or os.getenv("DB_POOL_TIMEOUT_SECONDS", "30")),
            pool_recycle_seconds=int(os.getenv(f"{prefix}_POOL_RECYCLE_SECONDS") or os.getenv("DB_POOL_RECYCLE_SECONDS", "1800")),
            pool_pre_ping=os.getenv(f"{prefix}_POOL_PRE_PING", "true").lower() in {"1", "true", "yes", "y"},
            echo_sql=os.getenv(f"{prefix}_ECHO_SQL") or os.getenv("DB_ECHO_SQL", "false").lower() in {"1", "true", "yes", "y"},
            retry=RetryConfig(max_attempts=int(os.getenv(f"{prefix}_MAX_RETRIES", "3"))),
        )

    def validate(self) -> None:
        if self.database_url:
            if not self.database_url.startswith(("postgresql://", "postgresql+", "postgres://")):
                raise PostgresConfigurationError("database_url must be a PostgreSQL URL.")
        else:
            if not self.host.strip():
                raise PostgresConfigurationError("host is required.")
            if not self.database.strip():
                raise PostgresConfigurationError("database is required.")
            if not self.username.strip():
                raise PostgresConfigurationError("username is required.")
            if not 1 <= self.port <= 65535:
                raise PostgresConfigurationError("port must be between 1 and 65535.")
        if self.pool_size < 1:
            raise PostgresConfigurationError("pool_size must be >= 1.")
        if self.max_overflow < 0:
            raise PostgresConfigurationError("max_overflow cannot be negative.")
        if self.pool_timeout_seconds <= 0:
            raise PostgresConfigurationError("pool_timeout_seconds must be positive.")
        self.retry.validate()
        self.circuit_breaker.validate()

    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        user = quote_plus(self.username)
        pwd = quote_plus(self.password)
        host = self.host
        database = quote_plus(self.database)
        return f"postgresql+{self.driver}://{user}:{pwd}@{host}:{self.port}/{database}"

    def safe_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["password"] = _redact(self.password)
        if data.get("database_url"):
            data["database_url"] = _redact_url(str(data["database_url"]))
        return _json_safe(data)


@dataclass
class QueryResult:
    request_id: str
    sql_hash: str
    rowcount: int
    rows: List[Dict[str, Any]]
    duration_ms: float
    columns: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class TableColumn:
    schema: str
    table: str
    name: str
    data_type: str
    is_nullable: bool
    ordinal_position: int
    default: Optional[str] = None
    max_length: Optional[int] = None
    numeric_precision: Optional[int] = None
    numeric_scale: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class CircuitBreakerState:
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    opened_at: Optional[float] = None
    half_open_calls: int = 0


# =============================================================================
# Helpers
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_sqlalchemy() -> None:
    if create_engine is None or text is None:
        raise PostgresDependencyError(
            "SQLAlchemy and a PostgreSQL driver are required. Install with: pip install sqlalchemy psycopg2-binary"
        )


def _optional_int(value: Optional[str], default: Optional[int]) -> Optional[int]:
    if value is None or value == "":
        return default
    return int(value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    return value


def _redact(value: Any) -> str:
    if value is None:
        return ""
    text_value = str(value)
    if len(text_value) <= 8:
        return "***"
    return f"{text_value[:3]}***{text_value[-3:]}"


def _redact_url(url: str) -> str:
    return re.sub(r"://([^:/?#]+):([^@]+)@", r"://\1:***@", url)


def _sql_hash(sql: str, params: Optional[Mapping[str, Any]] = None) -> str:
    payload = {"sql": sql, "params_keys": sorted((params or {}).keys())}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    import hashlib

    return hashlib.sha256(encoded).hexdigest()


def _quote_identifier(identifier: str) -> str:
    if not identifier or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise PostgresConfigurationError(f"Unsafe SQL identifier: {identifier!r}")
    return f'"{identifier}"'


def _qualified_name(table: str, schema: Optional[str] = None) -> str:
    if "." in table and schema is None:
        parts = table.split(".")
        if len(parts) != 2:
            raise PostgresConfigurationError(f"Invalid table name: {table}")
        return f"{_quote_identifier(parts[0])}.{_quote_identifier(parts[1])}"
    if schema:
        return f"{_quote_identifier(schema)}.{_quote_identifier(table)}"
    return _quote_identifier(table)


def _chunks(items: Sequence[Mapping[str, Any]], size: int) -> Iterator[Sequence[Mapping[str, Any]]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


# =============================================================================
# Sinks
# =============================================================================


class NoopMetricsSink:
    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        return None

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        return None

    def timing(self, metric_name: str, value_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        return None


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.events: List[Mapping[str, Any]] = []

    def write_event(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))


# =============================================================================
# Connector
# =============================================================================


class PostgresConnector:
    """Enterprise PostgreSQL connector."""

    def __init__(
        self,
        config: PostgresConfig,
        *,
        engine: Optional[Engine] = None,
        metrics_sink: Optional[MetricsSink] = None,
        audit_sink: Optional[AuditSink] = None,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        _require_sqlalchemy()
        self.config = config
        self.config.validate()
        self.metrics_sink = metrics_sink or NoopMetricsSink()
        self.audit_sink = audit_sink
        self.logger = logger_ or logger
        self.circuit = CircuitBreakerState()
        self.engine: Engine = engine or self._create_engine()

    @classmethod
    def from_env(cls, prefix: str = "POSTGRES", **kwargs: Any) -> "PostgresConnector":
        return cls(PostgresConfig.from_env(prefix), **kwargs)

    def _create_engine(self) -> Engine:
        connect_args: Dict[str, Any] = {
            "connect_timeout": self.config.connect_timeout_seconds,
            "application_name": self.config.application_name,
        }
        if self.config.ssl_mode:
            connect_args["sslmode"] = self.config.ssl_mode
        if self.config.statement_timeout_ms is not None:
            connect_args["options"] = f"-c statement_timeout={self.config.statement_timeout_ms}"

        kwargs: Dict[str, Any] = {
            "pool_size": self.config.pool_size,
            "max_overflow": self.config.max_overflow,
            "pool_timeout": self.config.pool_timeout_seconds,
            "pool_recycle": self.config.pool_recycle_seconds,
            "pool_pre_ping": self.config.pool_pre_ping,
            "echo": self.config.echo_sql,
            "future": True,
            "connect_args": connect_args,
        }
        if self.config.isolation_level:
            kwargs["isolation_level"] = self.config.isolation_level.value
        return create_engine(self.config.sqlalchemy_url(), **kwargs)  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Circuit breaker and retry
    # ------------------------------------------------------------------

    def _before_request(self) -> None:
        cfg = self.config.circuit_breaker
        if not cfg.enabled:
            return
        if self.circuit.state == CircuitState.OPEN:
            elapsed = time.time() - (self.circuit.opened_at or 0)
            if elapsed >= cfg.recovery_timeout_seconds:
                self.circuit.state = CircuitState.HALF_OPEN
                self.circuit.half_open_calls = 0
            else:
                raise PostgresCircuitOpenError("PostgreSQL circuit breaker is open.")
        if self.circuit.state == CircuitState.HALF_OPEN:
            if self.circuit.half_open_calls >= cfg.half_open_max_calls:
                raise PostgresCircuitOpenError("PostgreSQL circuit breaker is half-open and call limit was reached.")
            self.circuit.half_open_calls += 1

    def _record_success(self, operation: str, duration_ms: float) -> None:
        if self.config.circuit_breaker.enabled:
            self.circuit.state = CircuitState.CLOSED
            self.circuit.failure_count = 0
            self.circuit.opened_at = None
            self.circuit.half_open_calls = 0
        tags = {"operation": operation, "database": self.config.database, "schema": self.config.schema}
        self.metrics_sink.increment("postgres.operation.success", tags=tags)
        self.metrics_sink.timing("postgres.operation.duration_ms", duration_ms, tags=tags)

    def _record_failure(self, operation: str) -> None:
        if self.config.circuit_breaker.enabled:
            self.circuit.failure_count += 1
            if self.circuit.failure_count >= self.config.circuit_breaker.failure_threshold:
                self.circuit.state = CircuitState.OPEN
                self.circuit.opened_at = time.time()
        tags = {"operation": operation, "database": self.config.database, "schema": self.config.schema}
        self.metrics_sink.increment("postgres.operation.failure", tags=tags)
        self.metrics_sink.gauge("postgres.circuit.failure_count", self.circuit.failure_count, tags={"state": self.circuit.state.value})

    def _sleep_before_retry(self, attempt: int) -> None:
        cfg = self.config.retry
        delay = min(cfg.max_delay_seconds, cfg.base_delay_seconds * (cfg.backoff_multiplier ** (attempt - 1)))
        if cfg.jitter:
            delay *= random.uniform(0.5, 1.5)
        time.sleep(delay)

    def _should_retry(self, exc: Exception) -> bool:
        if not self.config.retry.retry_on_operational_errors:
            return False
        return isinstance(exc, (OperationalError, TimeoutError, DBAPIError))

    def _audit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if not self.audit_sink:
            return
        self.audit_sink.write_event({"event_type": event_type, "timestamp": utc_now_iso(), **dict(payload)})

    # ------------------------------------------------------------------
    # Core execution
    # ------------------------------------------------------------------

    def execute(
        self,
        sql: str,
        params: Optional[Mapping[str, Any]] = None,
        *,
        fetch_mode: FetchMode = FetchMode.ALL,
        many_size: Optional[int] = None,
        connection: Optional[Connection] = None,
        operation_name: str = "execute",
    ) -> QueryResult:
        """Execute a parameterized SQL statement."""
        request_id = str(uuid.uuid4())
        sql_digest = _sql_hash(sql, params)
        self._before_request()
        started = time.perf_counter()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.config.retry.max_attempts + 1):
            try:
                if connection is not None:
                    result = self._execute_on_connection(connection, sql, params, fetch_mode, many_size)
                else:
                    with self.engine.begin() as conn:
                        result = self._execute_on_connection(conn, sql, params, fetch_mode, many_size)

                duration_ms = (time.perf_counter() - started) * 1000
                query_result = QueryResult(
                    request_id=request_id,
                    sql_hash=sql_digest,
                    rowcount=result["rowcount"],
                    rows=result["rows"],
                    columns=result["columns"],
                    duration_ms=duration_ms,
                    metadata={"attempt": attempt, "operation": operation_name},
                )
                self._record_success(operation_name, duration_ms)
                self._audit(
                    "postgres_query_succeeded",
                    {
                        "request_id": request_id,
                        "sql_hash": sql_digest,
                        "operation": operation_name,
                        "rowcount": query_result.rowcount,
                        "duration_ms": duration_ms,
                    },
                )
                return query_result
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self._record_failure(operation_name)
                if attempt < self.config.retry.max_attempts and self._should_retry(exc):
                    self._sleep_before_retry(attempt)
                    continue
                duration_ms = (time.perf_counter() - started) * 1000
                self._audit(
                    "postgres_query_failed",
                    {
                        "request_id": request_id,
                        "sql_hash": sql_digest,
                        "operation": operation_name,
                        "duration_ms": duration_ms,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                raise PostgresExecutionError(f"PostgreSQL execution failed for operation '{operation_name}': {exc}") from exc

        raise PostgresExecutionError(f"PostgreSQL execution failed: {last_error}")

    def _execute_on_connection(
        self,
        conn: Connection,
        sql: str,
        params: Optional[Mapping[str, Any]],
        fetch_mode: FetchMode,
        many_size: Optional[int],
    ) -> Dict[str, Any]:
        result: Result = conn.execute(text(sql), dict(params or {}))
        rows: List[Dict[str, Any]] = []
        columns: List[str] = []
        if result.returns_rows:
            columns = list(result.keys())
            if fetch_mode == FetchMode.ONE:
                row = result.fetchone()
                rows = [dict(row._mapping)] if row is not None else []
            elif fetch_mode == FetchMode.MANY:
                fetched = result.fetchmany(many_size or 1000)
                rows = [dict(row._mapping) for row in fetched]
            elif fetch_mode == FetchMode.ALL:
                rows = [dict(row._mapping) for row in result.fetchall()]
            elif fetch_mode == FetchMode.NONE:
                rows = []
        rowcount = result.rowcount if result.rowcount is not None and result.rowcount >= 0 else len(rows)
        return {"rows": _json_safe(rows), "columns": columns, "rowcount": rowcount}

    def fetch_all(self, sql: str, params: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        return self.execute(sql, params, fetch_mode=FetchMode.ALL, operation_name="fetch_all").rows

    def fetch_one(self, sql: str, params: Optional[Mapping[str, Any]] = None) -> Optional[Dict[str, Any]]:
        rows = self.execute(sql, params, fetch_mode=FetchMode.ONE, operation_name="fetch_one").rows
        return rows[0] if rows else None

    def execute_non_query(self, sql: str, params: Optional[Mapping[str, Any]] = None) -> int:
        return self.execute(sql, params, fetch_mode=FetchMode.NONE, operation_name="execute_non_query").rowcount

    @contextmanager
    def transaction(self) -> Generator[Connection, None, None]:
        """Open a transaction and yield a SQLAlchemy connection."""
        self._before_request()
        started = time.perf_counter()
        operation = "transaction"
        try:
            with self.engine.begin() as conn:
                if self.config.schema:
                    conn.execute(text(f"SET search_path TO {_quote_identifier(self.config.schema)}"))
                yield conn
            self._record_success(operation, (time.perf_counter() - started) * 1000)
        except Exception as exc:  # noqa: BLE001
            self._record_failure(operation)
            self._audit("postgres_transaction_failed", {"error_type": type(exc).__name__, "error": str(exc)})
            raise

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def bulk_insert(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        schema: Optional[str] = None,
        batch_size: int = 1000,
        returning: Optional[Sequence[str]] = None,
    ) -> QueryResult:
        if not rows:
            return QueryResult(str(uuid.uuid4()), "", 0, [], 0.0, metadata={"operation": "bulk_insert_empty"})
        if batch_size < 1:
            raise PostgresConfigurationError("batch_size must be positive.")

        columns = sorted({key for row in rows for key in row.keys()})
        table_name = _qualified_name(table, schema or self.config.schema)
        col_sql = ", ".join(_quote_identifier(col) for col in columns)
        values_sql = ", ".join(f":{col}" for col in columns)
        returning_sql = ""
        if returning:
            returning_sql = " RETURNING " + ", ".join(_quote_identifier(col) for col in returning)
        sql = f"INSERT INTO {table_name} ({col_sql}) VALUES ({values_sql}){returning_sql}"

        started = time.perf_counter()
        total_rowcount = 0
        all_rows: List[Dict[str, Any]] = []
        with self.transaction() as conn:
            for batch in _chunks(list(rows), batch_size):
                normalized_batch = [{col: row.get(col) for col in columns} for row in batch]
                result = conn.execute(text(sql), normalized_batch)
                total_rowcount += result.rowcount if result.rowcount and result.rowcount > 0 else len(batch)
                if returning and result.returns_rows:
                    all_rows.extend(dict(row._mapping) for row in result.fetchall())
        duration_ms = (time.perf_counter() - started) * 1000
        self._record_success("bulk_insert", duration_ms)
        return QueryResult(str(uuid.uuid4()), _sql_hash(sql), total_rowcount, _json_safe(all_rows), duration_ms, columns=list(returning or []))

    def bulk_upsert(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        conflict_columns: Sequence[str],
        update_columns: Optional[Sequence[str]] = None,
        schema: Optional[str] = None,
        batch_size: int = 1000,
        action: ConflictAction = ConflictAction.DO_UPDATE,
        returning: Optional[Sequence[str]] = None,
    ) -> QueryResult:
        if not rows:
            return QueryResult(str(uuid.uuid4()), "", 0, [], 0.0, metadata={"operation": "bulk_upsert_empty"})
        if not conflict_columns:
            raise PostgresConfigurationError("conflict_columns are required for upsert.")

        columns = sorted({key for row in rows for key in row.keys()})
        update_columns = list(update_columns or [col for col in columns if col not in set(conflict_columns)])
        table_name = _qualified_name(table, schema or self.config.schema)
        col_sql = ", ".join(_quote_identifier(col) for col in columns)
        values_sql = ", ".join(f":{col}" for col in columns)
        conflict_sql = ", ".join(_quote_identifier(col) for col in conflict_columns)

        if action == ConflictAction.DO_NOTHING or not update_columns:
            conflict_action = "DO NOTHING"
        else:
            set_sql = ", ".join(f"{_quote_identifier(col)} = EXCLUDED.{_quote_identifier(col)}" for col in update_columns)
            conflict_action = f"DO UPDATE SET {set_sql}"

        returning_sql = ""
        if returning:
            returning_sql = " RETURNING " + ", ".join(_quote_identifier(col) for col in returning)

        sql = (
            f"INSERT INTO {table_name} ({col_sql}) VALUES ({values_sql}) "
            f"ON CONFLICT ({conflict_sql}) {conflict_action}{returning_sql}"
        )

        started = time.perf_counter()
        total_rowcount = 0
        all_rows: List[Dict[str, Any]] = []
        with self.transaction() as conn:
            for batch in _chunks(list(rows), batch_size):
                normalized_batch = [{col: row.get(col) for col in columns} for row in batch]
                result = conn.execute(text(sql), normalized_batch)
                total_rowcount += result.rowcount if result.rowcount and result.rowcount > 0 else len(batch)
                if returning and result.returns_rows:
                    all_rows.extend(dict(row._mapping) for row in result.fetchall())
        duration_ms = (time.perf_counter() - started) * 1000
        self._record_success("bulk_upsert", duration_ms)
        return QueryResult(str(uuid.uuid4()), _sql_hash(sql), total_rowcount, _json_safe(all_rows), duration_ms, columns=list(returning or []))

    # ------------------------------------------------------------------
    # Streaming and pandas
    # ------------------------------------------------------------------

    def stream_query(
        self,
        sql: str,
        params: Optional[Mapping[str, Any]] = None,
        *,
        chunk_size: int = 10_000,
    ) -> Iterator[List[Dict[str, Any]]]:
        if chunk_size < 1:
            raise PostgresConfigurationError("chunk_size must be positive.")
        with self.engine.connect().execution_options(stream_results=True) as conn:
            result = conn.execute(text(sql), dict(params or {}))
            while True:
                rows = result.fetchmany(chunk_size)
                if not rows:
                    break
                yield _json_safe([dict(row._mapping) for row in rows])

    def read_dataframe(self, sql: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        if pd is None:
            raise PostgresDependencyError("pandas is required for read_dataframe. Install with: pip install pandas")
        return pd.read_sql_query(sql=text(sql), con=self.engine, params=dict(params or {}))

    def iter_dataframes(
        self,
        sql: str,
        params: Optional[Mapping[str, Any]] = None,
        *,
        chunksize: int = 100_000,
    ) -> Iterator[Any]:
        if pd is None:
            raise PostgresDependencyError("pandas is required for iter_dataframes. Install with: pip install pandas")
        yield from pd.read_sql_query(sql=text(sql), con=self.engine, params=dict(params or {}), chunksize=chunksize)

    def write_dataframe(
        self,
        df: Any,
        table: str,
        *,
        schema: Optional[str] = None,
        if_exists: str = "append",
        index: bool = False,
        chunksize: int = 10_000,
        method: Optional[str] = "multi",
    ) -> int:
        if pd is None:
            raise PostgresDependencyError("pandas is required for write_dataframe. Install with: pip install pandas")
        if not isinstance(df, pd.DataFrame):
            raise PostgresExecutionError(f"Expected pandas DataFrame, got {type(df)!r}")
        started = time.perf_counter()
        df.to_sql(
            name=table,
            con=self.engine,
            schema=schema or self.config.schema,
            if_exists=if_exists,
            index=index,
            chunksize=chunksize,
            method=method,
        )
        duration_ms = (time.perf_counter() - started) * 1000
        self._record_success("write_dataframe", duration_ms)
        return int(len(df))

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_schemas(self) -> List[str]:
        rows = self.fetch_all(
            """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name NOT IN ('information_schema', 'pg_catalog')
            ORDER BY schema_name
            """
        )
        return [row["schema_name"] for row in rows]

    def list_tables(self, *, schema: Optional[str] = None) -> List[str]:
        rows = self.fetch_all(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = :schema
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
            {"schema": schema or self.config.schema},
        )
        return [row["table_name"] for row in rows]

    def describe_table(self, table: str, *, schema: Optional[str] = None) -> List[TableColumn]:
        rows = self.fetch_all(
            """
            SELECT
                table_schema,
                table_name,
                column_name,
                data_type,
                is_nullable,
                ordinal_position,
                column_default,
                character_maximum_length,
                numeric_precision,
                numeric_scale
            FROM information_schema.columns
            WHERE table_schema = :schema
              AND table_name = :table
            ORDER BY ordinal_position
            """,
            {"schema": schema or self.config.schema, "table": table},
        )
        return [
            TableColumn(
                schema=row["table_schema"],
                table=row["table_name"],
                name=row["column_name"],
                data_type=row["data_type"],
                is_nullable=row["is_nullable"] == "YES",
                ordinal_position=row["ordinal_position"],
                default=row.get("column_default"),
                max_length=row.get("character_maximum_length"),
                numeric_precision=row.get("numeric_precision"),
                numeric_scale=row.get("numeric_scale"),
            )
            for row in rows
        ]

    def table_exists(self, table: str, *, schema: Optional[str] = None) -> bool:
        row = self.fetch_one(
            """
            SELECT 1 AS exists_flag
            FROM information_schema.tables
            WHERE table_schema = :schema
              AND table_name = :table
            LIMIT 1
            """,
            {"schema": schema or self.config.schema, "table": table},
        )
        return row is not None

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            row = self.fetch_one(
                """
                SELECT
                    1 AS ok,
                    current_database() AS database_name,
                    current_schema() AS schema_name,
                    version() AS server_version,
                    now() AS server_time
                """
            )
            ok = bool(row and row.get("ok") == 1)
            error = None
        except Exception as exc:  # noqa: BLE001
            row = None
            ok = False
            error = str(exc)
        return {
            "ok": ok,
            "config": self.config.safe_dict(),
            "database_info": row,
            "circuit_state": self.circuit.state.value,
            "duration_ms": round((time.perf_counter() - started) * 1000, 4),
            "error": error,
            "checked_at": utc_now_iso(),
        }

    def vacuum_analyze(self, table: str, *, schema: Optional[str] = None) -> None:
        table_name = _qualified_name(table, schema or self.config.schema)
        with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f"VACUUM ANALYZE {table_name}"))

    def close(self) -> None:
        self.engine.dispose()

    def __enter__(self) -> "PostgresConnector":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self.close()
        return False


# =============================================================================
# Convenience API
# =============================================================================


def create_postgres_connector_from_env(prefix: str = "POSTGRES") -> PostgresConnector:
    return PostgresConnector.from_env(prefix)


def query_postgres(sql: str, params: Optional[Mapping[str, Any]] = None, *, prefix: str = "POSTGRES") -> List[Dict[str, Any]]:
    with create_postgres_connector_from_env(prefix) as connector:
        return connector.fetch_all(sql, params)


# =============================================================================
# Local smoke example
# =============================================================================


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    connector = PostgresConnector.from_env(audit_sink=InMemoryAuditSink())
    print(json.dumps(connector.health_check(), indent=2, ensure_ascii=False))

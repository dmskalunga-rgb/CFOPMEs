# kwanza-ai-core/infrastructure/database.py
from __future__ import annotations

import abc
import asyncio
import contextlib
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, TypeVar

T = TypeVar("T")


class DatabaseDriver(str, Enum):
    POSTGRESQL = "postgresql"
    SQLITE = "sqlite"


class DatabaseState(str, Enum):
    CREATED = "created"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


@dataclass(frozen=True)
class DatabaseConfig:
    driver: DatabaseDriver = DatabaseDriver.POSTGRESQL

    host: str = "localhost"
    port: int = 5432
    database: str = "kwanza_ai"
    username: str = "postgres"
    password: str = ""

    sqlite_path: str = "./storage/kwanza_ai.db"

    min_pool_size: int = 1
    max_pool_size: int = 20
    connect_timeout_seconds: float = 10.0
    command_timeout_seconds: float = 60.0
    statement_timeout_ms: int = 60_000

    ssl: bool = False
    application_name: str = "kwanza-ai-core"

    retry_attempts: int = 3
    retry_base_delay_seconds: float = 0.25

    enable_query_logging: bool = False
    slow_query_threshold_ms: float = 500.0

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.username}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    @property
    def safe_postgres_dsn(self) -> str:
        if not self.password:
            return self.postgres_dsn
        return self.postgres_dsn.replace(self.password, "***")


@dataclass(frozen=True)
class QueryResult:
    rows: List[Dict[str, Any]]
    rowcount: int
    elapsed_ms: float


@dataclass(frozen=True)
class DatabaseHealth:
    state: DatabaseState
    driver: DatabaseDriver
    latency_ms: float
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)


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


class DatabaseError(RuntimeError):
    pass


class DatabaseConnectionError(DatabaseError):
    pass


class DatabaseQueryError(DatabaseError):
    pass


class DatabaseTransactionError(DatabaseError):
    pass


class DatabaseMigrationError(DatabaseError):
    pass


class DatabaseBackend(abc.ABC):
    @abc.abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def fetch_all(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abc.abstractmethod
    async def fetch_one(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    @abc.abstractmethod
    async def execute(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> int:
        raise NotImplementedError

    @abc.abstractmethod
    async def execute_many(
        self,
        query: str,
        params: Iterable[Sequence[Any] | Mapping[str, Any]],
    ) -> int:
        raise NotImplementedError

    @abc.abstractmethod
    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator["DatabaseBackend"]:
        yield self

    @abc.abstractmethod
    async def health(self) -> DatabaseHealth:
        raise NotImplementedError


class PostgresBackend(DatabaseBackend):
    def __init__(
        self,
        config: DatabaseConfig,
        logger: logging.Logger,
    ) -> None:
        self.config = config
        self.logger = logger
        self.pool: Any = None
        self.state = DatabaseState.CREATED

    async def connect(self) -> None:
        try:
            import asyncpg
        except ImportError as exc:
            raise DatabaseConnectionError(
                "asyncpg não está instalado. Use: pip install asyncpg"
            ) from exc

        self.state = DatabaseState.CONNECTING

        try:
            self.pool = await asyncpg.create_pool(
                dsn=self.config.postgres_dsn,
                min_size=self.config.min_pool_size,
                max_size=self.config.max_pool_size,
                timeout=self.config.connect_timeout_seconds,
                command_timeout=self.config.command_timeout_seconds,
                server_settings={
                    "application_name": self.config.application_name,
                    "statement_timeout": str(self.config.statement_timeout_ms),
                },
            )
            self.state = DatabaseState.CONNECTED

        except Exception as exc:
            self.state = DatabaseState.FAILED
            raise DatabaseConnectionError(str(exc)) from exc

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
        self.state = DatabaseState.DISCONNECTED

    async def fetch_all(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        self._ensure_connected()

        async with self.pool.acquire() as conn:
            records = await conn.fetch(query, *(params or []))
            return [dict(record) for record in records]

    async def fetch_one(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        self._ensure_connected()

        async with self.pool.acquire() as conn:
            record = await conn.fetchrow(query, *(params or []))
            return dict(record) if record is not None else None

    async def execute(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> int:
        self._ensure_connected()

        async with self.pool.acquire() as conn:
            result = await conn.execute(query, *(params or []))
            return self._parse_affected_rows(result)

    async def execute_many(
        self,
        query: str,
        params: Iterable[Sequence[Any] | Mapping[str, Any]],
    ) -> int:
        self._ensure_connected()

        values = list(params)
        async with self.pool.acquire() as conn:
            await conn.executemany(query, values)
            return len(values)

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator["PostgresTransactionBackend"]:
        self._ensure_connected()

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                yield PostgresTransactionBackend(conn, self.config, self.logger)

    async def health(self) -> DatabaseHealth:
        started = time.monotonic()

        try:
            row = await self.fetch_one("SELECT 1 AS ok")
            elapsed = (time.monotonic() - started) * 1000

            return DatabaseHealth(
                state=self.state,
                driver=DatabaseDriver.POSTGRESQL,
                latency_ms=elapsed,
                message="PostgreSQL connection healthy",
                metadata={"ok": row.get("ok") if row else None},
            )

        except Exception as exc:
            return DatabaseHealth(
                state=DatabaseState.FAILED,
                driver=DatabaseDriver.POSTGRESQL,
                latency_ms=(time.monotonic() - started) * 1000,
                message=str(exc),
            )

    def _ensure_connected(self) -> None:
        if self.pool is None or self.state != DatabaseState.CONNECTED:
            raise DatabaseConnectionError("PostgreSQL backend is not connected")

    @staticmethod
    def _parse_affected_rows(status: str) -> int:
        parts = status.split()
        if not parts:
            return 0

        with contextlib.suppress(ValueError):
            return int(parts[-1])

        return 0


class PostgresTransactionBackend(DatabaseBackend):
    def __init__(
        self,
        conn: Any,
        config: DatabaseConfig,
        logger: logging.Logger,
    ) -> None:
        self.conn = conn
        self.config = config
        self.logger = logger
        self.state = DatabaseState.CONNECTED

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def fetch_all(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        records = await self.conn.fetch(query, *(params or []))
        return [dict(record) for record in records]

    async def fetch_one(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        record = await self.conn.fetchrow(query, *(params or []))
        return dict(record) if record is not None else None

    async def execute(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> int:
        result = await self.conn.execute(query, *(params or []))
        return PostgresBackend._parse_affected_rows(result)

    async def execute_many(
        self,
        query: str,
        params: Iterable[Sequence[Any] | Mapping[str, Any]],
    ) -> int:
        values = list(params)
        await self.conn.executemany(query, values)
        return len(values)

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator["PostgresTransactionBackend"]:
        async with self.conn.transaction():
            yield self

    async def health(self) -> DatabaseHealth:
        return DatabaseHealth(
            state=DatabaseState.CONNECTED,
            driver=DatabaseDriver.POSTGRESQL,
            latency_ms=0,
            message="Transaction connection active",
        )


class SQLiteBackend(DatabaseBackend):
    def __init__(
        self,
        config: DatabaseConfig,
        logger: logging.Logger,
    ) -> None:
        self.config = config
        self.logger = logger
        self.conn: Any = None
        self.state = DatabaseState.CREATED
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        try:
            import aiosqlite
        except ImportError as exc:
            raise DatabaseConnectionError(
                "aiosqlite não está instalado. Use: pip install aiosqlite"
            ) from exc

        self.state = DatabaseState.CONNECTING

        try:
            self.conn = await aiosqlite.connect(self.config.sqlite_path)
            self.conn.row_factory = sqlite3.Row

            await self.conn.execute("PRAGMA journal_mode=WAL")
            await self.conn.execute("PRAGMA foreign_keys=ON")
            await self.conn.execute("PRAGMA busy_timeout=5000")
            await self.conn.commit()

            self.state = DatabaseState.CONNECTED

        except Exception as exc:
            self.state = DatabaseState.FAILED
            raise DatabaseConnectionError(str(exc)) from exc

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
        self.state = DatabaseState.DISCONNECTED

    async def fetch_all(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        self._ensure_connected()

        async with self._lock:
            cursor = await self.conn.execute(query, params or [])
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]

    async def fetch_one(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        self._ensure_connected()

        async with self._lock:
            cursor = await self.conn.execute(query, params or [])
            row = await cursor.fetchone()
            await cursor.close()
            return dict(row) if row is not None else None

    async def execute(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> int:
        self._ensure_connected()

        async with self._lock:
            cursor = await self.conn.execute(query, params or [])
            await self.conn.commit()
            affected = cursor.rowcount
            await cursor.close()
            return affected

    async def execute_many(
        self,
        query: str,
        params: Iterable[Sequence[Any] | Mapping[str, Any]],
    ) -> int:
        self._ensure_connected()

        values = list(params)
        async with self._lock:
            cursor = await self.conn.executemany(query, values)
            await self.conn.commit()
            affected = cursor.rowcount
            await cursor.close()
            return affected if affected is not None else len(values)

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator["SQLiteTransactionBackend"]:
        self._ensure_connected()

        async with self._lock:
            try:
                await self.conn.execute("BEGIN")
                tx = SQLiteTransactionBackend(self.conn, self.config, self.logger)
                yield tx
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise

    async def health(self) -> DatabaseHealth:
        started = time.monotonic()

        try:
            row = await self.fetch_one("SELECT 1 AS ok")
            return DatabaseHealth(
                state=self.state,
                driver=DatabaseDriver.SQLITE,
                latency_ms=(time.monotonic() - started) * 1000,
                message="SQLite connection healthy",
                metadata={"ok": row.get("ok") if row else None},
            )

        except Exception as exc:
            return DatabaseHealth(
                state=DatabaseState.FAILED,
                driver=DatabaseDriver.SQLITE,
                latency_ms=(time.monotonic() - started) * 1000,
                message=str(exc),
            )

    def _ensure_connected(self) -> None:
        if self.conn is None or self.state != DatabaseState.CONNECTED:
            raise DatabaseConnectionError("SQLite backend is not connected")


class SQLiteTransactionBackend(DatabaseBackend):
    def __init__(
        self,
        conn: Any,
        config: DatabaseConfig,
        logger: logging.Logger,
    ) -> None:
        self.conn = conn
        self.config = config
        self.logger = logger
        self.state = DatabaseState.CONNECTED

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def fetch_all(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        cursor = await self.conn.execute(query, params or [])
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def fetch_one(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        cursor = await self.conn.execute(query, params or [])
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row is not None else None

    async def execute(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> int:
        cursor = await self.conn.execute(query, params or [])
        affected = cursor.rowcount
        await cursor.close()
        return affected

    async def execute_many(
        self,
        query: str,
        params: Iterable[Sequence[Any] | Mapping[str, Any]],
    ) -> int:
        values = list(params)
        cursor = await self.conn.executemany(query, values)
        affected = cursor.rowcount
        await cursor.close()
        return affected if affected is not None else len(values)

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator["SQLiteTransactionBackend"]:
        yield self

    async def health(self) -> DatabaseHealth:
        return DatabaseHealth(
            state=DatabaseState.CONNECTED,
            driver=DatabaseDriver.SQLITE,
            latency_ms=0,
            message="Transaction connection active",
        )


class Database:
    def __init__(
        self,
        config: DatabaseConfig,
        metrics: Optional[MetricsSink] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config
        self.metrics = metrics or NoopMetricsSink()
        self.logger = logger or logging.getLogger("kwanza.infrastructure.database")
        self.backend = self._build_backend()

    async def connect(self) -> None:
        await self._retry(self.backend.connect)
        self.metrics.increment("database.connected", tags=self._tags())

    async def close(self) -> None:
        await self.backend.close()
        self.metrics.increment("database.closed", tags=self._tags())

    async def fetch_all(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        started = time.monotonic()

        try:
            result = await self._retry(lambda: self.backend.fetch_all(query, params))
            self._record_query(query, started, success=True)
            return result

        except Exception as exc:
            self._record_query(query, started, success=False)
            raise DatabaseQueryError(str(exc)) from exc

    async def fetch_one(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        started = time.monotonic()

        try:
            result = await self._retry(lambda: self.backend.fetch_one(query, params))
            self._record_query(query, started, success=True)
            return result

        except Exception as exc:
            self._record_query(query, started, success=False)
            raise DatabaseQueryError(str(exc)) from exc

    async def execute(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> int:
        started = time.monotonic()

        try:
            result = await self._retry(lambda: self.backend.execute(query, params))
            self._record_query(query, started, success=True)
            return result

        except Exception as exc:
            self._record_query(query, started, success=False)
            raise DatabaseQueryError(str(exc)) from exc

    async def execute_many(
        self,
        query: str,
        params: Iterable[Sequence[Any] | Mapping[str, Any]],
    ) -> int:
        started = time.monotonic()

        try:
            result = await self._retry(lambda: self.backend.execute_many(query, params))
            self._record_query(query, started, success=True)
            return result

        except Exception as exc:
            self._record_query(query, started, success=False)
            raise DatabaseQueryError(str(exc)) from exc

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[DatabaseBackend]:
        started = time.monotonic()

        try:
            async with self.backend.transaction() as tx:
                yield tx

            self.metrics.increment("database.transaction.committed", tags=self._tags())
            self.metrics.timing(
                "database.transaction.latency_ms",
                (time.monotonic() - started) * 1000,
                tags=self._tags(),
            )

        except Exception as exc:
            self.metrics.increment("database.transaction.rolled_back", tags=self._tags())
            raise DatabaseTransactionError(str(exc)) from exc

    async def health(self) -> DatabaseHealth:
        return await self.backend.health()

    async def migrate(self, migrations: Sequence[str]) -> None:
        await self.ensure_migrations_table()

        for index, sql in enumerate(migrations, start=1):
            migration_id = f"{index:04d}_{hash_sql(sql)}"

            already_applied = await self.fetch_one(
                self._sql_select_migration(),
                [migration_id],
            )

            if already_applied:
                continue

            try:
                async with self.transaction() as tx:
                    await tx.execute(sql)
                    await tx.execute(
                        self._sql_insert_migration(),
                        [migration_id],
                    )

                self.logger.info("Migration applied: %s", migration_id)

            except Exception as exc:
                raise DatabaseMigrationError(
                    f"Failed to apply migration {migration_id}: {exc}"
                ) from exc

    async def ensure_migrations_table(self) -> None:
        if self.config.driver == DatabaseDriver.POSTGRESQL:
            sql = """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        else:
            sql = """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """

        await self.execute(sql)

    def _sql_select_migration(self) -> str:
        if self.config.driver == DatabaseDriver.POSTGRESQL:
            return "SELECT id FROM schema_migrations WHERE id = $1"
        return "SELECT id FROM schema_migrations WHERE id = ?"

    def _sql_insert_migration(self) -> str:
        if self.config.driver == DatabaseDriver.POSTGRESQL:
            return "INSERT INTO schema_migrations(id) VALUES($1)"
        return "INSERT INTO schema_migrations(id) VALUES(?)"

    def _build_backend(self) -> DatabaseBackend:
        if self.config.driver == DatabaseDriver.POSTGRESQL:
            return PostgresBackend(self.config, self.logger)

        if self.config.driver == DatabaseDriver.SQLITE:
            return SQLiteBackend(self.config, self.logger)

        raise DatabaseConnectionError(f"Unsupported database driver: {self.config.driver}")

    async def _retry(self, operation):
        last_exc: Optional[BaseException] = None

        for attempt in range(1, self.config.retry_attempts + 1):
            try:
                return await operation()

            except Exception as exc:
                last_exc = exc

                if attempt >= self.config.retry_attempts:
                    break

                await asyncio.sleep(
                    self.config.retry_base_delay_seconds * (2 ** (attempt - 1))
                )

        raise last_exc or DatabaseError("Unknown database retry error")

    def _record_query(self, query: str, started: float, success: bool) -> None:
        elapsed_ms = (time.monotonic() - started) * 1000

        tags = {
            **self._tags(),
            "success": str(success).lower(),
            "operation": query_operation(query),
        }

        self.metrics.increment("database.query.count", tags=tags)
        self.metrics.timing("database.query.latency_ms", elapsed_ms, tags=tags)

        if not success:
            self.metrics.increment("database.query.error", tags=tags)

        if elapsed_ms >= self.config.slow_query_threshold_ms:
            self.metrics.increment("database.query.slow", tags=tags)
            self.logger.warning(
                "Slow database query detected: %.2fms | %s",
                elapsed_ms,
                compact_sql(query),
            )

        elif self.config.enable_query_logging:
            self.logger.info(
                "Database query executed: %.2fms | %s",
                elapsed_ms,
                compact_sql(query),
            )

    def _tags(self) -> Dict[str, str]:
        return {
            "driver": self.config.driver.value,
            "database": self.config.database,
            "application": self.config.application_name,
        }


class UnitOfWork:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.connection: Optional[DatabaseBackend] = None
        self._ctx: Any = None

    async def __aenter__(self) -> "UnitOfWork":
        self._ctx = self.database.transaction()
        self.connection = await self._ctx.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._ctx.__aexit__(exc_type, exc, tb)

    async def fetch_all(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        self._ensure_active()
        return await self.connection.fetch_all(query, params)

    async def fetch_one(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        self._ensure_active()
        return await self.connection.fetch_one(query, params)

    async def execute(
        self,
        query: str,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
    ) -> int:
        self._ensure_active()
        return await self.connection.execute(query, params)

    async def execute_many(
        self,
        query: str,
        params: Iterable[Sequence[Any] | Mapping[str, Any]],
    ) -> int:
        self._ensure_active()
        return await self.connection.execute_many(query, params)

    def _ensure_active(self) -> None:
        if self.connection is None:
            raise DatabaseTransactionError("UnitOfWork is not active")


class BaseRepository(Generic[T], abc.ABC):
    def __init__(self, database: Database | DatabaseBackend | UnitOfWork) -> None:
        self.database = database

    @abc.abstractmethod
    async def get_by_id(self, entity_id: str) -> Optional[T]:
        raise NotImplementedError

    @abc.abstractmethod
    async def save(self, entity: T) -> T:
        raise NotImplementedError

    @abc.abstractmethod
    async def delete(self, entity_id: str) -> None:
        raise NotImplementedError


def query_operation(query: str) -> str:
    compact = query.strip().split()
    if not compact:
        return "unknown"
    return compact[0].lower()


def compact_sql(query: str, max_length: int = 500) -> str:
    compact = " ".join(query.strip().split())
    if len(compact) <= max_length:
        return compact
    return compact[:max_length] + "..."


def hash_sql(sql: str) -> str:
    import hashlib

    return hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]


def postgres_placeholders(count: int, start: int = 1) -> str:
    return ", ".join(f"${i}" for i in range(start, start + count))


def sqlite_placeholders(count: int) -> str:
    return ", ".join("?" for _ in range(count))


def build_database_from_env() -> Database:
    driver = DatabaseDriver(
        str(os.getenv("DB_DRIVER", DatabaseDriver.POSTGRESQL.value)).lower()
    )

    config = DatabaseConfig(
        driver=driver,
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", "kwanza_ai"),
        username=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
        sqlite_path=os.getenv("SQLITE_PATH", "./storage/kwanza_ai.db"),
        min_pool_size=int(os.getenv("DB_POOL_MIN_SIZE", "1")),
        max_pool_size=int(os.getenv("DB_POOL_MAX_SIZE", "20")),
        connect_timeout_seconds=float(os.getenv("DB_CONNECT_TIMEOUT_SECONDS", "10")),
        command_timeout_seconds=float(os.getenv("DB_COMMAND_TIMEOUT_SECONDS", "60")),
        statement_timeout_ms=int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "60000")),
        application_name=os.getenv("DB_APPLICATION_NAME", "kwanza-ai-core"),
        enable_query_logging=os.getenv("DB_ENABLE_QUERY_LOGGING", "false").lower()
        == "true",
        slow_query_threshold_ms=float(os.getenv("DB_SLOW_QUERY_THRESHOLD_MS", "500")),
    )

    return Database(config=config)
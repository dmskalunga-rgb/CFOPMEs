# kwanza-ai-core/tests/conftest.py
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterator

import pytest


# =========================================================
# Test Environment Defaults
# =========================================================

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_DEBUG", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("LOG_FORMAT", "json")

os.environ.setdefault("CACHE_BACKEND", "memory")
os.environ.setdefault("DB_DRIVER", "sqlite")
os.environ.setdefault("STORAGE_BACKEND", "local")

os.environ.setdefault("METRICS_ENABLED", "true")
os.environ.setdefault("TRACER_ENABLED", "false")
os.environ.setdefault("ALERTING_ENABLED", "false")
os.environ.setdefault("AUDIT_ENABLED", "true")
os.environ.setdefault("SECURITY_SECRET_KEY", "test-secret-key-change-me-with-32-chars")


# =========================================================
# Pytest Configuration
# =========================================================

def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: testes unitários rápidos")
    config.addinivalue_line("markers", "integration: testes de integração")
    config.addinivalue_line("markers", "e2e: testes ponta a ponta")
    config.addinivalue_line("markers", "security: testes de segurança")
    config.addinivalue_line("markers", "observability: testes de observabilidade")
    config.addinivalue_line("markers", "slow: testes lentos")
    config.addinivalue_line("markers", "database: testes com banco de dados")
    config.addinivalue_line("markers", "redis: testes que exigem Redis")
    config.addinivalue_line("markers", "s3: testes que exigem S3/MinIO")
    config.addinivalue_line("markers", "ml: testes de machine learning")


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# =========================================================
# Paths / Temp Directories
# =========================================================

@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def tests_root() -> Path:
    return Path(__file__).resolve().parent


@pytest.fixture()
def test_id() -> str:
    return uuid.uuid4().hex


@pytest.fixture()
def temp_dir() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="kwanza-test-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def storage_dir(temp_dir: Path) -> Path:
    path = temp_dir / "storage"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture()
def logs_dir(temp_dir: Path) -> Path:
    path = temp_dir / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


# =========================================================
# Environment Fixture
# =========================================================

@pytest.fixture()
def test_env(temp_dir: Path, storage_dir: Path, logs_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, str]:
    env = {
        "APP_ENV": "test",
        "APP_DEBUG": "false",
        "INSTANCE_ID": f"test-{uuid.uuid4().hex[:8]}",
        "DB_DRIVER": "sqlite",
        "SQLITE_PATH": str(temp_dir / "test.db"),
        "CACHE_BACKEND": "memory",
        "CACHE_NAMESPACE": f"test-cache-{uuid.uuid4().hex[:8]}",
        "STORAGE_BACKEND": "local",
        "STORAGE_BASE_PATH": str(storage_dir),
        "STORAGE_NAMESPACE": f"test-storage-{uuid.uuid4().hex[:8]}",
        "LOG_LEVEL": "WARNING",
        "LOG_ENABLE_CONSOLE": "false",
        "LOG_ENABLE_FILE": "false",
        "AUDIT_JSONL_PATH": str(logs_dir / "audit-events.jsonl"),
        "PROFILER_EXPORT_PATH": str(logs_dir / "profiles.jsonl"),
        "TRACER_JSONL_PATH": str(logs_dir / "traces.jsonl"),
        "OBS_LOGGER_JSONL_PATH": str(logs_dir / "observability-events.jsonl"),
        "OBS_METRICS_EXPORT_JSONL_PATH": str(logs_dir / "observability-metrics.jsonl"),
        "SECURITY_SECRET_KEY": "test-secret-key-change-me-with-32-chars",
        "SECURITY_ALLOWED_API_KEYS": "kwa_test_key",
    }

    for key, value in env.items():
        monkeypatch.setenv(key, value)

    return env


# =========================================================
# Core Fixtures
# =========================================================

@pytest.fixture()
def settings(test_env: Dict[str, str]) -> Any:
    from infrastructure.config import build_settings

    return build_settings(os.environ)


@pytest.fixture()
def metrics_manager(test_env: Dict[str, str]) -> Any:
    from infrastructure.metrics import MetricsConfig, MetricsManager

    return MetricsManager(
        MetricsConfig(
            namespace="kwanza_test",
            service_name="kwanza-ai-core-test",
            environment="test",
            instance_id=os.environ["INSTANCE_ID"],
            enabled=True,
        )
    )


@pytest.fixture()
def metrics_sink(metrics_manager: Any) -> Any:
    return metrics_manager.sink


@pytest.fixture()
def cache(test_env: Dict[str, str], metrics_sink: Any) -> Any:
    from infrastructure.cache import AsyncCache, CacheBackend, CacheConfig

    return AsyncCache(
        config=CacheConfig(
            backend=CacheBackend.MEMORY,
            namespace=os.environ["CACHE_NAMESPACE"],
            default_ttl_seconds=30,
            max_memory_items=10_000,
        ),
        metrics=metrics_sink,
    )


@pytest.fixture()
async def database(test_env: Dict[str, str], metrics_sink: Any) -> AsyncIterator[Any]:
    from infrastructure.database import Database, DatabaseConfig, DatabaseDriver

    db = Database(
        DatabaseConfig(
            driver=DatabaseDriver.SQLITE,
            sqlite_path=os.environ["SQLITE_PATH"],
            database="kwanza_test",
            application_name="kwanza-ai-core-test",
        ),
        metrics=metrics_sink,
    )

    await db.connect()

    try:
        yield db
    finally:
        await db.close()


@pytest.fixture()
async def storage(test_env: Dict[str, str], storage_dir: Path, metrics_sink: Any) -> AsyncIterator[Any]:
    from infrastructure.storage import StorageBackend, StorageConfig, StorageService

    service = StorageService(
        config=StorageConfig(
            backend=StorageBackend.LOCAL,
            base_path=storage_dir,
            namespace=os.environ["STORAGE_NAMESPACE"],
            bucket_name="kwanza-test",
            enable_versioning=True,
            enable_checksums=True,
        ),
        metrics=metrics_sink,
    )

    try:
        yield service
    finally:
        await service.close()


@pytest.fixture()
async def event_bus(test_env: Dict[str, str], metrics_sink: Any) -> AsyncIterator[Any]:
    from infrastructure.event_bus import EventBus, EventBusConfig, LoggingMiddleware

    bus = EventBus(
        config=EventBusConfig(
            max_queue_size=1_000,
            worker_count=1,
            retry_attempts=2,
            handler_timeout_seconds=5,
            enable_dead_letter=True,
            enable_idempotency=True,
        ),
        metrics=metrics_sink,
    )
    bus.use(LoggingMiddleware())

    await bus.start()

    try:
        yield bus
    finally:
        await bus.stop()


@pytest.fixture()
def security_manager(test_env: Dict[str, str], metrics_sink: Any) -> Any:
    from infrastructure.security import SecurityConfig, SecurityManager, TokenAlgorithm

    return SecurityManager(
        config=SecurityConfig(
            secret_key=os.environ["SECURITY_SECRET_KEY"],
            jwt_algorithm=TokenAlgorithm.HS256,
            allowed_api_keys=("kwa_test_key",),
            enable_rate_limit=True,
            rate_limit_per_minute=10_000,
        ),
        metrics=metrics_sink,
    )


@pytest.fixture()
def health_manager(test_env: Dict[str, str], metrics_sink: Any) -> Any:
    from infrastructure.healthcheck import HealthCheckConfig, HealthCheckManager

    return HealthCheckManager(
        config=HealthCheckConfig(
            service_name="kwanza-ai-core-test",
            environment="test",
            instance_id=os.environ["INSTANCE_ID"],
            default_timeout_seconds=2,
            cache_ttl_seconds=0,
        ),
        metrics=metrics_sink,
    )


# =========================================================
# Observability Fixtures
# =========================================================

@pytest.fixture()
async def audit_service(test_env: Dict[str, str], metrics_sink: Any) -> AsyncIterator[Any]:
    from observability.audit import AuditConfig, AuditService, AuditStoreType

    service = AuditService(
        config=AuditConfig(
            service_name="kwanza-ai-core-test",
            environment="test",
            instance_id=os.environ["INSTANCE_ID"],
            store_type=AuditStoreType.JSONL,
            jsonl_path=Path(os.environ["AUDIT_JSONL_PATH"]),
            enable_hash_chain=True,
            enable_redaction=True,
        ),
        metrics=metrics_sink,
    )

    try:
        yield service
    finally:
        await service.close()


@pytest.fixture()
def tracer(test_env: Dict[str, str], metrics_sink: Any) -> Any:
    from observability.tracer import InMemorySpanExporter, Tracer, TracerConfig

    exporter = InMemorySpanExporter()

    return Tracer(
        config=TracerConfig(
            service_name="kwanza-ai-core-test",
            environment="test",
            instance_id=os.environ["INSTANCE_ID"],
            enabled=True,
            export_to_console=False,
        ),
        exporters=[exporter],
        metrics=metrics_sink,
    )


@pytest.fixture()
def profiler(test_env: Dict[str, str], metrics_sink: Any) -> Any:
    from observability.profiler import Profiler, ProfilerConfig, ProfileStore

    return Profiler(
        config=ProfilerConfig(
            service_name="kwanza-ai-core-test",
            environment="test",
            instance_id=os.environ["INSTANCE_ID"],
            enabled=True,
            export_path=os.environ["PROFILER_EXPORT_PATH"],
            max_records=1_000,
        ),
        metrics=metrics_sink,
        store=ProfileStore(max_records=1_000),
    )


@pytest.fixture()
def observability_metrics(test_env: Dict[str, str]) -> Any:
    from observability.metrics import ObservabilityMetrics, ObservabilityMetricsConfig

    return ObservabilityMetrics(
        config=ObservabilityMetricsConfig(
            service_name="kwanza-ai-core-test",
            environment="test",
            instance_id=os.environ["INSTANCE_ID"],
            namespace="kwanza_test",
            enabled=True,
            export_jsonl_path=os.environ["OBS_METRICS_EXPORT_JSONL_PATH"],
        )
    )


@pytest.fixture()
async def observability_logger(test_env: Dict[str, str], metrics_sink: Any) -> AsyncIterator[Any]:
    from observability.logger import ObservabilityLogger, ObservabilityLoggerConfig

    logger = ObservabilityLogger(
        name="kwanza.test",
        config=ObservabilityLoggerConfig(
            service_name="kwanza-ai-core-test",
            environment="test",
            instance_id=os.environ["INSTANCE_ID"],
            console_enabled=False,
            jsonl_enabled=True,
            jsonl_path=os.environ["OBS_LOGGER_JSONL_PATH"],
            flush_interval_seconds=0.01,
        ),
        metrics=metrics_sink,
    )

    await logger.start()

    try:
        yield logger
    finally:
        await logger.stop()


# =========================================================
# Factories
# =========================================================

@pytest.fixture()
def sample_user() -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "name": "Test User",
        "email": "test@example.com",
        "tenant_id": "tenant-test",
        "roles": ["admin"],
        "permissions": ["*"],
    }


@pytest.fixture()
def sample_payload() -> Dict[str, Any]:
    return {
        "entity_id": str(uuid.uuid4()),
        "amount": 1500.75,
        "currency": "AOA",
        "category": "revenue",
        "timestamp": "2026-01-01T00:00:00Z",
        "metadata": {
            "source": "pytest",
            "confidence": 0.98,
        },
    }


@pytest.fixture()
def event_factory() -> Any:
    from infrastructure.event_bus import EventPriority, create_event

    def _factory(
        name: str = "test.event",
        payload: Dict[str, Any] | None = None,
        priority: EventPriority = EventPriority.NORMAL,
    ):
        return create_event(
            name=name,
            payload=payload or {"ok": True},
            source="pytest",
            tenant_id="tenant-test",
            user_id="user-test",
            priority=priority,
        )

    return _factory


@pytest.fixture()
def audit_actor(sample_user: Dict[str, Any]) -> Any:
    from observability.audit import AuditActor

    return AuditActor(
        actor_id=sample_user["id"],
        actor_type="user",
        tenant_id=sample_user["tenant_id"],
        ip_address="127.0.0.1",
        user_agent="pytest",
    )


@pytest.fixture()
def audit_resource() -> Any:
    from observability.audit import AuditResource

    return AuditResource(
        resource_type="test_resource",
        resource_id=str(uuid.uuid4()),
        resource_name="Test Resource",
        owner_tenant_id="tenant-test",
    )


# =========================================================
# Helpers
# =========================================================

@pytest.fixture()
def json_loader() -> Any:
    def _load(path: str | Path) -> Any:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    return _load


@pytest.fixture()
def jsonl_loader() -> Any:
    def _load(path: str | Path) -> list[Dict[str, Any]]:
        selected = Path(path)
        if not selected.exists:
            return []

        rows: list[Dict[str, Any]] = []
        for line in selected.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    return _load


@pytest.fixture()
def assert_eventually() -> Any:
    async def _assert_eventually(
        predicate,
        *,
        timeout_seconds: float = 3.0,
        interval_seconds: float = 0.05,
        message: str = "Condition was not met in time",
    ) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds

        while asyncio.get_running_loop().time() < deadline:
            result = predicate()

            if hasattr(result, "__await__"):
                result = await result

            if result:
                return

            await asyncio.sleep(interval_seconds)

        raise AssertionError(message)

    return _assert_eventually


# =========================================================
# Automatic Cleanup
# =========================================================

@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    original_env = os.environ.copy()
    yield

    for key in list(os.environ.keys()):
        if key not in original_env:
            monkeypatch.delenv(key, raising=False)

    for key, value in original_env.items():
        monkeypatch.setenv(key, value)
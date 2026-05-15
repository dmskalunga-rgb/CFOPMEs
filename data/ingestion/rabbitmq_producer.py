"""
data/ingestion/rabbitmq_producer.py

RabbitMQ Producer enterprise para pipelines de ingestão e publicação de eventos.

Recursos principais:
- Publicação resiliente com retry e backoff exponencial.
- Publisher confirms para maior segurança de entrega.
- Envelope padronizado de eventos.
- Headers com correlation_id, causation_id, trace_id e metadados.
- Suporte a exchange, routing_key, mandatory flag e delivery_mode persistente.
- Fallback local JSONL quando RabbitMQ estiver indisponível.
- Publicação individual e em batch.
- Validação e transformação plugáveis antes da publicação.
- Métricas internas de publicação, falha, retry, fallback e latência.
- Logs estruturados.
- Suporte a TLS, heartbeat, virtual host e timeouts.
- Context manager para flush/close seguro.

Dependências recomendadas:
    pip install pika pydantic

Variáveis de ambiente suportadas:
    RABBITMQ_HOST=localhost
    RABBITMQ_PORT=5672
    RABBITMQ_VHOST=/
    RABBITMQ_USERNAME=guest
    RABBITMQ_PASSWORD=guest
    RABBITMQ_EXCHANGE=enterprise.ingestion.exchange
    RABBITMQ_ROUTING_KEY=enterprise.ingestion
    RABBITMQ_EXCHANGE_TYPE=direct
    RABBITMQ_DURABLE_EXCHANGE=true
    RABBITMQ_MANDATORY=false
    RABBITMQ_DELIVERY_MODE=2
    RABBITMQ_HEARTBEAT=60
    RABBITMQ_BLOCKED_CONNECTION_TIMEOUT=300
    RABBITMQ_CONNECTION_ATTEMPTS=3
    RABBITMQ_RETRY_DELAY_SECONDS=5
    RABBITMQ_MAX_RETRIES=3
    RABBITMQ_RETRY_BASE_SECONDS=0.5
    RABBITMQ_RETRY_MAX_SECONDS=15.0
    RABBITMQ_USE_TLS=false
    RABBITMQ_FALLBACK_JSONL=data/fallback/rabbitmq_producer_fallback.jsonl
"""

from __future__ import annotations

import json
import logging
import os
import random
import socket
import ssl
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Union

try:
    import pika
    from pika.adapters.blocking_connection import BlockingChannel
    from pika.exceptions import AMQPError, AMQPConnectionError, ChannelClosedByBroker, UnroutableError
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Dependência ausente: instale com `pip install pika`.") from exc

try:
    from pydantic import BaseModel, Field, ValidationError
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Dependência ausente: instale com `pip install pydantic`.") from exc


# =============================================================================
# Logging
# =============================================================================

LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(name)s | "
    "%(message)s | service=%(service)s host=%(host)s"
)


class ContextFilter(logging.Filter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name
        self.host = socket.gethostname()

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self.service_name
        record.host = self.host
        return True


def build_logger(name: str = "data.ingestion.rabbitmq_producer") -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ContextFilter(service_name=os.getenv("SERVICE_NAME", "rabbitmq-producer")))

    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = build_logger()


# =============================================================================
# Enums e Models
# =============================================================================


class PublishStatus(str, Enum):
    CREATED = "created"
    VALIDATED = "validated"
    SERIALIZED = "serialized"
    PUBLISHED = "published"
    FAILED = "failed"
    FALLBACK_STORED = "fallback_stored"


class FallbackStrategy(str, Enum):
    DISABLED = "disabled"
    JSONL = "jsonl"
    RAISE = "raise"


class ExchangeType(str, Enum):
    DIRECT = "direct"
    TOPIC = "topic"
    FANOUT = "fanout"
    HEADERS = "headers"


class EventEnvelope(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    source: str = "unknown"
    occurred_at: str = Field(default_factory=lambda: utc_now_iso())
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    causation_id: Optional[str] = None
    trace_id: Optional[str] = None
    schema_version: str = "1.0"
    data: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    base_seconds: float = 0.5
    max_seconds: float = 15.0
    jitter: bool = True

    def sleep_seconds(self, attempt: int) -> float:
        exponential = self.base_seconds * (2 ** max(0, attempt - 1))
        jitter_value = random.uniform(0, self.base_seconds) if self.jitter else 0.0
        return min(exponential + jitter_value, self.max_seconds)


@dataclass(frozen=True)
class RabbitMQProducerConfig:
    host: str = "localhost"
    port: int = 5672
    virtual_host: str = "/"
    username: str = "guest"
    password: str = "guest"

    exchange_name: str = "enterprise.ingestion.exchange"
    routing_key: str = "enterprise.ingestion"
    exchange_type: ExchangeType = ExchangeType.DIRECT
    durable_exchange: bool = True
    auto_declare_exchange: bool = False

    mandatory: bool = False
    delivery_mode: int = 2
    content_type: str = "application/json"

    heartbeat: int = 60
    blocked_connection_timeout: int = 300
    connection_attempts: int = 3
    retry_delay_seconds: int = 5
    socket_timeout: int = 10

    publisher_confirms: bool = True

    use_tls: bool = False
    tls_ca_cert: Optional[str] = None
    tls_certfile: Optional[str] = None
    tls_keyfile: Optional[str] = None

    fallback_strategy: FallbackStrategy = FallbackStrategy.JSONL
    fallback_jsonl_path: Optional[Path] = Path("data/fallback/rabbitmq_producer_fallback.jsonl")

    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    @staticmethod
    def from_env() -> "RabbitMQProducerConfig":
        fallback_path_raw = os.getenv("RABBITMQ_FALLBACK_JSONL", "data/fallback/rabbitmq_producer_fallback.jsonl")

        return RabbitMQProducerConfig(
            host=os.getenv("RABBITMQ_HOST", "localhost"),
            port=int(os.getenv("RABBITMQ_PORT", "5672")),
            virtual_host=os.getenv("RABBITMQ_VHOST", "/"),
            username=os.getenv("RABBITMQ_USERNAME", "guest"),
            password=os.getenv("RABBITMQ_PASSWORD", "guest"),
            exchange_name=os.getenv("RABBITMQ_EXCHANGE", "enterprise.ingestion.exchange"),
            routing_key=os.getenv("RABBITMQ_ROUTING_KEY", "enterprise.ingestion"),
            exchange_type=ExchangeType(os.getenv("RABBITMQ_EXCHANGE_TYPE", ExchangeType.DIRECT.value)),
            durable_exchange=env_bool("RABBITMQ_DURABLE_EXCHANGE", True),
            auto_declare_exchange=env_bool("RABBITMQ_AUTO_DECLARE_EXCHANGE", False),
            mandatory=env_bool("RABBITMQ_MANDATORY", False),
            delivery_mode=int(os.getenv("RABBITMQ_DELIVERY_MODE", "2")),
            content_type=os.getenv("RABBITMQ_CONTENT_TYPE", "application/json"),
            heartbeat=int(os.getenv("RABBITMQ_HEARTBEAT", "60")),
            blocked_connection_timeout=int(os.getenv("RABBITMQ_BLOCKED_CONNECTION_TIMEOUT", "300")),
            connection_attempts=int(os.getenv("RABBITMQ_CONNECTION_ATTEMPTS", "3")),
            retry_delay_seconds=int(os.getenv("RABBITMQ_RETRY_DELAY_SECONDS", "5")),
            socket_timeout=int(os.getenv("RABBITMQ_SOCKET_TIMEOUT", "10")),
            publisher_confirms=env_bool("RABBITMQ_PUBLISHER_CONFIRMS", True),
            use_tls=env_bool("RABBITMQ_USE_TLS", False),
            tls_ca_cert=os.getenv("RABBITMQ_TLS_CA_CERT") or None,
            tls_certfile=os.getenv("RABBITMQ_TLS_CERTFILE") or None,
            tls_keyfile=os.getenv("RABBITMQ_TLS_KEYFILE") or None,
            fallback_strategy=FallbackStrategy(os.getenv("RABBITMQ_FALLBACK_STRATEGY", FallbackStrategy.JSONL.value)),
            fallback_jsonl_path=Path(fallback_path_raw) if fallback_path_raw else None,
            retry_policy=RetryPolicy(
                max_retries=int(os.getenv("RABBITMQ_MAX_RETRIES", "3")),
                base_seconds=float(os.getenv("RABBITMQ_RETRY_BASE_SECONDS", "0.5")),
                max_seconds=float(os.getenv("RABBITMQ_RETRY_MAX_SECONDS", "15.0")),
                jitter=env_bool("RABBITMQ_RETRY_JITTER", True),
            ),
        )


@dataclass
class PublishRequest:
    event_type: str
    data: Mapping[str, Any]
    exchange: Optional[str] = None
    routing_key: Optional[str] = None
    source: str = "unknown"
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    trace_id: Optional[str] = None
    schema_version: str = "1.0"
    metadata: Optional[Mapping[str, Any]] = None
    headers: Optional[Mapping[str, Any]] = None
    message_id: Optional[str] = None
    app_id: Optional[str] = None
    priority: Optional[int] = None
    expiration: Optional[str] = None
    content_type: Optional[str] = None
    mandatory: Optional[bool] = None


@dataclass
class PublishResult:
    success: bool
    exchange: Optional[str] = None
    routing_key: Optional[str] = None
    event_id: Optional[str] = None
    message_id: Optional[str] = None
    correlation_id: Optional[str] = None
    error: Optional[str] = None
    status: PublishStatus = PublishStatus.CREATED


@dataclass
class RabbitMQProducerMetrics:
    publish_requested: int = 0
    published: int = 0
    failed: int = 0
    retries: int = 0
    fallback_stored: int = 0
    batches_requested: int = 0
    total_publish_seconds: float = 0.0
    last_published_at: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        avg_latency = self.total_publish_seconds / self.publish_requested if self.publish_requested else 0.0
        return {
            "publish_requested": self.publish_requested,
            "published": self.published,
            "failed": self.failed,
            "retries": self.retries,
            "fallback_stored": self.fallback_stored,
            "batches_requested": self.batches_requested,
            "average_publish_seconds": round(avg_latency, 6),
            "total_publish_seconds": round(self.total_publish_seconds, 6),
            "last_published_at": self.last_published_at,
        }


# =============================================================================
# Protocols
# =============================================================================


class EventValidator(Protocol):
    def validate(self, envelope: EventEnvelope) -> EventEnvelope:
        """Valida envelope antes da publicação."""


class EventTransformer(Protocol):
    def transform(self, envelope: EventEnvelope) -> EventEnvelope:
        """Transforma envelope antes da publicação."""


class PublishObserver(Protocol):
    def on_publish(self, result: PublishResult) -> None:
        """Recebe resultado de publicação."""


# =============================================================================
# Implementações base
# =============================================================================


class NoOpEventValidator:
    def validate(self, envelope: EventEnvelope) -> EventEnvelope:
        return envelope


class NoOpEventTransformer:
    def transform(self, envelope: EventEnvelope) -> EventEnvelope:
        return envelope


class LoggingPublishObserver:
    def on_publish(self, result: PublishResult) -> None:
        if result.success:
            logger.info(
                "Evento publicado RabbitMQ. exchange=%s routing_key=%s event_id=%s message_id=%s",
                result.exchange,
                result.routing_key,
                result.event_id,
                result.message_id,
            )
        else:
            logger.error(
                "Falha publicação RabbitMQ. exchange=%s routing_key=%s event_id=%s error=%s status=%s",
                result.exchange,
                result.routing_key,
                result.event_id,
                result.error,
                result.status.value,
            )


class RequiredEventFieldsValidator:
    def __init__(self, required_data_fields: Sequence[str]) -> None:
        self.required_data_fields = list(required_data_fields)

    def validate(self, envelope: EventEnvelope) -> EventEnvelope:
        missing = [field for field in self.required_data_fields if get_nested_value(envelope.data, field) in (None, "")]
        if missing:
            raise ValueError(f"Campos obrigatórios ausentes no evento: {missing}")
        return envelope


class MetadataTransformer:
    def __init__(self, metadata: Mapping[str, Any]) -> None:
        self.metadata = dict(metadata)

    def transform(self, envelope: EventEnvelope) -> EventEnvelope:
        envelope.metadata.update(self.metadata)
        envelope.metadata.setdefault("producer_host", socket.gethostname())
        envelope.metadata.setdefault("published_by", os.getenv("SERVICE_NAME", "rabbitmq-producer"))
        return envelope


# =============================================================================
# Producer principal
# =============================================================================


class EnterpriseRabbitMQProducer:
    def __init__(
        self,
        config: Optional[RabbitMQProducerConfig] = None,
        validator: Optional[EventValidator] = None,
        transformer: Optional[EventTransformer] = None,
        observers: Optional[Sequence[PublishObserver]] = None,
    ) -> None:
        self.config = config or RabbitMQProducerConfig.from_env()
        self.validator = validator or NoOpEventValidator()
        self.transformer = transformer or NoOpEventTransformer()
        self.observers = list(observers or [LoggingPublishObserver()])
        self.metrics = RabbitMQProducerMetrics()

        self.connection: Optional[pika.BlockingConnection] = None
        self.channel: Optional[BlockingChannel] = None

        self._connect()

    def publish(self, request: PublishRequest) -> PublishResult:
        started = time.perf_counter()
        self.metrics.publish_requested += 1

        try:
            envelope = self._build_envelope(request)
            envelope = self.validator.validate(envelope)
            envelope = self.transformer.transform(envelope)

            body = self._serialize(envelope)
            exchange = request.exchange or self.config.exchange_name
            routing_key = request.routing_key or self.config.routing_key
            properties = self._build_properties(request, envelope)
            mandatory = self.config.mandatory if request.mandatory is None else request.mandatory

            self._publish_with_retry(
                exchange=exchange,
                routing_key=routing_key,
                body=body,
                properties=properties,
                mandatory=mandatory,
            )

            self.metrics.published += 1
            self.metrics.last_published_at = utc_now_iso()

            result = PublishResult(
                success=True,
                exchange=exchange,
                routing_key=routing_key,
                event_id=envelope.event_id,
                message_id=properties.message_id,
                correlation_id=envelope.correlation_id,
                status=PublishStatus.PUBLISHED,
            )
            self._notify_observers(result)
            return result

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.failed += 1
            logger.exception("Falha ao publicar evento RabbitMQ. event_type=%s error=%s", request.event_type, exc)
            result = self._handle_publish_failure(request, exc)
            self._notify_observers(result)
            return result

        finally:
            self.metrics.total_publish_seconds += time.perf_counter() - started

    def publish_many(self, requests: Iterable[PublishRequest]) -> List[PublishResult]:
        self.metrics.batches_requested += 1
        results: List[PublishResult] = []

        for request in requests:
            results.append(self.publish(request))

        return results

    def publish_raw(
        self,
        body: Union[str, bytes, Mapping[str, Any]],
        exchange: Optional[str] = None,
        routing_key: Optional[str] = None,
        headers: Optional[Mapping[str, Any]] = None,
        content_type: Optional[str] = None,
        message_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        mandatory: Optional[bool] = None,
    ) -> PublishResult:
        started = time.perf_counter()
        self.metrics.publish_requested += 1

        selected_exchange = exchange or self.config.exchange_name
        selected_routing_key = routing_key or self.config.routing_key

        try:
            if isinstance(body, bytes):
                payload = body
            elif isinstance(body, str):
                payload = body.encode("utf-8")
            else:
                payload = json.dumps(body, ensure_ascii=False, default=json_default).encode("utf-8")

            properties = pika.BasicProperties(
                content_type=content_type or self.config.content_type,
                delivery_mode=self.config.delivery_mode,
                message_id=message_id or str(uuid.uuid4()),
                correlation_id=correlation_id or str(uuid.uuid4()),
                timestamp=int(time.time()),
                app_id=os.getenv("SERVICE_NAME", "rabbitmq-producer"),
                headers=dict(headers or {}),
            )

            self._publish_with_retry(
                exchange=selected_exchange,
                routing_key=selected_routing_key,
                body=payload,
                properties=properties,
                mandatory=self.config.mandatory if mandatory is None else mandatory,
            )

            self.metrics.published += 1
            self.metrics.last_published_at = utc_now_iso()

            result = PublishResult(
                success=True,
                exchange=selected_exchange,
                routing_key=selected_routing_key,
                message_id=properties.message_id,
                correlation_id=properties.correlation_id,
                status=PublishStatus.PUBLISHED,
            )
            self._notify_observers(result)
            return result

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.failed += 1
            logger.exception("Falha ao publicar raw RabbitMQ. error=%s", exc)
            result = PublishResult(
                success=False,
                exchange=selected_exchange,
                routing_key=selected_routing_key,
                message_id=message_id,
                correlation_id=correlation_id,
                error=str(exc),
                status=PublishStatus.FAILED,
            )
            self._notify_observers(result)
            return result

        finally:
            self.metrics.total_publish_seconds += time.perf_counter() - started

    def close(self) -> None:
        logger.info("Encerrando RabbitMQ producer. metrics=%s", json.dumps(self.metrics.snapshot()))
        self._safe_close()

    def _connect(self) -> None:
        parameters = pika.ConnectionParameters(
            host=self.config.host,
            port=self.config.port,
            virtual_host=self.config.virtual_host,
            credentials=pika.PlainCredentials(self.config.username, self.config.password),
            heartbeat=self.config.heartbeat,
            blocked_connection_timeout=self.config.blocked_connection_timeout,
            connection_attempts=self.config.connection_attempts,
            retry_delay=self.config.retry_delay_seconds,
            socket_timeout=self.config.socket_timeout,
            ssl_options=self._ssl_options(),
        )

        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()

        if self.config.publisher_confirms:
            self.channel.confirm_delivery()

        if self.config.auto_declare_exchange:
            self.channel.exchange_declare(
                exchange=self.config.exchange_name,
                exchange_type=self.config.exchange_type.value,
                durable=self.config.durable_exchange,
            )

        logger.info(
            "RabbitMQ producer conectado. host=%s port=%s exchange=%s routing_key=%s",
            self.config.host,
            self.config.port,
            self.config.exchange_name,
            self.config.routing_key,
        )

    def _ensure_connected(self) -> None:
        if self.connection and self.connection.is_open and self.channel and self.channel.is_open:
            return
        self._safe_close()
        self._connect()

    def _publish_with_retry(
        self,
        exchange: str,
        routing_key: str,
        body: bytes,
        properties: pika.BasicProperties,
        mandatory: bool,
    ) -> None:
        attempts = self.config.retry_policy.max_retries + 1
        last_error: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                self._ensure_connected()
                assert self.channel is not None

                self.channel.basic_publish(
                    exchange=exchange,
                    routing_key=routing_key,
                    body=body,
                    properties=properties,
                    mandatory=mandatory,
                )
                return

            except (AMQPConnectionError, ChannelClosedByBroker, UnroutableError, AMQPError, OSError) as exc:
                last_error = exc
                logger.warning(
                    "Erro ao publicar RabbitMQ. attempt=%s/%s exchange=%s routing_key=%s error=%s",
                    attempt,
                    attempts,
                    exchange,
                    routing_key,
                    exc,
                )
                self._safe_close()

            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_error = exc
                logger.warning(
                    "Erro inesperado ao publicar RabbitMQ. attempt=%s/%s error=%s",
                    attempt,
                    attempts,
                    exc,
                )
                self._safe_close()

            if attempt < attempts:
                self.metrics.retries += 1
                time.sleep(self.config.retry_policy.sleep_seconds(attempt))

        raise RuntimeError("Falha ao publicar no RabbitMQ após retries máximos") from last_error

    def _build_envelope(self, request: PublishRequest) -> EventEnvelope:
        envelope = EventEnvelope(
            event_type=request.event_type,
            source=request.source,
            correlation_id=request.correlation_id or str(uuid.uuid4()),
            causation_id=request.causation_id,
            trace_id=request.trace_id,
            schema_version=request.schema_version,
            data=dict(request.data),
            metadata=dict(request.metadata or {}),
        )
        envelope.metadata.setdefault("producer_host", socket.gethostname())
        envelope.metadata.setdefault("producer_app", os.getenv("SERVICE_NAME", "rabbitmq-producer"))
        return envelope

    def _serialize(self, envelope: EventEnvelope) -> bytes:
        return json.dumps(model_to_dict(envelope), ensure_ascii=False, default=json_default).encode("utf-8")

    def _build_properties(self, request: PublishRequest, envelope: EventEnvelope) -> pika.BasicProperties:
        headers: Dict[str, Any] = {
            "x-event-id": envelope.event_id,
            "x-event-type": envelope.event_type,
            "x-source": envelope.source,
            "x-correlation-id": envelope.correlation_id,
            "x-schema-version": envelope.schema_version,
            "x-produced-at": utc_now_iso(),
        }

        if envelope.causation_id:
            headers["x-causation-id"] = envelope.causation_id
        if envelope.trace_id:
            headers["x-trace-id"] = envelope.trace_id
        if request.headers:
            headers.update(dict(request.headers))

        return pika.BasicProperties(
            content_type=request.content_type or self.config.content_type,
            delivery_mode=self.config.delivery_mode,
            message_id=request.message_id or envelope.event_id,
            correlation_id=envelope.correlation_id,
            timestamp=int(time.time()),
            app_id=request.app_id or os.getenv("SERVICE_NAME", "rabbitmq-producer"),
            priority=request.priority,
            expiration=request.expiration,
            headers=headers,
            type=envelope.event_type,
        )

    def _handle_publish_failure(self, request: PublishRequest, exc: Exception) -> PublishResult:
        if self.config.fallback_strategy == FallbackStrategy.RAISE:
            raise exc

        exchange = request.exchange or self.config.exchange_name
        routing_key = request.routing_key or self.config.routing_key

        if self.config.fallback_strategy == FallbackStrategy.DISABLED:
            return PublishResult(
                success=False,
                exchange=exchange,
                routing_key=routing_key,
                event_id=None,
                message_id=request.message_id,
                correlation_id=request.correlation_id,
                error=str(exc),
                status=PublishStatus.FAILED,
            )

        self._write_fallback_jsonl(request, exc)
        self.metrics.fallback_stored += 1

        return PublishResult(
            success=False,
            exchange=exchange,
            routing_key=routing_key,
            event_id=None,
            message_id=request.message_id,
            correlation_id=request.correlation_id,
            error=str(exc),
            status=PublishStatus.FALLBACK_STORED,
        )

    def _write_fallback_jsonl(self, request: PublishRequest, exc: Exception) -> None:
        if not self.config.fallback_jsonl_path:
            logger.warning("Fallback JSONL sem path configurado. Evento não armazenado. error=%s", exc)
            return

        self.config.fallback_jsonl_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "status": PublishStatus.FALLBACK_STORED.value,
            "failed_at": utc_now_iso(),
            "error": str(exc),
            "error_type": exc.__class__.__name__,
            "request": {
                "exchange": request.exchange or self.config.exchange_name,
                "routing_key": request.routing_key or self.config.routing_key,
                "event_type": request.event_type,
                "source": request.source,
                "correlation_id": request.correlation_id,
                "causation_id": request.causation_id,
                "trace_id": request.trace_id,
                "schema_version": request.schema_version,
                "message_id": request.message_id,
                "app_id": request.app_id,
                "priority": request.priority,
                "expiration": request.expiration,
                "content_type": request.content_type,
                "data": dict(request.data),
                "metadata": dict(request.metadata or {}),
                "headers": dict(request.headers or {}),
            },
        }

        with self.config.fallback_jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=json_default) + "\n")

        logger.warning(
            "Evento RabbitMQ armazenado em fallback JSONL. path=%s event_type=%s error=%s",
            self.config.fallback_jsonl_path,
            request.event_type,
            exc,
        )

    def _notify_observers(self, result: PublishResult) -> None:
        for observer in self.observers:
            try:
                observer.on_publish(result)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Observer de publicação falhou. error=%s", exc)

    def _ssl_options(self) -> Optional[pika.SSLOptions]:
        if not self.config.use_tls:
            return None

        context = ssl.create_default_context(cafile=self.config.tls_ca_cert)
        if self.config.tls_certfile and self.config.tls_keyfile:
            context.load_cert_chain(self.config.tls_certfile, self.config.tls_keyfile)

        return pika.SSLOptions(context, self.config.host)

    def _safe_close(self) -> None:
        try:
            if self.channel and self.channel.is_open:
                self.channel.close()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Falha ao fechar canal RabbitMQ producer: %s", exc)

        try:
            if self.connection and self.connection.is_open:
                self.connection.close()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Falha ao fechar conexão RabbitMQ producer: %s", exc)

        self.channel = None
        self.connection = None

    def __enter__(self) -> "EnterpriseRabbitMQProducer":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


# =============================================================================
# Utilitários
# =============================================================================


def get_nested_value(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def model_to_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()  # type: ignore[no-any-return]
    return model.dict()  # type: ignore[no-any-return]


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, Path)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return str(value)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "sim", "s"}


# =============================================================================
# Exemplo de uso
# =============================================================================


def example_publish() -> None:
    config = RabbitMQProducerConfig.from_env()

    producer = EnterpriseRabbitMQProducer(
        config=config,
        validator=RequiredEventFieldsValidator(["id"]),
        transformer=MetadataTransformer({"domain": "example", "environment": os.getenv("ENVIRONMENT", "dev")}),
    )

    request = PublishRequest(
        exchange=config.exchange_name,
        routing_key=config.routing_key,
        event_type="customer.created",
        source="example-service",
        data={
            "id": 1,
            "name": "Cliente Exemplo",
            "created_at": utc_now_iso(),
        },
        metadata={"schema": "customer-created-v1"},
    )

    result = producer.publish(request)
    logger.info("Resultado da publicação RabbitMQ: %s", result)
    producer.close()


if __name__ == "__main__":
    example_publish()

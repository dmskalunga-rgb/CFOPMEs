"""
data/ingestion/rabbitmq_consumer.py

RabbitMQ Consumer enterprise para pipelines de ingestão.

Recursos principais:
- Conexão resiliente com retry e backoff exponencial.
- Consumo com QoS/prefetch configurável.
- Ack/Nack manual seguro.
- Suporte a DLQ/DLX via rejeição ou publicação explícita.
- Retry controlado por headers.
- Validação de envelope JSON com Pydantic.
- Processamento plugável por protocolo EventProcessor.
- Idempotência básica por message_id/event_id/correlation_id.
- Métricas internas de consumo, sucesso, falha, retry, DLQ e latência.
- Logs estruturados.
- Shutdown gracioso com SIGINT/SIGTERM.
- Suporte a TLS, heartbeat e blocked connection timeout.

Dependências recomendadas:
    pip install pika pydantic

Variáveis de ambiente suportadas:
    RABBITMQ_HOST=localhost
    RABBITMQ_PORT=5672
    RABBITMQ_VHOST=/
    RABBITMQ_USERNAME=guest
    RABBITMQ_PASSWORD=guest
    RABBITMQ_QUEUE=enterprise.ingestion.queue
    RABBITMQ_EXCHANGE=enterprise.ingestion.exchange
    RABBITMQ_ROUTING_KEY=enterprise.ingestion
    RABBITMQ_DLX_EXCHANGE=enterprise.ingestion.dlx
    RABBITMQ_DLQ_ROUTING_KEY=enterprise.ingestion.dlq
    RABBITMQ_PREFETCH_COUNT=20
    RABBITMQ_HEARTBEAT=60
    RABBITMQ_CONNECTION_ATTEMPTS=3
    RABBITMQ_RETRY_DELAY_SECONDS=5
    RABBITMQ_MAX_PROCESSING_RETRIES=3
    RABBITMQ_REQUEUE_ON_FAILURE=false
    RABBITMQ_USE_TLS=false
"""

from __future__ import annotations

import json
import logging
import os
import random
import signal
import socket
import ssl
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Event
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Protocol, Tuple

try:
    import pika
    from pika.adapters.blocking_connection import BlockingChannel
    from pika.spec import Basic, BasicProperties
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


def build_logger(name: str = "data.ingestion.rabbitmq_consumer") -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ContextFilter(service_name=os.getenv("SERVICE_NAME", "rabbitmq-consumer")))

    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = build_logger()


# =============================================================================
# Models
# =============================================================================


class MessageStatus(str, Enum):
    RECEIVED = "received"
    VALIDATED = "validated"
    PROCESSED = "processed"
    FAILED = "failed"
    RETRIED = "retried"
    REJECTED = "rejected"
    SENT_TO_DLQ = "sent_to_dlq"
    SKIPPED_DUPLICATE = "skipped_duplicate"


class FailureStrategy(str, Enum):
    REQUEUE = "requeue"
    REJECT = "reject"
    PUBLISH_TO_DLQ = "publish_to_dlq"


class EventEnvelope(BaseModel):
    event_id: Optional[str] = Field(default=None)
    event_type: Optional[str] = Field(default=None)
    source: Optional[str] = Field(default=None)
    occurred_at: Optional[str] = Field(default=None)
    correlation_id: Optional[str] = Field(default=None)
    data: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class RetryPolicy:
    max_processing_retries: int = 3
    base_seconds: float = 0.5
    max_seconds: float = 15.0
    jitter: bool = True

    def sleep_seconds(self, attempt: int) -> float:
        exponential = self.base_seconds * (2 ** max(0, attempt - 1))
        jitter_value = random.uniform(0, self.base_seconds) if self.jitter else 0.0
        return min(exponential + jitter_value, self.max_seconds)


@dataclass(frozen=True)
class RabbitMQConsumerConfig:
    host: str = "localhost"
    port: int = 5672
    virtual_host: str = "/"
    username: str = "guest"
    password: str = "guest"

    queue_name: str = "enterprise.ingestion.queue"
    exchange_name: Optional[str] = None
    routing_key: Optional[str] = None

    dlx_exchange: Optional[str] = "enterprise.ingestion.dlx"
    dlq_routing_key: Optional[str] = "enterprise.ingestion.dlq"

    prefetch_count: int = 20
    heartbeat: int = 60
    blocked_connection_timeout: int = 300
    connection_attempts: int = 3
    retry_delay_seconds: int = 5
    socket_timeout: int = 10

    durable_queue: bool = True
    auto_declare: bool = False
    auto_ack: bool = False
    consumer_tag: Optional[str] = None

    use_tls: bool = False
    tls_ca_cert: Optional[str] = None
    tls_certfile: Optional[str] = None
    tls_keyfile: Optional[str] = None

    failure_strategy: FailureStrategy = FailureStrategy.PUBLISH_TO_DLQ
    requeue_on_failure: bool = False
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    idempotency_enabled: bool = True

    @staticmethod
    def from_env() -> "RabbitMQConsumerConfig":
        return RabbitMQConsumerConfig(
            host=os.getenv("RABBITMQ_HOST", "localhost"),
            port=int(os.getenv("RABBITMQ_PORT", "5672")),
            virtual_host=os.getenv("RABBITMQ_VHOST", "/"),
            username=os.getenv("RABBITMQ_USERNAME", "guest"),
            password=os.getenv("RABBITMQ_PASSWORD", "guest"),
            queue_name=os.getenv("RABBITMQ_QUEUE", "enterprise.ingestion.queue"),
            exchange_name=os.getenv("RABBITMQ_EXCHANGE") or None,
            routing_key=os.getenv("RABBITMQ_ROUTING_KEY") or None,
            dlx_exchange=os.getenv("RABBITMQ_DLX_EXCHANGE", "enterprise.ingestion.dlx") or None,
            dlq_routing_key=os.getenv("RABBITMQ_DLQ_ROUTING_KEY", "enterprise.ingestion.dlq") or None,
            prefetch_count=int(os.getenv("RABBITMQ_PREFETCH_COUNT", "20")),
            heartbeat=int(os.getenv("RABBITMQ_HEARTBEAT", "60")),
            blocked_connection_timeout=int(os.getenv("RABBITMQ_BLOCKED_CONNECTION_TIMEOUT", "300")),
            connection_attempts=int(os.getenv("RABBITMQ_CONNECTION_ATTEMPTS", "3")),
            retry_delay_seconds=int(os.getenv("RABBITMQ_RETRY_DELAY_SECONDS", "5")),
            socket_timeout=int(os.getenv("RABBITMQ_SOCKET_TIMEOUT", "10")),
            durable_queue=env_bool("RABBITMQ_DURABLE_QUEUE", True),
            auto_declare=env_bool("RABBITMQ_AUTO_DECLARE", False),
            auto_ack=env_bool("RABBITMQ_AUTO_ACK", False),
            consumer_tag=os.getenv("RABBITMQ_CONSUMER_TAG") or None,
            use_tls=env_bool("RABBITMQ_USE_TLS", False),
            tls_ca_cert=os.getenv("RABBITMQ_TLS_CA_CERT") or None,
            tls_certfile=os.getenv("RABBITMQ_TLS_CERTFILE") or None,
            tls_keyfile=os.getenv("RABBITMQ_TLS_KEYFILE") or None,
            failure_strategy=FailureStrategy(
                os.getenv("RABBITMQ_FAILURE_STRATEGY", FailureStrategy.PUBLISH_TO_DLQ.value)
            ),
            requeue_on_failure=env_bool("RABBITMQ_REQUEUE_ON_FAILURE", False),
            retry_policy=RetryPolicy(
                max_processing_retries=int(os.getenv("RABBITMQ_MAX_PROCESSING_RETRIES", "3")),
                base_seconds=float(os.getenv("RABBITMQ_RETRY_BASE_SECONDS", "0.5")),
                max_seconds=float(os.getenv("RABBITMQ_RETRY_MAX_SECONDS", "15.0")),
                jitter=env_bool("RABBITMQ_RETRY_JITTER", True),
            ),
            idempotency_enabled=env_bool("RABBITMQ_IDEMPOTENCY_ENABLED", True),
        )


@dataclass
class RabbitMQMessageContext:
    queue: str
    exchange: str
    routing_key: str
    delivery_tag: int
    redelivered: bool
    message_id: Optional[str]
    correlation_id: Optional[str]
    content_type: Optional[str]
    headers: Dict[str, Any]
    received_at: str


@dataclass
class RabbitMQConsumerMetrics:
    received: int = 0
    processed: int = 0
    failed: int = 0
    retried: int = 0
    rejected: int = 0
    sent_to_dlq: int = 0
    duplicates: int = 0
    acked: int = 0
    nacked: int = 0
    total_processing_seconds: float = 0.0
    last_message_at: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        average_latency = self.total_processing_seconds / self.processed if self.processed else 0.0
        return {
            "received": self.received,
            "processed": self.processed,
            "failed": self.failed,
            "retried": self.retried,
            "rejected": self.rejected,
            "sent_to_dlq": self.sent_to_dlq,
            "duplicates": self.duplicates,
            "acked": self.acked,
            "nacked": self.nacked,
            "average_processing_seconds": round(average_latency, 6),
            "total_processing_seconds": round(self.total_processing_seconds, 6),
            "last_message_at": self.last_message_at,
        }


# =============================================================================
# Protocols
# =============================================================================


class EventProcessor(Protocol):
    def process(self, event: EventEnvelope, context: RabbitMQMessageContext) -> None:
        """Processa evento validado."""


class IdempotencyStore(Protocol):
    def exists(self, key: str) -> bool:
        """Retorna True se a mensagem já foi processada."""

    def mark_processed(self, key: str) -> None:
        """Marca mensagem como processada."""


class EventValidator(Protocol):
    def validate(self, event: EventEnvelope, context: RabbitMQMessageContext) -> EventEnvelope:
        """Valida o evento antes do processamento."""


# =============================================================================
# Implementações base
# =============================================================================


class InMemoryIdempotencyStore:
    def __init__(self, max_items: int = 100_000) -> None:
        self.max_items = max_items
        self._keys: Dict[str, float] = {}

    def exists(self, key: str) -> bool:
        return key in self._keys

    def mark_processed(self, key: str) -> None:
        if len(self._keys) >= self.max_items:
            oldest_key = min(self._keys, key=self._keys.get)  # type: ignore[arg-type]
            self._keys.pop(oldest_key, None)
        self._keys[key] = time.time()


class NoOpEventValidator:
    def validate(self, event: EventEnvelope, context: RabbitMQMessageContext) -> EventEnvelope:
        return event


class LoggingEventProcessor:
    def process(self, event: EventEnvelope, context: RabbitMQMessageContext) -> None:
        logger.info(
            "Evento RabbitMQ processado. event_id=%s event_type=%s queue=%s routing_key=%s delivery_tag=%s",
            event.event_id,
            event.event_type,
            context.queue,
            context.routing_key,
            context.delivery_tag,
        )


class RequiredFieldsValidator:
    def __init__(self, required_data_fields: Iterable[str]) -> None:
        self.required_data_fields = list(required_data_fields)

    def validate(self, event: EventEnvelope, context: RabbitMQMessageContext) -> EventEnvelope:
        missing = [field for field in self.required_data_fields if get_nested_value(event.data, field) in (None, "")]
        if missing:
            raise ValueError(f"Campos obrigatórios ausentes: {missing}")
        return event


# =============================================================================
# Consumer principal
# =============================================================================


class EnterpriseRabbitMQConsumer:
    def __init__(
        self,
        config: Optional[RabbitMQConsumerConfig] = None,
        processor: Optional[EventProcessor] = None,
        validator: Optional[EventValidator] = None,
        idempotency_store: Optional[IdempotencyStore] = None,
    ) -> None:
        self.config = config or RabbitMQConsumerConfig.from_env()
        self.processor = processor or LoggingEventProcessor()
        self.validator = validator or NoOpEventValidator()
        self.idempotency_store = idempotency_store or InMemoryIdempotencyStore()

        self.metrics = RabbitMQConsumerMetrics()
        self.stop_event = Event()
        self.connection: Optional[pika.BlockingConnection] = None
        self.channel: Optional[BlockingChannel] = None

    def start(self) -> None:
        logger.info(
            "Iniciando RabbitMQ consumer. host=%s port=%s queue=%s prefetch=%s",
            self.config.host,
            self.config.port,
            self.config.queue_name,
            self.config.prefetch_count,
        )

        while not self.stop_event.is_set():
            try:
                self._connect()
                self._consume_loop()
            except KeyboardInterrupt:
                self.stop()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.exception("Erro no consumer RabbitMQ. error=%s", exc)
                self._safe_close()
                if not self.stop_event.is_set():
                    time.sleep(self.config.retry_delay_seconds)

        self.shutdown()

    def stop(self) -> None:
        logger.info("Solicitação de parada recebida.")
        self.stop_event.set()
        if self.channel and self.channel.is_open:
            try:
                self.channel.stop_consuming()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Falha ao parar consumo: %s", exc)

    def shutdown(self) -> None:
        logger.info("Encerrando RabbitMQ consumer. metrics=%s", json.dumps(self.metrics.snapshot()))
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
        self.channel.basic_qos(prefetch_count=self.config.prefetch_count)

        if self.config.auto_declare:
            self._declare_topology()

        logger.info("Conectado ao RabbitMQ. queue=%s", self.config.queue_name)

    def _consume_loop(self) -> None:
        if not self.channel:
            raise RuntimeError("Canal RabbitMQ não inicializado.")

        self.channel.basic_consume(
            queue=self.config.queue_name,
            on_message_callback=self._on_message,
            auto_ack=self.config.auto_ack,
            consumer_tag=self.config.consumer_tag,
        )

        logger.info("Aguardando mensagens RabbitMQ. queue=%s", self.config.queue_name)
        self.channel.start_consuming()

    def _on_message(
        self,
        channel: BlockingChannel,
        method: Basic.Deliver,
        properties: BasicProperties,
        body: bytes,
    ) -> None:
        started = time.perf_counter()
        context = self._build_context(method, properties)

        self.metrics.received += 1
        self.metrics.last_message_at = utc_now_iso()

        try:
            event = self._parse_event(body, context)
            event = self.validator.validate(event, context)
            idempotency_key = self._build_idempotency_key(event, context)

            if self.config.idempotency_enabled and idempotency_key:
                if self.idempotency_store.exists(idempotency_key):
                    self.metrics.duplicates += 1
                    logger.info(
                        "Mensagem duplicada ignorada. idempotency_key=%s delivery_tag=%s",
                        idempotency_key,
                        context.delivery_tag,
                    )
                    self._ack(channel, method.delivery_tag)
                    return

            self._process_with_retry(event, context)

            if self.config.idempotency_enabled and idempotency_key:
                self.idempotency_store.mark_processed(idempotency_key)

            self._ack(channel, method.delivery_tag)
            self.metrics.processed += 1
            self.metrics.total_processing_seconds += time.perf_counter() - started

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.failed += 1
            logger.exception(
                "Falha ao processar mensagem RabbitMQ. delivery_tag=%s error=%s",
                method.delivery_tag,
                exc,
            )
            self._handle_failure(channel, method, properties, body, context, exc)

    def _process_with_retry(self, event: EventEnvelope, context: RabbitMQMessageContext) -> None:
        last_error: Optional[Exception] = None
        attempts = self.config.retry_policy.max_processing_retries + 1

        for attempt in range(1, attempts + 1):
            try:
                self.processor.process(event, context)
                return
            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_error = exc

                if attempt >= attempts:
                    break

                self.metrics.retried += 1
                sleep_seconds = self.config.retry_policy.sleep_seconds(attempt)
                logger.warning(
                    "Erro no processamento RabbitMQ. retry=%s/%s sleep=%.2fs error=%s",
                    attempt,
                    attempts - 1,
                    sleep_seconds,
                    exc,
                )
                time.sleep(sleep_seconds)

        raise RuntimeError("Falha após retries máximos de processamento") from last_error

    def _handle_failure(
        self,
        channel: BlockingChannel,
        method: Basic.Deliver,
        properties: BasicProperties,
        body: bytes,
        context: RabbitMQMessageContext,
        exc: Exception,
    ) -> None:
        if self.config.failure_strategy == FailureStrategy.REQUEUE:
            self._nack(channel, method.delivery_tag, requeue=True)
            return

        if self.config.failure_strategy == FailureStrategy.REJECT:
            self.metrics.rejected += 1
            self._nack(channel, method.delivery_tag, requeue=self.config.requeue_on_failure)
            return

        self._publish_to_dlq(properties, body, context, exc)
        self._ack(channel, method.delivery_tag)

    def _publish_to_dlq(
        self,
        properties: BasicProperties,
        body: bytes,
        context: RabbitMQMessageContext,
        exc: Exception,
    ) -> None:
        if not self.channel or not self.config.dlx_exchange or not self.config.dlq_routing_key:
            logger.warning("DLQ explícita não configurada. Mensagem será rejeitada sem requeue.")
            if self.channel:
                self.metrics.rejected += 1
            return

        payload = {
            "status": MessageStatus.SENT_TO_DLQ.value,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
            "traceback": traceback.format_exc(),
            "failed_at": utc_now_iso(),
            "original_context": {
                "queue": context.queue,
                "exchange": context.exchange,
                "routing_key": context.routing_key,
                "delivery_tag": context.delivery_tag,
                "message_id": context.message_id,
                "correlation_id": context.correlation_id,
                "headers": context.headers,
            },
            "payload": safe_decode(body),
        }

        headers = dict(context.headers or {})
        headers["x-original-queue"] = context.queue
        headers["x-original-routing-key"] = context.routing_key
        headers["x-error-type"] = exc.__class__.__name__
        headers["x-failed-at"] = utc_now_iso()

        dlq_properties = pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,
            correlation_id=context.correlation_id,
            message_id=context.message_id or str(uuid.uuid4()),
            headers=headers,
        )

        self.channel.basic_publish(
            exchange=self.config.dlx_exchange,
            routing_key=self.config.dlq_routing_key,
            body=json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
            properties=dlq_properties,
            mandatory=False,
        )

        self.metrics.sent_to_dlq += 1
        logger.warning(
            "Mensagem enviada para DLQ. exchange=%s routing_key=%s delivery_tag=%s",
            self.config.dlx_exchange,
            self.config.dlq_routing_key,
            context.delivery_tag,
        )

    def _parse_event(self, body: bytes, context: RabbitMQMessageContext) -> EventEnvelope:
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Payload RabbitMQ não é JSON válido") from exc

        if not isinstance(payload, dict):
            raise ValueError("Payload RabbitMQ deve ser um objeto JSON")

        try:
            if hasattr(EventEnvelope, "model_validate"):
                event = EventEnvelope.model_validate(payload)
            else:
                event = EventEnvelope.parse_obj(payload)  # type: ignore[attr-defined]
        except ValidationError as exc:
            raise ValueError(f"Payload RabbitMQ inválido: {exc}") from exc

        if not event.event_id:
            event.event_id = context.message_id
        if not event.correlation_id:
            event.correlation_id = context.correlation_id

        return event

    def _build_context(self, method: Basic.Deliver, properties: BasicProperties) -> RabbitMQMessageContext:
        headers = dict(properties.headers or {})
        return RabbitMQMessageContext(
            queue=self.config.queue_name,
            exchange=method.exchange or "",
            routing_key=method.routing_key or "",
            delivery_tag=method.delivery_tag,
            redelivered=method.redelivered,
            message_id=properties.message_id,
            correlation_id=properties.correlation_id or headers.get("x-correlation-id"),
            content_type=properties.content_type,
            headers=headers,
            received_at=utc_now_iso(),
        )

    def _build_idempotency_key(self, event: EventEnvelope, context: RabbitMQMessageContext) -> Optional[str]:
        if event.event_id:
            return event.event_id
        if context.message_id:
            return context.message_id
        if context.correlation_id:
            return context.correlation_id
        return f"{context.queue}:{context.delivery_tag}"

    def _ack(self, channel: BlockingChannel, delivery_tag: int) -> None:
        if self.config.auto_ack:
            return
        channel.basic_ack(delivery_tag=delivery_tag)
        self.metrics.acked += 1

    def _nack(self, channel: BlockingChannel, delivery_tag: int, requeue: bool) -> None:
        if self.config.auto_ack:
            return
        channel.basic_nack(delivery_tag=delivery_tag, requeue=requeue)
        self.metrics.nacked += 1

    def _declare_topology(self) -> None:
        if not self.channel:
            raise RuntimeError("Canal não inicializado.")

        queue_args: Dict[str, Any] = {}
        if self.config.dlx_exchange:
            queue_args["x-dead-letter-exchange"] = self.config.dlx_exchange
        if self.config.dlq_routing_key:
            queue_args["x-dead-letter-routing-key"] = self.config.dlq_routing_key

        if self.config.exchange_name:
            self.channel.exchange_declare(
                exchange=self.config.exchange_name,
                exchange_type="direct",
                durable=True,
            )

        if self.config.dlx_exchange:
            self.channel.exchange_declare(
                exchange=self.config.dlx_exchange,
                exchange_type="direct",
                durable=True,
            )

        self.channel.queue_declare(
            queue=self.config.queue_name,
            durable=self.config.durable_queue,
            arguments=queue_args or None,
        )

        if self.config.exchange_name and self.config.routing_key:
            self.channel.queue_bind(
                queue=self.config.queue_name,
                exchange=self.config.exchange_name,
                routing_key=self.config.routing_key,
            )

        logger.info("Topologia RabbitMQ declarada. queue=%s", self.config.queue_name)

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
            logger.warning("Falha ao fechar canal RabbitMQ: %s", exc)

        try:
            if self.connection and self.connection.is_open:
                self.connection.close()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Falha ao fechar conexão RabbitMQ: %s", exc)

        self.channel = None
        self.connection = None


# =============================================================================
# Utilitários
# =============================================================================


def install_signal_handlers(consumer: EnterpriseRabbitMQConsumer) -> None:
    def _handler(signum: int, frame: Any) -> None:  # pylint: disable=unused-argument
        logger.info("Signal recebido: %s", signum)
        consumer.stop()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def get_nested_value(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def safe_decode(value: Optional[bytes]) -> Optional[str]:
    if value is None:
        return None
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.hex()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "sim", "s"}


# =============================================================================
# Processor exemplo enterprise
# =============================================================================


class EnterpriseIngestionProcessor:
    def process(self, event: EventEnvelope, context: RabbitMQMessageContext) -> None:
        self._audit_received(event, context)
        normalized = self._normalize(event, context)
        self._persist(normalized, context)
        self._audit_processed(event, context)

    def _normalize(self, event: EventEnvelope, context: RabbitMQMessageContext) -> Dict[str, Any]:
        return {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "source": event.source,
            "occurred_at": event.occurred_at,
            "correlation_id": event.correlation_id,
            "data": event.data,
            "metadata": event.metadata,
            "queue": context.queue,
            "routing_key": context.routing_key,
            "ingested_at": utc_now_iso(),
        }

    def _persist(self, normalized: Dict[str, Any], context: RabbitMQMessageContext) -> None:
        logger.info(
            "Persistência simulada RabbitMQ. event_id=%s queue=%s delivery_tag=%s",
            normalized.get("event_id"),
            context.queue,
            context.delivery_tag,
        )

    def _audit_received(self, event: EventEnvelope, context: RabbitMQMessageContext) -> None:
        logger.info(
            "Auditoria RabbitMQ: recebido. event_id=%s type=%s queue=%s delivery_tag=%s",
            event.event_id,
            event.event_type,
            context.queue,
            context.delivery_tag,
        )

    def _audit_processed(self, event: EventEnvelope, context: RabbitMQMessageContext) -> None:
        logger.info(
            "Auditoria RabbitMQ: processado. event_id=%s type=%s queue=%s delivery_tag=%s",
            event.event_id,
            event.event_type,
            context.queue,
            context.delivery_tag,
        )


# =============================================================================
# Bootstrap
# =============================================================================


def main() -> None:
    config = RabbitMQConsumerConfig.from_env()
    consumer = EnterpriseRabbitMQConsumer(
        config=config,
        processor=EnterpriseIngestionProcessor(),
        validator=NoOpEventValidator(),
        idempotency_store=InMemoryIdempotencyStore(),
    )
    install_signal_handlers(consumer)
    consumer.start()


if __name__ == "__main__":
    main()

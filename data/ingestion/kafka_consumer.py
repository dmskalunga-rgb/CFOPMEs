"""
data/ingestion/kafka_consumer.py

Kafka consumer enterprise para pipelines de ingestão.

Recursos principais:
- Configuração tipada via dataclass e variáveis de ambiente.
- Consumer resiliente com retry, backoff exponencial e jitter.
- Suporte a DLQ (Dead Letter Queue).
- Commit manual seguro após processamento bem-sucedido.
- Hooks extensíveis para processamento de eventos.
- Validação de payload JSON.
- Métricas internas de consumo, erro, retry, DLQ e latência.
- Logs estruturados.
- Shutdown gracioso com SIGINT/SIGTERM.
- Controle básico de idempotência por chave/event_id.
- Suporte a headers, tracing/correlation_id e metadados Kafka.

Dependências recomendadas:
    pip install confluent-kafka pydantic

Variáveis de ambiente suportadas:
    KAFKA_BOOTSTRAP_SERVERS=localhost:9092
    KAFKA_GROUP_ID=enterprise-ingestion-group
    KAFKA_TOPICS=topic-a,topic-b
    KAFKA_AUTO_OFFSET_RESET=earliest
    KAFKA_ENABLE_AUTO_COMMIT=false
    KAFKA_SECURITY_PROTOCOL=PLAINTEXT
    KAFKA_SASL_MECHANISM=
    KAFKA_SASL_USERNAME=
    KAFKA_SASL_PASSWORD=
    KAFKA_DLQ_TOPIC=enterprise-ingestion-dlq
    KAFKA_MAX_RETRIES=3
    KAFKA_POLL_TIMEOUT_SECONDS=1.0
    KAFKA_SESSION_TIMEOUT_MS=45000
    KAFKA_HEARTBEAT_INTERVAL_MS=15000
    KAFKA_MAX_POLL_INTERVAL_MS=300000
    KAFKA_FETCH_MAX_BYTES=52428800
    KAFKA_BATCH_SIZE=100
    KAFKA_IDEMPOTENCY_ENABLED=true
"""

from __future__ import annotations

import json
import logging
import os
import random
import signal
import socket
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Event
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Tuple

try:
    from confluent_kafka import Consumer, KafkaError, KafkaException, Message, Producer
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "Dependência ausente: instale com `pip install confluent-kafka`."
    ) from exc

try:
    from pydantic import BaseModel, Field, ValidationError
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "Dependência ausente: instale com `pip install pydantic`."
    ) from exc


# =============================================================================
# Logging
# =============================================================================

LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(name)s | "
    "%(message)s | service=%(service)s host=%(host)s"
)


class ContextFilter(logging.Filter):
    """Adiciona contexto padrão aos logs."""

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name
        self.host = socket.gethostname()

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self.service_name
        record.host = self.host
        return True


def build_logger(name: str = "data.ingestion.kafka_consumer") -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ContextFilter(service_name=os.getenv("SERVICE_NAME", "kafka-consumer")))

    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = build_logger()


# =============================================================================
# Models
# =============================================================================


class EventStatus(str, Enum):
    RECEIVED = "received"
    PROCESSED = "processed"
    FAILED = "failed"
    SENT_TO_DLQ = "sent_to_dlq"
    SKIPPED_DUPLICATE = "skipped_duplicate"


class EventEnvelope(BaseModel):
    """
    Envelope padrão esperado no payload.

    O campo `data` contém o conteúdo real do evento.
    O campo `event_id` é usado para idempotência quando disponível.
    """

    event_id: Optional[str] = Field(default=None)
    event_type: Optional[str] = Field(default=None)
    source: Optional[str] = Field(default=None)
    occurred_at: Optional[str] = Field(default=None)
    correlation_id: Optional[str] = Field(default=None)
    data: Dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class KafkaConsumerConfig:
    bootstrap_servers: str
    group_id: str
    topics: List[str]

    auto_offset_reset: str = "earliest"
    enable_auto_commit: bool = False

    security_protocol: str = "PLAINTEXT"
    sasl_mechanism: Optional[str] = None
    sasl_username: Optional[str] = None
    sasl_password: Optional[str] = None

    poll_timeout_seconds: float = 1.0
    batch_size: int = 100
    session_timeout_ms: int = 45000
    heartbeat_interval_ms: int = 15000
    max_poll_interval_ms: int = 300000
    fetch_max_bytes: int = 52_428_800

    dlq_topic: Optional[str] = "enterprise-ingestion-dlq"
    max_retries: int = 3
    retry_base_seconds: float = 0.5
    retry_max_seconds: float = 15.0

    idempotency_enabled: bool = True
    commit_on_duplicate: bool = True

    client_id: str = field(default_factory=lambda: f"consumer-{socket.gethostname()}")

    @staticmethod
    def from_env() -> "KafkaConsumerConfig":
        topics_raw = os.getenv("KAFKA_TOPICS", "")
        topics = [item.strip() for item in topics_raw.split(",") if item.strip()]

        if not topics:
            raise ValueError("KAFKA_TOPICS é obrigatório. Exemplo: topic-a,topic-b")

        return KafkaConsumerConfig(
            bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            group_id=os.getenv("KAFKA_GROUP_ID", "enterprise-ingestion-group"),
            topics=topics,
            auto_offset_reset=os.getenv("KAFKA_AUTO_OFFSET_RESET", "earliest"),
            enable_auto_commit=_env_bool("KAFKA_ENABLE_AUTO_COMMIT", False),
            security_protocol=os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT"),
            sasl_mechanism=os.getenv("KAFKA_SASL_MECHANISM") or None,
            sasl_username=os.getenv("KAFKA_SASL_USERNAME") or None,
            sasl_password=os.getenv("KAFKA_SASL_PASSWORD") or None,
            poll_timeout_seconds=float(os.getenv("KAFKA_POLL_TIMEOUT_SECONDS", "1.0")),
            batch_size=int(os.getenv("KAFKA_BATCH_SIZE", "100")),
            session_timeout_ms=int(os.getenv("KAFKA_SESSION_TIMEOUT_MS", "45000")),
            heartbeat_interval_ms=int(os.getenv("KAFKA_HEARTBEAT_INTERVAL_MS", "15000")),
            max_poll_interval_ms=int(os.getenv("KAFKA_MAX_POLL_INTERVAL_MS", "300000")),
            fetch_max_bytes=int(os.getenv("KAFKA_FETCH_MAX_BYTES", "52428800")),
            dlq_topic=os.getenv("KAFKA_DLQ_TOPIC", "enterprise-ingestion-dlq") or None,
            max_retries=int(os.getenv("KAFKA_MAX_RETRIES", "3")),
            retry_base_seconds=float(os.getenv("KAFKA_RETRY_BASE_SECONDS", "0.5")),
            retry_max_seconds=float(os.getenv("KAFKA_RETRY_MAX_SECONDS", "15.0")),
            idempotency_enabled=_env_bool("KAFKA_IDEMPOTENCY_ENABLED", True),
            commit_on_duplicate=_env_bool("KAFKA_COMMIT_ON_DUPLICATE", True),
            client_id=os.getenv("KAFKA_CLIENT_ID", f"consumer-{socket.gethostname()}"),
        )

    def to_consumer_dict(self) -> Dict[str, Any]:
        config: Dict[str, Any] = {
            "bootstrap.servers": self.bootstrap_servers,
            "group.id": self.group_id,
            "client.id": self.client_id,
            "auto.offset.reset": self.auto_offset_reset,
            "enable.auto.commit": self.enable_auto_commit,
            "session.timeout.ms": self.session_timeout_ms,
            "heartbeat.interval.ms": self.heartbeat_interval_ms,
            "max.poll.interval.ms": self.max_poll_interval_ms,
            "fetch.max.bytes": self.fetch_max_bytes,
            "security.protocol": self.security_protocol,
        }

        if self.sasl_mechanism:
            config["sasl.mechanisms"] = self.sasl_mechanism
        if self.sasl_username:
            config["sasl.username"] = self.sasl_username
        if self.sasl_password:
            config["sasl.password"] = self.sasl_password

        return config

    def to_producer_dict(self) -> Dict[str, Any]:
        config: Dict[str, Any] = {
            "bootstrap.servers": self.bootstrap_servers,
            "client.id": f"{self.client_id}-dlq-producer",
            "security.protocol": self.security_protocol,
            "enable.idempotence": True,
            "acks": "all",
            "retries": 5,
            "linger.ms": 10,
        }

        if self.sasl_mechanism:
            config["sasl.mechanisms"] = self.sasl_mechanism
        if self.sasl_username:
            config["sasl.username"] = self.sasl_username
        if self.sasl_password:
            config["sasl.password"] = self.sasl_password

        return config


@dataclass
class KafkaMessageContext:
    topic: str
    partition: int
    offset: int
    key: Optional[str]
    timestamp: Optional[int]
    headers: Dict[str, str]
    correlation_id: Optional[str]
    received_at: str


@dataclass
class ConsumerMetrics:
    received: int = 0
    processed: int = 0
    failed: int = 0
    retries: int = 0
    sent_to_dlq: int = 0
    duplicates: int = 0
    commits: int = 0
    last_message_at: Optional[str] = None
    total_processing_seconds: float = 0.0

    def snapshot(self) -> Dict[str, Any]:
        average_latency = (
            self.total_processing_seconds / self.processed
            if self.processed > 0
            else 0.0
        )
        return {
            "received": self.received,
            "processed": self.processed,
            "failed": self.failed,
            "retries": self.retries,
            "sent_to_dlq": self.sent_to_dlq,
            "duplicates": self.duplicates,
            "commits": self.commits,
            "last_message_at": self.last_message_at,
            "average_processing_seconds": round(average_latency, 6),
        }


# =============================================================================
# Protocols / Interfaces
# =============================================================================


class EventProcessor(Protocol):
    def process(self, event: EventEnvelope, context: KafkaMessageContext) -> None:
        """Processa um evento validado."""


class IdempotencyStore(Protocol):
    def exists(self, key: str) -> bool:
        """Retorna True se o evento já foi processado."""

    def mark_processed(self, key: str) -> None:
        """Marca o evento como processado."""


# =============================================================================
# Implementações auxiliares
# =============================================================================


class InMemoryIdempotencyStore:
    """
    Store simples em memória.

    Em produção, substitua por Redis, PostgreSQL, DynamoDB ou outro storage
    compartilhado entre instâncias.
    """

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


class LoggingEventProcessor:
    """
    Processor padrão para exemplo.

    Substitua por uma implementação real:
    - gravar no data lake;
    - chamar uma API interna;
    - persistir no banco;
    - publicar em outro tópico;
    - acionar validações e enriquecimentos.
    """

    def process(self, event: EventEnvelope, context: KafkaMessageContext) -> None:
        logger.info(
            "Evento processado com sucesso: event_id=%s event_type=%s topic=%s partition=%s offset=%s",
            event.event_id,
            event.event_type,
            context.topic,
            context.partition,
            context.offset,
        )


# =============================================================================
# Consumer principal
# =============================================================================


class EnterpriseKafkaConsumer:
    def __init__(
        self,
        config: KafkaConsumerConfig,
        processor: EventProcessor,
        idempotency_store: Optional[IdempotencyStore] = None,
        validator: Optional[Callable[[Mapping[str, Any]], Mapping[str, Any]]] = None,
    ) -> None:
        self.config = config
        self.processor = processor
        self.idempotency_store = idempotency_store or InMemoryIdempotencyStore()
        self.validator = validator

        self.consumer = Consumer(config.to_consumer_dict())
        self.producer = Producer(config.to_producer_dict()) if config.dlq_topic else None

        self.metrics = ConsumerMetrics()
        self.stop_event = Event()

    def start(self) -> None:
        logger.info(
            "Iniciando consumer Kafka. topics=%s group_id=%s bootstrap=%s",
            self.config.topics,
            self.config.group_id,
            self.config.bootstrap_servers,
        )

        self.consumer.subscribe(
            self.config.topics,
            on_assign=self._on_assign,
            on_revoke=self._on_revoke,
            on_lost=self._on_lost,
        )

        while not self.stop_event.is_set():
            try:
                messages = self.consumer.consume(
                    num_messages=self.config.batch_size,
                    timeout=self.config.poll_timeout_seconds,
                )

                if not messages:
                    continue

                for message in messages:
                    if self.stop_event.is_set():
                        break
                    self._handle_message(message)

            except KafkaException as exc:
                logger.exception("Erro Kafka no loop principal: %s", exc)
                time.sleep(2)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.exception("Erro inesperado no loop principal: %s", exc)
                time.sleep(2)

        self.shutdown()

    def stop(self) -> None:
        logger.info("Solicitação de parada recebida.")
        self.stop_event.set()

    def shutdown(self) -> None:
        logger.info("Encerrando consumer Kafka com shutdown gracioso.")

        try:
            if self.producer:
                self.producer.flush(timeout=10)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Falha ao executar flush do producer DLQ: %s", exc)

        try:
            self.consumer.close()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Falha ao fechar consumer Kafka: %s", exc)

        logger.info("Consumer encerrado. metrics=%s", json.dumps(self.metrics.snapshot()))

    def _handle_message(self, message: Message) -> None:
        if message.error():
            self._handle_message_error(message)
            return

        start = time.perf_counter()
        self.metrics.received += 1
        self.metrics.last_message_at = utc_now_iso()

        context = self._build_context(message)
        raw_value = message.value()

        try:
            event = self._parse_and_validate(raw_value, context)
            idempotency_key = self._build_idempotency_key(event, context)

            if self.config.idempotency_enabled and idempotency_key:
                if self.idempotency_store.exists(idempotency_key):
                    self.metrics.duplicates += 1
                    logger.info(
                        "Evento duplicado ignorado. idempotency_key=%s topic=%s partition=%s offset=%s",
                        idempotency_key,
                        context.topic,
                        context.partition,
                        context.offset,
                    )
                    if self.config.commit_on_duplicate:
                        self._commit(message)
                    return

            self._process_with_retry(event, context)

            if self.config.idempotency_enabled and idempotency_key:
                self.idempotency_store.mark_processed(idempotency_key)

            self._commit(message)
            self.metrics.processed += 1
            self.metrics.total_processing_seconds += time.perf_counter() - start

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.failed += 1
            logger.exception(
                "Falha ao processar mensagem. topic=%s partition=%s offset=%s error=%s",
                context.topic,
                context.partition,
                context.offset,
                exc,
            )
            self._send_to_dlq(message, context, exc)
            self._commit(message)

    def _process_with_retry(self, event: EventEnvelope, context: KafkaMessageContext) -> None:
        attempt = 0
        last_error: Optional[Exception] = None

        while attempt <= self.config.max_retries:
            try:
                self.processor.process(event, context)
                return
            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_error = exc

                if attempt >= self.config.max_retries:
                    break

                attempt += 1
                self.metrics.retries += 1
                sleep_seconds = self._retry_sleep_seconds(attempt)

                logger.warning(
                    "Erro no processamento. Tentando novamente. attempt=%s max_retries=%s sleep=%.2fs error=%s",
                    attempt,
                    self.config.max_retries,
                    sleep_seconds,
                    exc,
                )
                time.sleep(sleep_seconds)

        raise RuntimeError("Falha após retries máximos") from last_error

    def _parse_and_validate(
        self,
        raw_value: Optional[bytes],
        context: KafkaMessageContext,
    ) -> EventEnvelope:
        if raw_value is None:
            raise ValueError("Mensagem Kafka sem payload")

        try:
            payload = json.loads(raw_value.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Payload não é um JSON válido") from exc

        if not isinstance(payload, dict):
            raise ValueError("Payload deve ser um objeto JSON")

        if self.validator:
            payload = dict(self.validator(payload))

        try:
            event = EventEnvelope.model_validate(payload)
        except AttributeError:
            # Compatibilidade com Pydantic v1.
            event = EventEnvelope.parse_obj(payload)  # type: ignore[attr-defined]
        except ValidationError as exc:
            raise ValueError(f"Payload inválido: {exc}") from exc

        if not event.correlation_id:
            event.correlation_id = context.correlation_id

        return event

    def _send_to_dlq(
        self,
        message: Message,
        context: KafkaMessageContext,
        exc: Exception,
    ) -> None:
        if not self.producer or not self.config.dlq_topic:
            logger.warning("DLQ desabilitada. Mensagem com erro será apenas commitada.")
            return

        dlq_payload = {
            "status": EventStatus.SENT_TO_DLQ.value,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
            "traceback": traceback.format_exc(),
            "original_topic": context.topic,
            "original_partition": context.partition,
            "original_offset": context.offset,
            "original_key": context.key,
            "original_headers": context.headers,
            "correlation_id": context.correlation_id,
            "failed_at": utc_now_iso(),
            "payload": safe_decode(message.value()),
        }

        headers = [
            ("x-original-topic", context.topic.encode("utf-8")),
            ("x-original-partition", str(context.partition).encode("utf-8")),
            ("x-original-offset", str(context.offset).encode("utf-8")),
        ]

        if context.correlation_id:
            headers.append(("x-correlation-id", context.correlation_id.encode("utf-8")))

        try:
            self.producer.produce(
                topic=self.config.dlq_topic,
                key=message.key(),
                value=json.dumps(dlq_payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                callback=self._dlq_delivery_report,
            )
            self.producer.poll(0)
            self.metrics.sent_to_dlq += 1
        except BufferError:
            logger.warning("Buffer do producer cheio. Executando poll e nova tentativa para DLQ.")
            self.producer.poll(1)
            self.producer.produce(
                topic=self.config.dlq_topic,
                key=message.key(),
                value=json.dumps(dlq_payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                callback=self._dlq_delivery_report,
            )
            self.metrics.sent_to_dlq += 1
        except Exception as dlq_exc:  # pylint: disable=broad-exception-caught
            logger.exception("Falha crítica ao enviar mensagem para DLQ: %s", dlq_exc)

    def _commit(self, message: Message) -> None:
        try:
            self.consumer.commit(message=message, asynchronous=False)
            self.metrics.commits += 1
        except KafkaException as exc:
            logger.exception(
                "Falha ao realizar commit. topic=%s partition=%s offset=%s error=%s",
                message.topic(),
                message.partition(),
                message.offset(),
                exc,
            )
            raise

    def _handle_message_error(self, message: Message) -> None:
        error = message.error()
        if error is None:
            return

        if error.code() == KafkaError._PARTITION_EOF:  # pylint: disable=protected-access
            logger.debug(
                "Fim da partição. topic=%s partition=%s offset=%s",
                message.topic(),
                message.partition(),
                message.offset(),
            )
            return

        logger.error("Erro na mensagem Kafka: %s", error)

    def _build_context(self, message: Message) -> KafkaMessageContext:
        headers = decode_headers(message.headers())
        correlation_id = (
            headers.get("x-correlation-id")
            or headers.get("correlation_id")
            or headers.get("correlation-id")
        )

        key = safe_decode(message.key())

        return KafkaMessageContext(
            topic=message.topic(),
            partition=message.partition(),
            offset=message.offset(),
            key=key,
            timestamp=message.timestamp()[1] if message.timestamp() else None,
            headers=headers,
            correlation_id=correlation_id,
            received_at=utc_now_iso(),
        )

    def _build_idempotency_key(
        self,
        event: EventEnvelope,
        context: KafkaMessageContext,
    ) -> Optional[str]:
        if event.event_id:
            return event.event_id
        if context.key:
            return f"{context.topic}:{context.key}"
        return f"{context.topic}:{context.partition}:{context.offset}"

    def _retry_sleep_seconds(self, attempt: int) -> float:
        exponential = self.config.retry_base_seconds * (2 ** max(0, attempt - 1))
        jitter = random.uniform(0, self.config.retry_base_seconds)
        return min(exponential + jitter, self.config.retry_max_seconds)

    def _dlq_delivery_report(self, err: Optional[KafkaError], msg: Message) -> None:
        if err is not None:
            logger.error("Falha no delivery DLQ: %s", err)
        else:
            logger.info(
                "Mensagem enviada para DLQ. topic=%s partition=%s offset=%s",
                msg.topic(),
                msg.partition(),
                msg.offset(),
            )

    def _on_assign(self, consumer: Consumer, partitions: List[Any]) -> None:
        logger.info("Partições atribuídas: %s", partitions)

    def _on_revoke(self, consumer: Consumer, partitions: List[Any]) -> None:
        logger.warning("Partições revogadas: %s", partitions)

    def _on_lost(self, consumer: Consumer, partitions: List[Any]) -> None:
        logger.error("Partições perdidas: %s", partitions)


# =============================================================================
# Utilitários
# =============================================================================


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "sim", "s"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_decode(value: Optional[bytes]) -> Optional[str]:
    if value is None:
        return None
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.hex()


def decode_headers(headers: Optional[Iterable[Tuple[str, Optional[bytes]]]]) -> Dict[str, str]:
    decoded: Dict[str, str] = {}
    if not headers:
        return decoded

    for key, value in headers:
        if value is None:
            decoded[key] = ""
        else:
            decoded[key] = safe_decode(value) or ""

    return decoded


def install_signal_handlers(consumer: EnterpriseKafkaConsumer) -> None:
    def _handler(signum: int, frame: Any) -> None:  # pylint: disable=unused-argument
        logger.info("Signal recebido: %s", signum)
        consumer.stop()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


# =============================================================================
# Exemplo de processor real customizável
# =============================================================================


class EnterpriseIngestionProcessor:
    """
    Processor exemplo para ser usado como base enterprise.

    Aqui é onde normalmente entram:
    - validação de schema por tipo de evento;
    - normalização;
    - enriquecimento;
    - persistência;
    - publicação downstream;
    - auditoria.
    """

    def process(self, event: EventEnvelope, context: KafkaMessageContext) -> None:
        self._audit_received(event, context)
        normalized = self._normalize(event)
        self._persist(normalized, context)
        self._audit_processed(event, context)

    def _normalize(self, event: EventEnvelope) -> Dict[str, Any]:
        return {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "source": event.source,
            "occurred_at": event.occurred_at,
            "correlation_id": event.correlation_id,
            "data": event.data,
            "ingested_at": utc_now_iso(),
        }

    def _persist(self, normalized: Dict[str, Any], context: KafkaMessageContext) -> None:
        # Substitua por persistência real:
        # - PostgreSQL com SQLAlchemy;
        # - Data Lake S3/MinIO;
        # - BigQuery/Snowflake;
        # - Elasticsearch/OpenSearch;
        # - outro tópico Kafka.
        logger.info(
            "Persistência simulada. event_id=%s topic=%s offset=%s",
            normalized.get("event_id"),
            context.topic,
            context.offset,
        )

    def _audit_received(self, event: EventEnvelope, context: KafkaMessageContext) -> None:
        logger.info(
            "Auditoria: evento recebido. event_id=%s type=%s topic=%s partition=%s offset=%s",
            event.event_id,
            event.event_type,
            context.topic,
            context.partition,
            context.offset,
        )

    def _audit_processed(self, event: EventEnvelope, context: KafkaMessageContext) -> None:
        logger.info(
            "Auditoria: evento processado. event_id=%s type=%s topic=%s partition=%s offset=%s",
            event.event_id,
            event.event_type,
            context.topic,
            context.partition,
            context.offset,
        )


# =============================================================================
# Bootstrap
# =============================================================================


def main() -> None:
    config = KafkaConsumerConfig.from_env()
    processor = EnterpriseIngestionProcessor()
    idempotency_store = InMemoryIdempotencyStore()

    consumer = EnterpriseKafkaConsumer(
        config=config,
        processor=processor,
        idempotency_store=idempotency_store,
    )

    install_signal_handlers(consumer)
    consumer.start()


if __name__ == "__main__":
    main()

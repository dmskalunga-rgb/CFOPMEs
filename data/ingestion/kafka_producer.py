"""
data/ingestion/kafka_producer.py

Kafka Producer enterprise para pipelines de ingestão e publicação de eventos.

Recursos principais:
- Producer Kafka idempotente com acks=all.
- Configuração tipada via dataclass e variáveis de ambiente.
- Publicação síncrona e assíncrona.
- Suporte a batches.
- Envelope padronizado de eventos.
- Headers com correlation_id, causation_id, trace_id e metadados.
- Serialização JSON segura.
- Retry com backoff exponencial e jitter.
- Fallback local JSONL quando Kafka estiver indisponível.
- Callbacks de delivery.
- Métricas internas de publicação, erro, retry, fallback e latência.
- Logs estruturados.
- Flush e shutdown gracioso.
- Particionamento por key.
- Hooks opcionais para validação e transformação antes da publicação.

Dependências recomendadas:
    pip install confluent-kafka pydantic

Variáveis de ambiente suportadas:
    KAFKA_BOOTSTRAP_SERVERS=localhost:9092
    KAFKA_CLIENT_ID=enterprise-kafka-producer
    KAFKA_DEFAULT_TOPIC=enterprise-events
    KAFKA_SECURITY_PROTOCOL=PLAINTEXT
    KAFKA_SASL_MECHANISM=
    KAFKA_SASL_USERNAME=
    KAFKA_SASL_PASSWORD=
    KAFKA_LINGER_MS=10
    KAFKA_BATCH_NUM_MESSAGES=10000
    KAFKA_QUEUE_BUFFERING_MAX_MESSAGES=100000
    KAFKA_MESSAGE_TIMEOUT_MS=300000
    KAFKA_REQUEST_TIMEOUT_MS=30000
    KAFKA_COMPRESSION_TYPE=snappy
    KAFKA_MAX_RETRIES=3
    KAFKA_RETRY_BASE_SECONDS=0.5
    KAFKA_RETRY_MAX_SECONDS=15.0
    KAFKA_FALLBACK_JSONL=data/fallback/kafka_producer_fallback.jsonl
"""

from __future__ import annotations

import json
import logging
import os
import random
import socket
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple, Union

try:
    from confluent_kafka import KafkaError, KafkaException, Message, Producer
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "Dependência ausente: instale com `pip install confluent-kafka`."
    ) from exc

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


def build_logger(name: str = "data.ingestion.kafka_producer") -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ContextFilter(service_name=os.getenv("SERVICE_NAME", "kafka-producer")))

    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = build_logger()


# =============================================================================
# Enums e Models
# =============================================================================


class PublishMode(str, Enum):
    ASYNC = "async"
    SYNC = "sync"


class PublishStatus(str, Enum):
    CREATED = "created"
    VALIDATED = "validated"
    SERIALIZED = "serialized"
    QUEUED = "queued"
    DELIVERED = "delivered"
    FAILED = "failed"
    FALLBACK_STORED = "fallback_stored"


class FallbackStrategy(str, Enum):
    DISABLED = "disabled"
    JSONL = "jsonl"
    RAISE = "raise"


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
class KafkaProducerConfig:
    bootstrap_servers: str
    client_id: str = "enterprise-kafka-producer"
    default_topic: str = "enterprise-events"

    security_protocol: str = "PLAINTEXT"
    sasl_mechanism: Optional[str] = None
    sasl_username: Optional[str] = None
    sasl_password: Optional[str] = None

    enable_idempotence: bool = True
    acks: str = "all"
    compression_type: str = "snappy"
    linger_ms: int = 10
    batch_num_messages: int = 10_000
    queue_buffering_max_messages: int = 100_000
    message_timeout_ms: int = 300_000
    request_timeout_ms: int = 30_000
    retries: int = 5
    max_in_flight_requests_per_connection: int = 5

    default_publish_mode: PublishMode = PublishMode.ASYNC
    flush_timeout_seconds: float = 30.0
    poll_timeout_seconds: float = 0.0

    fallback_strategy: FallbackStrategy = FallbackStrategy.JSONL
    fallback_jsonl_path: Optional[Path] = Path("data/fallback/kafka_producer_fallback.jsonl")

    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    @staticmethod
    def from_env() -> "KafkaProducerConfig":
        fallback_raw = os.getenv("KAFKA_FALLBACK_STRATEGY", FallbackStrategy.JSONL.value)
        fallback_path_raw = os.getenv("KAFKA_FALLBACK_JSONL", "data/fallback/kafka_producer_fallback.jsonl")

        return KafkaProducerConfig(
            bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            client_id=os.getenv("KAFKA_CLIENT_ID", "enterprise-kafka-producer"),
            default_topic=os.getenv("KAFKA_DEFAULT_TOPIC", "enterprise-events"),
            security_protocol=os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT"),
            sasl_mechanism=os.getenv("KAFKA_SASL_MECHANISM") or None,
            sasl_username=os.getenv("KAFKA_SASL_USERNAME") or None,
            sasl_password=os.getenv("KAFKA_SASL_PASSWORD") or None,
            enable_idempotence=env_bool("KAFKA_ENABLE_IDEMPOTENCE", True),
            acks=os.getenv("KAFKA_ACKS", "all"),
            compression_type=os.getenv("KAFKA_COMPRESSION_TYPE", "snappy"),
            linger_ms=int(os.getenv("KAFKA_LINGER_MS", "10")),
            batch_num_messages=int(os.getenv("KAFKA_BATCH_NUM_MESSAGES", "10000")),
            queue_buffering_max_messages=int(os.getenv("KAFKA_QUEUE_BUFFERING_MAX_MESSAGES", "100000")),
            message_timeout_ms=int(os.getenv("KAFKA_MESSAGE_TIMEOUT_MS", "300000")),
            request_timeout_ms=int(os.getenv("KAFKA_REQUEST_TIMEOUT_MS", "30000")),
            retries=int(os.getenv("KAFKA_PRODUCER_INTERNAL_RETRIES", "5")),
            max_in_flight_requests_per_connection=int(
                os.getenv("KAFKA_MAX_IN_FLIGHT_REQUESTS_PER_CONNECTION", "5")
            ),
            default_publish_mode=PublishMode(os.getenv("KAFKA_DEFAULT_PUBLISH_MODE", PublishMode.ASYNC.value)),
            flush_timeout_seconds=float(os.getenv("KAFKA_FLUSH_TIMEOUT_SECONDS", "30.0")),
            poll_timeout_seconds=float(os.getenv("KAFKA_POLL_TIMEOUT_SECONDS", "0.0")),
            fallback_strategy=FallbackStrategy(fallback_raw),
            fallback_jsonl_path=Path(fallback_path_raw) if fallback_path_raw else None,
            retry_policy=RetryPolicy(
                max_retries=int(os.getenv("KAFKA_MAX_RETRIES", "3")),
                base_seconds=float(os.getenv("KAFKA_RETRY_BASE_SECONDS", "0.5")),
                max_seconds=float(os.getenv("KAFKA_RETRY_MAX_SECONDS", "15.0")),
                jitter=env_bool("KAFKA_RETRY_JITTER", True),
            ),
        )

    def to_producer_dict(self) -> Dict[str, Any]:
        config: Dict[str, Any] = {
            "bootstrap.servers": self.bootstrap_servers,
            "client.id": self.client_id,
            "enable.idempotence": self.enable_idempotence,
            "acks": self.acks,
            "compression.type": self.compression_type,
            "linger.ms": self.linger_ms,
            "batch.num.messages": self.batch_num_messages,
            "queue.buffering.max.messages": self.queue_buffering_max_messages,
            "message.timeout.ms": self.message_timeout_ms,
            "request.timeout.ms": self.request_timeout_ms,
            "retries": self.retries,
            "max.in.flight.requests.per.connection": self.max_in_flight_requests_per_connection,
            "security.protocol": self.security_protocol,
        }

        if self.sasl_mechanism:
            config["sasl.mechanisms"] = self.sasl_mechanism
        if self.sasl_username:
            config["sasl.username"] = self.sasl_username
        if self.sasl_password:
            config["sasl.password"] = self.sasl_password

        return config


@dataclass
class PublishRequest:
    event_type: str
    data: Mapping[str, Any]
    topic: Optional[str] = None
    key: Optional[Union[str, bytes]] = None
    source: str = "unknown"
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    trace_id: Optional[str] = None
    schema_version: str = "1.0"
    metadata: Optional[Mapping[str, Any]] = None
    headers: Optional[Mapping[str, Union[str, bytes, int, float, bool, None]]] = None
    partition: Optional[int] = None
    timestamp_ms: Optional[int] = None
    opaque: Optional[Any] = None


@dataclass
class DeliveryResult:
    success: bool
    topic: Optional[str] = None
    partition: Optional[int] = None
    offset: Optional[int] = None
    key: Optional[str] = None
    event_id: Optional[str] = None
    error: Optional[str] = None
    status: PublishStatus = PublishStatus.CREATED


@dataclass
class KafkaProducerMetrics:
    publish_requested: int = 0
    queued: int = 0
    delivered: int = 0
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
            "queued": self.queued,
            "delivered": self.delivered,
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
        """Valida o envelope antes da publicação."""


class EventTransformer(Protocol):
    def transform(self, envelope: EventEnvelope) -> EventEnvelope:
        """Transforma o envelope antes da publicação."""


class DeliveryObserver(Protocol):
    def on_delivery(self, result: DeliveryResult) -> None:
        """Recebe callback de entrega."""


# =============================================================================
# Implementações base
# =============================================================================


class NoOpEventValidator:
    def validate(self, envelope: EventEnvelope) -> EventEnvelope:
        return envelope


class NoOpEventTransformer:
    def transform(self, envelope: EventEnvelope) -> EventEnvelope:
        return envelope


class LoggingDeliveryObserver:
    def on_delivery(self, result: DeliveryResult) -> None:
        if result.success:
            logger.info(
                "Evento entregue. topic=%s partition=%s offset=%s key=%s event_id=%s",
                result.topic,
                result.partition,
                result.offset,
                result.key,
                result.event_id,
            )
        else:
            logger.error(
                "Falha na entrega. topic=%s key=%s event_id=%s error=%s",
                result.topic,
                result.key,
                result.event_id,
                result.error,
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
        envelope.metadata.setdefault("published_by", os.getenv("SERVICE_NAME", "kafka-producer"))
        return envelope


# =============================================================================
# Producer principal
# =============================================================================


class EnterpriseKafkaProducer:
    def __init__(
        self,
        config: Optional[KafkaProducerConfig] = None,
        validator: Optional[EventValidator] = None,
        transformer: Optional[EventTransformer] = None,
        observers: Optional[Sequence[DeliveryObserver]] = None,
    ) -> None:
        self.config = config or KafkaProducerConfig.from_env()
        self.validator = validator or NoOpEventValidator()
        self.transformer = transformer or NoOpEventTransformer()
        self.observers = list(observers or [LoggingDeliveryObserver()])
        self.metrics = KafkaProducerMetrics()
        self.producer = Producer(self.config.to_producer_dict())

        logger.info(
            "Kafka producer iniciado. bootstrap=%s client_id=%s default_topic=%s",
            self.config.bootstrap_servers,
            self.config.client_id,
            self.config.default_topic,
        )

    def publish(
        self,
        request: PublishRequest,
        mode: Optional[PublishMode] = None,
    ) -> DeliveryResult:
        started = time.perf_counter()
        self.metrics.publish_requested += 1

        selected_mode = mode or self.config.default_publish_mode

        try:
            envelope = self._build_envelope(request)
            envelope = self.validator.validate(envelope)
            envelope = self.transformer.transform(envelope)

            payload = self._serialize(envelope)
            topic = request.topic or self.config.default_topic
            key_bytes = normalize_key(request.key or envelope.event_id)
            headers = self._build_headers(request, envelope)

            result = self._produce_with_retry(
                topic=topic,
                key=key_bytes,
                value=payload,
                headers=headers,
                partition=request.partition,
                timestamp_ms=request.timestamp_ms,
                opaque=request.opaque,
                envelope=envelope,
                sync=selected_mode == PublishMode.SYNC,
            )

            self.metrics.last_published_at = utc_now_iso()
            return result

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.failed += 1
            logger.exception("Falha ao publicar evento. event_type=%s error=%s", request.event_type, exc)
            fallback_result = self._handle_publish_failure(request, exc)
            return fallback_result

        finally:
            self.metrics.total_publish_seconds += time.perf_counter() - started
            self.producer.poll(self.config.poll_timeout_seconds)

    def publish_many(
        self,
        requests: Iterable[PublishRequest],
        mode: Optional[PublishMode] = None,
        flush: bool = True,
    ) -> List[DeliveryResult]:
        self.metrics.batches_requested += 1
        results: List[DeliveryResult] = []

        for request in requests:
            results.append(self.publish(request, mode=mode or PublishMode.ASYNC))

        if flush:
            self.flush()

        return results

    def publish_raw(
        self,
        topic: str,
        value: Union[str, bytes, Mapping[str, Any]],
        key: Optional[Union[str, bytes]] = None,
        headers: Optional[Mapping[str, Union[str, bytes, int, float, bool, None]]] = None,
        mode: Optional[PublishMode] = None,
    ) -> DeliveryResult:
        selected_mode = mode or self.config.default_publish_mode
        started = time.perf_counter()
        self.metrics.publish_requested += 1

        try:
            if isinstance(value, bytes):
                payload = value
            elif isinstance(value, str):
                payload = value.encode("utf-8")
            else:
                payload = json.dumps(value, ensure_ascii=False, default=json_default).encode("utf-8")

            key_bytes = normalize_key(key)
            header_list = encode_headers(headers or {})

            result = self._produce_with_retry(
                topic=topic,
                key=key_bytes,
                value=payload,
                headers=header_list,
                partition=None,
                timestamp_ms=None,
                opaque=None,
                envelope=None,
                sync=selected_mode == PublishMode.SYNC,
            )
            self.metrics.last_published_at = utc_now_iso()
            return result

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.failed += 1
            logger.exception("Falha ao publicar mensagem raw. topic=%s error=%s", topic, exc)
            return DeliveryResult(success=False, topic=topic, key=safe_decode(key_bytes), error=str(exc), status=PublishStatus.FAILED)

        finally:
            self.metrics.total_publish_seconds += time.perf_counter() - started
            self.producer.poll(self.config.poll_timeout_seconds)

    def flush(self, timeout: Optional[float] = None) -> int:
        timeout_value = self.config.flush_timeout_seconds if timeout is None else timeout
        remaining = self.producer.flush(timeout=timeout_value)
        if remaining > 0:
            logger.warning("Flush finalizado com mensagens pendentes. remaining=%s", remaining)
        else:
            logger.info("Flush concluído sem mensagens pendentes.")
        return remaining

    def close(self) -> None:
        logger.info("Encerrando Kafka producer. metrics=%s", json.dumps(self.metrics.snapshot()))
        self.flush()

    def _produce_with_retry(
        self,
        topic: str,
        key: Optional[bytes],
        value: bytes,
        headers: List[Tuple[str, bytes]],
        partition: Optional[int],
        timestamp_ms: Optional[int],
        opaque: Optional[Any],
        envelope: Optional[EventEnvelope],
        sync: bool,
    ) -> DeliveryResult:
        attempts = self.config.retry_policy.max_retries + 1
        last_error: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                delivery_result_holder: Dict[str, DeliveryResult] = {}

                callback = self._make_delivery_callback(
                    topic=topic,
                    key=key,
                    envelope=envelope,
                    holder=delivery_result_holder,
                )

                self.producer.produce(
                    topic=topic,
                    key=key,
                    value=value,
                    headers=headers,
                    partition=partition if partition is not None else -1,
                    timestamp=timestamp_ms,
                    callback=callback,
                    opaque=opaque,
                )
                self.metrics.queued += 1

                if sync:
                    self.producer.flush(timeout=self.config.flush_timeout_seconds)
                    result = delivery_result_holder.get(
                        "result",
                        DeliveryResult(
                            success=True,
                            topic=topic,
                            key=safe_decode(key),
                            event_id=envelope.event_id if envelope else None,
                            status=PublishStatus.QUEUED,
                        ),
                    )
                    return result

                return DeliveryResult(
                    success=True,
                    topic=topic,
                    key=safe_decode(key),
                    event_id=envelope.event_id if envelope else None,
                    status=PublishStatus.QUEUED,
                )

            except BufferError as exc:
                last_error = exc
                logger.warning("Buffer local do producer cheio. Executando poll. error=%s", exc)
                self.producer.poll(1)
            except KafkaException as exc:
                last_error = exc
                logger.warning("KafkaException ao produzir. attempt=%s/%s error=%s", attempt, attempts, exc)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_error = exc
                logger.warning("Erro ao produzir. attempt=%s/%s error=%s", attempt, attempts, exc)

            if attempt < attempts:
                self.metrics.retries += 1
                sleep_seconds = self.config.retry_policy.sleep_seconds(attempt)
                time.sleep(sleep_seconds)

        raise RuntimeError("Falha ao publicar após retries máximos") from last_error

    def _make_delivery_callback(
        self,
        topic: str,
        key: Optional[bytes],
        envelope: Optional[EventEnvelope],
        holder: Dict[str, DeliveryResult],
    ) -> Callable[[Optional[KafkaError], Message], None]:
        def _callback(err: Optional[KafkaError], msg: Message) -> None:
            if err is not None:
                self.metrics.failed += 1
                result = DeliveryResult(
                    success=False,
                    topic=topic,
                    key=safe_decode(key),
                    event_id=envelope.event_id if envelope else None,
                    error=str(err),
                    status=PublishStatus.FAILED,
                )
            else:
                self.metrics.delivered += 1
                result = DeliveryResult(
                    success=True,
                    topic=msg.topic(),
                    partition=msg.partition(),
                    offset=msg.offset(),
                    key=safe_decode(key),
                    event_id=envelope.event_id if envelope else None,
                    status=PublishStatus.DELIVERED,
                )

            holder["result"] = result
            self._notify_observers(result)

        return _callback

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
        envelope.metadata.setdefault("producer_client_id", self.config.client_id)
        envelope.metadata.setdefault("producer_host", socket.gethostname())
        envelope.metadata.setdefault("created_by", os.getenv("SERVICE_NAME", "kafka-producer"))
        return envelope

    def _serialize(self, envelope: EventEnvelope) -> bytes:
        payload = model_to_dict(envelope)
        return json.dumps(payload, ensure_ascii=False, default=json_default).encode("utf-8")

    def _build_headers(self, request: PublishRequest, envelope: EventEnvelope) -> List[Tuple[str, bytes]]:
        headers: Dict[str, Union[str, bytes, int, float, bool, None]] = {
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
            headers.update(request.headers)

        return encode_headers(headers)

    def _handle_publish_failure(self, request: PublishRequest, exc: Exception) -> DeliveryResult:
        if self.config.fallback_strategy == FallbackStrategy.RAISE:
            raise exc

        if self.config.fallback_strategy == FallbackStrategy.DISABLED:
            return DeliveryResult(
                success=False,
                topic=request.topic or self.config.default_topic,
                key=safe_decode(normalize_key(request.key)),
                error=str(exc),
                status=PublishStatus.FAILED,
            )

        self._write_fallback_jsonl(request, exc)
        self.metrics.fallback_stored += 1
        return DeliveryResult(
            success=False,
            topic=request.topic or self.config.default_topic,
            key=safe_decode(normalize_key(request.key)),
            error=str(exc),
            status=PublishStatus.FALLBACK_STORED,
        )

    def _write_fallback_jsonl(self, request: PublishRequest, exc: Exception) -> None:
        if not self.config.fallback_jsonl_path:
            logger.warning("Fallback JSONL sem path configurado. Evento perdido. error=%s", exc)
            return

        self.config.fallback_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": PublishStatus.FALLBACK_STORED.value,
            "failed_at": utc_now_iso(),
            "error": str(exc),
            "error_type": exc.__class__.__name__,
            "request": {
                "topic": request.topic or self.config.default_topic,
                "event_type": request.event_type,
                "source": request.source,
                "key": safe_decode(normalize_key(request.key)),
                "correlation_id": request.correlation_id,
                "causation_id": request.causation_id,
                "trace_id": request.trace_id,
                "schema_version": request.schema_version,
                "data": dict(request.data),
                "metadata": dict(request.metadata or {}),
                "headers": dict(request.headers or {}),
            },
        }

        with self.config.fallback_jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=json_default) + "\n")

        logger.warning(
            "Evento armazenado em fallback JSONL. path=%s event_type=%s error=%s",
            self.config.fallback_jsonl_path,
            request.event_type,
            exc,
        )

    def _notify_observers(self, result: DeliveryResult) -> None:
        for observer in self.observers:
            try:
                observer.on_delivery(result)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Observer de delivery falhou. error=%s", exc)

    def __enter__(self) -> "EnterpriseKafkaProducer":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


# =============================================================================
# Utilitários
# =============================================================================


def encode_headers(headers: Mapping[str, Union[str, bytes, int, float, bool, None]]) -> List[Tuple[str, bytes]]:
    encoded: List[Tuple[str, bytes]] = []
    for key, value in headers.items():
        if value is None:
            continue
        if isinstance(value, bytes):
            encoded.append((key, value))
        else:
            encoded.append((key, str(value).encode("utf-8")))
    return encoded


def normalize_key(key: Optional[Union[str, bytes]]) -> Optional[bytes]:
    if key is None:
        return None
    if isinstance(key, bytes):
        return key
    return str(key).encode("utf-8")


def safe_decode(value: Optional[Union[str, bytes]]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.hex()


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
    config = KafkaProducerConfig.from_env()

    producer = EnterpriseKafkaProducer(
        config=config,
        validator=RequiredEventFieldsValidator(["id"]),
        transformer=MetadataTransformer({"domain": "example", "environment": os.getenv("ENVIRONMENT", "dev")}),
    )

    request = PublishRequest(
        topic=config.default_topic,
        key="customer-1",
        event_type="customer.created",
        source="example-service",
        data={
            "id": 1,
            "name": "Cliente Exemplo",
            "created_at": utc_now_iso(),
        },
        metadata={"schema": "customer-created-v1"},
    )

    result = producer.publish(request, mode=PublishMode.SYNC)
    logger.info("Resultado da publicação: %s", result)
    producer.close()


if __name__ == "__main__":
    example_publish()

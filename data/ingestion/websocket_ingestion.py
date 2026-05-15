"""
data/ingestion/websocket_ingestion.py

WebSocket ingestion enterprise para pipelines de dados em tempo real.

Recursos principais:
- Cliente WebSocket resiliente com reconexão automática.
- Backoff exponencial com jitter.
- Heartbeat/ping-pong configurável.
- Autenticação via headers, bearer token ou API key.
- Consumo assíncrono com asyncio.
- Validação de envelope JSON com Pydantic.
- Transformação e enriquecimento plugáveis.
- Buffer interno com backpressure.
- Processamento em batches.
- DLQ local JSONL para mensagens inválidas/falhas.
- Sink plugável para persistência downstream.
- Métricas internas de mensagens, batches, erros, reconexões e latência.
- Logs estruturados.
- Shutdown gracioso com SIGINT/SIGTERM.

Dependências recomendadas:
    pip install websockets pydantic

Variáveis de ambiente suportadas:
    WEBSOCKET_URL=ws://localhost:8080/stream
    WEBSOCKET_AUTH_BEARER_TOKEN=
    WEBSOCKET_API_KEY=
    WEBSOCKET_API_KEY_HEADER=X-API-Key
    WEBSOCKET_EXTRA_HEADERS_JSON={}
    WEBSOCKET_SUBSCRIBE_MESSAGE_JSON={}
    WEBSOCKET_BATCH_SIZE=500
    WEBSOCKET_BATCH_TIMEOUT_SECONDS=5
    WEBSOCKET_QUEUE_MAXSIZE=10000
    WEBSOCKET_PING_INTERVAL=20
    WEBSOCKET_PING_TIMEOUT=20
    WEBSOCKET_CLOSE_TIMEOUT=10
    WEBSOCKET_MAX_RECONNECTS=0
    WEBSOCKET_RECONNECT_BASE_SECONDS=1
    WEBSOCKET_RECONNECT_MAX_SECONDS=60
    WEBSOCKET_DLQ_PATH=data/dlq/websocket_ingestion_dlq.jsonl
    WEBSOCKET_OUTPUT_JSONL=data/output/websocket_ingestion_output.jsonl
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import signal
import socket
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Union

try:
    import websockets
    from websockets.client import WebSocketClientProtocol
    from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatusCode, WebSocketException
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Dependência ausente: instale com `pip install websockets`.") from exc

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


def build_logger(name: str = "data.ingestion.websocket_ingestion") -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ContextFilter(service_name=os.getenv("SERVICE_NAME", "websocket-ingestion")))

    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = build_logger()


# =============================================================================
# Enums e Models
# =============================================================================


class MessageStatus(str, Enum):
    RECEIVED = "received"
    VALIDATED = "validated"
    TRANSFORMED = "transformed"
    WRITTEN = "written"
    FAILED = "failed"
    SENT_TO_DLQ = "sent_to_dlq"
    SKIPPED = "skipped"


class InvalidMessageStrategy(str, Enum):
    RAISE = "raise"
    SKIP = "skip"
    SEND_TO_DLQ = "send_to_dlq"


class WebSocketMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message_type: Optional[str] = None
    source: Optional[str] = None
    occurred_at: Optional[str] = None
    correlation_id: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    received_at: str = Field(default_factory=lambda: utc_now_iso())
    status: MessageStatus = MessageStatus.RECEIVED

    def touch(self, status: Optional[MessageStatus] = None) -> "WebSocketMessage":
        if status:
            self.status = status
        self.metadata["updated_at"] = utc_now_iso()
        return self


@dataclass(frozen=True)
class BackoffPolicy:
    base_seconds: float = 1.0
    max_seconds: float = 60.0
    jitter: bool = True

    def sleep_seconds(self, attempt: int) -> float:
        exponential = self.base_seconds * (2 ** max(0, attempt - 1))
        jitter_value = random.uniform(0, self.base_seconds) if self.jitter else 0.0
        return min(exponential + jitter_value, self.max_seconds)


@dataclass(frozen=True)
class WebSocketIngestionConfig:
    url: str
    bearer_token: Optional[str] = None
    api_key: Optional[str] = None
    api_key_header: str = "X-API-Key"
    extra_headers: Dict[str, str] = field(default_factory=dict)
    subscribe_message: Optional[Dict[str, Any]] = None

    batch_size: int = 500
    batch_timeout_seconds: float = 5.0
    queue_maxsize: int = 10_000

    ping_interval: Optional[float] = 20.0
    ping_timeout: Optional[float] = 20.0
    close_timeout: Optional[float] = 10.0
    max_size: Optional[int] = 16 * 1024 * 1024
    max_queue: Optional[int] = 32

    max_reconnects: int = 0
    reconnect_policy: BackoffPolicy = field(default_factory=BackoffPolicy)

    invalid_message_strategy: InvalidMessageStrategy = InvalidMessageStrategy.SEND_TO_DLQ
    dlq_path: Optional[Path] = Path("data/dlq/websocket_ingestion_dlq.jsonl")

    output_jsonl_path: Optional[Path] = None
    dry_run: bool = False

    @staticmethod
    def from_env() -> "WebSocketIngestionConfig":
        url = os.getenv("WEBSOCKET_URL")
        if not url:
            raise ValueError("WEBSOCKET_URL é obrigatório.")

        extra_headers = parse_json_env("WEBSOCKET_EXTRA_HEADERS_JSON", default={})
        subscribe_message = parse_json_env("WEBSOCKET_SUBSCRIBE_MESSAGE_JSON", default=None)

        dlq_raw = os.getenv("WEBSOCKET_DLQ_PATH", "data/dlq/websocket_ingestion_dlq.jsonl")
        output_raw = os.getenv("WEBSOCKET_OUTPUT_JSONL")

        return WebSocketIngestionConfig(
            url=url,
            bearer_token=os.getenv("WEBSOCKET_AUTH_BEARER_TOKEN") or None,
            api_key=os.getenv("WEBSOCKET_API_KEY") or None,
            api_key_header=os.getenv("WEBSOCKET_API_KEY_HEADER", "X-API-Key"),
            extra_headers={str(k): str(v) for k, v in dict(extra_headers or {}).items()},
            subscribe_message=subscribe_message,
            batch_size=int(os.getenv("WEBSOCKET_BATCH_SIZE", "500")),
            batch_timeout_seconds=float(os.getenv("WEBSOCKET_BATCH_TIMEOUT_SECONDS", "5")),
            queue_maxsize=int(os.getenv("WEBSOCKET_QUEUE_MAXSIZE", "10000")),
            ping_interval=parse_optional_float(os.getenv("WEBSOCKET_PING_INTERVAL", "20")),
            ping_timeout=parse_optional_float(os.getenv("WEBSOCKET_PING_TIMEOUT", "20")),
            close_timeout=parse_optional_float(os.getenv("WEBSOCKET_CLOSE_TIMEOUT", "10")),
            max_size=parse_optional_int(os.getenv("WEBSOCKET_MAX_SIZE", str(16 * 1024 * 1024))),
            max_queue=parse_optional_int(os.getenv("WEBSOCKET_MAX_QUEUE", "32")),
            max_reconnects=int(os.getenv("WEBSOCKET_MAX_RECONNECTS", "0")),
            reconnect_policy=BackoffPolicy(
                base_seconds=float(os.getenv("WEBSOCKET_RECONNECT_BASE_SECONDS", "1")),
                max_seconds=float(os.getenv("WEBSOCKET_RECONNECT_MAX_SECONDS", "60")),
                jitter=env_bool("WEBSOCKET_RECONNECT_JITTER", True),
            ),
            invalid_message_strategy=InvalidMessageStrategy(
                os.getenv("WEBSOCKET_INVALID_MESSAGE_STRATEGY", InvalidMessageStrategy.SEND_TO_DLQ.value)
            ),
            dlq_path=Path(dlq_raw) if dlq_raw else None,
            output_jsonl_path=Path(output_raw) if output_raw else None,
            dry_run=env_bool("WEBSOCKET_DRY_RUN", False),
        )

    def headers(self) -> Dict[str, str]:
        headers = dict(self.extra_headers)
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.api_key:
            headers[self.api_key_header] = self.api_key
        return headers


@dataclass
class WebSocketIngestionMetrics:
    connections_opened: int = 0
    reconnects: int = 0
    messages_received: int = 0
    messages_validated: int = 0
    messages_transformed: int = 0
    messages_written: int = 0
    messages_failed: int = 0
    messages_skipped: int = 0
    messages_sent_to_dlq: int = 0
    batches_written: int = 0
    queue_full_events: int = 0
    total_processing_seconds: float = 0.0
    started_at: Optional[str] = None
    last_message_at: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        avg_latency = self.total_processing_seconds / self.messages_written if self.messages_written else 0.0
        return {
            "connections_opened": self.connections_opened,
            "reconnects": self.reconnects,
            "messages_received": self.messages_received,
            "messages_validated": self.messages_validated,
            "messages_transformed": self.messages_transformed,
            "messages_written": self.messages_written,
            "messages_failed": self.messages_failed,
            "messages_skipped": self.messages_skipped,
            "messages_sent_to_dlq": self.messages_sent_to_dlq,
            "batches_written": self.batches_written,
            "queue_full_events": self.queue_full_events,
            "average_processing_seconds": round(avg_latency, 6),
            "total_processing_seconds": round(self.total_processing_seconds, 6),
            "started_at": self.started_at,
            "last_message_at": self.last_message_at,
        }


# =============================================================================
# Protocols
# =============================================================================


class WebSocketMessageValidator(Protocol):
    async def validate(self, message: WebSocketMessage) -> WebSocketMessage:
        """Valida mensagem recebida."""


class WebSocketMessageTransformer(Protocol):
    async def transform(self, message: WebSocketMessage) -> WebSocketMessage:
        """Transforma/enriquece mensagem recebida."""


class WebSocketMessageSink(Protocol):
    async def write_batch(self, messages: List[WebSocketMessage]) -> None:
        """Persiste batch de mensagens."""


# =============================================================================
# Implementações base
# =============================================================================


class NoOpWebSocketMessageValidator:
    async def validate(self, message: WebSocketMessage) -> WebSocketMessage:
        return message.touch(MessageStatus.VALIDATED)


class NoOpWebSocketMessageTransformer:
    async def transform(self, message: WebSocketMessage) -> WebSocketMessage:
        return message.touch(MessageStatus.TRANSFORMED)


class RequiredFieldsValidator:
    def __init__(self, required_data_fields: Sequence[str]) -> None:
        self.required_data_fields = list(required_data_fields)

    async def validate(self, message: WebSocketMessage) -> WebSocketMessage:
        missing = [field for field in self.required_data_fields if get_nested_value(message.data, field) in (None, "")]
        if missing:
            raise ValueError(f"Campos obrigatórios ausentes: {missing}")
        return message.touch(MessageStatus.VALIDATED)


class MetadataTransformer:
    def __init__(self, metadata: Mapping[str, Any]) -> None:
        self.metadata = dict(metadata)

    async def transform(self, message: WebSocketMessage) -> WebSocketMessage:
        message.metadata.update(self.metadata)
        message.metadata.setdefault("ingestion_host", socket.gethostname())
        message.metadata.setdefault("ingested_by", os.getenv("SERVICE_NAME", "websocket-ingestion"))
        return message.touch(MessageStatus.TRANSFORMED)


class LoggingWebSocketSink:
    async def write_batch(self, messages: List[WebSocketMessage]) -> None:
        logger.info("Batch WebSocket recebido pelo sink. size=%s", len(messages))


class JsonlWebSocketSink:
    def __init__(self, output_path: Union[str, Path]) -> None:
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    async def write_batch(self, messages: List[WebSocketMessage]) -> None:
        lines = [json.dumps(model_to_dict(message), ensure_ascii=False, default=json_default) for message in messages]
        await asyncio.to_thread(self._append_lines, lines)
        logger.info("Batch WebSocket gravado em JSONL. output=%s size=%s", self.output_path, len(messages))

    def _append_lines(self, lines: List[str]) -> None:
        with self.output_path.open("a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line + "\n")


class CallbackWebSocketSink:
    def __init__(self, callback: Callable[[List[WebSocketMessage]], Union[None, Awaitable[None]]]) -> None:
        self.callback = callback

    async def write_batch(self, messages: List[WebSocketMessage]) -> None:
        result = self.callback(messages)
        if asyncio.iscoroutine(result):
            await result


# =============================================================================
# Ingestion principal
# =============================================================================


class EnterpriseWebSocketIngestion:
    def __init__(
        self,
        config: Optional[WebSocketIngestionConfig] = None,
        validator: Optional[WebSocketMessageValidator] = None,
        transformer: Optional[WebSocketMessageTransformer] = None,
        sink: Optional[WebSocketMessageSink] = None,
    ) -> None:
        self.config = config or WebSocketIngestionConfig.from_env()
        self.validator = validator or NoOpWebSocketMessageValidator()
        self.transformer = transformer or NoOpWebSocketMessageTransformer()
        self.sink = sink or (JsonlWebSocketSink(self.config.output_jsonl_path) if self.config.output_jsonl_path else LoggingWebSocketSink())

        self.metrics = WebSocketIngestionMetrics()
        self.stop_event = asyncio.Event()
        self.queue: asyncio.Queue[WebSocketMessage] = asyncio.Queue(maxsize=self.config.queue_maxsize)
        self.consumer_task: Optional[asyncio.Task[Any]] = None
        self.processor_task: Optional[asyncio.Task[Any]] = None

    async def start(self) -> WebSocketIngestionMetrics:
        self.metrics.started_at = utc_now_iso()
        logger.info(
            "Iniciando WebSocket ingestion. url=%s batch_size=%s queue_maxsize=%s",
            self.config.url,
            self.config.batch_size,
            self.config.queue_maxsize,
        )

        self.consumer_task = asyncio.create_task(self._connection_loop(), name="websocket-consumer-loop")
        self.processor_task = asyncio.create_task(self._batch_processor_loop(), name="websocket-batch-processor")

        try:
            await asyncio.gather(self.consumer_task, self.processor_task)
        except asyncio.CancelledError:
            logger.info("WebSocket ingestion cancelado.")
        finally:
            await self.shutdown()

        return self.metrics

    async def stop(self) -> None:
        logger.info("Solicitação de parada recebida para WebSocket ingestion.")
        self.stop_event.set()

        for task in (self.consumer_task, self.processor_task):
            if task and not task.done():
                task.cancel()

    async def shutdown(self) -> None:
        logger.info("Encerrando WebSocket ingestion. metrics=%s", json.dumps(self.metrics.snapshot()))
        await self._drain_queue()

    async def _connection_loop(self) -> None:
        attempt = 0

        while not self.stop_event.is_set():
            if self.config.max_reconnects and attempt >= self.config.max_reconnects:
                logger.error("Número máximo de reconexões atingido. max_reconnects=%s", self.config.max_reconnects)
                self.stop_event.set()
                break

            attempt += 1

            try:
                await self._connect_and_consume()
                attempt = 0

            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, InvalidHandshake, InvalidStatusCode, WebSocketException, OSError) as exc:
                self.metrics.reconnects += 1
                sleep_seconds = self.config.reconnect_policy.sleep_seconds(attempt)
                logger.warning(
                    "Conexão WebSocket perdida/falhou. attempt=%s sleep=%.2fs error=%s",
                    attempt,
                    sleep_seconds,
                    exc,
                )
                await asyncio.sleep(sleep_seconds)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.metrics.reconnects += 1
                sleep_seconds = self.config.reconnect_policy.sleep_seconds(attempt)
                logger.exception(
                    "Erro inesperado no loop WebSocket. attempt=%s sleep=%.2fs error=%s",
                    attempt,
                    sleep_seconds,
                    exc,
                )
                await asyncio.sleep(sleep_seconds)

    async def _connect_and_consume(self) -> None:
        headers = self.config.headers()

        async with websockets.connect(
            self.config.url,
            extra_headers=headers or None,
            ping_interval=self.config.ping_interval,
            ping_timeout=self.config.ping_timeout,
            close_timeout=self.config.close_timeout,
            max_size=self.config.max_size,
            max_queue=self.config.max_queue,
        ) as websocket:
            self.metrics.connections_opened += 1
            logger.info("Conexão WebSocket aberta. url=%s", self.config.url)

            if self.config.subscribe_message:
                await self._send_subscribe_message(websocket, self.config.subscribe_message)

            async for raw_message in websocket:
                if self.stop_event.is_set():
                    break
                await self._handle_raw_message(raw_message)

    async def _send_subscribe_message(
        self,
        websocket: WebSocketClientProtocol,
        message: Mapping[str, Any],
    ) -> None:
        await websocket.send(json.dumps(message, ensure_ascii=False, default=json_default))
        logger.info("Mensagem de subscribe enviada para WebSocket.")

    async def _handle_raw_message(self, raw_message: Union[str, bytes]) -> None:
        started = time.perf_counter()

        try:
            message = self._parse_message(raw_message)
            self.metrics.messages_received += 1
            self.metrics.last_message_at = utc_now_iso()

            try:
                self.queue.put_nowait(message)
            except asyncio.QueueFull:
                self.metrics.queue_full_events += 1
                raise RuntimeError("Fila interna cheia. Backpressure ativado.")

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.messages_failed += 1
            logger.exception("Falha ao tratar mensagem WebSocket bruta. error=%s", exc)
            await self._handle_invalid_raw_message(raw_message, exc)
        finally:
            self.metrics.total_processing_seconds += time.perf_counter() - started

    async def _batch_processor_loop(self) -> None:
        batch: List[WebSocketMessage] = []
        last_flush = time.monotonic()

        while not self.stop_event.is_set():
            timeout = max(0.1, self.config.batch_timeout_seconds - (time.monotonic() - last_flush))

            try:
                message = await asyncio.wait_for(self.queue.get(), timeout=timeout)
                processed = await self._process_message_safely(message)
                if processed:
                    batch.append(processed)
                self.queue.task_done()

                if len(batch) >= self.config.batch_size:
                    await self._write_batch(batch)
                    batch = []
                    last_flush = time.monotonic()

            except asyncio.TimeoutError:
                if batch:
                    await self._write_batch(batch)
                    batch = []
                    last_flush = time.monotonic()
            except asyncio.CancelledError:
                break

        if batch:
            await self._write_batch(batch)

    async def _process_message_safely(self, message: WebSocketMessage) -> Optional[WebSocketMessage]:
        try:
            validated = await self.validator.validate(message)
            self.metrics.messages_validated += 1

            transformed = await self.transformer.transform(validated)
            self.metrics.messages_transformed += 1
            return transformed

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.messages_failed += 1
            logger.exception("Falha ao validar/transformar mensagem WebSocket. message_id=%s error=%s", message.message_id, exc)
            await self._handle_invalid_message(message, exc)
            return None

    async def _write_batch(self, batch: List[WebSocketMessage]) -> None:
        if not batch:
            return

        if self.config.dry_run:
            logger.info("Dry-run ativo. Batch WebSocket não será persistido. size=%s", len(batch))
            self.metrics.batches_written += 1
            self.metrics.messages_written += len(batch)
            return

        try:
            await self.sink.write_batch(batch)
            for message in batch:
                message.touch(MessageStatus.WRITTEN)
            self.metrics.batches_written += 1
            self.metrics.messages_written += len(batch)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.messages_failed += len(batch)
            logger.exception("Falha ao escrever batch WebSocket. size=%s error=%s", len(batch), exc)
            for message in batch:
                await self._handle_invalid_message(message, exc)

    async def _drain_queue(self) -> None:
        batch: List[WebSocketMessage] = []

        while not self.queue.empty():
            try:
                message = self.queue.get_nowait()
                processed = await self._process_message_safely(message)
                if processed:
                    batch.append(processed)
                self.queue.task_done()
            except asyncio.QueueEmpty:
                break

            if len(batch) >= self.config.batch_size:
                await self._write_batch(batch)
                batch = []

        if batch:
            await self._write_batch(batch)

    def _parse_message(self, raw_message: Union[str, bytes]) -> WebSocketMessage:
        if isinstance(raw_message, bytes):
            raw_text = raw_message.decode("utf-8")
        else:
            raw_text = raw_message

        payload = json.loads(raw_text)
        if not isinstance(payload, dict):
            raise ValueError("Mensagem WebSocket deve ser um objeto JSON.")

        normalized = self._normalize_payload(payload)

        try:
            if hasattr(WebSocketMessage, "model_validate"):
                return WebSocketMessage.model_validate(normalized)
            return WebSocketMessage.parse_obj(normalized)  # type: ignore[attr-defined]
        except ValidationError as exc:
            raise ValueError(f"Mensagem WebSocket inválida: {exc}") from exc

    def _normalize_payload(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        if "data" in payload and isinstance(payload.get("data"), Mapping):
            normalized = dict(payload)
        else:
            normalized = {
                "message_type": payload.get("message_type") or payload.get("type") or payload.get("event_type"),
                "source": payload.get("source"),
                "occurred_at": payload.get("occurred_at") or payload.get("timestamp"),
                "correlation_id": payload.get("correlation_id"),
                "data": dict(payload),
                "metadata": {},
            }

        normalized.setdefault("message_id", payload.get("message_id") or payload.get("id") or str(uuid.uuid4()))
        normalized.setdefault("received_at", utc_now_iso())
        normalized.setdefault("status", MessageStatus.RECEIVED.value)
        normalized.setdefault("metadata", {})
        normalized["metadata"].setdefault("websocket_url", self.config.url)
        return normalized

    async def _handle_invalid_raw_message(self, raw_message: Union[str, bytes], exc: Exception) -> None:
        fallback = WebSocketMessage(
            message_type="invalid_raw_message",
            data={"raw_message": safe_decode_raw(raw_message)},
            metadata={"error": str(exc)},
            status=MessageStatus.FAILED,
        )
        await self._handle_invalid_message(fallback, exc)

    async def _handle_invalid_message(self, message: WebSocketMessage, exc: Exception) -> None:
        message.touch(MessageStatus.FAILED)

        if self.config.invalid_message_strategy == InvalidMessageStrategy.RAISE:
            raise exc

        if self.config.invalid_message_strategy == InvalidMessageStrategy.SKIP:
            self.metrics.messages_skipped += 1
            logger.warning("Mensagem WebSocket ignorada. message_id=%s error=%s", message.message_id, exc)
            return

        await self._send_to_dlq(message, exc)

    async def _send_to_dlq(self, message: WebSocketMessage, exc: Exception) -> None:
        if not self.config.dlq_path:
            self.metrics.messages_skipped += 1
            logger.warning("DLQ WebSocket não configurada. Mensagem será ignorada. error=%s", exc)
            return

        payload = {
            "status": MessageStatus.SENT_TO_DLQ.value,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
            "failed_at": utc_now_iso(),
            "message": model_to_dict(message),
        }

        await asyncio.to_thread(self._append_dlq_line, payload)
        self.metrics.messages_sent_to_dlq += 1
        logger.warning("Mensagem WebSocket enviada para DLQ. message_id=%s dlq=%s", message.message_id, self.config.dlq_path)

    def _append_dlq_line(self, payload: Mapping[str, Any]) -> None:
        assert self.config.dlq_path is not None
        self.config.dlq_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config.dlq_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=json_default) + "\n")


# =============================================================================
# Utilitários
# =============================================================================


def install_signal_handlers(ingestion: EnterpriseWebSocketIngestion) -> None:
    loop = asyncio.get_running_loop()

    async def _stop() -> None:
        await ingestion.stop()

    def _handler() -> None:
        logger.info("Signal recebido para parar WebSocket ingestion.")
        asyncio.create_task(_stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handler)
        except NotImplementedError:  # Windows fallback
            signal.signal(sig, lambda *_: asyncio.create_task(_stop()))


def parse_json_env(name: str, default: Any) -> Any:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Variável {name} não contém JSON válido") from exc


def parse_optional_float(value: Optional[str]) -> Optional[float]:
    if value is None or value.strip().lower() in {"", "none", "null"}:
        return None
    return float(value)


def parse_optional_int(value: Optional[str]) -> Optional[int]:
    if value is None or value.strip().lower() in {"", "none", "null"}:
        return None
    return int(value)


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
    if isinstance(value, (datetime, Path, Enum)):
        return str(value)
    return str(value)


def safe_decode_raw(value: Union[str, bytes]) -> str:
    if isinstance(value, str):
        return value
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
# Bootstrap
# =============================================================================


async def async_main() -> None:
    config = WebSocketIngestionConfig.from_env()
    ingestion = EnterpriseWebSocketIngestion(
        config=config,
        validator=NoOpWebSocketMessageValidator(),
        transformer=MetadataTransformer({"environment": os.getenv("ENVIRONMENT", "dev")}),
    )
    install_signal_handlers(ingestion)
    await ingestion.start()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()

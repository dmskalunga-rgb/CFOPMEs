#!/usr/bin/env python3
"""
ai_engine/inference.py

Enterprise-grade Inference Engine.

Objetivo:
- Padronizar inferência single/batch para modelos internos, rule engines e scoring engines.
- Fornecer contratos de request/response, validação, roteamento, métricas, auditoria e fallback.
- Suportar carregamento dinâmico por módulo/classe, artefatos JSON e handlers registrados.
- Ser base para APIs, workers, jobs batch, filas, antifraude, risco, forecasting, NLP e inteligência.

Exemplos:
    python ai_engine/inference.py run \
        --handler echo \
        --input data/inference_requests.json \
        --output reports/ai/inference_results.json

    python ai_engine/inference.py run \
        --handler module \
        --module-path models.financial_engine \
        --class-name FinancialEngine \
        --input data/inference_requests.json \
        --output reports/ai/inference_results.json

Formato esperado JSON:
    [
      {
        "request_id": "req_001",
        "entity_id": "customer_123",
        "operation": "predict",
        "payload": {"x": 1},
        "metadata": {"source": "api"}
      }
    ]

ou:
    { "requests": [ ... ] }

Formato CSV:
    request_id,entity_id,operation,payload,metadata

Onde payload e metadata podem ser JSON string.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import importlib
import json
import logging
import statistics
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, getcontext
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple


APP_NAME = "inference"
ENGINE_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc
DEFAULT_PRECISION = 38

getcontext().prec = DEFAULT_PRECISION


class OutputFormat(str, Enum):
    JSON = "json"
    CSV = "csv"


class InferenceMode(str, Enum):
    SINGLE = "single"
    BATCH = "batch"


class InferenceStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"
    FALLBACK = "fallback"


class RiskSafeMode(str, Enum):
    STRICT = "strict"
    PERMISSIVE = "permissive"


@dataclass(frozen=True)
class InferencePolicy:
    timeout_ms: int = 30_000
    max_batch_size: int = 10_000
    safe_mode: RiskSafeMode = RiskSafeMode.STRICT
    hash_entity_ids: bool = True
    include_stacktrace: bool = False
    fail_fast: bool = False
    enable_fallback: bool = True
    audit_payload_hash_only: bool = True


@dataclass(frozen=True)
class InferenceRequest:
    request_id: str
    entity_id: Optional[str]
    entity_id_hash: Optional[str]
    operation: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any]
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class InferenceResponse:
    request_id: str
    entity_id_hash: Optional[str]
    status: str
    operation: str
    result: Dict[str, Any]
    error: Optional[str]
    latency_ms: Decimal
    handler: str
    model_version: Optional[str]
    created_at: str
    warnings: List[str]
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "entity_id_hash": self.entity_id_hash,
            "status": self.status,
            "operation": self.operation,
            "result": self.result,
            "error": self.error,
            "latency_ms": decimal_str(self.latency_ms),
            "handler": self.handler,
            "model_version": self.model_version,
            "created_at": self.created_at,
            "warnings": self.warnings,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class InferenceBatchSummary:
    total_requests: int
    success_count: int
    error_count: int
    skipped_count: int
    fallback_count: int
    avg_latency_ms: Decimal
    p95_latency_ms: Decimal
    max_latency_ms: Decimal
    handler: str
    started_at: str
    completed_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "skipped_count": self.skipped_count,
            "fallback_count": self.fallback_count,
            "avg_latency_ms": decimal_str(self.avg_latency_ms),
            "p95_latency_ms": decimal_str(self.p95_latency_ms),
            "max_latency_ms": decimal_str(self.max_latency_ms),
            "handler": self.handler,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True)
class AuditRecord:
    audit_id: str
    request_id: str
    entity_id_hash: Optional[str]
    operation: str
    handler: str
    status: str
    payload_hash: Optional[str]
    latency_ms: Decimal
    created_at: str
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "request_id": self.request_id,
            "entity_id_hash": self.entity_id_hash,
            "operation": self.operation,
            "handler": self.handler,
            "status": self.status,
            "payload_hash": self.payload_hash,
            "latency_ms": decimal_str(self.latency_ms),
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


class InferenceHandler(Protocol):
    name: str
    version: str

    def infer(self, request: InferenceRequest) -> Dict[str, Any]:
        ...


class InferenceError(Exception):
    """Base exception for inference engine."""


class InferenceValidationError(InferenceError):
    """Raised when request validation fails."""


class HandlerLoadError(InferenceError):
    """Raised when handler cannot be loaded."""


class HandlerExecutionError(InferenceError):
    """Raised when handler execution fails."""


class EchoHandler:
    name = "echo"
    version = "1.0.0"

    def infer(self, request: InferenceRequest) -> Dict[str, Any]:
        return {
            "echo": request.payload,
            "operation": request.operation,
            "metadata": request.metadata,
        }


class RuleScoreHandler:
    name = "rule_score"
    version = "1.0.0"

    def infer(self, request: InferenceRequest) -> Dict[str, Any]:
        score = Decimal("0")
        reasons: List[str] = []
        payload = request.payload
        for key, value in payload.items():
            if isinstance(value, (int, float, str)):
                try:
                    number = to_decimal(value)
                    if number > 0:
                        score += min(number, Decimal("100")) / Decimal("10")
                        reasons.append(f"positive_numeric_feature:{key}")
                except Exception:
                    continue
        score = clamp_decimal(score, Decimal("0"), Decimal("100"))
        return {
            "score": decimal_str(score),
            "risk_level": risk_level(score),
            "reasons": reasons or ["no_numeric_signal"],
        }


class ModuleClassHandler:
    """Adapter para carregar uma classe arbitrária e tentar métodos comuns de inferência."""

    def __init__(self, module_path: str, class_name: str, init_kwargs: Optional[Dict[str, Any]] = None) -> None:
        self.module_path = module_path
        self.class_name = class_name
        self.init_kwargs = init_kwargs or {}
        self.name = f"{module_path}.{class_name}"
        self.version = "unknown"
        self.instance = self._load()
        self.version = str(getattr(self.instance, "version", getattr(self.instance, "MODEL_VERSION", "unknown")))

    def _load(self) -> Any:
        try:
            module = importlib.import_module(self.module_path)
            klass = getattr(module, self.class_name)
            return klass(**self.init_kwargs)
        except Exception as exc:  # noqa: BLE001
            raise HandlerLoadError(f"Falha ao carregar {self.module_path}.{self.class_name}: {exc}") from exc

    def infer(self, request: InferenceRequest) -> Dict[str, Any]:
        for method_name in ("infer", "predict_one", "predict", "score", "analyze", "run"):
            method = getattr(self.instance, method_name, None)
            if callable(method):
                try:
                    result = method(request.payload)
                    return normalize_result(result)
                except TypeError:
                    continue
        raise HandlerExecutionError(f"Nenhum método de inferência compatível encontrado em {self.name}")


class ArtifactModelHandler:
    """Adapter simples para artefatos JSON do BaseModel que contenham estado e classe não importada."""

    def __init__(self, artifact_path: Path) -> None:
        if not artifact_path.exists():
            raise HandlerLoadError(f"Artefato não encontrado: {artifact_path}")
        self.artifact_path = artifact_path
        self.artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        metadata = self.artifact.get("metadata", {})
        config = self.artifact.get("config", {})
        self.name = str(config.get("name") or metadata.get("name") or "artifact_model")
        self.version = str(config.get("version") or metadata.get("version") or "unknown")

    def infer(self, request: InferenceRequest) -> Dict[str, Any]:
        state = self.artifact.get("state", {})
        known_keys = state.get("keys") or state.get("feature_names") or []
        unknown_keys = sorted(set(request.payload.keys()) - set(known_keys)) if known_keys else []
        return {
            "artifact_model": self.name,
            "version": self.version,
            "known_keys": known_keys,
            "unknown_keys": unknown_keys,
            "payload_hash": hash_payload(request.payload),
            "note": "generic_artifact_handler_no_model_class_execution",
        }


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: Dict[str, InferenceHandler] = {}
        self.register(EchoHandler())
        self.register(RuleScoreHandler())

    def register(self, handler: InferenceHandler) -> None:
        self._handlers[handler.name] = handler

    def get(self, name: str) -> InferenceHandler:
        if name not in self._handlers:
            raise HandlerLoadError(f"Handler não registrado: {name}")
        return self._handlers[name]

    def names(self) -> List[str]:
        return sorted(self._handlers)


class InferenceEngine:
    def __init__(self, handler: InferenceHandler, policy: Optional[InferencePolicy] = None) -> None:
        self.handler = handler
        self.policy = policy or InferencePolicy()
        self.audit_log: List[AuditRecord] = []
        self.logger = logging.getLogger(f"{APP_NAME}.{handler.name}")

    def infer_one(self, request: InferenceRequest) -> InferenceResponse:
        self._validate_request(request)
        started = time.perf_counter()
        warnings: List[str] = []
        try:
            result = self.handler.infer(request)
            status = InferenceStatus.SUCCESS
            error = None
        except Exception as exc:  # noqa: BLE001
            if self.policy.enable_fallback:
                result = self._fallback_result(request, exc)
                status = InferenceStatus.FALLBACK
                error = str(exc)
                warnings.append("fallback_used")
            else:
                result = {}
                status = InferenceStatus.ERROR
                error = self._format_error(exc)
        latency_ms = Decimal(str((time.perf_counter() - started) * 1000))
        response = InferenceResponse(
            request_id=request.request_id,
            entity_id_hash=request.entity_id_hash,
            status=status.value,
            operation=request.operation,
            result=normalize_result(result),
            error=error,
            latency_ms=latency_ms,
            handler=self.handler.name,
            model_version=getattr(self.handler, "version", None),
            created_at=utc_now_iso(),
            warnings=warnings,
            metadata={"engine_version": ENGINE_VERSION},
        )
        self._audit(request, response)
        if status == InferenceStatus.ERROR and self.policy.fail_fast:
            raise HandlerExecutionError(error or "handler failed")
        return response

    def infer_many(self, requests: Sequence[InferenceRequest]) -> Tuple[InferenceBatchSummary, List[InferenceResponse]]:
        if not requests:
            raise InferenceValidationError("requests não pode ser vazio")
        if len(requests) > self.policy.max_batch_size:
            raise InferenceValidationError(f"batch excede max_batch_size={self.policy.max_batch_size}")
        started_at = utc_now_iso()
        responses: List[InferenceResponse] = []
        for request in requests:
            responses.append(self.infer_one(request))
        completed_at = utc_now_iso()
        summary = self._summary(responses, started_at, completed_at)
        return summary, responses

    def _validate_request(self, request: InferenceRequest) -> None:
        if not request.request_id:
            raise InferenceValidationError("request_id é obrigatório")
        if not request.operation:
            raise InferenceValidationError("operation é obrigatório")
        if not isinstance(request.payload, dict):
            raise InferenceValidationError("payload precisa ser objeto/dict")
        if self.policy.safe_mode == RiskSafeMode.STRICT and len(json.dumps(request.payload, default=str)) > 1_000_000:
            raise InferenceValidationError("payload excede limite de 1MB em strict mode")

    def _fallback_result(self, request: InferenceRequest, exc: Exception) -> Dict[str, Any]:
        return {
            "fallback": True,
            "handler": self.handler.name,
            "request_payload_hash": hash_payload(request.payload),
            "error_type": exc.__class__.__name__,
            "decision": "manual_review",
        }

    def _format_error(self, exc: Exception) -> str:
        if self.policy.include_stacktrace:
            return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        return f"{exc.__class__.__name__}: {exc}"

    def _audit(self, request: InferenceRequest, response: InferenceResponse) -> None:
        self.audit_log.append(
            AuditRecord(
                audit_id="aud_" + hashlib.sha256(f"{request.request_id}|{uuid.uuid4()}".encode("utf-8")).hexdigest()[:20],
                request_id=request.request_id,
                entity_id_hash=request.entity_id_hash,
                operation=request.operation,
                handler=self.handler.name,
                status=response.status,
                payload_hash=hash_payload(request.payload) if self.policy.audit_payload_hash_only else None,
                latency_ms=response.latency_ms,
                created_at=response.created_at,
                metadata={"warnings": response.warnings},
            )
        )

    def _summary(self, responses: Sequence[InferenceResponse], started_at: str, completed_at: str) -> InferenceBatchSummary:
        latencies = [item.latency_ms for item in responses]
        statuses = Counter(item.status for item in responses)
        return InferenceBatchSummary(
            total_requests=len(responses),
            success_count=statuses.get(InferenceStatus.SUCCESS.value, 0),
            error_count=statuses.get(InferenceStatus.ERROR.value, 0),
            skipped_count=statuses.get(InferenceStatus.SKIPPED.value, 0),
            fallback_count=statuses.get(InferenceStatus.FALLBACK.value, 0),
            avg_latency_ms=mean_decimal(latencies),
            p95_latency_ms=percentile_decimal(latencies, 95),
            max_latency_ms=max(latencies) if latencies else Decimal("0"),
            handler=self.handler.name,
            started_at=started_at,
            completed_at=completed_at,
        )


class FileLoader:
    @staticmethod
    def load(path: Path, policy: InferencePolicy) -> List[InferenceRequest]:
        if not path.exists():
            raise InferenceValidationError(f"Arquivo não encontrado: {path}")
        if path.suffix.lower() == ".json":
            return FileLoader._load_json(path, policy)
        if path.suffix.lower() == ".csv":
            return FileLoader._load_csv(path, policy)
        raise InferenceValidationError("Formato não suportado. Use .json ou .csv")

    @staticmethod
    def _load_json(path: Path, policy: InferencePolicy) -> List[InferenceRequest]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload if isinstance(payload, list) else payload.get("requests") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise InferenceValidationError("JSON esperado: lista ou objeto com chave 'requests'")
        return [parse_request(dict(row), index + 1, policy) for index, row in enumerate(rows)]

    @staticmethod
    def _load_csv(path: Path, policy: InferencePolicy) -> List[InferenceRequest]:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            rows = [dict(row) for row in csv.DictReader(file)]
        return [parse_request(row, index + 1, policy) for index, row in enumerate(rows)]


class ResultWriter:
    @staticmethod
    def write(summary: InferenceBatchSummary, responses: Sequence[InferenceResponse], audit_log: Sequence[AuditRecord], output: Path, output_format: OutputFormat) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output_format == OutputFormat.JSON:
            payload = {
                "engine_version": ENGINE_VERSION,
                "summary": summary.to_dict(),
                "responses": [item.to_dict() for item in responses],
                "audit": [item.to_dict() for item in audit_log],
            }
            output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            return output
        if output_format == OutputFormat.CSV:
            fieldnames = ["request_id", "entity_id_hash", "status", "operation", "result", "error", "latency_ms", "handler", "model_version", "warnings"]
            with output.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=fieldnames)
                writer.writeheader()
                for response in responses:
                    payload = response.to_dict()
                    writer.writerow(
                        {
                            "request_id": payload["request_id"],
                            "entity_id_hash": payload["entity_id_hash"],
                            "status": payload["status"],
                            "operation": payload["operation"],
                            "result": json.dumps(payload["result"], ensure_ascii=False, default=str),
                            "error": payload["error"],
                            "latency_ms": payload["latency_ms"],
                            "handler": payload["handler"],
                            "model_version": payload["model_version"],
                            "warnings": "|".join(payload["warnings"]),
                        }
                    )
            return output
        raise InferenceError(f"Formato não suportado: {output_format}")


def parse_request(row: Mapping[str, Any], index: int, policy: InferencePolicy) -> InferenceRequest:
    request_id = optional_str(row, "request_id") or f"req_{index:08d}"
    entity_id = optional_str(row, "entity_id")
    operation = optional_str(row, "operation") or "predict"
    payload = parse_json_object(row.get("payload"), default={})
    metadata = parse_json_object(row.get("metadata"), default={})
    created_at = optional_str(row, "created_at") or utc_now_iso()
    entity_hash = hash_identifier(entity_id) if entity_id and policy.hash_entity_ids else entity_id
    return InferenceRequest(
        request_id=request_id,
        entity_id=entity_id,
        entity_id_hash=entity_hash,
        operation=operation,
        payload=payload,
        metadata=metadata,
        created_at=created_at,
    )


def parse_json_object(value: Any, default: Dict[str, Any]) -> Dict[str, Any]:
    if value is None or str(value).strip() == "":
        return dict(default)
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}
        except json.JSONDecodeError:
            return {"value": value}
    return {"value": value}


def load_handler(args: argparse.Namespace) -> InferenceHandler:
    registry = HandlerRegistry()
    if args.handler in {"echo", "rule_score"}:
        return registry.get(args.handler)
    if args.handler == "module":
        if not args.module_path or not args.class_name:
            raise HandlerLoadError("--module-path e --class-name são obrigatórios para handler=module")
        init_kwargs = parse_json_object(args.init_kwargs, default={})
        return ModuleClassHandler(args.module_path, args.class_name, init_kwargs)
    if args.handler == "artifact":
        if not args.artifact_path:
            raise HandlerLoadError("--artifact-path é obrigatório para handler=artifact")
        return ArtifactModelHandler(args.artifact_path)
    raise HandlerLoadError(f"Handler inválido: {args.handler}")


def normalize_result(result: Any) -> Dict[str, Any]:
    if result is None:
        return {}
    if isinstance(result, dict):
        return dict(result)
    if hasattr(result, "to_dict") and callable(result.to_dict):
        converted = result.to_dict()
        return converted if isinstance(converted, dict) else {"value": converted}
    if dataclasses.is_dataclass(result):
        return dataclasses.asdict(result)
    return {"value": result}


def optional_str(row: Mapping[str, Any], key: str) -> Optional[str]:
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, AttributeError) as exc:
        raise InferenceValidationError(f"decimal inválido: {value}") from exc


def decimal_str(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def mean_decimal(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def percentile_decimal(values: Sequence[Decimal], percent: int) -> Decimal:
    if not values:
        return Decimal("0")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = Decimal(len(ordered) - 1) * Decimal(percent) / Decimal("100")
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - Decimal(lower)
    return ordered[lower] * (Decimal("1") - weight) + ordered[upper] * weight


def clamp_decimal(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(value, high))


def risk_level(score: Decimal) -> str:
    if score >= Decimal("85"):
        return "critical"
    if score >= Decimal("65"):
        return "high"
    if score >= Decimal("35"):
        return "medium"
    return "low"


def hash_identifier(value: str, length: int = 32) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def hash_payload(payload: Mapping[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Enterprise inference engine.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_cmd = subparsers.add_parser("run", help="Executa inferência single/batch a partir de JSON/CSV.")
    run_cmd.add_argument("--handler", required=True, choices=["echo", "rule_score", "module", "artifact"])
    run_cmd.add_argument("--input", required=True, type=Path)
    run_cmd.add_argument("--output", required=True, type=Path)
    run_cmd.add_argument("--format", default=OutputFormat.JSON.value, choices=[item.value for item in OutputFormat])
    run_cmd.add_argument("--module-path")
    run_cmd.add_argument("--class-name")
    run_cmd.add_argument("--init-kwargs", default="{}")
    run_cmd.add_argument("--artifact-path", type=Path)
    run_cmd.add_argument("--timeout-ms", default=30_000, type=int)
    run_cmd.add_argument("--max-batch-size", default=10_000, type=int)
    run_cmd.add_argument("--safe-mode", default=RiskSafeMode.STRICT.value, choices=[item.value for item in RiskSafeMode])
    run_cmd.add_argument("--no-hash-entity-ids", action="store_true")
    run_cmd.add_argument("--include-stacktrace", action="store_true")
    run_cmd.add_argument("--fail-fast", action="store_true")
    run_cmd.add_argument("--disable-fallback", action="store_true")

    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args(argv)


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s - %(message)s")


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)
    logger = logging.getLogger(APP_NAME)

    try:
        if args.command == "run":
            policy = InferencePolicy(
                timeout_ms=args.timeout_ms,
                max_batch_size=args.max_batch_size,
                safe_mode=RiskSafeMode(args.safe_mode),
                hash_entity_ids=not args.no_hash_entity_ids,
                include_stacktrace=args.include_stacktrace,
                fail_fast=args.fail_fast,
                enable_fallback=not args.disable_fallback,
            )
            handler = load_handler(args)
            logger.info("Carregando requests de %s", args.input)
            requests = FileLoader.load(args.input, policy)
            logger.info("Executando inferência com handler=%s em %s request(s)", handler.name, len(requests))
            engine = InferenceEngine(handler, policy)
            summary, responses = engine.infer_many(requests)
            ResultWriter.write(summary, responses, engine.audit_log, args.output, OutputFormat(args.format))
            logger.info("Resultado salvo em %s", args.output)
            print(args.output)
            return 0

        raise InferenceError(f"Comando não suportado: {args.command}")

    except InferenceError as exc:
        logger.error("Erro no inference engine: %s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erro inesperado: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(run())

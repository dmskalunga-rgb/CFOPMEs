"""
data/validation/validation_pipeline.py

Enterprise-grade validation pipeline orchestrator.

Este módulo orquestra validações de dados em nível enterprise, integrando regras
customizadas, schema, qualidade, integridade, PII, auditoria, métricas,
políticas de falha, execução por etapas e geração de relatórios consolidados.

Capacidades principais:
- Pipeline declarativo de etapas de validação.
- Execução sequencial com fail-fast opcional.
- Suporte a validadores plugáveis e funções customizadas.
- Contexto único de execução com run_id/correlation_id.
- Auditoria e métricas padronizadas.
- Resultado consolidado com status, score, issues, warnings e errors.
- Preparado para batch, streaming micro-batch, jobs Airflow/Dagster/Prefect,
  APIs internas e pipelines lakehouse.
- Sem dependências obrigatórias além de pandas para datasets tabulares.

Exemplo:
    pipeline = ValidationPipeline(
        name="customer_validation",
        steps=[
            ValidationStep.custom(
                name="row_count_check",
                validator=lambda data, ctx: StepValidationResult.passed("row_count_check")
            )
        ]
    )

    result = pipeline.run(dataset=df, context=PipelineContext(dataset_name="customers"))
    result.raise_for_failure()
"""

from __future__ import annotations

import json
import logging
import math
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple, Union

try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore


logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
DataLike = Union["pd.DataFrame", Sequence[Mapping[str, Any]]]
StepCallable = Callable[[DataLike, "PipelineContext"], "StepValidationResult"]


class PipelineStatus(str, Enum):
    """Status consolidado do pipeline ou de uma etapa."""

    PASSED = "PASSED"
    WARNING = "WARNING"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class PipelineSeverity(str, Enum):
    """Severidade de um issue consolidado."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class StepType(str, Enum):
    """Tipo lógico da etapa de validação."""

    SCHEMA = "SCHEMA"
    QUALITY = "QUALITY"
    INTEGRITY = "INTEGRITY"
    PII = "PII"
    COMPLIANCE = "COMPLIANCE"
    CONSISTENCY = "CONSISTENCY"
    CONTRACT = "CONTRACT"
    DRIFT = "DRIFT"
    CUSTOM = "CUSTOM"


class FailurePolicy(str, Enum):
    """Política de continuação do pipeline após falha."""

    CONTINUE = "CONTINUE"
    FAIL_FAST = "FAIL_FAST"
    STOP_ON_CRITICAL = "STOP_ON_CRITICAL"
    STOP_ON_ERROR = "STOP_ON_ERROR"


class PipelineExecutionError(Exception):
    """Erro para falha bloqueante de execução do pipeline."""


class PipelineConfigurationError(Exception):
    """Erro de configuração inválida do pipeline."""


class AuditSink(Protocol):
    """Contrato mínimo para auditoria."""

    def emit(self, event: Mapping[str, Any]) -> None:
        """Emite evento de auditoria."""


class MetricsSink(Protocol):
    """Contrato compatível com coletores simples de métricas."""

    def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Incrementa contador."""

    def gauge(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Publica gauge."""

    def timing(self, name: str, value_ms: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Publica timing."""


@dataclass(frozen=True)
class PipelineContext:
    """Contexto único de execução do pipeline."""

    dataset_name: str
    pipeline_name: Optional[str] = None
    environment: str = "production"
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: Optional[str] = None
    tenant_id: Optional[str] = None
    source_system: Optional[str] = None
    data_product: Optional[str] = None
    data_owner: Optional[str] = None
    execution_ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def tags(self, extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        tags = {
            "dataset": self.dataset_name,
            "pipeline": self.pipeline_name or "unknown",
            "environment": self.environment,
            "tenant": self.tenant_id or "default",
            "source": self.source_system or "unknown",
            "product": self.data_product or "unknown",
            "owner": self.data_owner or "unknown",
            "run_id": self.run_id,
        }
        if self.correlation_id:
            tags["correlation_id"] = self.correlation_id
        if extra:
            tags.update({str(k): str(v) for k, v in extra.items()})
        return tags

    def to_dict(self) -> JsonDict:
        return {
            "dataset_name": self.dataset_name,
            "pipeline_name": self.pipeline_name,
            "environment": self.environment,
            "run_id": self.run_id,
            "correlation_id": self.correlation_id,
            "tenant_id": self.tenant_id,
            "source_system": self.source_system,
            "data_product": self.data_product,
            "data_owner": self.data_owner,
            "execution_ts": self.execution_ts.isoformat(),
            "metadata": safe_json_value(dict(self.metadata)),
        }


@dataclass(frozen=True)
class PipelineIssue:
    """Issue consolidado produzido por uma etapa."""

    step_name: str
    step_type: StepType
    severity: PipelineSeverity
    message: str
    code: Optional[str] = None
    column: Optional[str] = None
    row_index: Optional[Any] = None
    offending_value: Optional[Any] = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "step_name": self.step_name,
            "step_type": self.step_type.value,
            "severity": self.severity.value,
            "message": self.message,
            "code": self.code,
            "column": self.column,
            "row_index": self.row_index,
            "offending_value": safe_json_value(self.offending_value),
            "evidence": safe_json_value(dict(self.evidence)),
        }


@dataclass(frozen=True)
class StepValidationResult:
    """Resultado individual de uma etapa de validação."""

    step_name: str
    step_type: StepType
    status: PipelineStatus
    score: float = 1.0
    severity: PipelineSeverity = PipelineSeverity.INFO
    issues: Tuple[PipelineIssue, ...] = field(default_factory=tuple)
    metrics: Mapping[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error_message: Optional[str] = None

    @staticmethod
    def passed(step_name: str, step_type: StepType = StepType.CUSTOM, score: float = 1.0, metrics: Optional[Mapping[str, Any]] = None) -> "StepValidationResult":
        now = datetime.now(timezone.utc)
        return StepValidationResult(
            step_name=step_name,
            step_type=step_type,
            status=PipelineStatus.PASSED,
            score=score,
            severity=PipelineSeverity.INFO,
            metrics=metrics or {},
            started_at=now,
            finished_at=now,
        )

    @staticmethod
    def failed(
        step_name: str,
        step_type: StepType,
        message: str,
        severity: PipelineSeverity = PipelineSeverity.ERROR,
        score: float = 0.0,
        issues: Optional[Sequence[PipelineIssue]] = None,
    ) -> "StepValidationResult":
        now = datetime.now(timezone.utc)
        issue_values = tuple(issues or [PipelineIssue(step_name, step_type, severity, message)])
        return StepValidationResult(
            step_name=step_name,
            step_type=step_type,
            status=PipelineStatus.FAILED,
            score=score,
            severity=severity,
            issues=issue_values,
            started_at=now,
            finished_at=now,
            error_message=message,
        )

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.finished_at - self.started_at).total_seconds() * 1000.0)

    @property
    def is_successful(self) -> bool:
        return self.status in {PipelineStatus.PASSED, PipelineStatus.WARNING, PipelineStatus.SKIPPED}

    def to_dict(self) -> JsonDict:
        return {
            "step_name": self.step_name,
            "step_type": self.step_type.value,
            "status": self.status.value,
            "score": self.score,
            "severity": self.severity.value,
            "issues": [issue.to_dict() for issue in self.issues],
            "metrics": safe_json_value(dict(self.metrics)),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class ValidationStep:
    """Definição declarativa de uma etapa do pipeline."""

    name: str
    step_type: StepType
    validator: StepCallable
    enabled: bool = True
    required: bool = True
    weight: float = 1.0
    timeout_seconds: Optional[float] = None
    description: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @staticmethod
    def custom(
        name: str,
        validator: StepCallable,
        *,
        enabled: bool = True,
        required: bool = True,
        weight: float = 1.0,
        description: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "ValidationStep":
        return ValidationStep(
            name=name,
            step_type=StepType.CUSTOM,
            validator=validator,
            enabled=enabled,
            required=required,
            weight=weight,
            description=description,
            metadata=metadata or {},
        )


@dataclass(frozen=True)
class PipelineExecutionResult:
    """Resultado consolidado da execução do pipeline."""

    pipeline_name: str
    context: PipelineContext
    status: PipelineStatus
    score: float
    step_results: Tuple[StepValidationResult, ...]
    started_at: datetime
    finished_at: datetime
    dataset_rows: Optional[int] = None
    dataset_columns: Tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.finished_at - self.started_at).total_seconds() * 1000.0)

    @property
    def issues(self) -> Tuple[PipelineIssue, ...]:
        values: List[PipelineIssue] = []
        for result in self.step_results:
            values.extend(result.issues)
        return tuple(values)

    @property
    def is_successful(self) -> bool:
        return self.status in {PipelineStatus.PASSED, PipelineStatus.WARNING}

    def summary(self) -> str:
        counts = Counter(step.status.value for step in self.step_results)
        severities = Counter(issue.severity.value for issue in self.issues)
        return (
            f"PipelineExecutionResult(pipeline={self.pipeline_name}, dataset={self.context.dataset_name}, "
            f"status={self.status.value}, score={self.score:.4f}, steps={len(self.step_results)}, "
            f"passed={counts.get('PASSED', 0)}, warnings={counts.get('WARNING', 0)}, "
            f"failed={counts.get('FAILED', 0)}, errors={counts.get('ERROR', 0)}, "
            f"issues={len(self.issues)}, critical={severities.get('CRITICAL', 0)}, "
            f"duration_ms={self.duration_ms:.2f})"
        )

    def to_dict(self) -> JsonDict:
        return {
            "pipeline_name": self.pipeline_name,
            "context": self.context.to_dict(),
            "status": self.status.value,
            "score": self.score,
            "dataset_rows": self.dataset_rows,
            "dataset_columns": list(self.dataset_columns),
            "step_results": [step.to_dict() for step in self.step_results],
            "issues": [issue.to_dict() for issue in self.issues],
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
            "metadata": safe_json_value(dict(self.metadata)),
            "summary": self.summary(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)

    def raise_for_failure(self) -> None:
        if self.status in {PipelineStatus.FAILED, PipelineStatus.ERROR}:
            raise PipelineExecutionError(self.summary())


class ValidationPipeline:
    """Orquestrador enterprise de validações de dados."""

    def __init__(
        self,
        *,
        name: str,
        steps: Sequence[ValidationStep],
        failure_policy: FailurePolicy = FailurePolicy.STOP_ON_CRITICAL,
        min_score: float = 0.95,
        warning_score: float = 0.98,
        audit_sink: Optional[AuditSink] = None,
        metrics_sink: Optional[MetricsSink] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if not name:
            raise PipelineConfigurationError("Pipeline name is required")
        if not steps:
            raise PipelineConfigurationError("At least one validation step is required")
        duplicate_steps = [step for step, count in Counter(s.name for s in steps).items() if count > 1]
        if duplicate_steps:
            raise PipelineConfigurationError(f"Duplicated validation step names: {duplicate_steps}")
        if min_score < 0 or min_score > 1:
            raise PipelineConfigurationError("min_score must be between 0 and 1")
        if warning_score < 0 or warning_score > 1:
            raise PipelineConfigurationError("warning_score must be between 0 and 1")

        self.name = name
        self.steps = tuple(steps)
        self.failure_policy = failure_policy
        self.min_score = min_score
        self.warning_score = warning_score
        self.audit_sink = audit_sink
        self.metrics_sink = metrics_sink
        self.metadata = dict(metadata or {})

    def run(self, dataset: DataLike, context: PipelineContext) -> PipelineExecutionResult:
        """Executa o pipeline de validação."""
        started = datetime.now(timezone.utc)
        start_perf = time.perf_counter()
        context = self._normalize_context(context)
        rows, columns = dataset_shape(dataset)

        self._emit_audit(
            "validation_pipeline_started",
            context,
            {
                "pipeline": self.name,
                "step_count": len(self.steps),
                "failure_policy": self.failure_policy.value,
                "rows": rows,
                "columns": list(columns),
            },
        )

        results: List[StepValidationResult] = []

        for step in self.steps:
            if not step.enabled:
                result = self._skipped_result(step)
                results.append(result)
                self._record_step_metrics(result, context, step)
                continue

            result = self._execute_step(step, dataset, context)
            results.append(result)
            self._record_step_metrics(result, context, step)
            self._emit_audit(
                "validation_step_finished",
                context,
                {
                    "pipeline": self.name,
                    "step": step.name,
                    "step_type": step.step_type.value,
                    "status": result.status.value,
                    "score": result.score,
                    "issue_count": len(result.issues),
                    "duration_ms": result.duration_ms,
                },
            )

            if self._should_stop(result, step):
                logger.info("Validation pipeline stopped by failure policy: %s", self.failure_policy.value)
                break

        score = self._compute_score(results)
        status = self._compute_status(results, score)
        finished = datetime.now(timezone.utc)
        result = PipelineExecutionResult(
            pipeline_name=self.name,
            context=context,
            status=status,
            score=score,
            step_results=tuple(results),
            started_at=started,
            finished_at=finished,
            dataset_rows=rows,
            dataset_columns=columns,
            metadata=self.metadata,
        )

        elapsed_ms = (time.perf_counter() - start_perf) * 1000.0
        self._record_pipeline_metrics(result, elapsed_ms)
        self._emit_audit(
            "validation_pipeline_finished",
            context,
            {
                "pipeline": self.name,
                "status": result.status.value,
                "score": result.score,
                "issue_count": len(result.issues),
                "duration_ms": result.duration_ms,
                "summary": result.summary(),
            },
        )
        return result

    def _execute_step(self, step: ValidationStep, dataset: DataLike, context: PipelineContext) -> StepValidationResult:
        started = datetime.now(timezone.utc)
        start_perf = time.perf_counter()
        self._emit_audit(
            "validation_step_started",
            context,
            {"pipeline": self.name, "step": step.name, "step_type": step.step_type.value},
        )
        try:
            result = step.validator(dataset, context)
            if not isinstance(result, StepValidationResult):
                raise PipelineExecutionError(f"Step {step.name} returned invalid result type: {type(result)!r}")
            return StepValidationResult(
                step_name=result.step_name or step.name,
                step_type=result.step_type or step.step_type,
                status=result.status,
                score=max(0.0, min(1.0, float(result.score))),
                severity=result.severity,
                issues=result.issues,
                metrics=result.metrics,
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                error_message=result.error_message,
            )
        except Exception as exc:
            logger.exception("Validation step failed: %s", step.name)
            issue = PipelineIssue(
                step_name=step.name,
                step_type=step.step_type,
                severity=PipelineSeverity.CRITICAL if step.required else PipelineSeverity.ERROR,
                message="Validation step execution error",
                code="STEP_EXECUTION_ERROR",
                evidence={"error": str(exc), "error_type": type(exc).__name__},
            )
            return StepValidationResult(
                step_name=step.name,
                step_type=step.step_type,
                status=PipelineStatus.ERROR,
                score=0.0,
                severity=issue.severity,
                issues=(issue,),
                metrics={"duration_ms": (time.perf_counter() - start_perf) * 1000.0},
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                error_message=str(exc),
            )

    def _skipped_result(self, step: ValidationStep) -> StepValidationResult:
        now = datetime.now(timezone.utc)
        return StepValidationResult(
            step_name=step.name,
            step_type=step.step_type,
            status=PipelineStatus.SKIPPED,
            score=1.0,
            severity=PipelineSeverity.INFO,
            metrics={"reason": "disabled"},
            started_at=now,
            finished_at=now,
        )

    def _should_stop(self, result: StepValidationResult, step: ValidationStep) -> bool:
        if self.failure_policy == FailurePolicy.CONTINUE:
            return False
        if self.failure_policy == FailurePolicy.FAIL_FAST and result.status in {PipelineStatus.FAILED, PipelineStatus.ERROR}:
            return True
        if self.failure_policy == FailurePolicy.STOP_ON_ERROR and result.status == PipelineStatus.ERROR:
            return True
        if self.failure_policy == FailurePolicy.STOP_ON_CRITICAL:
            return any(issue.severity == PipelineSeverity.CRITICAL for issue in result.issues) or (
                step.required and result.status == PipelineStatus.ERROR
            )
        return False

    def _compute_score(self, results: Sequence[StepValidationResult]) -> float:
        active = [result for result in results if result.status != PipelineStatus.SKIPPED]
        if not active:
            return 1.0
        step_by_name = {step.name: step for step in self.steps}
        weighted_sum = 0.0
        total_weight = 0.0
        for result in active:
            step = step_by_name.get(result.step_name)
            weight = step.weight if step else 1.0
            if result.status == PipelineStatus.ERROR:
                step_score = 0.0
            elif result.status == PipelineStatus.FAILED:
                step_score = min(result.score, 0.5)
            else:
                step_score = result.score
            weighted_sum += step_score * weight
            total_weight += weight
        return round(weighted_sum / max(total_weight, 1e-9), 6)

    def _compute_status(self, results: Sequence[StepValidationResult], score: float) -> PipelineStatus:
        if any(result.status == PipelineStatus.ERROR and self._step_required(result.step_name) for result in results):
            return PipelineStatus.ERROR
        if any(issue.severity == PipelineSeverity.CRITICAL for result in results for issue in result.issues):
            return PipelineStatus.FAILED
        if score < self.min_score:
            return PipelineStatus.FAILED
        if score < self.warning_score:
            return PipelineStatus.WARNING
        if any(result.status in {PipelineStatus.FAILED, PipelineStatus.WARNING} for result in results):
            return PipelineStatus.WARNING
        return PipelineStatus.PASSED

    def _step_required(self, step_name: str) -> bool:
        for step in self.steps:
            if step.name == step_name:
                return step.required
        return True

    def _normalize_context(self, context: PipelineContext) -> PipelineContext:
        if not context.dataset_name:
            raise PipelineConfigurationError("context.dataset_name is required")
        if context.pipeline_name == self.name:
            return context
        return PipelineContext(
            dataset_name=context.dataset_name,
            pipeline_name=context.pipeline_name or self.name,
            environment=context.environment,
            run_id=context.run_id,
            correlation_id=context.correlation_id,
            tenant_id=context.tenant_id,
            source_system=context.source_system,
            data_product=context.data_product,
            data_owner=context.data_owner,
            execution_ts=context.execution_ts,
            metadata=context.metadata,
        )

    def _record_step_metrics(self, result: StepValidationResult, context: PipelineContext, step: ValidationStep) -> None:
        if not self.metrics_sink:
            return
        tags = context.tags({"pipeline": self.name, "step": step.name, "step_type": step.step_type.value, "status": result.status.value})
        self.metrics_sink.increment("validation.pipeline.step.executed", tags=tags)
        if result.status in {PipelineStatus.FAILED, PipelineStatus.ERROR}:
            self.metrics_sink.increment("validation.pipeline.step.failed", tags=tags)
        self.metrics_sink.gauge("validation.pipeline.step.score", result.score, tags=tags)
        self.metrics_sink.gauge("validation.pipeline.step.issues", len(result.issues), tags=tags)
        self.metrics_sink.timing("validation.pipeline.step.duration_ms", result.duration_ms, tags=tags)

    def _record_pipeline_metrics(self, result: PipelineExecutionResult, elapsed_ms: float) -> None:
        if not self.metrics_sink:
            return
        tags = result.context.tags({"pipeline": self.name, "status": result.status.value})
        self.metrics_sink.increment("validation.pipeline.executed", tags=tags)
        if result.status in {PipelineStatus.PASSED, PipelineStatus.WARNING}:
            self.metrics_sink.increment("validation.pipeline.succeeded", tags=tags)
        else:
            self.metrics_sink.increment("validation.pipeline.failed", tags=tags)
        self.metrics_sink.gauge("validation.pipeline.score", result.score, tags=tags)
        self.metrics_sink.gauge("validation.pipeline.issues", len(result.issues), tags=tags)
        self.metrics_sink.gauge("validation.pipeline.steps", len(result.step_results), tags=tags)
        if result.dataset_rows is not None:
            self.metrics_sink.gauge("validation.pipeline.rows", result.dataset_rows, tags=tags)
        self.metrics_sink.timing("validation.pipeline.duration_ms", elapsed_ms, tags=tags)

    def _emit_audit(self, event_name: str, context: PipelineContext, payload: Mapping[str, Any]) -> None:
        if not self.audit_sink:
            return
        event = {
            "event_id": str(uuid.uuid4()),
            "event_name": event_name,
            "emitted_at": datetime.now(timezone.utc).isoformat(),
            "context": context.to_dict(),
            "payload": safe_json_value(dict(payload)),
        }
        self.audit_sink.emit(event)


class InMemoryAuditSink:
    """Audit sink simples para testes e execução local."""

    def __init__(self) -> None:
        self.events: List[Mapping[str, Any]] = []

    def emit(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))


class InMemoryMetricsSink:
    """Metrics sink simples para testes e execução local."""

    def __init__(self) -> None:
        self.counters: MutableMapping[str, int] = defaultdict(int)
        self.gauges: MutableMapping[str, float] = {}
        self.timings: MutableMapping[str, List[float]] = defaultdict(list)

    def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        self.counters[self._key(name, tags)] += value

    def gauge(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        self.gauges[self._key(name, tags)] = float(value)

    def timing(self, name: str, value_ms: float, tags: Optional[Mapping[str, str]] = None) -> None:
        self.timings[self._key(name, tags)].append(float(value_ms))

    def _key(self, name: str, tags: Optional[Mapping[str, str]]) -> str:
        if not tags:
            return name
        tag_text = ",".join(f"{key}={value}" for key, value in sorted(tags.items()))
        return f"{name}|{tag_text}"


def make_step_from_validator(
    *,
    name: str,
    step_type: StepType,
    validator_object: Any,
    method_name: str = "validate",
    kwargs: Optional[Mapping[str, Any]] = None,
    result_adapter: Optional[Callable[[Any, str, StepType], StepValidationResult]] = None,
    enabled: bool = True,
    required: bool = True,
    weight: float = 1.0,
) -> ValidationStep:
    """Cria uma etapa a partir de um objeto validador existente.

    O adapter padrão tenta converter objetos com atributos comuns:
    status, score, is_valid, is_compliant, is_acceptable, violations, issues e findings.
    """
    call_kwargs = dict(kwargs or {})

    def _runner(dataset: DataLike, context: PipelineContext) -> StepValidationResult:
        method = getattr(validator_object, method_name, None)
        if not callable(method):
            raise PipelineConfigurationError(f"Validator object does not have callable method: {method_name}")
        raw_result = method(dataset=dataset, **call_kwargs)
        adapter = result_adapter or adapt_external_result
        return adapter(raw_result, name, step_type)

    return ValidationStep(
        name=name,
        step_type=step_type,
        validator=_runner,
        enabled=enabled,
        required=required,
        weight=weight,
    )


def adapt_external_result(raw_result: Any, step_name: str, step_type: StepType) -> StepValidationResult:
    """Adapta resultados de validadores externos para StepValidationResult."""
    if isinstance(raw_result, StepValidationResult):
        return raw_result

    status_text = str(getattr(raw_result, "status", "PASSED"))
    if "." in status_text:
        status_text = status_text.rsplit(".", 1)[-1]
    status_text = status_text.upper()

    is_ok = bool(
        getattr(raw_result, "is_valid", False)
        or getattr(raw_result, "is_compliant", False)
        or getattr(raw_result, "is_acceptable", False)
        or status_text in {"PASSED", "COMPLIANT", "SUCCEEDED", "WARNING"}
    )

    if status_text in {"ERROR"}:
        status = PipelineStatus.ERROR
    elif status_text in {"FAILED", "NON_COMPLIANT"}:
        status = PipelineStatus.FAILED
    elif status_text in {"WARNING"}:
        status = PipelineStatus.WARNING
    elif is_ok:
        status = PipelineStatus.PASSED
    else:
        status = PipelineStatus.FAILED

    score = float(getattr(raw_result, "score", 1.0 if is_ok else 0.0))
    raw_issues = []
    for attr_name in ("issues", "violations", "findings"):
        values = getattr(raw_result, attr_name, None)
        if values:
            raw_issues.extend(list(values))

    issues = tuple(_adapt_issue(item, step_name, step_type) for item in raw_issues[:500])
    severity = _highest_issue_severity(issues)
    metrics = {}
    for attr in ("dataset_rows", "risk_score", "duration_ms"):
        if hasattr(raw_result, attr):
            metrics[attr] = safe_json_value(getattr(raw_result, attr))

    return StepValidationResult(
        step_name=step_name,
        step_type=step_type,
        status=status,
        score=max(0.0, min(1.0, score if score <= 1 else score / 100.0)),
        severity=severity,
        issues=issues,
        metrics=metrics,
        error_message=getattr(raw_result, "error_message", None),
    )


def _adapt_issue(item: Any, step_name: str, step_type: StepType) -> PipelineIssue:
    if isinstance(item, PipelineIssue):
        return item
    severity_text = str(getattr(item, "severity", PipelineSeverity.ERROR.value))
    if "." in severity_text:
        severity_text = severity_text.rsplit(".", 1)[-1]
    severity = parse_severity(severity_text)
    return PipelineIssue(
        step_name=step_name,
        step_type=step_type,
        severity=severity,
        message=str(getattr(item, "message", "Validation issue detected")),
        code=str(getattr(item, "code", "")) or None,
        column=getattr(item, "column", None) or getattr(item, "field_name", None),
        row_index=getattr(item, "row_index", None),
        offending_value=getattr(item, "offending_value", None),
        evidence=getattr(item, "evidence", {}) or {},
    )


def _highest_issue_severity(issues: Sequence[PipelineIssue]) -> PipelineSeverity:
    if not issues:
        return PipelineSeverity.INFO
    order = {
        PipelineSeverity.INFO: 1,
        PipelineSeverity.WARNING: 2,
        PipelineSeverity.ERROR: 3,
        PipelineSeverity.CRITICAL: 4,
    }
    return max((issue.severity for issue in issues), key=lambda value: order[value])


def parse_severity(value: str) -> PipelineSeverity:
    normalized = value.upper()
    if normalized in PipelineSeverity.__members__:
        return PipelineSeverity[normalized]
    if normalized in {"LOW", "MEDIUM"}:
        return PipelineSeverity.WARNING
    if normalized == "HIGH":
        return PipelineSeverity.ERROR
    return PipelineSeverity.CRITICAL if normalized == "CRITICAL" else PipelineSeverity.ERROR


def dataset_shape(dataset: DataLike) -> Tuple[Optional[int], Tuple[str, ...]]:
    """Retorna quantidade de linhas e colunas de forma tolerante."""
    if pd is not None and isinstance(dataset, pd.DataFrame):
        return len(dataset), tuple(map(str, dataset.columns))
    if isinstance(dataset, Sequence):
        rows = len(dataset)
        columns: List[str] = []
        for item in dataset:
            if isinstance(item, Mapping):
                columns = list(map(str, item.keys()))
                break
        return rows, tuple(columns)
    return None, tuple()


def safe_json_value(value: Any) -> Any:
    """Converte valores arbitrários para JSON seguro."""
    if isinstance(value, Mapping):
        return {str(key): safe_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [safe_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def build_basic_pipeline(
    *,
    name: str,
    custom_steps: Sequence[ValidationStep],
    audit_sink: Optional[AuditSink] = None,
    metrics_sink: Optional[MetricsSink] = None,
    failure_policy: FailurePolicy = FailurePolicy.STOP_ON_CRITICAL,
) -> ValidationPipeline:
    """Factory simples para montar um pipeline com etapas customizadas."""
    return ValidationPipeline(
        name=name,
        steps=custom_steps,
        failure_policy=failure_policy,
        audit_sink=audit_sink,
        metrics_sink=metrics_sink,
    )


__all__ = [
    "AuditSink",
    "DataLike",
    "FailurePolicy",
    "InMemoryAuditSink",
    "InMemoryMetricsSink",
    "MetricsSink",
    "PipelineConfigurationError",
    "PipelineContext",
    "PipelineExecutionError",
    "PipelineExecutionResult",
    "PipelineIssue",
    "PipelineSeverity",
    "PipelineStatus",
    "StepCallable",
    "StepType",
    "StepValidationResult",
    "ValidationPipeline",
    "ValidationStep",
    "adapt_external_result",
    "build_basic_pipeline",
    "dataset_shape",
    "make_step_from_validator",
    "parse_severity",
    "safe_json_value",
]

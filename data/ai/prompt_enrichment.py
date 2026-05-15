"""
data/ai/prompt_enrichment.py

Enterprise-grade prompt enrichment and prompt assembly layer.

This module prepares high-quality, policy-compliant prompts/messages before AI
inference. It can be used directly or as a pre-processor inside
`data/ai/inference_pipeline.py`.

Core capabilities:

- Prompt templates with variable interpolation
- Context/RAG document injection
- System instruction composition
- Output-format instruction injection
- Tenant/application/domain policy instructions
- Persona and tone instructions
- Few-shot example injection
- Safety/guardrail preambles
- Token-budget aware context packing
- Prompt versioning and hashing
- Deterministic prompt manifests
- Audit and metrics hooks
- PII redaction hooks
- Integration adapter for inference_pipeline.PreProcessor

Python:
    3.10+
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from string import Template
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class PromptEnrichmentError(Exception):
    """Base exception for prompt enrichment failures."""


class PromptTemplateError(PromptEnrichmentError):
    """Raised when prompt template rendering fails."""


class PromptValidationError(PromptEnrichmentError):
    """Raised when prompt input is invalid."""


class PromptPolicyError(PromptEnrichmentError):
    """Raised when prompt policy blocks enrichment."""


class PromptBudgetError(PromptEnrichmentError):
    """Raised when prompt cannot fit into budget."""


class PromptConfigurationError(PromptEnrichmentError):
    """Raised when enrichment configuration is invalid."""


# =============================================================================
# Enums
# =============================================================================


class PromptRole(str, Enum):
    """Prompt message roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class EnrichmentStage(str, Enum):
    """Named enrichment stages."""

    VALIDATION = "validation"
    VARIABLE_RENDERING = "variable_rendering"
    POLICY_INJECTION = "policy_injection"
    CONTEXT_PACKING = "context_packing"
    EXAMPLE_INJECTION = "example_injection"
    OUTPUT_CONTRACT = "output_contract"
    FINAL_ASSEMBLY = "final_assembly"
    AUDIT = "audit"


class ContextPackingStrategy(str, Enum):
    """How context documents are selected and ordered."""

    SCORE_DESC = "score_desc"
    RECENCY_DESC = "recency_desc"
    ORIGINAL_ORDER = "original_order"
    DIVERSITY_FIRST = "diversity_first"


class OutputFormat(str, Enum):
    """Common output contracts."""

    FREE_TEXT = "free_text"
    MARKDOWN = "markdown"
    JSON = "json"
    JSON_SCHEMA = "json_schema"
    BULLETS = "bullets"
    TABLE = "table"
    CODE = "code"


class SensitivityLevel(str, Enum):
    """Prompt/context sensitivity classification."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class PromptEnrichmentConfig:
    """Global enrichment configuration."""

    max_prompt_chars: int = 120_000
    max_context_chars: int = 50_000
    max_context_documents: int = 20
    max_examples: int = 8
    context_separator: str = "\n\n---\n\n"
    include_context_citations: bool = True
    include_prompt_manifest: bool = True
    enable_policy_instructions: bool = True
    enable_safety_instructions: bool = True
    enable_pii_redaction: bool = False
    fail_on_missing_variables: bool = True
    context_packing_strategy: ContextPackingStrategy = ContextPackingStrategy.SCORE_DESC
    default_output_format: OutputFormat = OutputFormat.MARKDOWN
    audit_enabled: bool = True
    metrics_enabled: bool = True
    version: str = "1.0.0"

    def validate(self) -> None:
        if self.max_prompt_chars <= 0:
            raise PromptConfigurationError("max_prompt_chars must be positive")
        if self.max_context_chars < 0:
            raise PromptConfigurationError("max_context_chars must be >= 0")
        if self.max_context_documents < 0:
            raise PromptConfigurationError("max_context_documents must be >= 0")
        if self.max_examples < 0:
            raise PromptConfigurationError("max_examples must be >= 0")


@dataclass(frozen=True)
class PromptContext:
    """Request metadata for prompt enrichment."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    application: Optional[str] = None
    domain: Optional[str] = None
    locale: Optional[str] = None
    trace_id: Optional[str] = None
    sensitivity: SensitivityLevel = SensitivityLevel.INTERNAL
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptMessage:
    """Provider-neutral message."""

    role: PromptRole
    content: str
    name: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptTemplate:
    """Template definition for prompt rendering."""

    template_id: str
    version: str
    content: str
    role: PromptRole = PromptRole.USER
    description: Optional[str] = None
    required_variables: Sequence[str] = field(default_factory=tuple)
    optional_variables: Mapping[str, Any] = field(default_factory=dict)
    tags: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.template_id:
            raise PromptTemplateError("template_id is required")
        if not self.version:
            raise PromptTemplateError("template version is required")
        if not self.content:
            raise PromptTemplateError("template content is required")


@dataclass(frozen=True)
class ContextDocument:
    """Context document injected into a prompt."""

    id: str
    text: str
    title: Optional[str] = None
    source: Optional[str] = None
    url: Optional[str] = None
    score: Optional[float] = None
    created_at: Optional[str] = None
    sensitivity: SensitivityLevel = SensitivityLevel.INTERNAL
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FewShotExample:
    """Few-shot example for prompt enrichment."""

    input_text: str
    output_text: str
    label: Optional[str] = None
    score: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutputContract:
    """Desired output contract for the model."""

    format: OutputFormat = OutputFormat.MARKDOWN
    json_schema: Optional[Mapping[str, Any]] = None
    instructions: Optional[str] = None
    require_citations: bool = False
    language: Optional[str] = None
    max_words: Optional[int] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyInstruction:
    """Instruction inserted based on governance policies."""

    id: str
    content: str
    priority: int = 100
    domains: Sequence[str] = field(default_factory=tuple)
    applications: Sequence[str] = field(default_factory=tuple)
    tenants: Sequence[str] = field(default_factory=tuple)
    sensitivity_levels: Sequence[SensitivityLevel] = field(default_factory=tuple)
    enabled: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def matches(self, context: PromptContext) -> bool:
        if not self.enabled:
            return False
        if self.domains and context.domain not in self.domains:
            return False
        if self.applications and context.application not in self.applications:
            return False
        if self.tenants and context.tenant_id not in self.tenants:
            return False
        if self.sensitivity_levels and context.sensitivity not in self.sensitivity_levels:
            return False
        return True


@dataclass(frozen=True)
class EnrichmentInput:
    """Input payload for prompt enrichment."""

    user_prompt: Optional[str] = None
    base_messages: Sequence[PromptMessage] = field(default_factory=tuple)
    template: Optional[PromptTemplate] = None
    variables: Mapping[str, Any] = field(default_factory=dict)
    context_documents: Sequence[ContextDocument] = field(default_factory=tuple)
    few_shot_examples: Sequence[FewShotExample] = field(default_factory=tuple)
    output_contract: Optional[OutputContract] = None
    system_instructions: Sequence[str] = field(default_factory=tuple)
    context: PromptContext = field(default_factory=PromptContext)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptManifest:
    """Deterministic manifest of prompt assembly."""

    manifest_id: str
    request_id: str
    enricher_version: str
    template_id: Optional[str]
    template_version: Optional[str]
    prompt_hash: str
    context_hash: str
    variables_hash: str
    context_document_ids: Sequence[str]
    example_count: int
    output_format: OutputFormat
    created_at: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StageTiming:
    """Timing for a prompt enrichment stage."""

    stage: EnrichmentStage
    latency_ms: float
    success: bool
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EnrichmentResult:
    """Prompt enrichment result."""

    request_id: str
    messages: Sequence[PromptMessage]
    prompt_text: str
    manifest: PromptManifest
    warnings: Sequence[str] = field(default_factory=tuple)
    stage_timings: Sequence[StageTiming] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


# =============================================================================
# Protocols
# =============================================================================


class AuditSink(Protocol):
    """Audit sink protocol."""

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        """Emit audit event."""


class MetricsSink(Protocol):
    """Metrics sink protocol."""

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Increment counter."""

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Observe metric."""


class PiiRedactor(Protocol):
    """PII redaction protocol."""

    async def redact(self, text: str, context: PromptContext) -> str:
        """Return redacted text."""


# =============================================================================
# Utility Functions
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / 4))


def normalize_whitespace(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def extract_template_variables(content: str) -> Sequence[str]:
    names = re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", content)
    return tuple(sorted(set(names)))


def truncate_text(text: str, max_chars: int) -> str:
    if max_chars < 0:
        raise ValueError("max_chars must be >= 0")
    if len(text) <= max_chars:
        return text
    if max_chars <= 32:
        return text[:max_chars]
    return text[: max_chars - 24].rstrip() + "\n...[TRUNCATED]"


def simple_redact_text(text: str) -> str:
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", text)
    text = re.sub(r"\b(?:\+?\d[\d\s().-]{7,}\d)\b", "[REDACTED_PHONE]", text)
    text = re.sub(r"\b(?:sk-|ghp_|xoxb-)[A-Za-z0-9_\-]{12,}\b", "[REDACTED_SECRET]", text)
    return text


# =============================================================================
# Default sinks and redactor
# =============================================================================


class LoggingAuditSink:
    """Audit sink using logging."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        self.logger.info("prompt_enrichment_audit=%s payload=%s", event_name, safe_json(payload))


class LoggingMetricsSink:
    """Metrics sink using logging."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("prompt_enrichment_metric_counter=%s value=%s tags=%s", name, value, dict(tags or {}))

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("prompt_enrichment_metric_observe=%s value=%s tags=%s", name, value, dict(tags or {}))


class RegexPiiRedactor:
    """Dependency-light PII redactor."""

    async def redact(self, text: str, context: PromptContext) -> str:
        await asyncio.sleep(0)
        return simple_redact_text(text)


# =============================================================================
# Template Registry
# =============================================================================


class PromptTemplateRegistry:
    """In-memory prompt template registry."""

    def __init__(self) -> None:
        self._templates: Dict[Tuple[str, str], PromptTemplate] = {}
        self._latest: Dict[str, str] = {}

    def register(self, template: PromptTemplate, *, latest: bool = True) -> None:
        template.validate()
        self._templates[(template.template_id, template.version)] = template
        if latest:
            self._latest[template.template_id] = template.version

    def get(self, template_id: str, version: Optional[str] = None) -> PromptTemplate:
        active_version = version or self._latest.get(template_id)
        if active_version is None:
            raise PromptTemplateError(f"Template not found: {template_id}")
        try:
            return self._templates[(template_id, active_version)]
        except KeyError as exc:
            raise PromptTemplateError(f"Template not found: {template_id}@{active_version}") from exc

    def list_templates(self) -> Sequence[PromptTemplate]:
        return tuple(self._templates.values())


# =============================================================================
# Prompt Enricher
# =============================================================================


class PromptEnricher:
    """Enterprise prompt enrichment engine."""

    DEFAULT_SAFETY_INSTRUCTIONS = (
        "Prioritize factual accuracy. If evidence is missing or uncertain, state the uncertainty clearly.",
        "Do not invent citations, numbers, dates, names, or source details.",
        "For high-stakes topics such as medical, legal, financial, security, or compliance, recommend expert review where appropriate.",
        "Follow the requested output format exactly.",
    )

    def __init__(
        self,
        *,
        config: Optional[PromptEnrichmentConfig] = None,
        policy_instructions: Sequence[PolicyInstruction] = (),
        pii_redactor: Optional[PiiRedactor] = None,
        audit_sink: Optional[AuditSink] = None,
        metrics_sink: Optional[MetricsSink] = None,
    ) -> None:
        self.config = config or PromptEnrichmentConfig()
        self.config.validate()
        self.policy_instructions = tuple(sorted(policy_instructions, key=lambda item: item.priority))
        self.pii_redactor = pii_redactor or RegexPiiRedactor()
        self.audit_sink = audit_sink or LoggingAuditSink()
        self.metrics_sink = metrics_sink or LoggingMetricsSink()

    async def enrich(self, payload: EnrichmentInput) -> EnrichmentResult:
        """Build enriched messages from a prompt/template/context payload."""

        started = time.perf_counter()
        timings: List[StageTiming] = []
        warnings: List[str] = []

        try:
            await self._stage(EnrichmentStage.VALIDATION, timings, lambda: self._validate(payload))

            rendered_user_prompt = await self._stage(
                EnrichmentStage.VARIABLE_RENDERING,
                timings,
                lambda: self._render_user_prompt(payload),
            )

            policy_messages = await self._stage(
                EnrichmentStage.POLICY_INJECTION,
                timings,
                lambda: self._build_policy_messages(payload.context),
            )

            context_text, selected_documents, context_warnings = await self._stage(
                EnrichmentStage.CONTEXT_PACKING,
                timings,
                lambda: self._pack_context(payload.context_documents, payload.context),
            )
            warnings.extend(context_warnings)

            examples_text = await self._stage(
                EnrichmentStage.EXAMPLE_INJECTION,
                timings,
                lambda: self._build_examples(payload.few_shot_examples),
            )

            output_contract_text = await self._stage(
                EnrichmentStage.OUTPUT_CONTRACT,
                timings,
                lambda: self._build_output_contract(payload.output_contract),
            )

            messages = await self._stage(
                EnrichmentStage.FINAL_ASSEMBLY,
                timings,
                lambda: self._assemble_messages(
                    payload=payload,
                    rendered_user_prompt=rendered_user_prompt,
                    policy_messages=policy_messages,
                    context_text=context_text,
                    examples_text=examples_text,
                    output_contract_text=output_contract_text,
                ),
            )

            messages = await self._maybe_redact_messages(messages, payload.context)
            prompt_text = self._messages_to_prompt_text(messages)
            if len(prompt_text) > self.config.max_prompt_chars:
                raise PromptBudgetError(
                    f"Enriched prompt exceeds max_prompt_chars: {len(prompt_text)} > {self.config.max_prompt_chars}"
                )

            manifest = self._build_manifest(payload, messages, selected_documents)
            result = EnrichmentResult(
                request_id=payload.context.request_id,
                messages=tuple(messages),
                prompt_text=prompt_text,
                manifest=manifest,
                warnings=tuple(warnings),
                stage_timings=tuple(timings),
                metadata={
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "prompt_chars": len(prompt_text),
                    "estimated_tokens": estimate_tokens(prompt_text),
                },
            )

            await self._record_success(payload, result)
            await self._audit("prompt_enrichment_completed", payload, result)
            return result

        except Exception as exc:
            await self._record_failure(payload, exc, (time.perf_counter() - started) * 1000)
            await self._audit_failure("prompt_enrichment_failed", payload, exc, timings)
            raise

    def enrich_sync(self, payload: EnrichmentInput) -> EnrichmentResult:
        """Synchronous convenience wrapper."""

        return asyncio.run(self.enrich(payload))

    def _validate(self, payload: EnrichmentInput) -> None:
        if not payload.user_prompt and not payload.base_messages and not payload.template:
            raise PromptValidationError("Provide user_prompt, base_messages, or template")
        if payload.template:
            payload.template.validate()
        if len(payload.context_documents) > self.config.max_context_documents * 5 and self.config.max_context_documents > 0:
            logger.warning("Large context document set received: %s", len(payload.context_documents))

    def _render_user_prompt(self, payload: EnrichmentInput) -> str:
        base = payload.template.content if payload.template else (payload.user_prompt or "")
        variables = dict(payload.template.optional_variables if payload.template else {})
        variables.update(dict(payload.variables or {}))

        if payload.template:
            required = set(payload.template.required_variables or extract_template_variables(payload.template.content))
            missing = sorted(name for name in required if name not in variables)
            if missing and self.config.fail_on_missing_variables:
                raise PromptTemplateError(f"Missing required template variables: {', '.join(missing)}")

        try:
            if self.config.fail_on_missing_variables:
                rendered = Template(base).substitute(**variables)
            else:
                rendered = Template(base).safe_substitute(**variables)
        except KeyError as exc:
            raise PromptTemplateError(f"Missing template variable: {exc}") from exc
        except ValueError as exc:
            raise PromptTemplateError(f"Invalid template syntax: {exc}") from exc

        return rendered.strip()

    def _build_policy_messages(self, context: PromptContext) -> List[PromptMessage]:
        if not self.config.enable_policy_instructions:
            return []
        instructions: List[str] = []
        if self.config.enable_safety_instructions:
            instructions.extend(self.DEFAULT_SAFETY_INSTRUCTIONS)
        for policy in self.policy_instructions:
            if policy.matches(context):
                instructions.append(policy.content)
        if not instructions:
            return []
        return [
            PromptMessage(
                role=PromptRole.SYSTEM,
                content="\n".join(f"- {instruction}" for instruction in instructions),
                name="policy_instructions",
                metadata={"kind": "policy"},
            )
        ]

    def _pack_context(
        self,
        documents: Sequence[ContextDocument],
        context: PromptContext,
    ) -> Tuple[str, Sequence[ContextDocument], Sequence[str]]:
        warnings: List[str] = []
        if not documents or self.config.max_context_chars == 0 or self.config.max_context_documents == 0:
            return "", tuple(), tuple()

        ordered = self._order_context_documents(documents)
        selected: List[ContextDocument] = []
        used_chars = 0
        blocks: List[str] = []

        for index, doc in enumerate(ordered[: self.config.max_context_documents]):
            if doc.sensitivity == SensitivityLevel.RESTRICTED and context.sensitivity != SensitivityLevel.RESTRICTED:
                warnings.append(f"Context document skipped due to sensitivity: {doc.id}")
                continue
            remaining = self.config.max_context_chars - used_chars
            if remaining <= 0:
                break
            block = self._format_context_document(doc, index + 1)
            if len(block) > remaining:
                block = truncate_text(block, remaining)
                warnings.append("Context was truncated to fit the configured budget.")
            blocks.append(block)
            selected.append(doc)
            used_chars += len(block) + len(self.config.context_separator)

        if len(documents) > len(selected):
            warnings.append(f"Selected {len(selected)} of {len(documents)} available context documents.")

        context_text = self.config.context_separator.join(blocks).strip()
        return context_text, tuple(selected), tuple(warnings)

    def _order_context_documents(self, documents: Sequence[ContextDocument]) -> List[ContextDocument]:
        if self.config.context_packing_strategy == ContextPackingStrategy.ORIGINAL_ORDER:
            return list(documents)
        if self.config.context_packing_strategy == ContextPackingStrategy.RECENCY_DESC:
            return sorted(documents, key=lambda doc: doc.created_at or "", reverse=True)
        if self.config.context_packing_strategy == ContextPackingStrategy.DIVERSITY_FIRST:
            return self._diversity_order(documents)
        return sorted(documents, key=lambda doc: doc.score if doc.score is not None else 0.0, reverse=True)

    def _diversity_order(self, documents: Sequence[ContextDocument]) -> List[ContextDocument]:
        by_source: Dict[str, List[ContextDocument]] = {}
        for doc in sorted(documents, key=lambda item: item.score if item.score is not None else 0.0, reverse=True):
            by_source.setdefault(doc.source or "unknown", []).append(doc)
        result: List[ContextDocument] = []
        while any(by_source.values()):
            for source in list(by_source.keys()):
                if by_source[source]:
                    result.append(by_source[source].pop(0))
        return result

    def _format_context_document(self, doc: ContextDocument, index: int) -> str:
        citation = f"[C{index}]" if self.config.include_context_citations else f"Context {index}"
        header_parts = [citation]
        if doc.title:
            header_parts.append(f"Title: {doc.title}")
        if doc.source:
            header_parts.append(f"Source: {doc.source}")
        if doc.url:
            header_parts.append(f"URL: {doc.url}")
        if doc.score is not None:
            header_parts.append(f"Score: {doc.score:.3f}")
        header = " | ".join(header_parts)
        return f"{header}\n{doc.text.strip()}"

    def _build_examples(self, examples: Sequence[FewShotExample]) -> str:
        if not examples or self.config.max_examples == 0:
            return ""
        ordered = sorted(examples, key=lambda item: item.score if item.score is not None else 0.0, reverse=True)
        blocks = []
        for index, example in enumerate(ordered[: self.config.max_examples], start=1):
            label = f" ({example.label})" if example.label else ""
            blocks.append(
                f"Example {index}{label}:\n"
                f"Input:\n{example.input_text.strip()}\n"
                f"Output:\n{example.output_text.strip()}"
            )
        return "\n\n".join(blocks)

    def _build_output_contract(self, contract: Optional[OutputContract]) -> str:
        active = contract or OutputContract(format=self.config.default_output_format)
        instructions: List[str] = []

        if active.language:
            instructions.append(f"Respond in this language/locale: {active.language}.")
        if active.max_words:
            instructions.append(f"Keep the response within {active.max_words} words.")
        if active.require_citations:
            instructions.append("Cite the provided context using citation markers such as [C1], [C2] when making factual claims.")

        if active.format == OutputFormat.JSON:
            instructions.append("Return valid JSON only. Do not wrap the JSON in markdown fences.")
        elif active.format == OutputFormat.JSON_SCHEMA:
            instructions.append("Return valid JSON that conforms exactly to the provided JSON Schema.")
            if active.json_schema:
                instructions.append("JSON Schema:\n" + json.dumps(active.json_schema, ensure_ascii=False, indent=2))
        elif active.format == OutputFormat.MARKDOWN:
            instructions.append("Return well-structured Markdown.")
        elif active.format == OutputFormat.BULLETS:
            instructions.append("Return concise bullet points.")
        elif active.format == OutputFormat.TABLE:
            instructions.append("Return a Markdown table when suitable.")
        elif active.format == OutputFormat.CODE:
            instructions.append("Return production-ready code only where requested, with concise comments when useful.")

        if active.instructions:
            instructions.append(active.instructions.strip())

        return "\n".join(f"- {instruction}" for instruction in instructions).strip()

    def _assemble_messages(
        self,
        *,
        payload: EnrichmentInput,
        rendered_user_prompt: str,
        policy_messages: Sequence[PromptMessage],
        context_text: str,
        examples_text: str,
        output_contract_text: str,
    ) -> List[PromptMessage]:
        messages: List[PromptMessage] = []

        for instruction in payload.system_instructions:
            if instruction.strip():
                messages.append(
                    PromptMessage(
                        role=PromptRole.SYSTEM,
                        content=instruction.strip(),
                        name="system_instruction",
                    )
                )

        messages.extend(policy_messages)
        messages.extend(payload.base_messages)

        user_sections: List[str] = []
        if context_text:
            user_sections.append("# Context\n" + context_text)
        if examples_text:
            user_sections.append("# Examples\n" + examples_text)
        if output_contract_text:
            user_sections.append("# Output Contract\n" + output_contract_text)
        if rendered_user_prompt:
            user_sections.append("# User Task\n" + rendered_user_prompt)

        if user_sections:
            messages.append(
                PromptMessage(
                    role=PromptRole.USER,
                    content="\n\n".join(user_sections).strip(),
                    name="enriched_user_prompt",
                    metadata={"kind": "enriched"},
                )
            )

        return messages

    async def _maybe_redact_messages(self, messages: Sequence[PromptMessage], context: PromptContext) -> List[PromptMessage]:
        if not self.config.enable_pii_redaction:
            return list(messages)
        redacted: List[PromptMessage] = []
        for message in messages:
            redacted.append(
                PromptMessage(
                    role=message.role,
                    content=await self.pii_redactor.redact(message.content, context),
                    name=message.name,
                    metadata=message.metadata,
                )
            )
        return redacted

    def _messages_to_prompt_text(self, messages: Sequence[PromptMessage]) -> str:
        return "\n\n".join(f"[{message.role.value.upper()}]\n{message.content}" for message in messages)

    def _build_manifest(
        self,
        payload: EnrichmentInput,
        messages: Sequence[PromptMessage],
        selected_documents: Sequence[ContextDocument],
    ) -> PromptManifest:
        prompt_text = self._messages_to_prompt_text(messages)
        context_text = "\n".join(doc.text for doc in selected_documents)
        variables_text = safe_json(dict(payload.variables or {}))
        output_contract = payload.output_contract or OutputContract(format=self.config.default_output_format)
        return PromptManifest(
            manifest_id=str(uuid.uuid4()),
            request_id=payload.context.request_id,
            enricher_version=self.config.version,
            template_id=payload.template.template_id if payload.template else None,
            template_version=payload.template.version if payload.template else None,
            prompt_hash=stable_hash(prompt_text),
            context_hash=stable_hash(context_text),
            variables_hash=stable_hash(variables_text),
            context_document_ids=tuple(doc.id for doc in selected_documents),
            example_count=min(len(payload.few_shot_examples), self.config.max_examples),
            output_format=output_contract.format,
            created_at=utc_now_iso(),
            metadata={
                "tenant_id": payload.context.tenant_id,
                "application": payload.context.application,
                "domain": payload.context.domain,
                "sensitivity": payload.context.sensitivity.value,
            },
        )

    async def _stage(self, stage: EnrichmentStage, timings: List[StageTiming], func: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        try:
            result = func()
            if asyncio.iscoroutine(result):
                result = await result
            timings.append(StageTiming(stage=stage, latency_ms=(time.perf_counter() - started) * 1000, success=True))
            return result
        except Exception:
            timings.append(StageTiming(stage=stage, latency_ms=(time.perf_counter() - started) * 1000, success=False))
            raise

    async def _record_success(self, payload: EnrichmentInput, result: EnrichmentResult) -> None:
        if not self.config.metrics_enabled:
            return
        tags = self._metric_tags(payload.context)
        await self.metrics_sink.increment("ai.prompt_enrichment.success", 1, tags)
        await self.metrics_sink.observe("ai.prompt_enrichment.prompt_chars", len(result.prompt_text), tags)
        await self.metrics_sink.observe("ai.prompt_enrichment.estimated_tokens", estimate_tokens(result.prompt_text), tags)

    async def _record_failure(self, payload: EnrichmentInput, exc: BaseException, latency_ms: float) -> None:
        if not self.config.metrics_enabled:
            return
        tags = {**self._metric_tags(payload.context), "error_type": type(exc).__name__}
        await self.metrics_sink.increment("ai.prompt_enrichment.failure", 1, tags)
        await self.metrics_sink.observe("ai.prompt_enrichment.failure_latency_ms", latency_ms, tags)

    def _metric_tags(self, context: PromptContext) -> Mapping[str, str]:
        return {
            "tenant_id": context.tenant_id or "unknown",
            "application": context.application or "unknown",
            "domain": context.domain or "unknown",
            "sensitivity": context.sensitivity.value,
        }

    async def _audit(self, event_name: str, payload: EnrichmentInput, result: EnrichmentResult) -> None:
        if not self.config.audit_enabled:
            return
        data = {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": payload.context.request_id,
            "tenant_id": payload.context.tenant_id,
            "user_id": payload.context.user_id,
            "application": payload.context.application,
            "domain": payload.context.domain,
            "trace_id": payload.context.trace_id,
            "manifest": asdict(result.manifest),
            "prompt_chars": len(result.prompt_text),
            "estimated_tokens": estimate_tokens(result.prompt_text),
            "warnings": list(result.warnings),
            "stage_timings": [asdict(timing) for timing in result.stage_timings],
        }
        await self.audit_sink.emit(event_name, data)

    async def _audit_failure(
        self,
        event_name: str,
        payload: EnrichmentInput,
        exc: BaseException,
        timings: Sequence[StageTiming],
    ) -> None:
        if not self.config.audit_enabled:
            return
        data = {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": payload.context.request_id,
            "tenant_id": payload.context.tenant_id,
            "user_id": payload.context.user_id,
            "application": payload.context.application,
            "domain": payload.context.domain,
            "trace_id": payload.context.trace_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "stage_timings": [asdict(timing) for timing in timings],
        }
        await self.audit_sink.emit(event_name, data)


# =============================================================================
# Inference pipeline adapter
# =============================================================================


class PromptEnrichmentPreProcessor:
    """Adapter for inference_pipeline.PreProcessor.

    It enriches incoming inference requests by transforming their messages/prompt
    before model execution. The adapter avoids a hard dependency on
    inference_pipeline.py until runtime.
    """

    def __init__(
        self,
        enricher: PromptEnricher,
        *,
        template: Optional[PromptTemplate] = None,
        variables_provider: Optional[Callable[[Any], Mapping[str, Any]]] = None,
        context_provider: Optional[Callable[[Any], Sequence[ContextDocument]]] = None,
        output_contract_provider: Optional[Callable[[Any], OutputContract]] = None,
        system_instructions_provider: Optional[Callable[[Any], Sequence[str]]] = None,
    ) -> None:
        self.enricher = enricher
        self.template = template
        self.variables_provider = variables_provider
        self.context_provider = context_provider
        self.output_contract_provider = output_contract_provider
        self.system_instructions_provider = system_instructions_provider

    async def process(self, request: Any) -> Any:
        payload = self._payload_from_request(request)
        result = await self.enricher.enrich(payload)
        return self._request_from_result(request, result)

    def _payload_from_request(self, request: Any) -> EnrichmentInput:
        raw_context = getattr(request, "context", None)
        prompt_context = PromptContext(
            request_id=getattr(raw_context, "request_id", str(uuid.uuid4())),
            tenant_id=getattr(raw_context, "tenant_id", None),
            user_id=getattr(raw_context, "user_id", None),
            application=getattr(raw_context, "application", None),
            domain=getattr(raw_context, "domain", None),
            locale=getattr(raw_context, "locale", None),
            trace_id=getattr(raw_context, "trace_id", None),
            metadata=getattr(raw_context, "metadata", {}) or {},
        )

        base_messages = []
        for message in getattr(request, "messages", ()) or ():
            role_value = getattr(getattr(message, "role", None), "value", str(getattr(message, "role", "user")))
            base_messages.append(
                PromptMessage(
                    role=PromptRole(role_value),
                    content=getattr(message, "content", ""),
                    name=getattr(message, "name", None),
                    metadata=getattr(message, "metadata", {}) or {},
                )
            )

        variables = self.variables_provider(request) if self.variables_provider else {}
        context_docs = self.context_provider(request) if self.context_provider else ()
        output_contract = self.output_contract_provider(request) if self.output_contract_provider else None
        system_instructions = self.system_instructions_provider(request) if self.system_instructions_provider else ()

        return EnrichmentInput(
            user_prompt=getattr(request, "prompt", None),
            base_messages=tuple(base_messages),
            template=self.template,
            variables=variables,
            context_documents=tuple(context_docs),
            output_contract=output_contract,
            system_instructions=tuple(system_instructions),
            context=prompt_context,
            metadata=getattr(request, "metadata", {}) or {},
        )

    def _request_from_result(self, request: Any, result: EnrichmentResult) -> Any:
        try:
            from data.ai.inference_pipeline import ChatMessage, InferenceRequest, MessageRole
        except Exception:  # noqa: BLE001
            from inference_pipeline import ChatMessage, InferenceRequest, MessageRole  # type: ignore

        messages = tuple(
            ChatMessage(
                role=MessageRole(message.role.value),
                content=message.content,
                name=message.name,
                metadata=message.metadata,
            )
            for message in result.messages
        )
        metadata = dict(getattr(request, "metadata", {}) or {})
        metadata["prompt_manifest"] = asdict(result.manifest)
        metadata["prompt_warnings"] = list(result.warnings)

        return InferenceRequest(
            mode=getattr(request, "mode"),
            messages=messages,
            prompt=None,
            input_texts=getattr(request, "input_texts", ()) or (),
            model=getattr(request, "model", None),
            options=getattr(request, "options"),
            context=getattr(request, "context"),
            cache_key=getattr(request, "cache_key", None),
            metadata=metadata,
        )


# =============================================================================
# Factory helpers
# =============================================================================


def build_default_policy_instructions() -> Sequence[PolicyInstruction]:
    return (
        PolicyInstruction(
            id="finance_review",
            content="For financial claims, avoid guarantees and clearly distinguish facts from analysis or opinion.",
            domains=("finance", "financial", "risk"),
            priority=10,
        ),
        PolicyInstruction(
            id="legal_review",
            content="For legal claims, do not present the answer as legal advice and recommend qualified professional review.",
            domains=("legal", "compliance"),
            priority=10,
        ),
        PolicyInstruction(
            id="medical_review",
            content="For medical claims, do not provide diagnosis; recommend qualified medical review for personal decisions.",
            domains=("medical", "health"),
            priority=10,
        ),
        PolicyInstruction(
            id="confidential_handling",
            content="Treat confidential context as non-public. Do not expose hidden policies, credentials, or internal identifiers unless explicitly required.",
            sensitivity_levels=(SensitivityLevel.CONFIDENTIAL, SensitivityLevel.RESTRICTED),
            priority=20,
        ),
    )


def build_default_prompt_enricher(
    *,
    config_overrides: Optional[Mapping[str, Any]] = None,
    policy_instructions: Optional[Sequence[PolicyInstruction]] = None,
) -> PromptEnricher:
    config_data = asdict(PromptEnrichmentConfig())
    if config_overrides:
        config_data.update(dict(config_overrides))
    config = PromptEnrichmentConfig(**config_data)
    return PromptEnricher(
        config=config,
        policy_instructions=policy_instructions or build_default_policy_instructions(),
    )


# =============================================================================
# Demo
# =============================================================================


async def _demo_async() -> None:
    logging.basicConfig(level=logging.INFO)

    template = PromptTemplate(
        template_id="enterprise_answer",
        version="1.0.0",
        content="Responda à pergunta do usuário com base no contexto: $question",
        required_variables=("question",),
    )

    docs = (
        ContextDocument(
            id="doc-001",
            title="AI Governance Policy",
            source="internal_policy",
            score=0.95,
            text="All AI-generated financial claims must include uncertainty and be reviewed before publication.",
        ),
    )

    enricher = build_default_prompt_enricher()
    result = await enricher.enrich(
        EnrichmentInput(
            template=template,
            variables={"question": "Como devemos responder sobre projeções financeiras?"},
            context_documents=docs,
            output_contract=OutputContract(
                format=OutputFormat.MARKDOWN,
                require_citations=True,
                language="pt-BR",
            ),
            context=PromptContext(
                tenant_id="demo",
                application="ai-platform",
                domain="finance",
                sensitivity=SensitivityLevel.CONFIDENTIAL,
            ),
        )
    )

    print(result.to_json(indent=2))


if __name__ == "__main__":
    asyncio.run(_demo_async())

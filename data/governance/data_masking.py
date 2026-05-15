"""
data_masking.py
===============

Enterprise-grade data masking module for data governance platforms.

Core capabilities
-----------------
- Field-level and record-level data masking policies.
- Static and dynamic masking strategies.
- Irreversible masking: full redact, partial mask, SHA/HMAC hashing.
- Reversible tokenization through pluggable token vaults.
- Format-preserving masking for email, phone, CPF, credit card-like values.
- Classification-aware policy resolution.
- Deterministic and non-deterministic masking modes.
- Batch masking for dictionaries, records and pandas DataFrames.
- Audit trail with masking decisions, policy hits and safety metrics.
- Validation helpers to detect potential leakage after masking.

This module is vendor-neutral and dependency-light. pandas support is optional.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import enum
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Set, Tuple, Union, runtime_checkable

try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
Record = Mapping[str, Any]
MutableRecord = MutableMapping[str, Any]
MaskingFunction = Callable[[Any, "MaskingContext"], Any]


class DataMaskingError(Exception):
    """Base exception for data masking failures."""


class MaskingPolicyError(DataMaskingError):
    """Raised when masking policy configuration is invalid."""


class TokenVaultError(DataMaskingError):
    """Raised when tokenization or detokenization fails."""


class MaskingStrategy(str, enum.Enum):
    NONE = "none"
    FULL_REDACTION = "full_redaction"
    PARTIAL = "partial"
    EMAIL = "email"
    PHONE = "phone"
    CPF = "cpf"
    CREDIT_CARD = "credit_card"
    HASH_SHA256 = "hash_sha256"
    HMAC_SHA256 = "hmac_sha256"
    TOKENIZE = "tokenize"
    NULLIFY = "nullify"
    CONSTANT = "constant"
    DATE_SHIFT = "date_shift"
    NUMERIC_NOISE = "numeric_noise"
    CUSTOM = "custom"


class MaskingMode(str, enum.Enum):
    STATIC = "static"
    DYNAMIC = "dynamic"


class SensitivityLevel(str, enum.Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    HIGHLY_RESTRICTED = "highly_restricted"


class MaskingFailurePolicy(str, enum.Enum):
    FAIL = "fail"
    KEEP_ORIGINAL = "keep_original"
    FULL_REDACT = "full_redact"
    NULLIFY = "nullify"


class MatchMode(str, enum.Enum):
    EXACT = "exact"
    REGEX = "regex"
    CLASSIFICATION = "classification"
    TAG = "tag"


@dataclass(frozen=True)
class MaskingContext:
    field_name: str
    record_id: Optional[str] = None
    dataset_name: Optional[str] = None
    tenant_id: Optional[str] = None
    actor_id: Optional[str] = None
    purpose: Optional[str] = None
    environment: str = "prod"
    classification: Optional[str] = None
    sensitivity: Optional[SensitivityLevel] = None
    tags: Set[str] = field(default_factory=set)
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "field_name": self.field_name,
            "record_id": self.record_id,
            "dataset_name": self.dataset_name,
            "tenant_id": self.tenant_id,
            "actor_id": self.actor_id,
            "purpose": self.purpose,
            "environment": self.environment,
            "classification": self.classification,
            "sensitivity": self.sensitivity.value if self.sensitivity else None,
            "tags": sorted(self.tags),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class MaskingPolicy:
    policy_id: str
    name: str
    strategy: MaskingStrategy
    match_mode: MatchMode = MatchMode.EXACT
    fields: Tuple[str, ...] = field(default_factory=tuple)
    field_patterns: Tuple[str, ...] = field(default_factory=tuple)
    classifications: Tuple[str, ...] = field(default_factory=tuple)
    sensitivity_levels: Tuple[SensitivityLevel, ...] = field(default_factory=tuple)
    tags: Tuple[str, ...] = field(default_factory=tuple)
    enabled: bool = True
    priority: int = 100
    mode: MaskingMode = MaskingMode.STATIC
    constant_value: Any = "***MASKED***"
    mask_char: str = "*"
    reveal_first: int = 2
    reveal_last: int = 2
    deterministic: bool = True
    salt: str = ""
    hmac_secret: Optional[str] = None
    date_shift_days: int = 0
    numeric_noise_ratio: float = 0.05
    custom_function: Optional[MaskingFunction] = None
    reversible: bool = False
    metadata: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("policy_id is required")
        if self.strategy == MaskingStrategy.HMAC_SHA256 and not self.hmac_secret:
            raise ValueError("hmac_secret is required for HMAC_SHA256")
        if self.strategy == MaskingStrategy.CUSTOM and self.custom_function is None:
            raise ValueError("custom_function is required for CUSTOM strategy")
        if len(self.mask_char) != 1:
            raise ValueError("mask_char must be a single character")

    def matches(self, context: MaskingContext) -> bool:
        if not self.enabled:
            return False
        field = context.field_name
        field_norm = normalize_field_name(field)

        if self.match_mode == MatchMode.EXACT:
            return field_norm in {normalize_field_name(item) for item in self.fields}
        if self.match_mode == MatchMode.REGEX:
            return any(re.search(pattern, field, re.IGNORECASE) for pattern in self.field_patterns)
        if self.match_mode == MatchMode.CLASSIFICATION:
            classification_match = bool(context.classification and context.classification in self.classifications)
            sensitivity_match = bool(context.sensitivity and context.sensitivity in self.sensitivity_levels)
            return classification_match or sensitivity_match
        if self.match_mode == MatchMode.TAG:
            return bool(set(self.tags).intersection(context.tags))
        return False

    def to_dict(self) -> JsonDict:
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "strategy": self.strategy.value,
            "match_mode": self.match_mode.value,
            "fields": list(self.fields),
            "field_patterns": list(self.field_patterns),
            "classifications": list(self.classifications),
            "sensitivity_levels": [level.value for level in self.sensitivity_levels],
            "tags": list(self.tags),
            "enabled": self.enabled,
            "priority": self.priority,
            "mode": self.mode.value,
            "reveal_first": self.reveal_first,
            "reveal_last": self.reveal_last,
            "deterministic": self.deterministic,
            "reversible": self.reversible,
            "metadata": dict(self.metadata),
        }


@dataclass
class MaskingDecision:
    field_name: str
    policy_id: Optional[str]
    strategy: MaskingStrategy
    masked: bool
    reversible: bool = False
    reason: str = ""
    original_hash: Optional[str] = None
    masked_hash: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> JsonDict:
        return {
            "field_name": self.field_name,
            "policy_id": self.policy_id,
            "strategy": self.strategy.value,
            "masked": self.masked,
            "reversible": self.reversible,
            "reason": self.reason,
            "original_hash": self.original_hash,
            "masked_hash": self.masked_hash,
            "error": self.error,
        }


@dataclass
class MaskingResult:
    output: Any
    decisions: List[MaskingDecision]
    audit_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def duration_ms(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at) * 1000, 3)

    def finish(self) -> None:
        self.finished_at = time.time()

    def to_dict(self) -> JsonDict:
        return {
            "audit_id": self.audit_id,
            "duration_ms": self.duration_ms,
            "decisions": [decision.to_dict() for decision in self.decisions],
            "masked_fields": [decision.field_name for decision in self.decisions if decision.masked],
        }


@dataclass
class MaskingReport:
    total_records: int = 0
    total_fields_seen: int = 0
    total_fields_masked: int = 0
    policy_hits: Counter = field(default_factory=Counter)
    strategy_counts: Counter = field(default_factory=Counter)
    errors: List[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def duration_ms(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at) * 1000, 3)

    def finish(self) -> None:
        self.finished_at = time.time()

    def to_dict(self) -> JsonDict:
        return {
            "total_records": self.total_records,
            "total_fields_seen": self.total_fields_seen,
            "total_fields_masked": self.total_fields_masked,
            "policy_hits": dict(self.policy_hits),
            "strategy_counts": dict(self.strategy_counts),
            "errors": list(self.errors),
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class DataMaskingConfig:
    failure_policy: MaskingFailurePolicy = MaskingFailurePolicy.FAIL
    default_mask_char: str = "*"
    hash_salt: str = ""
    enable_audit: bool = True
    validate_output: bool = True
    max_leakage_examples: int = 10
    metadata: JsonDict = field(default_factory=dict)


@runtime_checkable
class TokenVault(Protocol):
    def tokenize(self, value: Any, context: MaskingContext) -> str:
        ...

    def detokenize(self, token: str, context: MaskingContext) -> Any:
        ...


class InMemoryTokenVault(TokenVault):
    """Simple in-memory token vault for development and tests.

    For production, replace with an HSM-backed vault, KMS-backed token service,
    format-preserving encryption service or enterprise tokenization platform.
    """

    def __init__(self, prefix: str = "tok") -> None:
        self.prefix = prefix
        self._token_to_value: Dict[str, Any] = {}
        self._value_hash_to_token: Dict[str, str] = {}

    def tokenize(self, value: Any, context: MaskingContext) -> str:
        value_hash = stable_hash({"tenant": context.tenant_id, "field": context.field_name, "value": value})
        if value_hash in self._value_hash_to_token:
            return self._value_hash_to_token[value_hash]
        token = f"{self.prefix}_{secrets.token_urlsafe(24)}"
        self._token_to_value[token] = value
        self._value_hash_to_token[value_hash] = token
        return token

    def detokenize(self, token: str, context: MaskingContext) -> Any:
        if token not in self._token_to_value:
            raise TokenVaultError("Token not found")
        return self._token_to_value[token]

    def stats(self) -> JsonDict:
        return {"tokens": len(self._token_to_value)}


@runtime_checkable
class MaskingAuditSink(Protocol):
    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        ...


class LoggingMaskingAuditSink:
    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self.log.info("data_masking_audit", extra={"event_type": event_type, "payload": dict(payload)})


class MaskingPolicyRegistry:
    """Registry and resolver for data masking policies."""

    def __init__(self, policies: Optional[Iterable[MaskingPolicy]] = None) -> None:
        self._policies: Dict[str, MaskingPolicy] = {}
        for policy in default_masking_policies():
            self.register(policy, replace=True)
        if policies:
            for policy in policies:
                self.register(policy, replace=True)

    def register(self, policy: MaskingPolicy, *, replace: bool = False) -> None:
        if policy.policy_id in self._policies and not replace:
            raise MaskingPolicyError(f"Policy already registered: {policy.policy_id}")
        self._policies[policy.policy_id] = policy

    def remove(self, policy_id: str) -> bool:
        return self._policies.pop(policy_id, None) is not None

    def resolve(self, context: MaskingContext) -> Optional[MaskingPolicy]:
        matches = [policy for policy in self._policies.values() if policy.matches(context)]
        if not matches:
            return None
        return sorted(matches, key=lambda policy: policy.priority)[0]

    def list_policies(self, *, enabled_only: bool = True) -> List[MaskingPolicy]:
        policies = list(self._policies.values())
        if enabled_only:
            policies = [policy for policy in policies if policy.enabled]
        return sorted(policies, key=lambda policy: policy.priority)

    def to_dict(self) -> JsonDict:
        return {policy_id: policy.to_dict() for policy_id, policy in self._policies.items()}


class DataMaskingEngine:
    """Main enterprise data masking engine."""

    def __init__(
        self,
        *,
        config: Optional[DataMaskingConfig] = None,
        registry: Optional[MaskingPolicyRegistry] = None,
        token_vault: Optional[TokenVault] = None,
        audit_sink: Optional[MaskingAuditSink] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or DataMaskingConfig()
        self.registry = registry or MaskingPolicyRegistry()
        self.token_vault = token_vault or InMemoryTokenVault()
        self.audit = audit_sink or LoggingMaskingAuditSink()
        self.log = log or logger

    def mask_value(self, value: Any, context: MaskingContext, policy: Optional[MaskingPolicy] = None) -> Tuple[Any, MaskingDecision]:
        policy = policy or self.registry.resolve(context)
        original_hash = stable_hash(value)

        if policy is None or policy.strategy == MaskingStrategy.NONE:
            return value, MaskingDecision(
                field_name=context.field_name,
                policy_id=None,
                strategy=MaskingStrategy.NONE,
                masked=False,
                reason="no_policy_matched",
                original_hash=original_hash,
                masked_hash=original_hash,
            )

        try:
            masked = self._apply_strategy(value, context, policy)
            masked_hash = stable_hash(masked)
            decision = MaskingDecision(
                field_name=context.field_name,
                policy_id=policy.policy_id,
                strategy=policy.strategy,
                masked=masked != value,
                reversible=policy.reversible or policy.strategy == MaskingStrategy.TOKENIZE,
                reason="policy_applied",
                original_hash=original_hash,
                masked_hash=masked_hash,
            )
            return masked, decision
        except Exception as exc:
            return self._handle_failure(value, context, policy, exc, original_hash)

    def mask_record(
        self,
        record: Record,
        *,
        dataset_name: Optional[str] = None,
        record_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        purpose: Optional[str] = None,
        classifications: Optional[Mapping[str, str]] = None,
        sensitivities: Optional[Mapping[str, SensitivityLevel]] = None,
        tags_by_field: Optional[Mapping[str, Set[str]]] = None,
    ) -> MaskingResult:
        output: JsonDict = {}
        decisions: List[MaskingDecision] = []
        result = MaskingResult(output=output, decisions=decisions)

        for field_name, value in record.items():
            context = MaskingContext(
                field_name=str(field_name),
                record_id=record_id or infer_record_id(record),
                dataset_name=dataset_name,
                tenant_id=tenant_id,
                actor_id=actor_id,
                purpose=purpose,
                classification=(classifications or {}).get(str(field_name)),
                sensitivity=(sensitivities or {}).get(str(field_name)),
                tags=(tags_by_field or {}).get(str(field_name), set()),
            )
            if isinstance(value, Mapping):
                nested = self.mask_record(
                    value,
                    dataset_name=dataset_name,
                    record_id=context.record_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    purpose=purpose,
                    classifications=classifications,
                    sensitivities=sensitivities,
                    tags_by_field=tags_by_field,
                )
                output[field_name] = nested.output
                decisions.extend(nested.decisions)
            else:
                output[field_name], decision = self.mask_value(value, context)
                decisions.append(decision)

        result.finish()
        if self.config.enable_audit:
            self.audit.emit("record_masked", result.to_dict())
        return result

    def mask_records(self, records: Iterable[Record], **kwargs: Any) -> Tuple[List[JsonDict], MaskingReport]:
        outputs: List[JsonDict] = []
        report = MaskingReport()

        for index, record in enumerate(records):
            report.total_records += 1
            try:
                result = self.mask_record(record, record_id=kwargs.pop("record_id", None) or str(index), **kwargs)
                outputs.append(result.output)
                report.total_fields_seen += len(result.decisions)
                for decision in result.decisions:
                    if decision.masked:
                        report.total_fields_masked += 1
                    if decision.policy_id:
                        report.policy_hits[decision.policy_id] += 1
                    report.strategy_counts[decision.strategy.value] += 1
                    if decision.error:
                        report.errors.append(decision.error)
            except Exception as exc:
                report.errors.append(str(exc))
                if self.config.failure_policy == MaskingFailurePolicy.FAIL:
                    raise
        report.finish()
        if self.config.enable_audit:
            self.audit.emit("records_masked", report.to_dict())
        return outputs, report

    def mask_dataframe(
        self,
        dataframe: Any,
        *,
        dataset_name: Optional[str] = None,
        tenant_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        purpose: Optional[str] = None,
        classifications: Optional[Mapping[str, str]] = None,
        sensitivities: Optional[Mapping[str, SensitivityLevel]] = None,
        tags_by_field: Optional[Mapping[str, Set[str]]] = None,
    ) -> Tuple[Any, MaskingReport]:
        if pd is None:
            raise DataMaskingError("pandas is required for mask_dataframe")
        if not isinstance(dataframe, pd.DataFrame):
            raise TypeError("dataframe must be a pandas DataFrame")

        output = dataframe.copy()
        report = MaskingReport(total_records=int(len(output)))

        for column in output.columns:
            context = MaskingContext(
                field_name=str(column),
                dataset_name=dataset_name,
                tenant_id=tenant_id,
                actor_id=actor_id,
                purpose=purpose,
                classification=(classifications or {}).get(str(column)),
                sensitivity=(sensitivities or {}).get(str(column)),
                tags=(tags_by_field or {}).get(str(column), set()),
            )
            policy = self.registry.resolve(context)
            report.total_fields_seen += int(len(output))
            if policy is None:
                report.strategy_counts[MaskingStrategy.NONE.value] += int(len(output))
                continue
            masked_values = []
            for value in output[column].tolist():
                masked, decision = self.mask_value(value, context, policy)
                masked_values.append(masked)
                if decision.masked:
                    report.total_fields_masked += 1
                report.policy_hits[decision.policy_id or "none"] += 1
                report.strategy_counts[decision.strategy.value] += 1
                if decision.error:
                    report.errors.append(decision.error)
            output[column] = masked_values

        report.finish()
        output.attrs["data_masking_report"] = report.to_dict()
        if self.config.enable_audit:
            self.audit.emit("dataframe_masked", report.to_dict())
        return output, report

    def detokenize_value(self, token: str, context: MaskingContext) -> Any:
        value = self.token_vault.detokenize(token, context)
        if self.config.enable_audit:
            self.audit.emit("value_detokenized", {"field_name": context.field_name, "token_hash": stable_hash(token), "context": context.to_dict()})
        return value

    def validate_masking(self, original: Record, masked: Record, sensitive_fields: Sequence[str]) -> JsonDict:
        leaks: List[JsonDict] = []
        for field in sensitive_fields:
            original_value = get_path(original, field)
            masked_value = get_path(masked, field)
            if original_value is None:
                continue
            if original_value == masked_value:
                leaks.append({"field": field, "type": "unchanged_sensitive_value", "example_hash": stable_hash(original_value)})
            elif isinstance(original_value, str) and isinstance(masked_value, str) and original_value in masked_value:
                leaks.append({"field": field, "type": "original_contained_in_masked", "example_hash": stable_hash(original_value)})
        return {
            "valid": not leaks,
            "leak_count": len(leaks),
            "leaks": leaks[: self.config.max_leakage_examples],
            "truncated": len(leaks) > self.config.max_leakage_examples,
        }

    def _apply_strategy(self, value: Any, context: MaskingContext, policy: MaskingPolicy) -> Any:
        if value is None:
            return None
        if policy.strategy == MaskingStrategy.FULL_REDACTION:
            return policy.constant_value
        if policy.strategy == MaskingStrategy.PARTIAL:
            return partial_mask(value, policy.reveal_first, policy.reveal_last, policy.mask_char)
        if policy.strategy == MaskingStrategy.EMAIL:
            return mask_email(str(value), policy.mask_char)
        if policy.strategy == MaskingStrategy.PHONE:
            return mask_phone(str(value), policy.mask_char)
        if policy.strategy == MaskingStrategy.CPF:
            return mask_cpf(str(value), policy.mask_char)
        if policy.strategy == MaskingStrategy.CREDIT_CARD:
            return mask_credit_card(str(value), policy.mask_char)
        if policy.strategy == MaskingStrategy.HASH_SHA256:
            return sha256_hash(value, policy.salt or self.config.hash_salt)
        if policy.strategy == MaskingStrategy.HMAC_SHA256:
            return hmac_sha256(value, policy.hmac_secret or "")
        if policy.strategy == MaskingStrategy.TOKENIZE:
            return self.token_vault.tokenize(value, context)
        if policy.strategy == MaskingStrategy.NULLIFY:
            return None
        if policy.strategy == MaskingStrategy.CONSTANT:
            return policy.constant_value
        if policy.strategy == MaskingStrategy.DATE_SHIFT:
            return shift_date(value, policy.date_shift_days or deterministic_shift_days(value, policy.salt or self.config.hash_salt))
        if policy.strategy == MaskingStrategy.NUMERIC_NOISE:
            return add_numeric_noise(value, policy.numeric_noise_ratio, policy.deterministic, policy.salt or self.config.hash_salt)
        if policy.strategy == MaskingStrategy.CUSTOM and policy.custom_function:
            return policy.custom_function(value, context)
        return value

    def _handle_failure(
        self,
        value: Any,
        context: MaskingContext,
        policy: MaskingPolicy,
        exc: Exception,
        original_hash: str,
    ) -> Tuple[Any, MaskingDecision]:
        error = str(exc)
        if self.config.failure_policy == MaskingFailurePolicy.FAIL:
            raise DataMaskingError(f"Masking failed for field {context.field_name}: {error}") from exc
        if self.config.failure_policy == MaskingFailurePolicy.KEEP_ORIGINAL:
            fallback = value
            strategy = policy.strategy
        elif self.config.failure_policy == MaskingFailurePolicy.NULLIFY:
            fallback = None
            strategy = MaskingStrategy.NULLIFY
        else:
            fallback = "***MASKED***"
            strategy = MaskingStrategy.FULL_REDACTION
        return fallback, MaskingDecision(
            field_name=context.field_name,
            policy_id=policy.policy_id,
            strategy=strategy,
            masked=fallback != value,
            reversible=False,
            reason="masking_failure_fallback",
            original_hash=original_hash,
            masked_hash=stable_hash(fallback),
            error=error,
        )

    def describe_policies(self) -> JsonDict:
        return self.registry.to_dict()


# -----------------------------------------------------------------------------
# Default policies
# -----------------------------------------------------------------------------


def default_masking_policies() -> List[MaskingPolicy]:
    return [
        MaskingPolicy(
            policy_id="mask_credentials",
            name="Mask credentials and secrets",
            strategy=MaskingStrategy.FULL_REDACTION,
            match_mode=MatchMode.REGEX,
            field_patterns=(r"password", r"passwd", r"secret", r"token", r"api[_-]?key", r"private[_-]?key", r"authorization"),
            priority=1,
            constant_value="***SECRET***",
            metadata={"category": "credential"},
        ),
        MaskingPolicy(
            policy_id="mask_email",
            name="Mask email fields",
            strategy=MaskingStrategy.EMAIL,
            match_mode=MatchMode.REGEX,
            field_patterns=(r"(^|_)(email|e_mail|mail|email_address)(_|$)",),
            priority=10,
            metadata={"category": "pii"},
        ),
        MaskingPolicy(
            policy_id="mask_phone",
            name="Mask phone fields",
            strategy=MaskingStrategy.PHONE,
            match_mode=MatchMode.REGEX,
            field_patterns=(r"phone", r"telefone", r"mobile", r"celular", r"whatsapp"),
            priority=20,
            metadata={"category": "pii"},
        ),
        MaskingPolicy(
            policy_id="mask_cpf",
            name="Mask CPF/document fields",
            strategy=MaskingStrategy.CPF,
            match_mode=MatchMode.REGEX,
            field_patterns=(r"(^|_)(cpf|documento|tax_id)(_|$)",),
            priority=30,
            metadata={"category": "pii"},
        ),
        MaskingPolicy(
            policy_id="mask_credit_card",
            name="Mask payment card fields",
            strategy=MaskingStrategy.CREDIT_CARD,
            match_mode=MatchMode.REGEX,
            field_patterns=(r"credit[_-]?card", r"card[_-]?number", r"payment[_-]?card", r"pan"),
            priority=40,
            metadata={"category": "pci"},
        ),
        MaskingPolicy(
            policy_id="hash_restricted_classification",
            name="Hash restricted classified fields",
            strategy=MaskingStrategy.HASH_SHA256,
            match_mode=MatchMode.CLASSIFICATION,
            classifications=("restricted", "highly_restricted", "financial_sensitive", "phi", "pci"),
            sensitivity_levels=(SensitivityLevel.RESTRICTED, SensitivityLevel.HIGHLY_RESTRICTED),
            priority=50,
            metadata={"classification_based": True},
        ),
        MaskingPolicy(
            policy_id="partial_confidential_classification",
            name="Partially mask confidential classified fields",
            strategy=MaskingStrategy.PARTIAL,
            match_mode=MatchMode.CLASSIFICATION,
            classifications=("pii", "personal", "confidential"),
            sensitivity_levels=(SensitivityLevel.CONFIDENTIAL,),
            priority=60,
            metadata={"classification_based": True},
        ),
    ]


# -----------------------------------------------------------------------------
# Strategy helpers
# -----------------------------------------------------------------------------


def partial_mask(value: Any, reveal_first: int = 2, reveal_last: int = 2, mask_char: str = "*") -> str:
    text = str(value)
    if not text:
        return text
    if len(text) <= reveal_first + reveal_last:
        return mask_char * len(text)
    return text[:reveal_first] + mask_char * (len(text) - reveal_first - reveal_last) + text[-reveal_last:]


def mask_email(value: str, mask_char: str = "*") -> str:
    if "@" not in value:
        return partial_mask(value, 2, 2, mask_char)
    local, domain = value.split("@", 1)
    return partial_mask(local, 1, 1, mask_char) + "@" + domain


def mask_phone(value: str, mask_char: str = "*") -> str:
    digits = re.sub(r"\D+", "", value)
    if len(digits) <= 4:
        return mask_char * len(value)
    masked_digits = mask_char * max(0, len(digits) - 4) + digits[-4:]
    output = []
    digit_index = 0
    for char in value:
        if char.isdigit():
            output.append(masked_digits[digit_index])
            digit_index += 1
        else:
            output.append(char)
    return "".join(output)


def mask_cpf(value: str, mask_char: str = "*") -> str:
    digits = re.sub(r"\D+", "", value)
    if len(digits) != 11:
        return partial_mask(value, 0, 2, mask_char)
    masked = mask_char * 9 + digits[-2:]
    return f"{masked[:3]}.{masked[3:6]}.{masked[6:9]}-{masked[9:]}"


def mask_credit_card(value: str, mask_char: str = "*") -> str:
    digits = re.sub(r"\D+", "", value)
    if len(digits) < 8:
        return partial_mask(value, 0, 2, mask_char)
    masked_digits = digits[:6] + mask_char * max(0, len(digits) - 10) + digits[-4:]
    output = []
    digit_index = 0
    for char in value:
        if char.isdigit():
            output.append(masked_digits[digit_index])
            digit_index += 1
        else:
            output.append(char)
    return "".join(output)


def sha256_hash(value: Any, salt: str = "") -> str:
    return hashlib.sha256((salt + str(value)).encode("utf-8")).hexdigest()


def hmac_sha256(value: Any, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), str(value).encode("utf-8"), hashlib.sha256).hexdigest()


def shift_date(value: Any, days: int) -> Any:
    parsed = parse_datetime(value)
    if parsed is None:
        return value
    shifted = parsed + dt.timedelta(days=days)
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return shifted.date()
    if isinstance(value, str):
        return shifted.isoformat()
    return shifted


def deterministic_shift_days(value: Any, salt: str = "", max_abs_days: int = 30) -> int:
    digest = hashlib.sha256((salt + str(value)).encode("utf-8")).hexdigest()
    number = int(digest[:8], 16)
    return (number % (2 * max_abs_days + 1)) - max_abs_days


def add_numeric_noise(value: Any, ratio: float, deterministic: bool = True, salt: str = "") -> Any:
    try:
        number = float(value)
    except Exception:
        return value
    if deterministic:
        digest = hashlib.sha256((salt + str(value)).encode("utf-8")).hexdigest()
        fraction = (int(digest[:8], 16) / 0xFFFFFFFF) - 0.5
    else:
        fraction = secrets.randbelow(1_000_000) / 1_000_000 - 0.5
    noisy = number + (number * ratio * fraction * 2)
    if isinstance(value, int):
        return int(round(noisy))
    return noisy


def parse_datetime(value: Any) -> Optional[dt.datetime]:
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min)
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
                try:
                    return dt.datetime.strptime(value, fmt)
                except ValueError:
                    continue
    return None


# -----------------------------------------------------------------------------
# Utility helpers
# -----------------------------------------------------------------------------


def normalize_field_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")


def stable_hash(value: Any) -> str:
    raw = json.dumps(to_json_safe(value), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def to_json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return to_json_safe(dataclasses.asdict(value))
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_safe(v) for v in value]
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return value


def get_path(data: Mapping[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return default
    return current


def infer_record_id(record: Record) -> Optional[str]:
    for key in ("id", "record_id", "uuid", "key"):
        if key in record and record[key] is not None:
            return str(record[key])
    return None


# -----------------------------------------------------------------------------
# Example factory
# -----------------------------------------------------------------------------


def build_default_data_masking_engine(*, hmac_secret: Optional[str] = None) -> DataMaskingEngine:
    registry = MaskingPolicyRegistry()
    if hmac_secret:
        registry.register(
            MaskingPolicy(
                policy_id="hmac_customer_identifiers",
                name="HMAC customer identifiers",
                strategy=MaskingStrategy.HMAC_SHA256,
                match_mode=MatchMode.REGEX,
                field_patterns=(r"customer[_-]?id", r"client[_-]?id"),
                hmac_secret=hmac_secret,
                priority=15,
                metadata={"category": "identifier"},
            ),
            replace=True,
        )
    return DataMaskingEngine(registry=registry)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    engine = build_default_data_masking_engine(hmac_secret="dev-secret")
    record = {
        "id": "1",
        "customer_email": "ana.silva@example.com",
        "phone": "+55 51 98120-6626",
        "cpf": "123.456.789-09",
        "password": "super-secret",
        "sales": 199.90,
    }
    result = engine.mask_record(record, dataset_name="customers", tenant_id="tenant-a", actor_id="governance-api")
    print(json.dumps(result.output, indent=2, ensure_ascii=False, default=str))
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))

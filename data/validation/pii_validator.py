"""
data/validation/pii_validator.py

Enterprise-grade PII validator and privacy risk analyzer.

Este módulo fornece uma camada avançada para identificação, validação, classificação,
mascaramento e auditoria de dados pessoais/sensíveis em datasets corporativos.

Principais capacidades:
- Detecção de PII por nome de coluna, regex, heurística e validadores específicos.
- Suporte a CPF, CNPJ, e-mail, telefone, IP, cartão de crédito, CEP, UUID, token e documentos genéricos.
- Classificação de risco por severidade e categoria de privacidade.
- Políticas configuráveis para LGPD, GDPR, PCI-like e regras internas.
- Evidências limitadas e seguras, evitando vazamento de PII no relatório.
- Mascaramento, hashing e tokenização determinística opcional.
- Relatórios estruturados para auditoria, observabilidade e governança.
- Integração com pandas DataFrame e datasets baseados em lista de dicionários.
- Hooks para audit sink e metrics sink.

Exemplo básico:
    validator = PIIValidator()
    result = validator.validate(
        dataset=df,
        policy=PIIPolicy.strict_lgpd(),
        context=PIIValidationContext(dataset_name="customers")
    )

    if not result.is_compliant:
        raise PIIComplianceError(result.summary())
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import math
import re
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Pattern,
    Protocol,
    Sequence,
    Set,
    Tuple,
    Union,
)

try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore


logger = logging.getLogger(__name__)


JsonDict = Dict[str, Any]
DataLike = Union["pd.DataFrame", Sequence[Mapping[str, Any]]]
CustomDetector = Callable[[Any, str, Mapping[str, Any]], bool]


class PIISeverity(str, Enum):
    """Nível de risco associado à exposição de PII."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PIIStatus(str, Enum):
    """Status consolidado da validação."""

    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"
    WARNING = "WARNING"
    ERROR = "ERROR"


class PIICategory(str, Enum):
    """Categorias corporativas de dados pessoais e sensíveis."""

    DIRECT_IDENTIFIER = "DIRECT_IDENTIFIER"
    INDIRECT_IDENTIFIER = "INDIRECT_IDENTIFIER"
    CONTACT = "CONTACT"
    FINANCIAL = "FINANCIAL"
    NETWORK = "NETWORK"
    LOCATION = "LOCATION"
    AUTHENTICATION = "AUTHENTICATION"
    GOVERNMENT_ID = "GOVERNMENT_ID"
    HEALTH = "HEALTH"
    BIOMETRIC = "BIOMETRIC"
    DEMOGRAPHIC = "DEMOGRAPHIC"
    FREE_TEXT = "FREE_TEXT"
    UNKNOWN = "UNKNOWN"


class PIIType(str, Enum):
    """Tipos conhecidos de PII detectáveis pelo módulo."""

    CPF = "CPF"
    CNPJ = "CNPJ"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    CREDIT_CARD = "CREDIT_CARD"
    IP_ADDRESS = "IP_ADDRESS"
    CEP = "CEP"
    PERSON_NAME = "PERSON_NAME"
    ADDRESS = "ADDRESS"
    BIRTH_DATE = "BIRTH_DATE"
    UUID = "UUID"
    TOKEN = "TOKEN"
    PASSWORD = "PASSWORD"
    API_KEY = "API_KEY"
    GENERIC_DOCUMENT = "GENERIC_DOCUMENT"
    FREE_TEXT_PII = "FREE_TEXT_PII"
    UNKNOWN = "UNKNOWN"


class PIIAction(str, Enum):
    """Ação recomendada ou obrigatória para PII detectada."""

    ALLOW = "ALLOW"
    WARN = "WARN"
    MASK = "MASK"
    HASH = "HASH"
    TOKENIZE = "TOKENIZE"
    BLOCK = "BLOCK"
    QUARANTINE = "QUARANTINE"


class MaskingStrategy(str, Enum):
    """Estratégias de proteção para valores sensíveis."""

    FULL = "FULL"
    PARTIAL = "PARTIAL"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    LAST4 = "LAST4"
    HASH_SHA256 = "HASH_SHA256"
    HMAC_SHA256 = "HMAC_SHA256"
    REDACT = "REDACT"


class PIIComplianceError(Exception):
    """Erro para falhas bloqueantes de compliance de PII."""


class PIIConfigurationError(Exception):
    """Erro de configuração inválida em política, detector ou dataset."""


class AuditSink(Protocol):
    """Contrato para envio de auditoria."""

    def emit(self, event: Mapping[str, Any]) -> None:
        """Emite evento de auditoria."""


class MetricsSink(Protocol):
    """Contrato para publicação de métricas."""

    def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Incrementa contador."""

    def gauge(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Publica métrica pontual."""

    def timing(self, name: str, value_ms: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Publica métrica de latência."""


@dataclass(frozen=True)
class PIIValidationContext:
    """Contexto operacional de execução da validação de privacidade."""

    dataset_name: str
    pipeline_name: Optional[str] = None
    environment: str = "production"
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    source_system: Optional[str] = None
    data_owner: Optional[str] = None
    legal_basis: Optional[str] = None
    correlation_id: Optional[str] = None
    execution_ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def tags(self) -> Dict[str, str]:
        return {
            "dataset": self.dataset_name,
            "pipeline": self.pipeline_name or "unknown",
            "environment": self.environment,
            "tenant": self.tenant_id or "default",
            "source": self.source_system or "unknown",
            "owner": self.data_owner or "unknown",
        }


@dataclass(frozen=True)
class PIIDetectorRule:
    """Regra de detecção de PII."""

    pii_type: PIIType
    category: PIICategory
    severity: PIISeverity
    action: PIIAction
    column_name_patterns: Tuple[Pattern[str], ...] = field(default_factory=tuple)
    value_patterns: Tuple[Pattern[str], ...] = field(default_factory=tuple)
    validator: Optional[CustomDetector] = None
    description: Optional[str] = None
    enabled: bool = True
    confidence: float = 0.85

    def matches_column(self, column: str) -> bool:
        return any(pattern.search(column) for pattern in self.column_name_patterns)

    def matches_value(self, value: Any, column: str, row: Mapping[str, Any]) -> bool:
        if _is_null(value):
            return False
        text = str(value).strip()
        if not text:
            return False
        if any(pattern.search(text) for pattern in self.value_patterns):
            return True
        if self.validator is not None:
            try:
                return bool(self.validator(value, column, row))
            except Exception:
                logger.debug("Custom PII detector failed", exc_info=True)
                return False
        return False


@dataclass(frozen=True)
class PIIPolicy:
    """Política corporativa de privacidade e tratamento de PII."""

    name: str
    description: str = ""
    detectors: Tuple[PIIDetectorRule, ...] = field(default_factory=tuple)
    blocked_types: Set[PIIType] = field(default_factory=set)
    warning_types: Set[PIIType] = field(default_factory=set)
    allowed_types: Set[PIIType] = field(default_factory=set)
    require_legal_basis: bool = False
    allow_free_text_scan: bool = True
    sample_limit_per_column: int = 10_000
    max_findings: int = 500
    min_column_detection_ratio: float = 0.02
    fail_on_high: bool = True
    fail_on_critical: bool = True
    include_safe_evidence: bool = True
    masking_secret: Optional[bytes] = None

    @staticmethod
    def default() -> "PIIPolicy":
        return PIIPolicy(
            name="default_pii_policy",
            description="Política padrão para identificação e alerta de PII.",
            detectors=tuple(default_detectors()),
            blocked_types={PIIType.CREDIT_CARD, PIIType.PASSWORD, PIIType.API_KEY, PIIType.TOKEN},
            warning_types={PIIType.EMAIL, PIIType.PHONE, PIIType.CPF, PIIType.CNPJ, PIIType.IP_ADDRESS},
            require_legal_basis=False,
            fail_on_high=True,
            fail_on_critical=True,
        )

    @staticmethod
    def strict_lgpd(masking_secret: Optional[bytes] = None) -> "PIIPolicy":
        return PIIPolicy(
            name="strict_lgpd",
            description="Política rígida inspirada em boas práticas de LGPD para dados pessoais e sensíveis.",
            detectors=tuple(default_detectors()),
            blocked_types={
                PIIType.CPF,
                PIIType.CNPJ,
                PIIType.CREDIT_CARD,
                PIIType.PASSWORD,
                PIIType.API_KEY,
                PIIType.TOKEN,
                PIIType.GENERIC_DOCUMENT,
            },
            warning_types={PIIType.EMAIL, PIIType.PHONE, PIIType.IP_ADDRESS, PIIType.CEP, PIIType.PERSON_NAME},
            require_legal_basis=True,
            fail_on_high=True,
            fail_on_critical=True,
            masking_secret=masking_secret,
        )

    @staticmethod
    def analytics_safe(masking_secret: Optional[bytes] = None) -> "PIIPolicy":
        return PIIPolicy(
            name="analytics_safe",
            description="Política para datasets analíticos, recomendando mascaramento/hash de identificadores.",
            detectors=tuple(default_detectors()),
            blocked_types={PIIType.CREDIT_CARD, PIIType.PASSWORD, PIIType.API_KEY, PIIType.TOKEN},
            warning_types={PIIType.CPF, PIIType.CNPJ, PIIType.EMAIL, PIIType.PHONE, PIIType.IP_ADDRESS},
            require_legal_basis=False,
            fail_on_high=False,
            fail_on_critical=True,
            masking_secret=masking_secret,
        )


@dataclass(frozen=True)
class PIIFinding:
    """Ocorrência segura de PII detectada."""

    finding_id: str
    pii_type: PIIType
    category: PIICategory
    severity: PIISeverity
    action: PIIAction
    column: str
    row_index: Optional[Any]
    confidence: float
    masked_value: Optional[str]
    message: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "finding_id": self.finding_id,
            "pii_type": self.pii_type.value,
            "category": self.category.value,
            "severity": self.severity.value,
            "action": self.action.value,
            "column": self.column,
            "row_index": self.row_index,
            "confidence": self.confidence,
            "masked_value": self.masked_value,
            "message": self.message,
            "evidence": _safe_json_value(dict(self.evidence)),
        }


@dataclass(frozen=True)
class PIIColumnProfile:
    """Perfil de PII por coluna."""

    column: str
    total_values: int
    non_null_values: int
    detected_values: int
    detection_ratio: float
    pii_types: Tuple[PIIType, ...]
    highest_severity: Optional[PIISeverity]
    recommended_action: PIIAction

    def to_dict(self) -> JsonDict:
        return {
            "column": self.column,
            "total_values": self.total_values,
            "non_null_values": self.non_null_values,
            "detected_values": self.detected_values,
            "detection_ratio": self.detection_ratio,
            "pii_types": [v.value for v in self.pii_types],
            "highest_severity": self.highest_severity.value if self.highest_severity else None,
            "recommended_action": self.recommended_action.value,
        }


@dataclass(frozen=True)
class PIIValidationResult:
    """Resultado consolidado da validação PII."""

    context: PIIValidationContext
    policy_name: str
    status: PIIStatus
    findings: Tuple[PIIFinding, ...]
    column_profiles: Tuple[PIIColumnProfile, ...]
    started_at: datetime
    finished_at: datetime
    dataset_rows: int
    dataset_columns: Tuple[str, ...]
    risk_score: float
    legal_basis_missing: bool = False
    error_message: Optional[str] = None

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.finished_at - self.started_at).total_seconds() * 1000.0)

    @property
    def is_compliant(self) -> bool:
        return self.status == PIIStatus.COMPLIANT

    def summary(self) -> str:
        counts = Counter(f.pii_type.value for f in self.findings)
        severity_counts = Counter(f.severity.value for f in self.findings)
        return (
            f"PIIValidationResult(dataset={self.context.dataset_name}, "
            f"policy={self.policy_name}, status={self.status.value}, "
            f"rows={self.dataset_rows}, columns={len(self.dataset_columns)}, "
            f"findings={len(self.findings)}, risk_score={self.risk_score:.2f}, "
            f"critical={severity_counts.get('CRITICAL', 0)}, high={severity_counts.get('HIGH', 0)}, "
            f"types={dict(counts)}, duration_ms={self.duration_ms:.2f})"
        )

    def to_dict(self) -> JsonDict:
        return {
            "context": {
                "dataset_name": self.context.dataset_name,
                "pipeline_name": self.context.pipeline_name,
                "environment": self.context.environment,
                "run_id": self.context.run_id,
                "tenant_id": self.context.tenant_id,
                "source_system": self.context.source_system,
                "data_owner": self.context.data_owner,
                "legal_basis": self.context.legal_basis,
                "correlation_id": self.context.correlation_id,
                "execution_ts": self.context.execution_ts.isoformat(),
                "metadata": _safe_json_value(dict(self.context.metadata)),
            },
            "policy_name": self.policy_name,
            "status": self.status.value,
            "dataset_rows": self.dataset_rows,
            "dataset_columns": list(self.dataset_columns),
            "risk_score": self.risk_score,
            "legal_basis_missing": self.legal_basis_missing,
            "findings": [f.to_dict() for f in self.findings],
            "column_profiles": [p.to_dict() for p in self.column_profiles],
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
            "error_message": self.error_message,
            "summary": self.summary(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)

    def raise_for_non_compliance(self) -> None:
        if not self.is_compliant:
            raise PIIComplianceError(self.summary())


class PIIValidator:
    """Motor enterprise para validação e governança de PII."""

    def __init__(
        self,
        *,
        audit_sink: Optional[AuditSink] = None,
        metrics_sink: Optional[MetricsSink] = None,
        default_policy: Optional[PIIPolicy] = None,
    ) -> None:
        self.audit_sink = audit_sink
        self.metrics_sink = metrics_sink
        self.default_policy = default_policy or PIIPolicy.default()

    def validate(
        self,
        dataset: DataLike,
        context: PIIValidationContext,
        policy: Optional[PIIPolicy] = None,
    ) -> PIIValidationResult:
        """Executa validação de PII contra o dataset informado."""
        started = datetime.now(timezone.utc)
        start_perf = time.perf_counter()
        policy = policy or self.default_policy

        try:
            df = self._to_dataframe(dataset)
            self._validate_inputs(df, context, policy)

            self._emit_audit(
                "pii_validation_started",
                context,
                {
                    "policy": policy.name,
                    "rows": len(df),
                    "columns": list(map(str, df.columns)),
                },
            )

            findings = self._scan_dataframe(df, context, policy)
            profiles = self._build_column_profiles(df, findings, policy)
            risk_score = self._calculate_risk_score(findings, profiles, policy)
            legal_basis_missing = bool(policy.require_legal_basis and not context.legal_basis)
            status = self._compute_status(findings, policy, legal_basis_missing)

            result = PIIValidationResult(
                context=context,
                policy_name=policy.name,
                status=status,
                findings=tuple(findings[: policy.max_findings]),
                column_profiles=tuple(profiles),
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                dataset_rows=len(df),
                dataset_columns=tuple(map(str, df.columns)),
                risk_score=risk_score,
                legal_basis_missing=legal_basis_missing,
            )

            elapsed_ms = (time.perf_counter() - start_perf) * 1000.0
            self._publish_metrics(result, elapsed_ms)
            self._emit_audit(
                "pii_validation_finished",
                context,
                {
                    "policy": policy.name,
                    "status": result.status.value,
                    "risk_score": result.risk_score,
                    "finding_count": len(result.findings),
                    "summary": result.summary(),
                },
            )
            return result

        except Exception as exc:
            logger.exception("PII validation failed")
            result = PIIValidationResult(
                context=context,
                policy_name=policy.name,
                status=PIIStatus.ERROR,
                findings=tuple(),
                column_profiles=tuple(),
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                dataset_rows=0,
                dataset_columns=tuple(),
                risk_score=100.0,
                error_message=str(exc),
            )
            self._emit_audit("pii_validation_error", context, {"policy": policy.name, "error": str(exc)})
            return result

    def mask_dataframe(
        self,
        dataset: DataLike,
        result: PIIValidationResult,
        strategy_by_type: Optional[Mapping[PIIType, MaskingStrategy]] = None,
        secret: Optional[bytes] = None,
    ) -> "pd.DataFrame":
        """Retorna uma cópia do DataFrame com colunas/valores PII protegidos."""
        df = self._to_dataframe(dataset).copy(deep=True)
        strategy_by_type = strategy_by_type or {}

        pii_columns: Dict[str, PIIType] = {}
        for finding in result.findings:
            pii_columns.setdefault(finding.column, finding.pii_type)

        for column, pii_type in pii_columns.items():
            if column not in df.columns:
                continue
            strategy = strategy_by_type.get(pii_type, self._default_masking_strategy(pii_type))
            df[column] = df[column].map(lambda value: mask_value(value, strategy=strategy, secret=secret))

        return df

    def classify_columns(
        self,
        dataset: DataLike,
        policy: Optional[PIIPolicy] = None,
    ) -> Dict[str, Set[PIIType]]:
        """Classifica colunas por tipos prováveis de PII sem exigir contexto completo."""
        policy = policy or self.default_policy
        df = self._to_dataframe(dataset)
        classification: Dict[str, Set[PIIType]] = defaultdict(set)

        for column in map(str, df.columns):
            for detector in policy.detectors:
                if detector.enabled and detector.matches_column(column.lower()):
                    classification[column].add(detector.pii_type)

        return dict(classification)

    def _scan_dataframe(
        self,
        df: "pd.DataFrame",
        context: PIIValidationContext,
        policy: PIIPolicy,
    ) -> List[PIIFinding]:
        findings: List[PIIFinding] = []
        column_name_hits = self._column_name_hits(df, policy)

        for column in df.columns:
            column_str = str(column)
            sample = df[column].head(policy.sample_limit_per_column)
            column_rules = column_name_hits.get(column_str, [])

            for idx, value in sample.items():
                if _is_null(value):
                    continue

                row = self._row_as_mapping(df, idx)
                matched_rules = self._match_value_rules(value, column_str, row, policy)
                if column_rules:
                    matched_rules = self._merge_rules(matched_rules, column_rules)

                for detector in matched_rules:
                    if not detector.enabled:
                        continue
                    action = self._resolve_action(detector, policy)
                    findings.append(
                        PIIFinding(
                            finding_id=str(uuid.uuid4()),
                            pii_type=detector.pii_type,
                            category=detector.category,
                            severity=detector.severity,
                            action=action,
                            column=column_str,
                            row_index=idx,
                            confidence=self._confidence(detector, value, column_str, column_rules),
                            masked_value=mask_value(value, strategy=self._default_masking_strategy(detector.pii_type), secret=policy.masking_secret),
                            message=f"Potential {detector.pii_type.value} detected in column '{column_str}'",
                            evidence={
                                "column_name_match": detector in column_rules,
                                "value_length": len(str(value)),
                                "safe_sample_hash": _hash_text(str(value)),
                            }
                            if policy.include_safe_evidence
                            else {},
                        )
                    )
                    if len(findings) >= policy.max_findings:
                        return findings

        if policy.allow_free_text_scan:
            findings.extend(self._scan_free_text_columns(df, policy, current_count=len(findings)))

        return findings[: policy.max_findings]

    def _scan_free_text_columns(self, df: "pd.DataFrame", policy: PIIPolicy, current_count: int) -> List[PIIFinding]:
        findings: List[PIIFinding] = []
        free_text_patterns = [
            (PIIType.EMAIL, re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)),
            (PIIType.CPF, re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")),
            (PIIType.CNPJ, re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")),
            (PIIType.CREDIT_CARD, re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
        ]
        detectors_by_type = {d.pii_type: d for d in policy.detectors if d.enabled}

        object_columns = [col for col in df.columns if str(df[col].dtype) in {"object", "string"}]
        for column in object_columns:
            for idx, value in df[column].head(policy.sample_limit_per_column).items():
                if _is_null(value):
                    continue
                text = str(value)
                if len(text) < 20:
                    continue
                for pii_type, pattern in free_text_patterns:
                    match = pattern.search(text)
                    if not match:
                        continue
                    detector = detectors_by_type.get(pii_type)
                    if detector is None:
                        continue
                    raw = match.group(0)
                    findings.append(
                        PIIFinding(
                            finding_id=str(uuid.uuid4()),
                            pii_type=pii_type,
                            category=PIICategory.FREE_TEXT,
                            severity=max_severity(detector.severity, PIISeverity.HIGH),
                            action=self._resolve_action(detector, policy),
                            column=str(column),
                            row_index=idx,
                            confidence=min(0.99, detector.confidence + 0.05),
                            masked_value=mask_value(raw, strategy=self._default_masking_strategy(pii_type), secret=policy.masking_secret),
                            message=f"Potential embedded {pii_type.value} detected inside free text",
                            evidence={"safe_sample_hash": _hash_text(raw), "text_length": len(text)} if policy.include_safe_evidence else {},
                        )
                    )
                    if current_count + len(findings) >= policy.max_findings:
                        return findings
        return findings

    def _column_name_hits(self, df: "pd.DataFrame", policy: PIIPolicy) -> Dict[str, List[PIIDetectorRule]]:
        hits: Dict[str, List[PIIDetectorRule]] = defaultdict(list)
        for column in map(str, df.columns):
            normalized = _normalize_column_name(column)
            for detector in policy.detectors:
                if detector.enabled and detector.matches_column(normalized):
                    hits[column].append(detector)
        return dict(hits)

    def _match_value_rules(
        self,
        value: Any,
        column: str,
        row: Mapping[str, Any],
        policy: PIIPolicy,
    ) -> List[PIIDetectorRule]:
        rules: List[PIIDetectorRule] = []
        for detector in policy.detectors:
            if detector.enabled and detector.matches_value(value, column, row):
                rules.append(detector)
        return rules

    def _merge_rules(self, left: Sequence[PIIDetectorRule], right: Sequence[PIIDetectorRule]) -> List[PIIDetectorRule]:
        seen: Set[PIIType] = set()
        merged: List[PIIDetectorRule] = []
        for rule in list(left) + list(right):
            if rule.pii_type not in seen:
                seen.add(rule.pii_type)
                merged.append(rule)
        return merged

    def _row_as_mapping(self, df: "pd.DataFrame", idx: Any) -> Mapping[str, Any]:
        try:
            row = df.loc[idx]
            return {str(k): row[k] for k in df.columns}
        except Exception:
            return {}

    def _build_column_profiles(
        self,
        df: "pd.DataFrame",
        findings: Sequence[PIIFinding],
        policy: PIIPolicy,
    ) -> List[PIIColumnProfile]:
        by_column: Dict[str, List[PIIFinding]] = defaultdict(list)
        for finding in findings:
            by_column[finding.column].append(finding)

        profiles: List[PIIColumnProfile] = []
        for column in map(str, df.columns):
            column_findings = by_column.get(column, [])
            non_null = int(df[column].notna().sum()) if column in df.columns else 0
            detected = len(column_findings)
            ratio = detected / max(non_null, 1)
            pii_types = tuple(sorted({f.pii_type for f in column_findings}, key=lambda x: x.value))
            severity = _highest_severity([f.severity for f in column_findings])
            action = _highest_action([f.action for f in column_findings]) if column_findings else PIIAction.ALLOW

            if detected > 0 or ratio >= policy.min_column_detection_ratio:
                profiles.append(
                    PIIColumnProfile(
                        column=column,
                        total_values=len(df),
                        non_null_values=non_null,
                        detected_values=detected,
                        detection_ratio=ratio,
                        pii_types=pii_types,
                        highest_severity=severity,
                        recommended_action=action,
                    )
                )
        return profiles

    def _calculate_risk_score(
        self,
        findings: Sequence[PIIFinding],
        profiles: Sequence[PIIColumnProfile],
        policy: PIIPolicy,
    ) -> float:
        if not findings:
            return 0.0
        severity_weight = {
            PIISeverity.LOW: 5.0,
            PIISeverity.MEDIUM: 15.0,
            PIISeverity.HIGH: 35.0,
            PIISeverity.CRITICAL: 60.0,
        }
        action_weight = {
            PIIAction.ALLOW: 0.0,
            PIIAction.WARN: 5.0,
            PIIAction.MASK: 10.0,
            PIIAction.HASH: 10.0,
            PIIAction.TOKENIZE: 15.0,
            PIIAction.BLOCK: 30.0,
            PIIAction.QUARANTINE: 40.0,
        }
        score = 0.0
        for finding in findings:
            score += severity_weight[finding.severity]
            score += action_weight[finding.action]
            if finding.pii_type in policy.blocked_types:
                score += 15.0
        score += min(25.0, len(profiles) * 2.5)
        return min(100.0, score / max(1.0, math.sqrt(len(findings))))

    def _compute_status(
        self,
        findings: Sequence[PIIFinding],
        policy: PIIPolicy,
        legal_basis_missing: bool,
    ) -> PIIStatus:
        if legal_basis_missing and findings:
            return PIIStatus.NON_COMPLIANT
        if any(f.action in {PIIAction.BLOCK, PIIAction.QUARANTINE} for f in findings):
            return PIIStatus.NON_COMPLIANT
        if policy.fail_on_critical and any(f.severity == PIISeverity.CRITICAL for f in findings):
            return PIIStatus.NON_COMPLIANT
        if policy.fail_on_high and any(f.severity == PIISeverity.HIGH for f in findings):
            return PIIStatus.NON_COMPLIANT
        if findings:
            return PIIStatus.WARNING
        return PIIStatus.COMPLIANT

    def _resolve_action(self, detector: PIIDetectorRule, policy: PIIPolicy) -> PIIAction:
        if detector.pii_type in policy.allowed_types:
            return PIIAction.ALLOW
        if detector.pii_type in policy.blocked_types:
            return PIIAction.BLOCK
        if detector.pii_type in policy.warning_types:
            return PIIAction.WARN if detector.action == PIIAction.ALLOW else detector.action
        return detector.action

    def _confidence(
        self,
        detector: PIIDetectorRule,
        value: Any,
        column: str,
        column_rules: Sequence[PIIDetectorRule],
    ) -> float:
        score = detector.confidence
        if detector in column_rules:
            score += 0.08
        text = str(value)
        if len(text) > 0:
            score += 0.02
        return round(min(0.99, max(0.01, score)), 4)

    def _default_masking_strategy(self, pii_type: PIIType) -> MaskingStrategy:
        if pii_type == PIIType.EMAIL:
            return MaskingStrategy.EMAIL
        if pii_type == PIIType.PHONE:
            return MaskingStrategy.PHONE
        if pii_type in {PIIType.CREDIT_CARD, PIIType.CPF, PIIType.CNPJ, PIIType.GENERIC_DOCUMENT}:
            return MaskingStrategy.LAST4
        if pii_type in {PIIType.PASSWORD, PIIType.API_KEY, PIIType.TOKEN}:
            return MaskingStrategy.REDACT
        return MaskingStrategy.PARTIAL

    def _to_dataframe(self, dataset: DataLike) -> "pd.DataFrame":
        if pd is None:
            raise ImportError("pandas is required for PIIValidator. Install with: pip install pandas")
        if dataset is None:
            raise PIIConfigurationError("dataset cannot be None")
        if isinstance(dataset, pd.DataFrame):
            return dataset.copy(deep=False)
        if isinstance(dataset, Sequence):
            return pd.DataFrame(list(dataset))
        raise PIIConfigurationError(f"Unsupported dataset type: {type(dataset)!r}")

    def _validate_inputs(self, df: "pd.DataFrame", context: PIIValidationContext, policy: PIIPolicy) -> None:
        if not context.dataset_name:
            raise PIIConfigurationError("context.dataset_name is required")
        if not policy.name:
            raise PIIConfigurationError("policy.name is required")
        if policy.sample_limit_per_column <= 0:
            raise PIIConfigurationError("policy.sample_limit_per_column must be greater than zero")
        if policy.max_findings <= 0:
            raise PIIConfigurationError("policy.max_findings must be greater than zero")
        if df.empty:
            logger.info("PII validation received empty dataset: %s", context.dataset_name)

    def _publish_metrics(self, result: PIIValidationResult, elapsed_ms: float) -> None:
        if not self.metrics_sink:
            return
        tags = {**result.context.tags(), "policy": result.policy_name, "status": result.status.value}
        self.metrics_sink.increment("pii.validation.executed", tags=tags)
        self.metrics_sink.gauge("pii.validation.findings", len(result.findings), tags=tags)
        self.metrics_sink.gauge("pii.validation.risk_score", result.risk_score, tags=tags)
        self.metrics_sink.gauge("pii.validation.columns_profiled", len(result.column_profiles), tags=tags)
        self.metrics_sink.timing("pii.validation.duration_ms", elapsed_ms, tags=tags)

        for finding in result.findings:
            f_tags = {
                **tags,
                "pii_type": finding.pii_type.value,
                "severity": finding.severity.value,
                "action": finding.action.value,
            }
            self.metrics_sink.increment("pii.finding.detected", tags=f_tags)

    def _emit_audit(self, event_name: str, context: PIIValidationContext, payload: Mapping[str, Any]) -> None:
        if not self.audit_sink:
            return
        event = {
            "event_id": str(uuid.uuid4()),
            "event_name": event_name,
            "emitted_at": datetime.now(timezone.utc).isoformat(),
            "context": {
                "dataset_name": context.dataset_name,
                "pipeline_name": context.pipeline_name,
                "environment": context.environment,
                "run_id": context.run_id,
                "tenant_id": context.tenant_id,
                "source_system": context.source_system,
                "data_owner": context.data_owner,
                "legal_basis": context.legal_basis,
                "correlation_id": context.correlation_id,
            },
            "payload": _safe_json_value(dict(payload)),
        }
        self.audit_sink.emit(event)


class InMemoryAuditSink:
    """Audit sink simples para testes e desenvolvimento local."""

    def __init__(self) -> None:
        self.events: List[Mapping[str, Any]] = []

    def emit(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))


class InMemoryMetricsSink:
    """Metrics sink simples para testes e desenvolvimento local."""

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

    def timing_summary(self, name: str, tags: Optional[Mapping[str, str]] = None) -> Mapping[str, float]:
        values = self.timings.get(self._key(name, tags), [])
        if not values:
            return {"count": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0}
        return {
            "count": float(len(values)),
            "min": min(values),
            "max": max(values),
            "avg": statistics.mean(values),
        }

    def _key(self, name: str, tags: Optional[Mapping[str, str]]) -> str:
        if not tags:
            return name
        tag_text = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}|{tag_text}"


def default_detectors() -> List[PIIDetectorRule]:
    """Retorna detectores padrão para PII comum em ambientes Brasil/global."""
    return [
        PIIDetectorRule(
            pii_type=PIIType.CPF,
            category=PIICategory.GOVERNMENT_ID,
            severity=PIISeverity.HIGH,
            action=PIIAction.MASK,
            column_name_patterns=_compile_many(r"\bcpf\b", r"documento_cpf", r"taxpayer"),
            value_patterns=(re.compile(r"^\d{3}\.?\d{3}\.?\d{3}-?\d{2}$"),),
            validator=lambda value, column, row: is_valid_cpf(str(value)),
            confidence=0.94,
        ),
        PIIDetectorRule(
            pii_type=PIIType.CNPJ,
            category=PIICategory.GOVERNMENT_ID,
            severity=PIISeverity.HIGH,
            action=PIIAction.MASK,
            column_name_patterns=_compile_many(r"\bcnpj\b", r"company_document", r"documento_empresa"),
            value_patterns=(re.compile(r"^\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}$"),),
            validator=lambda value, column, row: is_valid_cnpj(str(value)),
            confidence=0.94,
        ),
        PIIDetectorRule(
            pii_type=PIIType.EMAIL,
            category=PIICategory.CONTACT,
            severity=PIISeverity.MEDIUM,
            action=PIIAction.MASK,
            column_name_patterns=_compile_many(r"email", r"e_mail", r"mail_address"),
            value_patterns=(re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE),),
            confidence=0.96,
        ),
        PIIDetectorRule(
            pii_type=PIIType.PHONE,
            category=PIICategory.CONTACT,
            severity=PIISeverity.MEDIUM,
            action=PIIAction.MASK,
            column_name_patterns=_compile_many(r"phone", r"telefone", r"celular", r"whatsapp", r"mobile"),
            value_patterns=(re.compile(r"^\+?\d{0,3}\s?\(?\d{2,3}\)?\s?\d{4,5}[-\s]?\d{4}$"),),
            confidence=0.84,
        ),
        PIIDetectorRule(
            pii_type=PIIType.CREDIT_CARD,
            category=PIICategory.FINANCIAL,
            severity=PIISeverity.CRITICAL,
            action=PIIAction.BLOCK,
            column_name_patterns=_compile_many(r"credit_card", r"card_number", r"cartao", r"pan"),
            value_patterns=(re.compile(r"^(?:\d[ -]*?){13,19}$"),),
            validator=lambda value, column, row: is_valid_luhn(str(value)),
            confidence=0.97,
        ),
        PIIDetectorRule(
            pii_type=PIIType.IP_ADDRESS,
            category=PIICategory.NETWORK,
            severity=PIISeverity.LOW,
            action=PIIAction.WARN,
            column_name_patterns=_compile_many(r"ip", r"ip_address", r"client_ip", r"remote_addr"),
            validator=lambda value, column, row: is_valid_ip(str(value)),
            confidence=0.88,
        ),
        PIIDetectorRule(
            pii_type=PIIType.CEP,
            category=PIICategory.LOCATION,
            severity=PIISeverity.LOW,
            action=PIIAction.WARN,
            column_name_patterns=_compile_many(r"\bcep\b", r"postal", r"zipcode", r"zip_code"),
            value_patterns=(re.compile(r"^\d{5}-?\d{3}$"),),
            confidence=0.82,
        ),
        PIIDetectorRule(
            pii_type=PIIType.PERSON_NAME,
            category=PIICategory.DIRECT_IDENTIFIER,
            severity=PIISeverity.MEDIUM,
            action=PIIAction.WARN,
            column_name_patterns=_compile_many(r"nome", r"name", r"full_name", r"customer_name", r"client_name"),
            confidence=0.72,
        ),
        PIIDetectorRule(
            pii_type=PIIType.ADDRESS,
            category=PIICategory.LOCATION,
            severity=PIISeverity.MEDIUM,
            action=PIIAction.WARN,
            column_name_patterns=_compile_many(r"address", r"endereco", r"logradouro", r"street", r"bairro"),
            confidence=0.78,
        ),
        PIIDetectorRule(
            pii_type=PIIType.BIRTH_DATE,
            category=PIICategory.DEMOGRAPHIC,
            severity=PIISeverity.MEDIUM,
            action=PIIAction.WARN,
            column_name_patterns=_compile_many(r"birth", r"nascimento", r"date_of_birth", r"dob"),
            confidence=0.76,
        ),
        PIIDetectorRule(
            pii_type=PIIType.UUID,
            category=PIICategory.INDIRECT_IDENTIFIER,
            severity=PIISeverity.LOW,
            action=PIIAction.WARN,
            column_name_patterns=_compile_many(r"uuid", r"guid", r"external_id"),
            value_patterns=(re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.IGNORECASE),),
            confidence=0.83,
        ),
        PIIDetectorRule(
            pii_type=PIIType.PASSWORD,
            category=PIICategory.AUTHENTICATION,
            severity=PIISeverity.CRITICAL,
            action=PIIAction.BLOCK,
            column_name_patterns=_compile_many(r"password", r"passwd", r"senha", r"pwd"),
            confidence=0.99,
        ),
        PIIDetectorRule(
            pii_type=PIIType.API_KEY,
            category=PIICategory.AUTHENTICATION,
            severity=PIISeverity.CRITICAL,
            action=PIIAction.BLOCK,
            column_name_patterns=_compile_many(r"api_key", r"apikey", r"secret_key", r"access_key", r"private_key"),
            value_patterns=(re.compile(r"(?i)^(sk|pk|api|key|secret)[-_]?[a-z0-9]{16,}$"),),
            confidence=0.94,
        ),
        PIIDetectorRule(
            pii_type=PIIType.TOKEN,
            category=PIICategory.AUTHENTICATION,
            severity=PIISeverity.CRITICAL,
            action=PIIAction.BLOCK,
            column_name_patterns=_compile_many(r"token", r"bearer", r"refresh_token", r"access_token", r"jwt"),
            value_patterns=(re.compile(r"^[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}$"),),
            confidence=0.92,
        ),
        PIIDetectorRule(
            pii_type=PIIType.GENERIC_DOCUMENT,
            category=PIICategory.GOVERNMENT_ID,
            severity=PIISeverity.HIGH,
            action=PIIAction.MASK,
            column_name_patterns=_compile_many(r"document", r"documento", r"rg", r"passport", r"passaporte"),
            confidence=0.70,
        ),
    ]


def mask_value(value: Any, *, strategy: MaskingStrategy = MaskingStrategy.PARTIAL, secret: Optional[bytes] = None) -> Optional[str]:
    """Mascara/protege um valor sensível sem expor o original."""
    if _is_null(value):
        return None
    text = str(value)
    if strategy == MaskingStrategy.FULL:
        return "*" * min(len(text), 12)
    if strategy == MaskingStrategy.REDACT:
        return "[REDACTED]"
    if strategy == MaskingStrategy.EMAIL:
        return _mask_email(text)
    if strategy == MaskingStrategy.PHONE:
        return _mask_phone(text)
    if strategy == MaskingStrategy.LAST4:
        digits = re.sub(r"\D", "", text)
        last4 = digits[-4:] if len(digits) >= 4 else text[-4:]
        return f"***-***-{last4}"
    if strategy == MaskingStrategy.HASH_SHA256:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    if strategy == MaskingStrategy.HMAC_SHA256:
        if not secret:
            raise PIIConfigurationError("secret is required for HMAC_SHA256 masking")
        return hmac.new(secret, text.encode("utf-8"), hashlib.sha256).hexdigest()
    return _mask_partial(text)


def is_valid_cpf(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    numbers = [int(d) for d in digits]
    for digit_idx in [9, 10]:
        factor = digit_idx + 1
        total = sum(numbers[i] * (factor - i) for i in range(digit_idx))
        check = (total * 10) % 11
        check = 0 if check == 10 else check
        if check != numbers[digit_idx]:
            return False
    return True


def is_valid_cnpj(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    numbers = [int(d) for d in digits]
    weights_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    weights_2 = [6] + weights_1
    total = sum(numbers[i] * weights_1[i] for i in range(12))
    check_1 = 0 if total % 11 < 2 else 11 - (total % 11)
    total = sum(numbers[i] * weights_2[i] for i in range(13))
    check_2 = 0 if total % 11 < 2 else 11 - (total % 11)
    return numbers[12] == check_1 and numbers[13] == check_2


def is_valid_luhn(value: str) -> bool:
    digits = [int(d) for d in re.sub(r"\D", "", value)]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for idx, digit in enumerate(digits):
        if idx % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
        return True
    except Exception:
        return False


def max_severity(left: PIISeverity, right: PIISeverity) -> PIISeverity:
    order = [PIISeverity.LOW, PIISeverity.MEDIUM, PIISeverity.HIGH, PIISeverity.CRITICAL]
    return left if order.index(left) >= order.index(right) else right


def _highest_severity(values: Sequence[PIISeverity]) -> Optional[PIISeverity]:
    if not values:
        return None
    order = {PIISeverity.LOW: 1, PIISeverity.MEDIUM: 2, PIISeverity.HIGH: 3, PIISeverity.CRITICAL: 4}
    return max(values, key=lambda value: order[value])


def _highest_action(values: Sequence[PIIAction]) -> PIIAction:
    if not values:
        return PIIAction.ALLOW
    order = {
        PIIAction.ALLOW: 1,
        PIIAction.WARN: 2,
        PIIAction.MASK: 3,
        PIIAction.HASH: 4,
        PIIAction.TOKENIZE: 5,
        PIIAction.BLOCK: 6,
        PIIAction.QUARANTINE: 7,
    }
    return max(values, key=lambda value: order[value])


def _compile_many(*patterns: str) -> Tuple[Pattern[str], ...]:
    return tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)


def _normalize_column_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower())


def _mask_email(value: str) -> str:
    if "@" not in value:
        return _mask_partial(value)
    local, domain = value.split("@", 1)
    masked_local = local[:1] + "***" if local else "***"
    domain_parts = domain.split(".")
    if len(domain_parts) >= 2:
        masked_domain = domain_parts[0][:1] + "***." + ".".join(domain_parts[1:])
    else:
        masked_domain = "***"
    return f"{masked_local}@{masked_domain}"


def _mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) <= 4:
        return "***"
    return f"***{digits[-4:]}"


def _mask_partial(value: str) -> str:
    if len(value) <= 2:
        return "*" * len(value)
    if len(value) <= 6:
        return value[0] + "***" + value[-1]
    return value[:2] + "***" + value[-2:]


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd is not None and pd.isna(value):
            return True
    except Exception:
        pass
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def _safe_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _safe_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_json_value(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if _is_null(value):
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


__all__ = [
    "AuditSink",
    "CustomDetector",
    "DataLike",
    "InMemoryAuditSink",
    "InMemoryMetricsSink",
    "MaskingStrategy",
    "PIIAction",
    "PIICategory",
    "PIIColumnProfile",
    "PIIComplianceError",
    "PIIConfigurationError",
    "PIIDetectorRule",
    "PIIFinding",
    "PIIPolicy",
    "PIISeverity",
    "PIIStatus",
    "PIIType",
    "PIIValidationContext",
    "PIIValidationResult",
    "PIIValidator",
    "default_detectors",
    "is_valid_cnpj",
    "is_valid_cpf",
    "is_valid_ip",
    "is_valid_luhn",
    "mask_value",
]

"""
kwanza-ai-core/services/fraud_service.py

Enterprise-grade fraud detection service for financial/cashflow operations.

Design goals
------------
- Deterministic rule engine + optional ML model scoring.
- Explainable decisions with reason codes, evidence and risk factors.
- Tenant-aware and privacy-conscious by default.
- Async-first service API, safe fallbacks and dependency injection.
- Audit-ready outputs for compliance and investigation workflows.
- Production observability hooks: metrics, tracing context and structured logs.

This module is intentionally self-contained and framework-agnostic. It can be
wired into FastAPI, Celery, Kafka consumers, Supabase Edge Functions, batch jobs
or internal orchestration services.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import statistics
import time
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Deque,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
MetricTags = Mapping[str, str]


# =============================================================================
# Exceptions
# =============================================================================


class FraudServiceError(RuntimeError):
    """Base exception for fraud service failures."""


class FraudValidationError(FraudServiceError):
    """Raised when an input payload is invalid."""


class FraudDependencyError(FraudServiceError):
    """Raised when an external dependency fails and no safe fallback is possible."""


# =============================================================================
# Enums and data models
# =============================================================================


class FraudDecision(str, Enum):
    APPROVE = "approve"
    REVIEW = "review"
    CHALLENGE = "challenge"
    BLOCK = "block"


class FraudSignalSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FraudEntityType(str, Enum):
    USER = "user"
    ACCOUNT = "account"
    DEVICE = "device"
    IP = "ip"
    DOCUMENT = "document"
    CARD = "card"
    BENEFICIARY = "beneficiary"
    MERCHANT = "merchant"
    SESSION = "session"


class TransactionChannel(str, Enum):
    WEB = "web"
    MOBILE = "mobile"
    API = "api"
    POS = "pos"
    BACKOFFICE = "backoffice"
    UNKNOWN = "unknown"


class TransactionType(str, Enum):
    CASH_IN = "cash_in"
    CASH_OUT = "cash_out"
    TRANSFER = "transfer"
    PAYMENT = "payment"
    REFUND = "refund"
    REVERSAL = "reversal"
    LOGIN = "login"
    PROFILE_CHANGE = "profile_change"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GeoLocation:
    country: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    def compact(self) -> str:
        parts = [self.country, self.region, self.city]
        return "/".join(str(p).strip().lower() for p in parts if p)


@dataclass(frozen=True)
class DeviceContext:
    device_id: Optional[str] = None
    fingerprint: Optional[str] = None
    user_agent: Optional[str] = None
    os: Optional[str] = None
    app_version: Optional[str] = None
    is_emulator: Optional[bool] = None
    is_rooted_or_jailbroken: Optional[bool] = None
    risk_score: Optional[float] = None


@dataclass(frozen=True)
class NetworkContext:
    ip_address: Optional[str] = None
    asn: Optional[str] = None
    isp: Optional[str] = None
    vpn: Optional[bool] = None
    proxy: Optional[bool] = None
    tor: Optional[bool] = None
    datacenter: Optional[bool] = None
    geo: Optional[GeoLocation] = None


@dataclass(frozen=True)
class TransactionContext:
    transaction_id: str
    tenant_id: str
    user_id: Optional[str]
    account_id: Optional[str]
    amount: Decimal
    currency: str
    transaction_type: TransactionType = TransactionType.UNKNOWN
    channel: TransactionChannel = TransactionChannel.UNKNOWN
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    merchant_id: Optional[str] = None
    beneficiary_id: Optional[str] = None
    document_id: Optional[str] = None
    session_id: Optional[str] = None
    device: Optional[DeviceContext] = None
    network: Optional[NetworkContext] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FraudSignal:
    code: str
    message: str
    severity: FraudSignalSeverity
    score_delta: float
    evidence: Mapping[str, Any] = field(default_factory=dict)
    rule_id: Optional[str] = None


@dataclass(frozen=True)
class FraudDecisionResult:
    request_id: str
    transaction_id: str
    tenant_id: str
    decision: FraudDecision
    risk_score: float
    confidence: float
    signals: Sequence[FraudSignal]
    reason_codes: Sequence[str]
    model_score: Optional[float]
    rule_score: float
    threshold_snapshot: Mapping[str, float]
    evaluated_at: datetime
    processing_ms: float
    recommended_actions: Sequence[str] = field(default_factory=list)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["decision"] = self.decision.value
        payload["evaluated_at"] = self.evaluated_at.isoformat()
        for signal in payload["signals"]:
            signal["severity"] = signal["severity"].value if hasattr(signal["severity"], "value") else signal["severity"]
        return payload


@dataclass(frozen=True)
class FraudServiceConfig:
    approve_threshold: float = 34.99
    review_threshold: float = 55.0
    challenge_threshold: float = 74.0
    block_threshold: float = 88.0
    max_score: float = 100.0
    min_score: float = 0.0
    model_weight: float = 0.45
    rule_weight: float = 0.55
    enable_ml_model: bool = True
    fail_closed_on_dependency_error: bool = False
    high_value_amount: Decimal = Decimal("500000")
    velocity_window_minutes: int = 30
    velocity_txn_count_limit: int = 8
    velocity_amount_limit: Decimal = Decimal("1500000")
    new_beneficiary_window_hours: int = 24
    trusted_user_min_age_days: int = 30
    cache_ttl_seconds: int = 180
    audit_enabled: bool = True
    privacy_hash_salt: str = "change-me-in-production"

    def validate(self) -> None:
        ordered = [self.approve_threshold, self.review_threshold, self.challenge_threshold, self.block_threshold]
        if ordered != sorted(ordered):
            raise FraudValidationError("Fraud thresholds must be ordered increasingly.")
        if not 0 <= self.model_weight <= 1 or not 0 <= self.rule_weight <= 1:
            raise FraudValidationError("Model and rule weights must be between 0 and 1.")
        if not math.isclose(self.model_weight + self.rule_weight, 1.0, rel_tol=0.0001):
            raise FraudValidationError("Model and rule weights must sum to 1.0.")


# =============================================================================
# Protocols / dependency contracts
# =============================================================================


class FraudModel(Protocol):
    async def predict_risk(self, features: Mapping[str, Any]) -> float:
        """Return model probability/risk score in the range [0, 1] or [0, 100]."""


class FraudRepository(Protocol):
    async def get_user_profile(self, tenant_id: str, user_id: str) -> Optional[Mapping[str, Any]]: ...

    async def get_recent_transactions(
        self,
        tenant_id: str,
        entity_id: str,
        since: datetime,
        limit: int = 100,
    ) -> Sequence[Mapping[str, Any]]: ...

    async def is_known_beneficiary(self, tenant_id: str, user_id: str, beneficiary_id: str) -> bool: ...

    async def is_entity_blocklisted(self, tenant_id: str, entity_type: FraudEntityType, value: str) -> bool: ...

    async def save_fraud_evaluation(self, result: FraudDecisionResult) -> None: ...


class MetricsClient(Protocol):
    def increment(self, name: str, value: int = 1, tags: Optional[MetricTags] = None) -> None: ...

    def timing(self, name: str, value_ms: float, tags: Optional[MetricTags] = None) -> None: ...

    def gauge(self, name: str, value: float, tags: Optional[MetricTags] = None) -> None: ...


class AuditSink(Protocol):
    async def write(self, event_name: str, payload: Mapping[str, Any]) -> None: ...


# =============================================================================
# Default no-op dependencies
# =============================================================================


class NoopMetricsClient:
    def increment(self, name: str, value: int = 1, tags: Optional[MetricTags] = None) -> None:
        return None

    def timing(self, name: str, value_ms: float, tags: Optional[MetricTags] = None) -> None:
        return None

    def gauge(self, name: str, value: float, tags: Optional[MetricTags] = None) -> None:
        return None


class NoopAuditSink:
    async def write(self, event_name: str, payload: Mapping[str, Any]) -> None:
        return None


class InMemoryFraudRepository:
    """
    Lightweight repository useful for local development/tests.
    Replace with PostgreSQL/Supabase/warehouse-backed repository in production.
    """

    def __init__(self) -> None:
        self._profiles: Dict[Tuple[str, str], Mapping[str, Any]] = {}
        self._transactions: Dict[Tuple[str, str], Deque[Mapping[str, Any]]] = defaultdict(lambda: deque(maxlen=500))
        self._known_beneficiaries: set[Tuple[str, str, str]] = set()
        self._blocklist: set[Tuple[str, FraudEntityType, str]] = set()
        self._evaluations: List[FraudDecisionResult] = []

    def add_profile(self, tenant_id: str, user_id: str, profile: Mapping[str, Any]) -> None:
        self._profiles[(tenant_id, user_id)] = dict(profile)

    def add_transaction(self, tenant_id: str, entity_id: str, transaction: Mapping[str, Any]) -> None:
        self._transactions[(tenant_id, entity_id)].append(dict(transaction))

    def add_known_beneficiary(self, tenant_id: str, user_id: str, beneficiary_id: str) -> None:
        self._known_beneficiaries.add((tenant_id, user_id, beneficiary_id))

    def block_entity(self, tenant_id: str, entity_type: FraudEntityType, value: str) -> None:
        self._blocklist.add((tenant_id, entity_type, value))

    async def get_user_profile(self, tenant_id: str, user_id: str) -> Optional[Mapping[str, Any]]:
        return self._profiles.get((tenant_id, user_id))

    async def get_recent_transactions(
        self,
        tenant_id: str,
        entity_id: str,
        since: datetime,
        limit: int = 100,
    ) -> Sequence[Mapping[str, Any]]:
        rows = list(self._transactions.get((tenant_id, entity_id), []))
        filtered: List[Mapping[str, Any]] = []
        for row in reversed(rows):
            created_at = _parse_datetime(row.get("created_at"))
            if created_at and created_at >= since:
                filtered.append(row)
            if len(filtered) >= limit:
                break
        return filtered

    async def is_known_beneficiary(self, tenant_id: str, user_id: str, beneficiary_id: str) -> bool:
        return (tenant_id, user_id, beneficiary_id) in self._known_beneficiaries

    async def is_entity_blocklisted(self, tenant_id: str, entity_type: FraudEntityType, value: str) -> bool:
        return (tenant_id, entity_type, value) in self._blocklist

    async def save_fraud_evaluation(self, result: FraudDecisionResult) -> None:
        self._evaluations.append(result)


# =============================================================================
# TTL cache
# =============================================================================


class AsyncTTLCache:
    def __init__(self, ttl_seconds: int = 180, max_size: int = 10_000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._items: MutableMapping[str, Tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any:
        now = time.monotonic()
        async with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at < now:
                self._items.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any) -> None:
        expires_at = time.monotonic() + self.ttl_seconds
        async with self._lock:
            if len(self._items) >= self.max_size:
                oldest_key = next(iter(self._items))
                self._items.pop(oldest_key, None)
            self._items[key] = (expires_at, value)


# =============================================================================
# Utility functions
# =============================================================================


def _parse_decimal(value: Any, field_name: str = "amount") -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FraudValidationError(f"Invalid decimal value for {field_name}: {value!r}") from exc
    if amount < 0:
        raise FraudValidationError(f"{field_name} cannot be negative.")
    return amount


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        raw = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, min_value: float = 0.0, max_value: float = 100.0) -> float:
    return max(min_value, min(max_value, value))


def _hash_value(value: Optional[str], salt: str) -> Optional[str]:
    if not value:
        return None
    digest = hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()
    return digest[:20]


def _stable_json_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)


# =============================================================================
# Feature builder
# =============================================================================


class FraudFeatureBuilder:
    def __init__(self, config: FraudServiceConfig) -> None:
        self.config = config

    def build(
        self,
        tx: TransactionContext,
        user_profile: Optional[Mapping[str, Any]],
        recent_transactions: Sequence[Mapping[str, Any]],
        is_known_beneficiary: Optional[bool],
    ) -> JsonDict:
        amount = float(tx.amount)
        recent_amounts = [_safe_float(row.get("amount")) for row in recent_transactions]
        recent_count = len(recent_transactions)
        recent_total = sum(recent_amounts)
        avg_recent = statistics.mean(recent_amounts) if recent_amounts else 0.0
        std_recent = statistics.pstdev(recent_amounts) if len(recent_amounts) > 1 else 0.0
        amount_zscore = (amount - avg_recent) / std_recent if std_recent > 0 else 0.0

        user_created_at = _parse_datetime((user_profile or {}).get("created_at"))
        user_age_days = (_utc_now() - user_created_at).days if user_created_at else None

        geo = tx.network.geo if tx.network and tx.network.geo else None

        return {
            "tenant_id": tx.tenant_id,
            "transaction_type": tx.transaction_type.value,
            "channel": tx.channel.value,
            "amount": amount,
            "currency": tx.currency.upper(),
            "hour_of_day": tx.created_at.hour,
            "day_of_week": tx.created_at.weekday(),
            "recent_count": recent_count,
            "recent_total_amount": recent_total,
            "recent_avg_amount": avg_recent,
            "recent_amount_zscore": amount_zscore,
            "is_high_value": amount >= float(self.config.high_value_amount),
            "is_known_beneficiary": bool(is_known_beneficiary),
            "user_age_days": user_age_days,
            "is_new_user": user_age_days is not None and user_age_days < self.config.trusted_user_min_age_days,
            "device_risk_score": _safe_float(tx.device.risk_score if tx.device else None),
            "is_emulator": bool(tx.device and tx.device.is_emulator),
            "is_rooted_or_jailbroken": bool(tx.device and tx.device.is_rooted_or_jailbroken),
            "network_vpn": bool(tx.network and tx.network.vpn),
            "network_proxy": bool(tx.network and tx.network.proxy),
            "network_tor": bool(tx.network and tx.network.tor),
            "network_datacenter": bool(tx.network and tx.network.datacenter),
            "geo_country": geo.country if geo else None,
            "geo_compact": geo.compact() if geo else None,
            "metadata_keys": sorted(list(tx.metadata.keys()))[:50],
        }


# =============================================================================
# Rule engine
# =============================================================================


RuleCallable = Callable[[TransactionContext, Mapping[str, Any]], Awaitable[Optional[FraudSignal]]]


class FraudRuleEngine:
    def __init__(self, config: FraudServiceConfig) -> None:
        self.config = config
        self._rules: List[Tuple[str, RuleCallable]] = []
        self.register_default_rules()

    def register(self, rule_id: str, rule: RuleCallable) -> None:
        self._rules.append((rule_id, rule))

    def register_default_rules(self) -> None:
        self.register("amount.high_value", self._rule_high_value_amount)
        self.register("amount.outlier", self._rule_amount_outlier)
        self.register("velocity.count", self._rule_velocity_count)
        self.register("velocity.amount", self._rule_velocity_amount)
        self.register("beneficiary.new", self._rule_new_beneficiary)
        self.register("device.compromised", self._rule_compromised_device)
        self.register("network.anonymous", self._rule_anonymous_network)
        self.register("network.tor", self._rule_tor_network)
        self.register("behavior.night_activity", self._rule_night_activity)
        self.register("profile.new_user_high_value", self._rule_new_user_high_value)

    async def evaluate(self, tx: TransactionContext, features: Mapping[str, Any]) -> Tuple[float, List[FraudSignal]]:
        signals: List[FraudSignal] = []
        for rule_id, rule in self._rules:
            try:
                signal = await rule(tx, features)
                if signal:
                    signals.append(
                        FraudSignal(
                            code=signal.code,
                            message=signal.message,
                            severity=signal.severity,
                            score_delta=signal.score_delta,
                            evidence=dict(signal.evidence),
                            rule_id=rule_id,
                        )
                    )
            except Exception as exc:  # defensive isolation per rule
                logger.exception("Fraud rule failed", extra={"rule_id": rule_id, "transaction_id": tx.transaction_id})
                signals.append(
                    FraudSignal(
                        code="RULE_ERROR",
                        message=f"Rule {rule_id} failed safely: {exc.__class__.__name__}",
                        severity=FraudSignalSeverity.LOW,
                        score_delta=2.0,
                        evidence={"rule_id": rule_id},
                        rule_id=rule_id,
                    )
                )
        raw_score = sum(signal.score_delta for signal in signals)
        return _clamp(raw_score, self.config.min_score, self.config.max_score), signals

    async def _rule_high_value_amount(self, tx: TransactionContext, features: Mapping[str, Any]) -> Optional[FraudSignal]:
        if tx.amount >= self.config.high_value_amount:
            return FraudSignal(
                code="HIGH_VALUE_TRANSACTION",
                message="Transaction amount exceeds configured high-value threshold.",
                severity=FraudSignalSeverity.HIGH,
                score_delta=18.0,
                evidence={"amount": str(tx.amount), "threshold": str(self.config.high_value_amount)},
            )
        return None

    async def _rule_amount_outlier(self, tx: TransactionContext, features: Mapping[str, Any]) -> Optional[FraudSignal]:
        zscore = abs(_safe_float(features.get("recent_amount_zscore")))
        if zscore >= 4:
            return FraudSignal(
                code="AMOUNT_OUTLIER_CRITICAL",
                message="Transaction amount is an extreme outlier compared with recent behavior.",
                severity=FraudSignalSeverity.CRITICAL,
                score_delta=24.0,
                evidence={"zscore": round(zscore, 4)},
            )
        if zscore >= 2.5:
            return FraudSignal(
                code="AMOUNT_OUTLIER",
                message="Transaction amount is unusual compared with recent behavior.",
                severity=FraudSignalSeverity.MEDIUM,
                score_delta=12.0,
                evidence={"zscore": round(zscore, 4)},
            )
        return None

    async def _rule_velocity_count(self, tx: TransactionContext, features: Mapping[str, Any]) -> Optional[FraudSignal]:
        count = int(features.get("recent_count") or 0)
        if count >= self.config.velocity_txn_count_limit:
            return FraudSignal(
                code="VELOCITY_COUNT_LIMIT",
                message="Transaction count exceeded velocity threshold.",
                severity=FraudSignalSeverity.HIGH,
                score_delta=20.0,
                evidence={"recent_count": count, "limit": self.config.velocity_txn_count_limit},
            )
        return None

    async def _rule_velocity_amount(self, tx: TransactionContext, features: Mapping[str, Any]) -> Optional[FraudSignal]:
        total = Decimal(str(features.get("recent_total_amount") or "0")) + tx.amount
        if total >= self.config.velocity_amount_limit:
            return FraudSignal(
                code="VELOCITY_AMOUNT_LIMIT",
                message="Cumulative amount exceeded velocity threshold.",
                severity=FraudSignalSeverity.HIGH,
                score_delta=20.0,
                evidence={"window_total": str(total), "limit": str(self.config.velocity_amount_limit)},
            )
        return None

    async def _rule_new_beneficiary(self, tx: TransactionContext, features: Mapping[str, Any]) -> Optional[FraudSignal]:
        if tx.transaction_type in {TransactionType.TRANSFER, TransactionType.CASH_OUT, TransactionType.PAYMENT}:
            if tx.beneficiary_id and not features.get("is_known_beneficiary"):
                return FraudSignal(
                    code="NEW_BENEFICIARY",
                    message="Transaction targets a beneficiary not previously trusted for this user.",
                    severity=FraudSignalSeverity.MEDIUM,
                    score_delta=11.0,
                    evidence={"beneficiary_id_present": True},
                )
        return None

    async def _rule_compromised_device(self, tx: TransactionContext, features: Mapping[str, Any]) -> Optional[FraudSignal]:
        if features.get("is_rooted_or_jailbroken") or features.get("is_emulator"):
            return FraudSignal(
                code="RISKY_DEVICE",
                message="Device context indicates emulator or rooted/jailbroken environment.",
                severity=FraudSignalSeverity.HIGH,
                score_delta=18.0,
                evidence={
                    "is_emulator": bool(features.get("is_emulator")),
                    "is_rooted_or_jailbroken": bool(features.get("is_rooted_or_jailbroken")),
                },
            )
        device_risk = _safe_float(features.get("device_risk_score"))
        if device_risk >= 75:
            return FraudSignal(
                code="HIGH_DEVICE_RISK",
                message="Device risk score is elevated.",
                severity=FraudSignalSeverity.MEDIUM,
                score_delta=10.0,
                evidence={"device_risk_score": device_risk},
            )
        return None

    async def _rule_anonymous_network(self, tx: TransactionContext, features: Mapping[str, Any]) -> Optional[FraudSignal]:
        flags = ["network_vpn", "network_proxy", "network_datacenter"]
        active = [flag for flag in flags if features.get(flag)]
        if active:
            return FraudSignal(
                code="ANONYMOUS_NETWORK",
                message="Network context indicates VPN, proxy or datacenter usage.",
                severity=FraudSignalSeverity.MEDIUM,
                score_delta=10.0,
                evidence={"active_flags": active},
            )
        return None

    async def _rule_tor_network(self, tx: TransactionContext, features: Mapping[str, Any]) -> Optional[FraudSignal]:
        if features.get("network_tor"):
            return FraudSignal(
                code="TOR_NETWORK",
                message="Transaction originated from Tor network.",
                severity=FraudSignalSeverity.CRITICAL,
                score_delta=30.0,
                evidence={"tor": True},
            )
        return None

    async def _rule_night_activity(self, tx: TransactionContext, features: Mapping[str, Any]) -> Optional[FraudSignal]:
        hour = int(features.get("hour_of_day") or 0)
        if hour in {0, 1, 2, 3, 4} and tx.transaction_type in {TransactionType.CASH_OUT, TransactionType.TRANSFER}:
            return FraudSignal(
                code="UNUSUAL_TIME_OF_DAY",
                message="Sensitive transaction occurred during high-risk time window.",
                severity=FraudSignalSeverity.LOW,
                score_delta=6.0,
                evidence={"hour": hour},
            )
        return None

    async def _rule_new_user_high_value(self, tx: TransactionContext, features: Mapping[str, Any]) -> Optional[FraudSignal]:
        if features.get("is_new_user") and features.get("is_high_value"):
            return FraudSignal(
                code="NEW_USER_HIGH_VALUE",
                message="New user initiated a high-value transaction.",
                severity=FraudSignalSeverity.HIGH,
                score_delta=22.0,
                evidence={"user_age_days": features.get("user_age_days"), "amount": str(tx.amount)},
            )
        return None


# =============================================================================
# Main service
# =============================================================================


class FraudService:
    def __init__(
        self,
        repository: FraudRepository,
        config: Optional[FraudServiceConfig] = None,
        model: Optional[FraudModel] = None,
        metrics: Optional[MetricsClient] = None,
        audit_sink: Optional[AuditSink] = None,
        cache: Optional[AsyncTTLCache] = None,
    ) -> None:
        self.config = config or FraudServiceConfig()
        self.config.validate()
        self.repository = repository
        self.model = model
        self.metrics = metrics or NoopMetricsClient()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.cache = cache or AsyncTTLCache(ttl_seconds=self.config.cache_ttl_seconds)
        self.feature_builder = FraudFeatureBuilder(self.config)
        self.rule_engine = FraudRuleEngine(self.config)

    async def evaluate_transaction(self, tx: TransactionContext, request_id: Optional[str] = None) -> FraudDecisionResult:
        request_id = request_id or str(uuid.uuid4())
        start = time.perf_counter()
        self._validate_transaction(tx)

        tags = {"tenant_id": tx.tenant_id, "type": tx.transaction_type.value, "channel": tx.channel.value}
        self.metrics.increment("fraud.evaluation.started", tags=tags)

        try:
            result = await self._evaluate_transaction_safe(tx, request_id)
            self.metrics.increment("fraud.evaluation.completed", tags={**tags, "decision": result.decision.value})
            self.metrics.gauge("fraud.risk_score", result.risk_score, tags=tags)
            return result
        except Exception as exc:
            self.metrics.increment("fraud.evaluation.failed", tags={**tags, "error": exc.__class__.__name__})
            logger.exception("Fraud evaluation failed", extra={"request_id": request_id, "transaction_id": tx.transaction_id})
            if self.config.fail_closed_on_dependency_error:
                result = self._fallback_result(
                    tx=tx,
                    request_id=request_id,
                    start=start,
                    decision=FraudDecision.BLOCK,
                    score=self.config.block_threshold,
                    message="Fraud evaluation failed; fail-closed policy blocked transaction.",
                )
            else:
                result = self._fallback_result(
                    tx=tx,
                    request_id=request_id,
                    start=start,
                    decision=FraudDecision.REVIEW,
                    score=self.config.review_threshold,
                    message="Fraud evaluation failed; transaction routed to manual review.",
                )
            await self._persist_and_audit(result)
            return result
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.metrics.timing("fraud.evaluation.processing_ms", elapsed_ms, tags=tags)

    async def _evaluate_transaction_safe(self, tx: TransactionContext, request_id: str) -> FraudDecisionResult:
        start = time.perf_counter()

        blocklist_signals = await self._evaluate_blocklists(tx)
        if blocklist_signals:
            risk_score = self.config.max_score
            result = self._build_result(
                tx=tx,
                request_id=request_id,
                start=start,
                decision=FraudDecision.BLOCK,
                risk_score=risk_score,
                confidence=0.98,
                signals=blocklist_signals,
                model_score=None,
                rule_score=risk_score,
                metadata={"short_circuit": "blocklist"},
            )
            await self._persist_and_audit(result)
            return result

        user_profile, recent_transactions, known_beneficiary = await self._load_context(tx)
        features = self.feature_builder.build(tx, user_profile, recent_transactions, known_beneficiary)

        rule_score, rule_signals = await self.rule_engine.evaluate(tx, features)
        model_score = await self._score_model(features)

        if model_score is None:
            combined_score = rule_score
            confidence = self._confidence_from_signals(rule_signals, model_available=False)
        else:
            combined_score = (rule_score * self.config.rule_weight) + (model_score * self.config.model_weight)
            confidence = self._confidence_from_signals(rule_signals, model_available=True)

        risk_score = _clamp(combined_score, self.config.min_score, self.config.max_score)
        decision = self._decision_from_score(risk_score)
        recommended_actions = self._recommended_actions(decision, rule_signals, tx)

        result = self._build_result(
            tx=tx,
            request_id=request_id,
            start=start,
            decision=decision,
            risk_score=risk_score,
            confidence=confidence,
            signals=rule_signals,
            model_score=model_score,
            rule_score=rule_score,
            recommended_actions=recommended_actions,
            metadata={
                "feature_hash": _stable_json_hash(features),
                "context": {
                    "recent_transactions_count": len(recent_transactions),
                    "has_user_profile": bool(user_profile),
                    "known_beneficiary": known_beneficiary,
                },
            },
        )
        await self._persist_and_audit(result)
        return result

    async def _load_context(
        self,
        tx: TransactionContext,
    ) -> Tuple[Optional[Mapping[str, Any]], Sequence[Mapping[str, Any]], Optional[bool]]:
        since = _utc_now() - timedelta(minutes=self.config.velocity_window_minutes)
        entity_id = tx.user_id or tx.account_id or tx.transaction_id

        async def cached_user_profile() -> Optional[Mapping[str, Any]]:
            if not tx.user_id:
                return None
            key = f"profile:{tx.tenant_id}:{tx.user_id}"
            cached = await self.cache.get(key)
            if cached is not None:
                return cached
            profile = await self.repository.get_user_profile(tx.tenant_id, tx.user_id)
            await self.cache.set(key, profile)
            return profile

        async def recent_txns() -> Sequence[Mapping[str, Any]]:
            return await self.repository.get_recent_transactions(tx.tenant_id, entity_id, since=since, limit=200)

        async def beneficiary_known() -> Optional[bool]:
            if not tx.user_id or not tx.beneficiary_id:
                return None
            key = f"beneficiary:{tx.tenant_id}:{tx.user_id}:{tx.beneficiary_id}"
            cached = await self.cache.get(key)
            if cached is not None:
                return bool(cached)
            known = await self.repository.is_known_beneficiary(tx.tenant_id, tx.user_id, tx.beneficiary_id)
            await self.cache.set(key, known)
            return known

        return await asyncio.gather(cached_user_profile(), recent_txns(), beneficiary_known())

    async def _evaluate_blocklists(self, tx: TransactionContext) -> List[FraudSignal]:
        checks: List[Tuple[FraudEntityType, Optional[str]]] = [
            (FraudEntityType.USER, tx.user_id),
            (FraudEntityType.ACCOUNT, tx.account_id),
            (FraudEntityType.DEVICE, tx.device.device_id if tx.device else None),
            (FraudEntityType.IP, tx.network.ip_address if tx.network else None),
            (FraudEntityType.DOCUMENT, tx.document_id),
            (FraudEntityType.BENEFICIARY, tx.beneficiary_id),
            (FraudEntityType.MERCHANT, tx.merchant_id),
            (FraudEntityType.SESSION, tx.session_id),
        ]

        async def check(entity_type: FraudEntityType, value: Optional[str]) -> Optional[FraudSignal]:
            if not value:
                return None
            is_blocked = await self.repository.is_entity_blocklisted(tx.tenant_id, entity_type, value)
            if not is_blocked:
                return None
            return FraudSignal(
                code="BLOCKLIST_MATCH",
                message=f"{entity_type.value} matched fraud blocklist.",
                severity=FraudSignalSeverity.CRITICAL,
                score_delta=100.0,
                evidence={"entity_type": entity_type.value, "entity_hash": _hash_value(value, self.config.privacy_hash_salt)},
                rule_id="blocklist.match",
            )

        results = await asyncio.gather(*(check(t, v) for t, v in checks))
        return [signal for signal in results if signal is not None]

    async def _score_model(self, features: Mapping[str, Any]) -> Optional[float]:
        if not self.config.enable_ml_model or not self.model:
            return None
        try:
            raw_score = await self.model.predict_risk(features)
            score = _safe_float(raw_score)
            if 0 <= score <= 1:
                score *= 100
            return _clamp(score, self.config.min_score, self.config.max_score)
        except Exception as exc:
            self.metrics.increment("fraud.model.failed", tags={"error": exc.__class__.__name__})
            logger.exception("Fraud model scoring failed")
            return None

    def _decision_from_score(self, score: float) -> FraudDecision:
        if score >= self.config.block_threshold:
            return FraudDecision.BLOCK
        if score >= self.config.challenge_threshold:
            return FraudDecision.CHALLENGE
        if score >= self.config.review_threshold:
            return FraudDecision.REVIEW
        return FraudDecision.APPROVE

    def _confidence_from_signals(self, signals: Sequence[FraudSignal], model_available: bool) -> float:
        base = 0.62 if not model_available else 0.76
        severity_bonus = 0.0
        for signal in signals:
            if signal.severity == FraudSignalSeverity.CRITICAL:
                severity_bonus += 0.08
            elif signal.severity == FraudSignalSeverity.HIGH:
                severity_bonus += 0.05
            elif signal.severity == FraudSignalSeverity.MEDIUM:
                severity_bonus += 0.03
        return round(_clamp(base + severity_bonus, 0.0, 0.99), 4)

    def _recommended_actions(
        self,
        decision: FraudDecision,
        signals: Sequence[FraudSignal],
        tx: TransactionContext,
    ) -> List[str]:
        actions: List[str] = []
        codes = {s.code for s in signals}

        if decision == FraudDecision.APPROVE:
            actions.append("approve_transaction")
        elif decision == FraudDecision.REVIEW:
            actions.extend(["queue_manual_review", "hold_settlement_temporarily"])
        elif decision == FraudDecision.CHALLENGE:
            actions.extend(["step_up_authentication", "require_otp_or_biometric", "hold_transaction_until_verified"])
        elif decision == FraudDecision.BLOCK:
            actions.extend(["block_transaction", "open_fraud_case", "notify_risk_team"])

        if "NEW_BENEFICIARY" in codes:
            actions.append("verify_beneficiary_ownership")
        if "RISKY_DEVICE" in codes or "HIGH_DEVICE_RISK" in codes:
            actions.append("rebind_or_verify_device")
        if "ANONYMOUS_NETWORK" in codes or "TOR_NETWORK" in codes:
            actions.append("require_clean_network_reauthentication")
        if tx.amount >= self.config.high_value_amount:
            actions.append("apply_high_value_approval_workflow")

        return list(dict.fromkeys(actions))

    def _build_result(
        self,
        tx: TransactionContext,
        request_id: str,
        start: float,
        decision: FraudDecision,
        risk_score: float,
        confidence: float,
        signals: Sequence[FraudSignal],
        model_score: Optional[float],
        rule_score: float,
        recommended_actions: Optional[Sequence[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> FraudDecisionResult:
        threshold_snapshot = {
            "approve_threshold": self.config.approve_threshold,
            "review_threshold": self.config.review_threshold,
            "challenge_threshold": self.config.challenge_threshold,
            "block_threshold": self.config.block_threshold,
        }
        reason_codes = [signal.code for signal in sorted(signals, key=lambda s: s.score_delta, reverse=True)]
        processing_ms = (time.perf_counter() - start) * 1000
        return FraudDecisionResult(
            request_id=request_id,
            transaction_id=tx.transaction_id,
            tenant_id=tx.tenant_id,
            decision=decision,
            risk_score=round(risk_score, 4),
            confidence=round(confidence, 4),
            signals=signals,
            reason_codes=reason_codes,
            model_score=None if model_score is None else round(model_score, 4),
            rule_score=round(rule_score, 4),
            threshold_snapshot=threshold_snapshot,
            evaluated_at=_utc_now(),
            processing_ms=round(processing_ms, 4),
            recommended_actions=recommended_actions or [],
            metadata=metadata or {},
        )

    def _fallback_result(
        self,
        tx: TransactionContext,
        request_id: str,
        start: float,
        decision: FraudDecision,
        score: float,
        message: str,
    ) -> FraudDecisionResult:
        signal = FraudSignal(
            code="FRAUD_SERVICE_FALLBACK",
            message=message,
            severity=FraudSignalSeverity.HIGH,
            score_delta=score,
            evidence={"fail_closed": self.config.fail_closed_on_dependency_error},
            rule_id="service.fallback",
        )
        return self._build_result(
            tx=tx,
            request_id=request_id,
            start=start,
            decision=decision,
            risk_score=score,
            confidence=0.5,
            signals=[signal],
            model_score=None,
            rule_score=score,
            recommended_actions=["queue_manual_review"] if decision != FraudDecision.BLOCK else ["block_transaction", "notify_risk_team"],
            metadata={"fallback": True},
        )

    async def _persist_and_audit(self, result: FraudDecisionResult) -> None:
        try:
            await self.repository.save_fraud_evaluation(result)
        except Exception as exc:
            self.metrics.increment("fraud.persistence.failed", tags={"error": exc.__class__.__name__})
            logger.exception("Failed to persist fraud evaluation", extra={"request_id": result.request_id})

        if self.config.audit_enabled:
            try:
                await self.audit_sink.write(
                    "fraud.evaluation.completed",
                    {
                        "request_id": result.request_id,
                        "tenant_id": result.tenant_id,
                        "transaction_id": result.transaction_id,
                        "decision": result.decision.value,
                        "risk_score": result.risk_score,
                        "confidence": result.confidence,
                        "reason_codes": list(result.reason_codes),
                        "recommended_actions": list(result.recommended_actions),
                        "evaluated_at": result.evaluated_at.isoformat(),
                    },
                )
            except Exception as exc:
                self.metrics.increment("fraud.audit.failed", tags={"error": exc.__class__.__name__})
                logger.exception("Failed to audit fraud evaluation", extra={"request_id": result.request_id})

    def _validate_transaction(self, tx: TransactionContext) -> None:
        if not tx.transaction_id:
            raise FraudValidationError("transaction_id is required.")
        if not tx.tenant_id:
            raise FraudValidationError("tenant_id is required.")
        if not tx.currency or len(tx.currency.strip()) < 3:
            raise FraudValidationError("currency must be a valid ISO-like code.")
        if tx.amount < 0:
            raise FraudValidationError("amount cannot be negative.")
        if not tx.user_id and not tx.account_id:
            raise FraudValidationError("At least one of user_id or account_id is required.")

    @classmethod
    def transaction_from_payload(cls, payload: Mapping[str, Any]) -> TransactionContext:
        """Parse external JSON/API payload into a strongly typed transaction context."""
        device_payload = payload.get("device") or {}
        network_payload = payload.get("network") or {}
        geo_payload = network_payload.get("geo") or {}

        geo = GeoLocation(
            country=geo_payload.get("country"),
            region=geo_payload.get("region"),
            city=geo_payload.get("city"),
            latitude=geo_payload.get("latitude"),
            longitude=geo_payload.get("longitude"),
        ) if geo_payload else None

        device = DeviceContext(
            device_id=device_payload.get("device_id"),
            fingerprint=device_payload.get("fingerprint"),
            user_agent=device_payload.get("user_agent"),
            os=device_payload.get("os"),
            app_version=device_payload.get("app_version"),
            is_emulator=device_payload.get("is_emulator"),
            is_rooted_or_jailbroken=device_payload.get("is_rooted_or_jailbroken"),
            risk_score=device_payload.get("risk_score"),
        ) if device_payload else None

        network = NetworkContext(
            ip_address=network_payload.get("ip_address"),
            asn=network_payload.get("asn"),
            isp=network_payload.get("isp"),
            vpn=network_payload.get("vpn"),
            proxy=network_payload.get("proxy"),
            tor=network_payload.get("tor"),
            datacenter=network_payload.get("datacenter"),
            geo=geo,
        ) if network_payload else None

        created_at = _parse_datetime(payload.get("created_at")) or _utc_now()

        return TransactionContext(
            transaction_id=str(payload.get("transaction_id") or uuid.uuid4()),
            tenant_id=str(payload["tenant_id"]),
            user_id=payload.get("user_id"),
            account_id=payload.get("account_id"),
            amount=_parse_decimal(payload.get("amount", "0")),
            currency=str(payload.get("currency", "AOA")).upper(),
            transaction_type=TransactionType(payload.get("transaction_type", TransactionType.UNKNOWN.value)),
            channel=TransactionChannel(payload.get("channel", TransactionChannel.UNKNOWN.value)),
            created_at=created_at,
            merchant_id=payload.get("merchant_id"),
            beneficiary_id=payload.get("beneficiary_id"),
            document_id=payload.get("document_id"),
            session_id=payload.get("session_id"),
            device=device,
            network=network,
            metadata=payload.get("metadata") or {},
        )


# =============================================================================
# Example lightweight model adapter
# =============================================================================


class HeuristicFraudModel:
    """
    Example model adapter used for local tests.
    Replace with a real model wrapper, e.g. MLflow, BentoML, Vertex AI, SageMaker,
    ONNX Runtime, sklearn pipeline or an internal inference service.
    """

    async def predict_risk(self, features: Mapping[str, Any]) -> float:
        score = 0.0
        amount = _safe_float(features.get("amount"))
        recent_count = int(features.get("recent_count") or 0)
        zscore = abs(_safe_float(features.get("recent_amount_zscore")))

        if amount > 1_000_000:
            score += 0.20
        if recent_count > 5:
            score += 0.18
        if zscore > 3:
            score += 0.18
        if features.get("network_tor"):
            score += 0.30
        if features.get("network_vpn") or features.get("network_proxy"):
            score += 0.08
        if features.get("is_rooted_or_jailbroken") or features.get("is_emulator"):
            score += 0.18
        if features.get("is_new_user") and features.get("is_high_value"):
            score += 0.20
        if not features.get("is_known_beneficiary"):
            score += 0.06

        return _clamp(score, 0.0, 1.0)


# =============================================================================
# Factory
# =============================================================================


def build_fraud_service(
    repository: Optional[FraudRepository] = None,
    model: Optional[FraudModel] = None,
    config: Optional[FraudServiceConfig] = None,
    metrics: Optional[MetricsClient] = None,
    audit_sink: Optional[AuditSink] = None,
) -> FraudService:
    return FraudService(
        repository=repository or InMemoryFraudRepository(),
        model=model,
        config=config,
        metrics=metrics,
        audit_sink=audit_sink,
    )


# =============================================================================
# Manual smoke test
# =============================================================================


async def _demo() -> None:
    logging.basicConfig(level=logging.INFO)
    repo = InMemoryFraudRepository()
    now = _utc_now()

    repo.add_profile(
        tenant_id="tenant-ao",
        user_id="user-123",
        profile={"created_at": (now - timedelta(days=2)).isoformat(), "segment": "retail"},
    )
    for idx in range(7):
        repo.add_transaction(
            tenant_id="tenant-ao",
            entity_id="user-123",
            transaction={
                "transaction_id": f"old-{idx}",
                "amount": "150000",
                "created_at": (now - timedelta(minutes=idx * 3)).isoformat(),
            },
        )

    service = build_fraud_service(
        repository=repo,
        model=HeuristicFraudModel(),
        config=FraudServiceConfig(privacy_hash_salt="local-dev-salt"),
    )

    tx = FraudService.transaction_from_payload(
        {
            "transaction_id": "txn-001",
            "tenant_id": "tenant-ao",
            "user_id": "user-123",
            "account_id": "acc-123",
            "amount": "1250000",
            "currency": "AOA",
            "transaction_type": "transfer",
            "channel": "mobile",
            "beneficiary_id": "new-beneficiary-999",
            "device": {
                "device_id": "device-abc",
                "is_emulator": False,
                "is_rooted_or_jailbroken": True,
                "risk_score": 82,
            },
            "network": {
                "ip_address": "203.0.113.10",
                "vpn": True,
                "proxy": False,
                "tor": False,
                "datacenter": False,
                "geo": {"country": "AO", "city": "Luanda"},
            },
        }
    )

    result = await service.evaluate_transaction(tx)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(_demo())

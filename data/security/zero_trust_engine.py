"""
data/security/zero_trust_engine.py

Enterprise-grade Zero Trust policy engine for Python services, APIs, data
platforms, workers, internal tools and security gateways.

Core capabilities:
- Never trust, always verify authorization model
- Continuous risk and trust evaluation
- Identity, device, network, workload and resource context checks
- Adaptive access decisions: allow, deny, challenge, step-up MFA, restrict
- Policy-based evaluation with explainable decisions
- Tenant-aware enforcement
- Session trust decay and re-evaluation
- Device posture validation
- Network posture validation
- Resource sensitivity and data classification checks
- Risk scoring and confidence scoring
- Decision cache with TTL
- Structured audit events
- In-memory repositories for local development/tests
- Decorator helper for service/API integration

Security posture:
- Deny by default
- Fail closed by default
- Least privilege
- Explicit policy requirements
- Strong auditing for all access decisions
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import ipaddress
import json
import logging
import math
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple, TypeVar, Union, cast

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
F = TypeVar("F", bound=Callable[..., Any])


# =============================================================================
# Exceptions
# =============================================================================


class ZeroTrustError(Exception):
    """Base Zero Trust engine error."""


class ZeroTrustAccessDeniedError(ZeroTrustError):
    """Raised when access is denied."""


class ZeroTrustValidationError(ZeroTrustError):
    """Raised when request or policy is invalid."""


class ZeroTrustPolicyError(ZeroTrustError):
    """Raised when policy configuration is invalid."""


class ZeroTrustRepositoryError(ZeroTrustError):
    """Raised when repository operations fail."""


class ZeroTrustContextError(ZeroTrustError):
    """Raised when required context is missing."""


# =============================================================================
# Enums and config
# =============================================================================


class TrustDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    CHALLENGE = "challenge"
    STEP_UP_MFA = "step_up_mfa"
    RESTRICT = "restrict"
    NOT_APPLICABLE = "not_applicable"
    INDETERMINATE = "indeterminate"


class PolicyEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    CHALLENGE = "challenge"
    STEP_UP_MFA = "step_up_mfa"
    RESTRICT = "restrict"


class RiskLevel(str, Enum):
    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DeviceTrustLevel(str, Enum):
    UNKNOWN = "unknown"
    UNTRUSTED = "untrusted"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MANAGED = "managed"


class IdentityAssuranceLevel(str, Enum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    PHISHING_RESISTANT = "phishing_resistant"


class NetworkTrustLevel(str, Enum):
    UNKNOWN = "unknown"
    UNTRUSTED = "untrusted"
    PUBLIC = "public"
    PARTNER = "partner"
    CORPORATE = "corporate"
    PRIVATE = "private"


class ResourceSensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    CRITICAL = "critical"


class AccessOperation(str, Enum):
    READ = "read"
    WRITE = "write"
    UPDATE = "update"
    DELETE = "delete"
    EXECUTE = "execute"
    EXPORT = "export"
    ADMIN = "admin"
    LIST = "list"
    CUSTOM = "custom"


class CombiningStrategy(str, Enum):
    DENY_OVERRIDES = "deny_overrides"
    RISK_ADAPTIVE = "risk_adaptive"
    MOST_RESTRICTIVE = "most_restrictive"


@dataclass(frozen=True)
class ZeroTrustConfig:
    """Runtime configuration for Zero Trust evaluation."""

    deny_by_default: bool = True
    fail_closed: bool = True
    enable_cache: bool = True
    cache_ttl_seconds: int = 30
    max_cache_entries: int = 10_000
    enable_audit: bool = True
    redact_sensitive_audit_fields: bool = True
    combining_strategy: CombiningStrategy = CombiningStrategy.RISK_ADAPTIVE
    require_authenticated_identity: bool = True
    require_tenant_match: bool = True
    require_device_for_sensitive_access: bool = True
    trust_decay_seconds: int = 900
    max_session_age_seconds: int = 8 * 60 * 60
    challenge_risk_threshold: float = 45.0
    step_up_mfa_risk_threshold: float = 60.0
    deny_risk_threshold: float = 85.0
    minimum_trust_score_for_allow: float = 65.0
    system_principals: Tuple[str, ...] = ("system", "scheduler", "migration-service")
    allow_system_bypass: bool = True
    trusted_cidrs: Tuple[str, ...] = ()
    blocked_cidrs: Tuple[str, ...] = ()
    high_risk_countries: Tuple[str, ...] = ()


# =============================================================================
# Domain models
# =============================================================================


@dataclass(frozen=True)
class IdentityContext:
    """Identity and authentication context."""

    principal_id: str
    username: Optional[str] = None
    tenant_id: Optional[str] = None
    authenticated: bool = True
    assurance_level: IdentityAssuranceLevel = IdentityAssuranceLevel.LOW
    mfa_verified: bool = False
    mfa_method: Optional[str] = None
    roles: Tuple[str, ...] = ()
    groups: Tuple[str, ...] = ()
    scopes: Tuple[str, ...] = ()
    auth_time: Optional[datetime] = None
    last_verified_at: Optional[datetime] = None
    attributes: JsonDict = field(default_factory=dict)

    def is_system(self, config: ZeroTrustConfig) -> bool:
        return self.principal_id in config.system_principals or "system" in self.roles


@dataclass(frozen=True)
class DeviceContext:
    """Device posture context."""

    device_id: Optional[str] = None
    trust_level: DeviceTrustLevel = DeviceTrustLevel.UNKNOWN
    managed: bool = False
    compliant: bool = False
    encrypted_disk: bool = False
    firewall_enabled: bool = False
    edr_enabled: bool = False
    os_name: Optional[str] = None
    os_version: Optional[str] = None
    jailbreak_or_root_detected: bool = False
    last_seen_at: Optional[datetime] = None
    posture_checked_at: Optional[datetime] = None
    attributes: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class NetworkContext:
    """Network and geolocation context."""

    source_ip: Optional[str] = None
    destination_ip: Optional[str] = None
    trust_level: NetworkTrustLevel = NetworkTrustLevel.UNKNOWN
    country: Optional[str] = None
    region: Optional[str] = None
    asn: Optional[str] = None
    vpn_detected: bool = False
    proxy_detected: bool = False
    tor_detected: bool = False
    impossible_travel_detected: bool = False
    attributes: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class SessionContext:
    """Session context for continuous verification."""

    session_id: Optional[str] = None
    created_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    last_reauth_at: Optional[datetime] = None
    risk_score: float = 0.0
    trust_score: float = 50.0
    revoked: bool = False
    attributes: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceContext:
    """Resource being accessed."""

    resource_type: str
    resource_id: Optional[str] = None
    tenant_id: Optional[str] = None
    owner_id: Optional[str] = None
    sensitivity: ResourceSensitivity = ResourceSensitivity.INTERNAL
    classification: Optional[str] = None
    tags: Tuple[str, ...] = ()
    attributes: JsonDict = field(default_factory=dict)

    def canonical(self) -> str:
        return f"{self.resource_type}:{self.resource_id or '*'}"


@dataclass(frozen=True)
class AccessContext:
    """Access request context."""

    operation: AccessOperation
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    user_agent: Optional[str] = None
    workload_id: Optional[str] = None
    service_name: Optional[str] = None
    environment: JsonDict = field(default_factory=dict)
    metadata: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class ZeroTrustRequest:
    """Zero Trust access evaluation request."""

    identity: IdentityContext
    resource: ResourceContext
    access: AccessContext
    device: Optional[DeviceContext] = None
    network: Optional[NetworkContext] = None
    session: Optional[SessionContext] = None

    def validate(self) -> None:
        if not self.identity.principal_id:
            raise ZeroTrustValidationError("principal_id is required.")
        if not self.resource.resource_type:
            raise ZeroTrustValidationError("resource_type is required.")


@dataclass(frozen=True)
class ZeroTrustPolicy:
    """Zero Trust policy rule."""

    policy_id: str
    name: str
    effect: PolicyEffect
    description: str = ""
    enabled: bool = True
    priority: int = 100
    resource_types: Tuple[str, ...] = ("*",)
    operations: Tuple[AccessOperation, ...] = ()
    tenants: Tuple[str, ...] = ()
    roles_any: Tuple[str, ...] = ()
    scopes_all: Tuple[str, ...] = ()
    min_identity_assurance: IdentityAssuranceLevel = IdentityAssuranceLevel.UNKNOWN
    min_device_trust: DeviceTrustLevel = DeviceTrustLevel.UNKNOWN
    min_network_trust: NetworkTrustLevel = NetworkTrustLevel.UNKNOWN
    max_risk_score: Optional[float] = None
    min_trust_score: Optional[float] = None
    resource_sensitivities: Tuple[ResourceSensitivity, ...] = ()
    require_mfa: bool = False
    require_managed_device: bool = False
    require_compliant_device: bool = False
    deny_countries: Tuple[str, ...] = ()
    allow_cidrs: Tuple[str, ...] = ()
    deny_cidrs: Tuple[str, ...] = ()
    metadata: JsonDict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.policy_id:
            raise ZeroTrustPolicyError("policy_id is required.")
        if not self.name:
            raise ZeroTrustPolicyError("policy name is required.")
        for cidr in self.allow_cidrs + self.deny_cidrs:
            ipaddress.ip_network(cidr, strict=False)

    def applies_to(self, request: ZeroTrustRequest, risk_score: float, trust_score: float) -> bool:
        if not self.enabled:
            return False
        if "*" not in self.resource_types and request.resource.resource_type not in self.resource_types:
            return False
        if self.operations and request.access.operation not in self.operations:
            return False
        if self.tenants and request.identity.tenant_id not in self.tenants and request.resource.tenant_id not in self.tenants:
            return False
        if self.resource_sensitivities and request.resource.sensitivity not in self.resource_sensitivities:
            return False
        if self.roles_any and not set(self.roles_any).intersection(request.identity.roles):
            return False
        if self.scopes_all and not set(self.scopes_all).issubset(request.identity.scopes):
            return False
        if assurance_rank(request.identity.assurance_level) < assurance_rank(self.min_identity_assurance):
            return False
        device = request.device or DeviceContext()
        network = request.network or NetworkContext()
        if device_trust_rank(device.trust_level) < device_trust_rank(self.min_device_trust):
            return False
        if network_trust_rank(network.trust_level) < network_trust_rank(self.min_network_trust):
            return False
        if self.max_risk_score is not None and risk_score > self.max_risk_score:
            return False
        if self.min_trust_score is not None and trust_score < self.min_trust_score:
            return False
        if self.require_mfa and not request.identity.mfa_verified:
            return False
        if self.require_managed_device and not device.managed:
            return False
        if self.require_compliant_device and not device.compliant:
            return False
        if network.country and network.country in self.deny_countries:
            return False
        if self.deny_cidrs and any(ip_in_cidr(network.source_ip, cidr) for cidr in self.deny_cidrs):
            return True
        if self.allow_cidrs and not any(ip_in_cidr(network.source_ip, cidr) for cidr in self.allow_cidrs):
            return False
        return True


@dataclass(frozen=True)
class TrustSignal:
    """Individual trust/risk signal."""

    signal_id: str
    name: str
    risk_delta: float = 0.0
    trust_delta: float = 0.0
    reason: str = ""
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "signal_id": self.signal_id,
            "name": self.name,
            "risk_delta": self.risk_delta,
            "trust_delta": self.trust_delta,
            "reason": self.reason,
            "metadata": redact_sensitive(self.metadata),
        }


@dataclass(frozen=True)
class PolicyEvaluation:
    """Single policy evaluation result."""

    policy_id: str
    policy_name: str
    effect: PolicyEffect
    applicable: bool
    reason: str
    priority: int
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "policy_id": self.policy_id,
            "policy_name": self.policy_name,
            "effect": self.effect.value,
            "applicable": self.applicable,
            "reason": self.reason,
            "priority": self.priority,
            "metadata": redact_sensitive(self.metadata),
        }


@dataclass(frozen=True)
class ZeroTrustResult:
    """Final Zero Trust decision."""

    decision: TrustDecision
    allowed: bool
    reason: str
    risk_score: float
    trust_score: float
    risk_level: RiskLevel
    request_id: Optional[str]
    correlation_id: Optional[str]
    principal_id: str
    resource: str
    operation: AccessOperation
    signals: Tuple[TrustSignal, ...] = ()
    policy_evaluations: Tuple[PolicyEvaluation, ...] = ()
    restrictions: Tuple[str, ...] = ()
    required_actions: Tuple[str, ...] = ()
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cache_hit: bool = False
    diagnostics: JsonDict = field(default_factory=dict)

    def require_allowed(self) -> None:
        if not self.allowed:
            raise ZeroTrustAccessDeniedError(self.reason)

    def to_dict(self) -> JsonDict:
        return {
            "decision": self.decision.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "risk_score": self.risk_score,
            "trust_score": self.trust_score,
            "risk_level": self.risk_level.value,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "principal_id": self.principal_id,
            "resource": self.resource,
            "operation": self.operation.value,
            "signals": [s.to_dict() for s in self.signals],
            "policy_evaluations": [p.to_dict() for p in self.policy_evaluations],
            "restrictions": list(self.restrictions),
            "required_actions": list(self.required_actions),
            "evaluated_at": self.evaluated_at.isoformat(),
            "cache_hit": self.cache_hit,
            "diagnostics": redact_sensitive(self.diagnostics),
        }


@dataclass(frozen=True)
class ZeroTrustAuditEvent:
    """Structured audit event for Zero Trust evaluations."""

    event_type: str
    decision: TrustDecision
    allowed: bool
    reason: str
    risk_score: float
    trust_score: float
    principal_id: str
    tenant_id: Optional[str]
    resource: str
    operation: AccessOperation
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self, redact: bool = True) -> JsonDict:
        data = {
            "event_type": self.event_type,
            "decision": self.decision.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "risk_score": self.risk_score,
            "trust_score": self.trust_score,
            "principal_id": self.principal_id,
            "tenant_id": self.tenant_id,
            "resource": self.resource,
            "operation": self.operation.value,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "metadata": dict(self.metadata),
            "timestamp": self.timestamp.isoformat(),
        }
        return redact_sensitive(data) if redact else data


# =============================================================================
# Repositories / audit / cache
# =============================================================================


class ZeroTrustPolicyRepository(ABC):
    """Zero Trust policy repository abstraction."""

    @abstractmethod
    def list_policies(self) -> Sequence[ZeroTrustPolicy]:
        """List policies."""

    @abstractmethod
    def upsert_policy(self, policy: ZeroTrustPolicy) -> None:
        """Create or update policy."""


class InMemoryZeroTrustPolicyRepository(ZeroTrustPolicyRepository):
    """Thread-safe in-memory policy repository."""

    def __init__(self, policies: Optional[Iterable[ZeroTrustPolicy]] = None) -> None:
        self._policies: Dict[str, ZeroTrustPolicy] = {}
        self._lock = threading.RLock()
        for policy in policies or ():
            self.upsert_policy(policy)

    def list_policies(self) -> Sequence[ZeroTrustPolicy]:
        with self._lock:
            return tuple(sorted(self._policies.values(), key=lambda p: (p.priority, p.policy_id)))

    def upsert_policy(self, policy: ZeroTrustPolicy) -> None:
        policy.validate()
        with self._lock:
            self._policies[policy.policy_id] = policy


class ZeroTrustAuditSink(ABC):
    """Audit sink abstraction."""

    @abstractmethod
    def emit(self, event: ZeroTrustAuditEvent) -> None:
        """Emit audit event."""


class LoggingZeroTrustAuditSink(ZeroTrustAuditSink):
    """Logging-backed audit sink."""

    def __init__(self, audit_logger: Optional[logging.Logger] = None, redact: bool = True) -> None:
        self.audit_logger = audit_logger or logging.getLogger("security.zero_trust.audit")
        self.redact = redact

    def emit(self, event: ZeroTrustAuditEvent) -> None:
        level = logging.INFO if event.allowed else logging.WARNING
        self.audit_logger.log(level, "zero_trust_event=%s", json.dumps(event.to_dict(redact=self.redact), sort_keys=True, default=str))


@dataclass
class _DecisionCacheEntry:
    result: ZeroTrustResult
    expires_at: float


class ZeroTrustDecisionCache:
    """TTL cache for Zero Trust decisions."""

    def __init__(self, ttl_seconds: int = 30, max_entries: int = 10_000) -> None:
        self.ttl_seconds = max(0, ttl_seconds)
        self.max_entries = max(1, max_entries)
        self._cache: Dict[str, _DecisionCacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[ZeroTrustResult]:
        now = time.time()
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            if entry.expires_at <= now:
                self._cache.pop(key, None)
                return None
            return dataclasses.replace(entry.result, cache_hit=True)

    def set(self, key: str, result: ZeroTrustResult) -> None:
        if self.ttl_seconds <= 0:
            return
        with self._lock:
            if len(self._cache) >= self.max_entries:
                self._evict()
            self._cache[key] = _DecisionCacheEntry(result=result, expires_at=time.time() + self.ttl_seconds)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _evict(self) -> None:
        now = time.time()
        expired = [k for k, v in self._cache.items() if v.expires_at <= now]
        for key in expired:
            self._cache.pop(key, None)
        if len(self._cache) >= self.max_entries and self._cache:
            oldest = min(self._cache.items(), key=lambda item: item[1].expires_at)[0]
            self._cache.pop(oldest, None)


# =============================================================================
# Risk and trust scoring
# =============================================================================


class TrustScorer:
    """Produces Zero Trust signals and scores."""

    def __init__(self, config: ZeroTrustConfig) -> None:
        self.config = config

    def score(self, request: ZeroTrustRequest) -> Tuple[float, float, Tuple[TrustSignal, ...]]:
        risk = clamp_score(request.session.risk_score if request.session else 0.0)
        trust = clamp_score(request.session.trust_score if request.session else 50.0)
        signals: list[TrustSignal] = []

        identity = request.identity
        device = request.device or DeviceContext()
        network = request.network or NetworkContext()
        session = request.session or SessionContext()
        now = request.access.timestamp

        if not identity.authenticated:
            signals.append(signal("identity-not-authenticated", 90, -60, "Identity is not authenticated."))
        else:
            signals.append(signal("identity-authenticated", -10, 10, "Identity is authenticated."))

        assurance_bonus = {
            IdentityAssuranceLevel.UNKNOWN: (20, -20),
            IdentityAssuranceLevel.LOW: (10, -5),
            IdentityAssuranceLevel.MEDIUM: (-5, 10),
            IdentityAssuranceLevel.HIGH: (-15, 20),
            IdentityAssuranceLevel.PHISHING_RESISTANT: (-25, 30),
        }[identity.assurance_level]
        signals.append(signal("identity-assurance", assurance_bonus[0], assurance_bonus[1], f"Identity assurance is {identity.assurance_level.value}."))

        if identity.mfa_verified:
            signals.append(signal("mfa-verified", -15, 20, "MFA is verified."))
        else:
            signals.append(signal("mfa-missing", 15, -10, "MFA is not verified."))

        if request.resource.tenant_id and identity.tenant_id and request.resource.tenant_id != identity.tenant_id:
            signals.append(signal("tenant-mismatch", 80, -50, "Identity tenant does not match resource tenant."))

        if device.jailbreak_or_root_detected:
            signals.append(signal("device-rooted", 80, -50, "Device appears rooted or jailbroken."))
        if device.managed:
            signals.append(signal("device-managed", -15, 20, "Device is managed."))
        else:
            signals.append(signal("device-unmanaged", 15, -10, "Device is not managed."))
        if device.compliant:
            signals.append(signal("device-compliant", -15, 20, "Device is compliant."))
        else:
            signals.append(signal("device-noncompliant", 20, -15, "Device is not compliant."))

        device_rank = device_trust_rank(device.trust_level)
        signals.append(signal("device-trust-level", 20 - (device_rank * 8), (device_rank * 8) - 10, f"Device trust level is {device.trust_level.value}."))

        if network.source_ip and any(ip_in_cidr(network.source_ip, cidr) for cidr in self.config.blocked_cidrs):
            signals.append(signal("blocked-network", 95, -80, "Source IP belongs to blocked network."))
        if network.source_ip and any(ip_in_cidr(network.source_ip, cidr) for cidr in self.config.trusted_cidrs):
            signals.append(signal("trusted-network", -10, 15, "Source IP belongs to trusted network."))
        if network.tor_detected:
            signals.append(signal("tor-detected", 40, -25, "Tor usage detected."))
        if network.proxy_detected or network.vpn_detected:
            signals.append(signal("proxy-or-vpn", 15, -5, "Proxy or VPN usage detected."))
        if network.impossible_travel_detected:
            signals.append(signal("impossible-travel", 70, -50, "Impossible travel detected."))
        if network.country and network.country in self.config.high_risk_countries:
            signals.append(signal("high-risk-country", 25, -10, "Source country is configured as high risk."))

        sensitivity_risk = {
            ResourceSensitivity.PUBLIC: 0,
            ResourceSensitivity.INTERNAL: 5,
            ResourceSensitivity.CONFIDENTIAL: 20,
            ResourceSensitivity.RESTRICTED: 35,
            ResourceSensitivity.CRITICAL: 50,
        }[request.resource.sensitivity]
        signals.append(signal("resource-sensitivity", sensitivity_risk, -sensitivity_risk / 3, f"Resource sensitivity is {request.resource.sensitivity.value}."))

        if request.access.operation in {AccessOperation.DELETE, AccessOperation.EXPORT, AccessOperation.ADMIN}:
            signals.append(signal("sensitive-operation", 20, -10, f"Sensitive operation requested: {request.access.operation.value}."))

        if session.revoked:
            signals.append(signal("session-revoked", 100, -100, "Session is revoked."))
        if session.created_at:
            age = (now - ensure_aware(session.created_at)).total_seconds()
            if age > self.config.max_session_age_seconds:
                signals.append(signal("session-too-old", 40, -30, "Session age exceeds maximum."))
        if session.last_reauth_at:
            reauth_age = (now - ensure_aware(session.last_reauth_at)).total_seconds()
            if reauth_age > self.config.trust_decay_seconds:
                decay = min(30.0, reauth_age / self.config.trust_decay_seconds * 10.0)
                signals.append(signal("trust-decay", decay, -decay, "Session trust decayed since last re-authentication."))

        for item in signals:
            risk += item.risk_delta
            trust += item.trust_delta

        return clamp_score(risk), clamp_score(trust), tuple(signals)


# =============================================================================
# Zero Trust Engine
# =============================================================================


class ZeroTrustEngine:
    """Enterprise Zero Trust evaluation engine."""

    def __init__(
        self,
        policy_repository: Optional[ZeroTrustPolicyRepository] = None,
        audit_sink: Optional[ZeroTrustAuditSink] = None,
        config: Optional[ZeroTrustConfig] = None,
        cache: Optional[ZeroTrustDecisionCache] = None,
        scorer: Optional[TrustScorer] = None,
    ) -> None:
        self.config = config or ZeroTrustConfig()
        self.policy_repository = policy_repository or InMemoryZeroTrustPolicyRepository(default_zero_trust_policies())
        self.audit_sink = audit_sink or LoggingZeroTrustAuditSink(redact=self.config.redact_sensitive_audit_fields)
        self.cache = cache or ZeroTrustDecisionCache(self.config.cache_ttl_seconds, self.config.max_cache_entries)
        self.scorer = scorer or TrustScorer(self.config)

    def evaluate(self, request: ZeroTrustRequest) -> ZeroTrustResult:
        """Evaluate a Zero Trust access request."""
        request.validate()
        cache_key = self._cache_key(request)
        if self.config.enable_cache:
            cached = self.cache.get(cache_key)
            if cached:
                return cached

        try:
            result = self._evaluate(request)
        except Exception as exc:
            logger.exception("Zero Trust evaluation failed. request_id=%s", request.access.request_id)
            if self.config.fail_closed:
                result = self._build_result(
                    request=request,
                    decision=TrustDecision.DENY,
                    allowed=False,
                    reason="Zero Trust evaluation failed; fail-closed deny applied.",
                    risk_score=100.0,
                    trust_score=0.0,
                    signals=(signal("evaluation-error", 100, -100, str(exc), {"error_type": type(exc).__name__}),),
                    policy_evaluations=(),
                    required_actions=("investigate_evaluation_error",),
                )
            else:
                result = self._build_result(
                    request=request,
                    decision=TrustDecision.INDETERMINATE,
                    allowed=False,
                    reason="Zero Trust evaluation failed.",
                    risk_score=50.0,
                    trust_score=0.0,
                    signals=(),
                    policy_evaluations=(),
                    diagnostics={"error": str(exc), "error_type": type(exc).__name__},
                )

        if self.config.enable_cache:
            self.cache.set(cache_key, result)
        if self.config.enable_audit:
            self._audit(request, result)
        return result

    def require(self, request: ZeroTrustRequest) -> None:
        self.evaluate(request).require_allowed()

    def can_access(self, request: ZeroTrustRequest) -> bool:
        return self.evaluate(request).allowed

    def upsert_policy(self, policy: ZeroTrustPolicy) -> None:
        self.policy_repository.upsert_policy(policy)
        self.cache.clear()

    def clear_cache(self) -> None:
        self.cache.clear()

    def _evaluate(self, request: ZeroTrustRequest) -> ZeroTrustResult:
        if self.config.require_authenticated_identity and not request.identity.authenticated:
            return self._build_result(request, TrustDecision.DENY, False, "Identity is not authenticated.", 100.0, 0.0)

        if request.identity.is_system(self.config) and self.config.allow_system_bypass:
            return self._build_result(request, TrustDecision.ALLOW, True, "System principal bypass allowed.", 0.0, 100.0)

        risk_score, trust_score, signals = self.scorer.score(request)

        if self.config.require_tenant_match and request.resource.tenant_id and request.identity.tenant_id and request.resource.tenant_id != request.identity.tenant_id:
            return self._build_result(request, TrustDecision.DENY, False, "Tenant boundary mismatch.", risk_score, trust_score, signals=signals)

        if self.config.require_device_for_sensitive_access and request.resource.sensitivity in {ResourceSensitivity.RESTRICTED, ResourceSensitivity.CRITICAL}:
            if request.device is None or request.device.trust_level in {DeviceTrustLevel.UNKNOWN, DeviceTrustLevel.UNTRUSTED}:
                return self._build_result(
                    request,
                    TrustDecision.STEP_UP_MFA,
                    False,
                    "Sensitive resource requires trusted device posture.",
                    risk_score,
                    trust_score,
                    signals=signals,
                    required_actions=("verify_device_posture", "step_up_mfa"),
                )

        policy_evaluations = self._evaluate_policies(request, risk_score, trust_score)
        policy_decision = self._combine_policies(policy_evaluations)
        if policy_decision:
            decision, reason = policy_decision
            return self._decision_result(request, decision, reason, risk_score, trust_score, signals, policy_evaluations)

        if risk_score >= self.config.deny_risk_threshold:
            return self._build_result(request, TrustDecision.DENY, False, "Risk score exceeds deny threshold.", risk_score, trust_score, signals=signals, policy_evaluations=policy_evaluations)
        if risk_score >= self.config.step_up_mfa_risk_threshold:
            return self._build_result(request, TrustDecision.STEP_UP_MFA, False, "Risk score requires step-up MFA.", risk_score, trust_score, signals=signals, policy_evaluations=policy_evaluations, required_actions=("step_up_mfa",))
        if risk_score >= self.config.challenge_risk_threshold:
            return self._build_result(request, TrustDecision.CHALLENGE, False, "Risk score requires challenge.", risk_score, trust_score, signals=signals, policy_evaluations=policy_evaluations, required_actions=("challenge",))
        if trust_score < self.config.minimum_trust_score_for_allow:
            return self._build_result(request, TrustDecision.RESTRICT, False, "Trust score below allow threshold.", risk_score, trust_score, signals=signals, policy_evaluations=policy_evaluations, restrictions=("read_only", "no_export"))

        if self.config.deny_by_default and not policy_evaluations:
            return self._build_result(request, TrustDecision.DENY, False, "No applicable policy; deny-by-default applied.", risk_score, trust_score, signals=signals)

        return self._build_result(request, TrustDecision.ALLOW, True, "Zero Trust adaptive evaluation allowed access.", risk_score, trust_score, signals=signals, policy_evaluations=policy_evaluations)

    def _evaluate_policies(self, request: ZeroTrustRequest, risk_score: float, trust_score: float) -> Tuple[PolicyEvaluation, ...]:
        evaluations: list[PolicyEvaluation] = []
        for policy in self.policy_repository.list_policies():
            applicable = policy.applies_to(request, risk_score, trust_score)
            evaluations.append(PolicyEvaluation(
                policy_id=policy.policy_id,
                policy_name=policy.name,
                effect=policy.effect,
                applicable=applicable,
                reason="Policy applicable." if applicable else "Policy conditions did not match.",
                priority=policy.priority,
                metadata=dict(policy.metadata),
            ))
        return tuple(evaluations)

    def _combine_policies(self, evaluations: Sequence[PolicyEvaluation]) -> Optional[Tuple[TrustDecision, str]]:
        applicable = tuple(e for e in evaluations if e.applicable)
        if not applicable:
            return None
        deny = tuple(e for e in applicable if e.effect == PolicyEffect.DENY)
        if self.config.combining_strategy in {CombiningStrategy.DENY_OVERRIDES, CombiningStrategy.RISK_ADAPTIVE} and deny:
            return TrustDecision.DENY, f"Policy deny applied: {deny[0].policy_name}."
        if self.config.combining_strategy == CombiningStrategy.MOST_RESTRICTIVE:
            selected = sorted(applicable, key=lambda e: (effect_restrictiveness(e.effect), e.priority), reverse=True)[0]
        else:
            selected = sorted(applicable, key=lambda e: (effect_restrictiveness(e.effect), -e.priority), reverse=True)[0]
        return effect_to_decision(selected.effect), f"Policy applied: {selected.policy_name}."

    def _decision_result(
        self,
        request: ZeroTrustRequest,
        decision: TrustDecision,
        reason: str,
        risk_score: float,
        trust_score: float,
        signals: Tuple[TrustSignal, ...],
        policy_evaluations: Tuple[PolicyEvaluation, ...],
    ) -> ZeroTrustResult:
        allowed = decision == TrustDecision.ALLOW
        restrictions: Tuple[str, ...] = ()
        required_actions: Tuple[str, ...] = ()
        if decision == TrustDecision.CHALLENGE:
            required_actions = ("challenge",)
        elif decision == TrustDecision.STEP_UP_MFA:
            required_actions = ("step_up_mfa",)
        elif decision == TrustDecision.RESTRICT:
            restrictions = ("read_only", "no_export")
        return self._build_result(request, decision, allowed, reason, risk_score, trust_score, signals, policy_evaluations, restrictions, required_actions)

    def _build_result(
        self,
        request: ZeroTrustRequest,
        decision: TrustDecision,
        allowed: bool,
        reason: str,
        risk_score: float,
        trust_score: float,
        signals: Tuple[TrustSignal, ...] = (),
        policy_evaluations: Tuple[PolicyEvaluation, ...] = (),
        restrictions: Tuple[str, ...] = (),
        required_actions: Tuple[str, ...] = (),
        diagnostics: Optional[Mapping[str, Any]] = None,
    ) -> ZeroTrustResult:
        return ZeroTrustResult(
            decision=decision,
            allowed=allowed,
            reason=reason,
            risk_score=clamp_score(risk_score),
            trust_score=clamp_score(trust_score),
            risk_level=risk_level_for_score(risk_score),
            request_id=request.access.request_id,
            correlation_id=request.access.correlation_id,
            principal_id=request.identity.principal_id,
            resource=request.resource.canonical(),
            operation=request.access.operation,
            signals=signals,
            policy_evaluations=policy_evaluations,
            restrictions=restrictions,
            required_actions=required_actions,
            diagnostics=dict(diagnostics or {}),
        )

    def _cache_key(self, request: ZeroTrustRequest) -> str:
        payload = {
            "identity": dataclasses.asdict(request.identity),
            "resource": dataclasses.asdict(request.resource),
            "access": dataclasses.asdict(request.access),
            "device": dataclasses.asdict(request.device) if request.device else None,
            "network": dataclasses.asdict(request.network) if request.network else None,
            "session": dataclasses.asdict(request.session) if request.session else None,
        }
        return hashlib.sha256(json.dumps(canonicalize(payload), sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _audit(self, request: ZeroTrustRequest, result: ZeroTrustResult) -> None:
        try:
            event = ZeroTrustAuditEvent(
                event_type="zero_trust.evaluated",
                decision=result.decision,
                allowed=result.allowed,
                reason=result.reason,
                risk_score=result.risk_score,
                trust_score=result.trust_score,
                principal_id=request.identity.principal_id,
                tenant_id=request.identity.tenant_id or request.resource.tenant_id,
                resource=request.resource.canonical(),
                operation=request.access.operation,
                request_id=request.access.request_id,
                correlation_id=request.access.correlation_id,
                metadata={
                    "risk_level": result.risk_level.value,
                    "resource_sensitivity": request.resource.sensitivity.value,
                    "identity_assurance": request.identity.assurance_level.value,
                    "device_trust": (request.device.trust_level.value if request.device else None),
                    "network_trust": (request.network.trust_level.value if request.network else None),
                    "required_actions": list(result.required_actions),
                    "restrictions": list(result.restrictions),
                    "signals": [s.to_dict() for s in result.signals],
                },
            )
            self.audit_sink.emit(event)
        except Exception:
            logger.exception("Failed to emit Zero Trust audit event.")


# =============================================================================
# Decorators/helpers
# =============================================================================


class ZeroTrustContextProvider(ABC):
    """Extract Zero Trust request from decorated function arguments."""

    @abstractmethod
    def build_request(self, args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> ZeroTrustRequest:
        """Build Zero Trust request."""


class KeywordZeroTrustContextProvider(ZeroTrustContextProvider):
    """Context provider expecting keyword arguments identity/resource/access."""

    def __init__(self, identity_key: str = "identity", resource_key: str = "resource", access_key: str = "access") -> None:
        self.identity_key = identity_key
        self.resource_key = resource_key
        self.access_key = access_key

    def build_request(self, args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> ZeroTrustRequest:
        identity = kwargs.get(self.identity_key)
        resource = kwargs.get(self.resource_key)
        access = kwargs.get(self.access_key)
        if not isinstance(identity, IdentityContext):
            raise ZeroTrustContextError(f"{self.identity_key} must be IdentityContext.")
        if not isinstance(resource, ResourceContext):
            raise ZeroTrustContextError(f"{self.resource_key} must be ResourceContext.")
        if not isinstance(access, AccessContext):
            raise ZeroTrustContextError(f"{self.access_key} must be AccessContext.")
        return ZeroTrustRequest(
            identity=identity,
            resource=resource,
            access=access,
            device=kwargs.get("device") if isinstance(kwargs.get("device"), DeviceContext) else None,
            network=kwargs.get("network") if isinstance(kwargs.get("network"), NetworkContext) else None,
            session=kwargs.get("session") if isinstance(kwargs.get("session"), SessionContext) else None,
        )


def requires_zero_trust(engine: ZeroTrustEngine, provider: Optional[ZeroTrustContextProvider] = None) -> Callable[[F], F]:
    """Decorator enforcing Zero Trust before function execution."""
    context_provider = provider or KeywordZeroTrustContextProvider()

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            request = context_provider.build_request(args, kwargs)
            engine.require(request)
            return func(*args, **kwargs)

        return cast(F, wrapper)

    return decorator


# =============================================================================
# Defaults and utility functions
# =============================================================================


def default_zero_trust_policies() -> Tuple[ZeroTrustPolicy, ...]:
    return (
        ZeroTrustPolicy(
            policy_id="zt-deny-critical-without-mfa",
            name="Deny critical access without MFA",
            effect=PolicyEffect.STEP_UP_MFA,
            description="Critical resources require MFA.",
            priority=10,
            resource_sensitivities=(ResourceSensitivity.CRITICAL, ResourceSensitivity.RESTRICTED),
            require_mfa=True,
        ),
        ZeroTrustPolicy(
            policy_id="zt-deny-admin-without-high-assurance",
            name="Admin requires high assurance",
            effect=PolicyEffect.STEP_UP_MFA,
            description="Administrative operations require high identity assurance and MFA.",
            priority=15,
            operations=(AccessOperation.ADMIN, AccessOperation.DELETE, AccessOperation.EXPORT),
            min_identity_assurance=IdentityAssuranceLevel.HIGH,
            require_mfa=True,
        ),
        ZeroTrustPolicy(
            policy_id="zt-allow-low-risk-managed-device",
            name="Allow low-risk managed device access",
            effect=PolicyEffect.ALLOW,
            description="Allow access when risk is low and device is managed/compliant.",
            priority=100,
            min_device_trust=DeviceTrustLevel.MEDIUM,
            min_identity_assurance=IdentityAssuranceLevel.MEDIUM,
            max_risk_score=45,
            min_trust_score=65,
            require_compliant_device=True,
        ),
        ZeroTrustPolicy(
            policy_id="zt-restrict-confidential-unmanaged-device",
            name="Restrict confidential access from unmanaged devices",
            effect=PolicyEffect.RESTRICT,
            description="Confidential resources are restricted when device posture is not strong.",
            priority=50,
            resource_sensitivities=(ResourceSensitivity.CONFIDENTIAL, ResourceSensitivity.RESTRICTED, ResourceSensitivity.CRITICAL),
            min_device_trust=DeviceTrustLevel.HIGH,
        ),
    )


def signal(name: str, risk_delta: float, trust_delta: float, reason: str, metadata: Optional[Mapping[str, Any]] = None) -> TrustSignal:
    return TrustSignal(str(uuid.uuid4()), name, risk_delta, trust_delta, reason, dict(metadata or {}))


def clamp_score(value: float) -> float:
    try:
        numeric = float(value)
    except Exception:
        return 0.0
    if not math.isfinite(numeric):
        return 0.0
    return max(0.0, min(100.0, numeric))


def risk_level_for_score(score: float) -> RiskLevel:
    score = clamp_score(score)
    if score >= 90:
        return RiskLevel.CRITICAL
    if score >= 70:
        return RiskLevel.HIGH
    if score >= 45:
        return RiskLevel.MEDIUM
    if score >= 20:
        return RiskLevel.LOW
    return RiskLevel.VERY_LOW


def assurance_rank(level: IdentityAssuranceLevel) -> int:
    return {
        IdentityAssuranceLevel.UNKNOWN: 0,
        IdentityAssuranceLevel.LOW: 1,
        IdentityAssuranceLevel.MEDIUM: 2,
        IdentityAssuranceLevel.HIGH: 3,
        IdentityAssuranceLevel.PHISHING_RESISTANT: 4,
    }[level]


def device_trust_rank(level: DeviceTrustLevel) -> int:
    return {
        DeviceTrustLevel.UNKNOWN: 0,
        DeviceTrustLevel.UNTRUSTED: 0,
        DeviceTrustLevel.LOW: 1,
        DeviceTrustLevel.MEDIUM: 2,
        DeviceTrustLevel.HIGH: 3,
        DeviceTrustLevel.MANAGED: 4,
    }[level]


def network_trust_rank(level: NetworkTrustLevel) -> int:
    return {
        NetworkTrustLevel.UNKNOWN: 0,
        NetworkTrustLevel.UNTRUSTED: 0,
        NetworkTrustLevel.PUBLIC: 1,
        NetworkTrustLevel.PARTNER: 2,
        NetworkTrustLevel.CORPORATE: 3,
        NetworkTrustLevel.PRIVATE: 4,
    }[level]


def effect_restrictiveness(effect: PolicyEffect) -> int:
    return {
        PolicyEffect.ALLOW: 1,
        PolicyEffect.RESTRICT: 2,
        PolicyEffect.CHALLENGE: 3,
        PolicyEffect.STEP_UP_MFA: 4,
        PolicyEffect.DENY: 5,
    }[effect]


def effect_to_decision(effect: PolicyEffect) -> TrustDecision:
    return {
        PolicyEffect.ALLOW: TrustDecision.ALLOW,
        PolicyEffect.DENY: TrustDecision.DENY,
        PolicyEffect.CHALLENGE: TrustDecision.CHALLENGE,
        PolicyEffect.STEP_UP_MFA: TrustDecision.STEP_UP_MFA,
        PolicyEffect.RESTRICT: TrustDecision.RESTRICT,
    }[effect]


def ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def ip_in_cidr(ip_value: Optional[str], cidr: str) -> bool:
    if not ip_value:
        return False
    try:
        return ipaddress.ip_address(ip_value) in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False


def canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): canonicalize(value[k]) for k in sorted(value.keys(), key=str)}
    if isinstance(value, (list, tuple, set)):
        return [canonicalize(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def redact_sensitive(data: Mapping[str, Any]) -> JsonDict:
    sensitive_terms = (
        "password", "secret", "token", "api_key", "apikey", "authorization",
        "credential", "private_key", "session_cookie", "cookie",
    )

    def walk(value: Any) -> Any:
        if isinstance(value, Mapping):
            output: JsonDict = {}
            for key, item in value.items():
                if any(term in str(key).lower() for term in sensitive_terms):
                    output[str(key)] = "***REDACTED***"
                else:
                    output[str(key)] = walk(item)
            return output
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, tuple):
            return tuple(walk(item) for item in value)
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    return walk(dict(data))


def create_default_zero_trust_engine() -> ZeroTrustEngine:
    return ZeroTrustEngine()


__all__ = [
    "AccessContext",
    "AccessOperation",
    "CombiningStrategy",
    "DeviceContext",
    "DeviceTrustLevel",
    "IdentityAssuranceLevel",
    "IdentityContext",
    "InMemoryZeroTrustPolicyRepository",
    "LoggingZeroTrustAuditSink",
    "NetworkContext",
    "NetworkTrustLevel",
    "PolicyEffect",
    "PolicyEvaluation",
    "ResourceContext",
    "ResourceSensitivity",
    "RiskLevel",
    "SessionContext",
    "TrustDecision",
    "TrustScorer",
    "TrustSignal",
    "ZeroTrustAccessDeniedError",
    "ZeroTrustAuditEvent",
    "ZeroTrustAuditSink",
    "ZeroTrustConfig",
    "ZeroTrustContextError",
    "ZeroTrustDecisionCache",
    "ZeroTrustEngine",
    "ZeroTrustError",
    "ZeroTrustPolicy",
    "ZeroTrustPolicyError",
    "ZeroTrustPolicyRepository",
    "ZeroTrustRepositoryError",
    "ZeroTrustRequest",
    "ZeroTrustResult",
    "ZeroTrustValidationError",
    "assurance_rank",
    "canonicalize",
    "clamp_score",
    "create_default_zero_trust_engine",
    "default_zero_trust_policies",
    "device_trust_rank",
    "effect_restrictiveness",
    "effect_to_decision",
    "ensure_aware",
    "ip_in_cidr",
    "network_trust_rank",
    "redact_sensitive",
    "requires_zero_trust",
    "risk_level_for_score",
    "signal",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    engine = create_default_zero_trust_engine()
    request = ZeroTrustRequest(
        identity=IdentityContext(
            principal_id="user-001",
            username="analyst@example.com",
            tenant_id="default",
            authenticated=True,
            assurance_level=IdentityAssuranceLevel.HIGH,
            mfa_verified=True,
            roles=("analyst",),
            scopes=("datasets:read",),
            auth_time=datetime.now(timezone.utc),
            last_verified_at=datetime.now(timezone.utc),
        ),
        device=DeviceContext(
            device_id="device-001",
            trust_level=DeviceTrustLevel.HIGH,
            managed=True,
            compliant=True,
            encrypted_disk=True,
            firewall_enabled=True,
            edr_enabled=True,
        ),
        network=NetworkContext(
            source_ip="10.0.0.10",
            trust_level=NetworkTrustLevel.CORPORATE,
            country="BR",
        ),
        session=SessionContext(
            session_id="sess-001",
            created_at=datetime.now(timezone.utc),
            last_reauth_at=datetime.now(timezone.utc),
            trust_score=75,
        ),
        resource=ResourceContext(
            resource_type="dataset",
            resource_id="ds-001",
            tenant_id="default",
            sensitivity=ResourceSensitivity.CONFIDENTIAL,
        ),
        access=AccessContext(
            operation=AccessOperation.READ,
            request_id="req-demo",
            correlation_id="corr-demo",
            service_name="demo-service",
        ),
    )
    result = engine.evaluate(request)
    print(json.dumps(result.to_dict(), indent=2, default=str))

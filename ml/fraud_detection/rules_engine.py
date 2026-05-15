# ml/fraud_detection/rules_engine.py
"""
Enterprise Fraud Rules Engine.

Recursos:
- Motor de regras versionado
- DSL declarativa para condições
- Operadores AND/OR/NOT
- Regras por tenant, canal, país, moeda e categoria
- Severidade e score de risco
- Explicações/reason codes
- Hot reload via JSON
- Avaliação realtime e batch
"""

from __future__ import annotations

import json
import operator
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence


class RuleSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RuleAction(str, Enum):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"
    ESCALATE = "escalate"


class RuleStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    TESTING = "testing"
    ARCHIVED = "archived"


class ConditionOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    EXISTS = "exists"
    BETWEEN = "between"
    REGEX = "regex"


@dataclass(frozen=True)
class RuleCondition:
    field: str
    operator: ConditionOperator
    value: Any = None


@dataclass(frozen=True)
class RuleExpression:
    all: Sequence[RuleCondition | "RuleExpression"] = field(default_factory=list)
    any: Sequence[RuleCondition | "RuleExpression"] = field(default_factory=list)
    not_: Optional[RuleCondition | "RuleExpression"] = None


@dataclass(frozen=True)
class FraudRule:
    rule_id: str
    name: str
    description: str
    version: str
    status: RuleStatus
    severity: RuleSeverity
    action: RuleAction
    risk_score: float
    expression: RuleExpression
    priority: int = 100
    tenant_ids: Sequence[str] = field(default_factory=list)
    tags: Dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    name: str
    version: str
    severity: RuleSeverity
    action: RuleAction
    risk_score: float
    priority: int
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleEvaluationContext:
    tenant_id: Optional[str] = None
    environment: str = "production"
    correlation_id: Optional[str] = None
    request_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleEvaluationResult:
    evaluation_id: str
    evaluated_at: str
    matched: bool
    total_rules: int
    matched_rules: List[RuleMatch]
    final_action: RuleAction
    total_risk_score: float
    max_severity: RuleSeverity
    context: RuleEvaluationContext
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)


class RulesEngineError(RuntimeError):
    pass


class FieldResolver:
    @staticmethod
    def resolve(payload: Mapping[str, Any], path: str) -> Any:
        current: Any = payload

        for part in path.split("."):
            if isinstance(current, Mapping):
                current = current.get(part)
            else:
                return None

        return current


class ConditionEvaluator:
    OPERATORS: Dict[ConditionOperator, Callable[[Any, Any], bool]] = {
        ConditionOperator.EQ: operator.eq,
        ConditionOperator.NE: operator.ne,
        ConditionOperator.GT: lambda a, b: a is not None and a > b,
        ConditionOperator.GTE: lambda a, b: a is not None and a >= b,
        ConditionOperator.LT: lambda a, b: a is not None and a < b,
        ConditionOperator.LTE: lambda a, b: a is not None and a <= b,
        ConditionOperator.IN: lambda a, b: a in b if b is not None else False,
        ConditionOperator.NOT_IN: lambda a, b: a not in b if b is not None else True,
        ConditionOperator.CONTAINS: lambda a, b: b in a if a is not None else False,
        ConditionOperator.EXISTS: lambda a, b: a is not None,
        ConditionOperator.BETWEEN: lambda a, b: a is not None and b[0] <= a <= b[1],
    }

    def evaluate(self, condition: RuleCondition, payload: Mapping[str, Any]) -> bool:
        value = FieldResolver.resolve(payload, condition.field)

        if condition.operator == ConditionOperator.REGEX:
            import re

            return bool(value is not None and re.search(str(condition.value), str(value)))

        fn = self.OPERATORS.get(condition.operator)
        if not fn:
            raise RulesEngineError(f"Operador não suportado: {condition.operator}")

        try:
            return bool(fn(value, condition.value))
        except Exception:
            return False


class ExpressionEvaluator:
    def __init__(self) -> None:
        self.condition_evaluator = ConditionEvaluator()

    def evaluate(self, expression: RuleExpression | RuleCondition, payload: Mapping[str, Any]) -> bool:
        if isinstance(expression, RuleCondition):
            return self.condition_evaluator.evaluate(expression, payload)

        if expression.all:
            if not all(self.evaluate(item, payload) for item in expression.all):
                return False

        if expression.any:
            if not any(self.evaluate(item, payload) for item in expression.any):
                return False

        if expression.not_ is not None:
            if self.evaluate(expression.not_, payload):
                return False

        return True


class FraudRulesEngine:
    SEVERITY_RANK = {
        RuleSeverity.INFO: 0,
        RuleSeverity.LOW: 1,
        RuleSeverity.MEDIUM: 2,
        RuleSeverity.HIGH: 3,
        RuleSeverity.CRITICAL: 4,
    }

    ACTION_RANK = {
        RuleAction.ALLOW: 0,
        RuleAction.REVIEW: 1,
        RuleAction.ESCALATE: 2,
        RuleAction.BLOCK: 3,
    }

    def __init__(self, rules: Optional[Sequence[FraudRule]] = None) -> None:
        self._rules: Dict[str, FraudRule] = {}
        self._lock = RLock()
        self.evaluator = ExpressionEvaluator()

        for rule in rules or []:
            self.add_rule(rule)

    def add_rule(self, rule: FraudRule) -> None:
        self._validate_rule(rule)

        with self._lock:
            self._rules[rule.rule_id] = rule

    def remove_rule(self, rule_id: str) -> None:
        with self._lock:
            self._rules.pop(rule_id, None)

    def list_rules(self, include_disabled: bool = True) -> List[FraudRule]:
        with self._lock:
            rules = list(self._rules.values())

        if not include_disabled:
            rules = [r for r in rules if r.status == RuleStatus.ENABLED]

        return sorted(rules, key=lambda r: r.priority)

    def evaluate(
        self,
        payload: Mapping[str, Any],
        context: Optional[RuleEvaluationContext] = None,
    ) -> RuleEvaluationResult:
        context = context or RuleEvaluationContext()

        with self._lock:
            rules = list(self._rules.values())

        active_rules = [
            rule
            for rule in rules
            if rule.status in {RuleStatus.ENABLED, RuleStatus.TESTING}
            and self._tenant_allowed(rule, context.tenant_id)
        ]

        matches: List[RuleMatch] = []

        for rule in sorted(active_rules, key=lambda r: r.priority):
            matched = self.evaluator.evaluate(rule.expression, payload)

            if matched:
                matches.append(
                    RuleMatch(
                        rule_id=rule.rule_id,
                        name=rule.name,
                        version=rule.version,
                        severity=rule.severity,
                        action=rule.action,
                        risk_score=max(0.0, min(1.0, rule.risk_score)),
                        priority=rule.priority,
                        reason=rule.description,
                        evidence={
                            "tags": rule.tags,
                            "metadata": rule.metadata,
                            "testing": rule.status == RuleStatus.TESTING,
                        },
                    )
                )

        return RuleEvaluationResult(
            evaluation_id=str(uuid.uuid4()),
            evaluated_at=datetime.now(timezone.utc).isoformat(),
            matched=bool(matches),
            total_rules=len(active_rules),
            matched_rules=matches,
            final_action=self._final_action(matches),
            total_risk_score=self._total_score(matches),
            max_severity=self._max_severity(matches),
            context=context,
        )

    def evaluate_batch(
        self,
        payloads: Sequence[Mapping[str, Any]],
        context: Optional[RuleEvaluationContext] = None,
    ) -> List[RuleEvaluationResult]:
        return [self.evaluate(payload, context) for payload in payloads]

    def save_json(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            payload = [self._rule_to_dict(rule) for rule in self._rules.values()]

        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return target

    def load_json(self, path: str | Path, replace: bool = True) -> None:
        source = Path(path)

        if not source.exists():
            raise RulesEngineError(f"Arquivo de regras não encontrado: {source}")

        raw = json.loads(source.read_text(encoding="utf-8"))
        rules = [self._rule_from_dict(item) for item in raw]

        with self._lock:
            if replace:
                self._rules.clear()

            for rule in rules:
                self._validate_rule(rule)
                self._rules[rule.rule_id] = rule

    def hot_reload(self, path: str | Path) -> None:
        self.load_json(path, replace=True)

    def _validate_rule(self, rule: FraudRule) -> None:
        if not rule.rule_id:
            raise RulesEngineError("rule_id obrigatório.")
        if not rule.name:
            raise RulesEngineError("name obrigatório.")
        if rule.risk_score < 0 or rule.risk_score > 1:
            raise RulesEngineError("risk_score precisa estar entre 0 e 1.")

    def _tenant_allowed(self, rule: FraudRule, tenant_id: Optional[str]) -> bool:
        if not rule.tenant_ids:
            return True
        return tenant_id in set(rule.tenant_ids)

    def _final_action(self, matches: Sequence[RuleMatch]) -> RuleAction:
        if not matches:
            return RuleAction.ALLOW

        return max(matches, key=lambda m: self.ACTION_RANK[m.action]).action

    def _total_score(self, matches: Sequence[RuleMatch]) -> float:
        if not matches:
            return 0.0

        score = 1.0
        for match in matches:
            score *= 1.0 - match.risk_score

        return round(1.0 - score, 6)

    def _max_severity(self, matches: Sequence[RuleMatch]) -> RuleSeverity:
        if not matches:
            return RuleSeverity.INFO

        return max(matches, key=lambda m: self.SEVERITY_RANK[m.severity]).severity

    def _rule_to_dict(self, rule: FraudRule) -> Dict[str, Any]:
        return asdict(rule)

    def _rule_from_dict(self, raw: Mapping[str, Any]) -> FraudRule:
        return FraudRule(
            rule_id=raw["rule_id"],
            name=raw["name"],
            description=raw["description"],
            version=raw["version"],
            status=RuleStatus(raw["status"]),
            severity=RuleSeverity(raw["severity"]),
            action=RuleAction(raw["action"]),
            risk_score=float(raw["risk_score"]),
            expression=self._expression_from_dict(raw["expression"]),
            priority=int(raw.get("priority", 100)),
            tenant_ids=list(raw.get("tenant_ids", [])),
            tags=dict(raw.get("tags", {})),
            created_at=raw.get("created_at", datetime.now(timezone.utc).isoformat()),
            updated_at=raw.get("updated_at", datetime.now(timezone.utc).isoformat()),
            metadata=dict(raw.get("metadata", {})),
        )

    def _expression_from_dict(self, raw: Mapping[str, Any]) -> RuleExpression:
        return RuleExpression(
            all=[
                self._condition_or_expression(item)
                for item in raw.get("all", [])
            ],
            any=[
                self._condition_or_expression(item)
                for item in raw.get("any", [])
            ],
            not_=self._condition_or_expression(raw["not_"]) if raw.get("not_") else None,
        )

    def _condition_or_expression(self, raw: Mapping[str, Any]) -> RuleCondition | RuleExpression:
        if "field" in raw:
            return RuleCondition(
                field=raw["field"],
                operator=ConditionOperator(raw["operator"]),
                value=raw.get("value"),
            )

        return self._expression_from_dict(raw)


def default_enterprise_rules() -> List[FraudRule]:
    return [
        FraudRule(
            rule_id="FRD-AMOUNT-EXTREME",
            name="Extreme transaction amount",
            description="Transação com valor extremamente alto.",
            version="1.0.0",
            status=RuleStatus.ENABLED,
            severity=RuleSeverity.CRITICAL,
            action=RuleAction.BLOCK,
            risk_score=0.85,
            priority=10,
            expression=RuleExpression(
                all=[
                    RuleCondition("transaction.amount", ConditionOperator.GTE, 20_000),
                ]
            ),
            tags={"domain": "amount", "type": "hard_rule"},
        ),
        FraudRule(
            rule_id="FRD-VELOCITY-HIGH",
            name="High transaction velocity",
            description="Alta quantidade ou volume de transações em 24h.",
            version="1.0.0",
            status=RuleStatus.ENABLED,
            severity=RuleSeverity.HIGH,
            action=RuleAction.REVIEW,
            risk_score=0.45,
            priority=20,
            expression=RuleExpression(
                any=[
                    RuleCondition("profile.transaction_count_24h", ConditionOperator.GTE, 8),
                    RuleCondition("profile.transaction_amount_24h", ConditionOperator.GTE, 30_000),
                ]
            ),
            tags={"domain": "velocity"},
        ),
        FraudRule(
            rule_id="FRD-GEO-DEVICE-ANOMALY",
            name="Unknown country and device",
            description="País e dispositivo desconhecidos para o usuário.",
            version="1.0.0",
            status=RuleStatus.ENABLED,
            severity=RuleSeverity.HIGH,
            action=RuleAction.ESCALATE,
            risk_score=0.55,
            priority=30,
            expression=RuleExpression(
                all=[
                    RuleCondition("features.unknown_country", ConditionOperator.EQ, 1),
                    RuleCondition("features.unknown_device", ConditionOperator.EQ, 1),
                ]
            ),
            tags={"domain": "identity"},
        ),
        FraudRule(
            rule_id="FRD-CHARGEBACK-HISTORY",
            name="User chargeback history",
            description="Usuário possui histórico relevante de chargebacks.",
            version="1.0.0",
            status=RuleStatus.ENABLED,
            severity=RuleSeverity.MEDIUM,
            action=RuleAction.REVIEW,
            risk_score=0.35,
            priority=40,
            expression=RuleExpression(
                all=[
                    RuleCondition("profile.chargeback_count", ConditionOperator.GTE, 2),
                ]
            ),
            tags={"domain": "user_history"},
        ),
    ]


def build_payload(
    *,
    transaction: Mapping[str, Any],
    profile: Optional[Mapping[str, Any]] = None,
    features: Optional[Mapping[str, Any]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "transaction": dict(transaction),
        "profile": dict(profile or {}),
        "features": dict(features or {}),
        "metadata": dict(metadata or {}),
    }


if __name__ == "__main__":
    engine = FraudRulesEngine(default_enterprise_rules())

    payload = build_payload(
        transaction={
            "transaction_id": "tx-001",
            "amount": 25_000,
            "currency": "BRL",
            "country": "XX",
        },
        profile={
            "transaction_count_24h": 12,
            "transaction_amount_24h": 40_000,
            "chargeback_count": 3,
        },
        features={
            "unknown_country": 1,
            "unknown_device": 1,
        },
    )

    result = engine.evaluate(
        payload,
        RuleEvaluationContext(
            tenant_id="digital-meta",
            environment="development",
            request_id="req-001",
        ),
    )

    print(result.to_json())
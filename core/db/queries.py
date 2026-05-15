#!/usr/bin/env python3
"""
core/db/queries.py

Enterprise-grade SQL query utilities.

Objetivo:
- Centralizar construção segura de queries SQL para Postgres/Supabase/PostgREST adapters.
- Padronizar filtros, paginação, ordenação, whitelists de campos, parâmetros nomeados,
  templates de queries, auditoria e helpers comuns de CRUD.
- Evitar SQL injection por validação rígida de identificadores e uso de parâmetros.

Uso:
    from core.db.queries import SelectQuery, Filter, QueryOperator

    query = (
        SelectQuery(table="transactions")
        .select("id", "amount", "created_at")
        .where(Filter("amount", QueryOperator.GTE, 100))
        .order_by("created_at", "desc")
        .paginate(limit=100, offset=0)
    )

    sql, params = query.compile()

Notas:
- Este módulo não executa SQL. Ele apenas monta SQL + parâmetros.
- A execução fica em repositories/adapters, como asyncpg, psycopg, SQLAlchemy, Supabase etc.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union


QUERIES_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DOTTED_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


class QueryOperator(str, Enum):
    EQ = "="
    NE = "<>"
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    LIKE = "LIKE"
    ILIKE = "ILIKE"
    IN = "IN"
    NOT_IN = "NOT IN"
    IS_NULL = "IS NULL"
    IS_NOT_NULL = "IS NOT NULL"
    BETWEEN = "BETWEEN"
    JSON_CONTAINS = "@>"


class SortDirection(str, Enum):
    ASC = "ASC"
    DESC = "DESC"


class JoinType(str, Enum):
    INNER = "INNER JOIN"
    LEFT = "LEFT JOIN"
    RIGHT = "RIGHT JOIN"
    FULL = "FULL JOIN"


class ConflictAction(str, Enum):
    NOTHING = "DO NOTHING"
    UPDATE = "DO UPDATE"


@dataclass(frozen=True)
class SqlParam:
    name: str
    value: Any


@dataclass(frozen=True)
class Filter:
    field: str
    operator: QueryOperator
    value: Any = None
    value_to: Any = None


@dataclass(frozen=True)
class Sort:
    field: str
    direction: SortDirection = SortDirection.DESC
    nulls_last: bool = True


@dataclass(frozen=True)
class Join:
    table: str
    on: str
    join_type: JoinType = JoinType.LEFT
    alias: Optional[str] = None


@dataclass(frozen=True)
class Pagination:
    limit: int = 100
    offset: int = 0

    def validate(self, max_limit: int = 10_000) -> "Pagination":
        if self.limit < 1 or self.limit > max_limit:
            raise QueryValidationError(f"limit deve estar entre 1 e {max_limit}")
        if self.offset < 0:
            raise QueryValidationError("offset não pode ser negativo")
        return self


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    params: Dict[str, Any]
    query_id: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class QueryError(Exception):
    """Base query error."""


class QueryValidationError(QueryError):
    """Invalid query input."""


class QueryBuilder:
    def __init__(self) -> None:
        self.params: Dict[str, Any] = {}
        self._param_counter = 0

    def new_param(self, value: Any, prefix: str = "p") -> str:
        self._param_counter += 1
        name = f"{prefix}_{self._param_counter}"
        self.params[name] = value
        return f"%({name})s"

    def reset_params(self) -> None:
        self.params = {}
        self._param_counter = 0


class SelectQuery(QueryBuilder):
    def __init__(self, table: str, alias: Optional[str] = None) -> None:
        super().__init__()
        self.table = validate_identifier(table, allow_schema=True)
        self.alias = validate_identifier(alias) if alias else None
        self.columns: List[str] = ["*"]
        self.filters: List[Filter] = []
        self.sorts: List[Sort] = []
        self.joins: List[Join] = []
        self.group_fields: List[str] = []
        self.having_filters: List[Filter] = []
        self.pagination: Optional[Pagination] = None
        self.distinct_enabled = False

    def select(self, *columns: str) -> "SelectQuery":
        if columns:
            self.columns = [validate_select_expression(column) for column in columns]
        return self

    def distinct(self, enabled: bool = True) -> "SelectQuery":
        self.distinct_enabled = enabled
        return self

    def join(self, table: str, on: str, join_type: JoinType = JoinType.LEFT, alias: Optional[str] = None) -> "SelectQuery":
        self.joins.append(Join(table=validate_identifier(table, allow_schema=True), on=validate_join_condition(on), join_type=join_type, alias=validate_identifier(alias) if alias else None))
        return self

    def where(self, *filters: Filter) -> "SelectQuery":
        self.filters.extend(filters)
        return self

    def order_by(self, field: str, direction: Union[str, SortDirection] = SortDirection.DESC, nulls_last: bool = True) -> "SelectQuery":
        direction_value = direction if isinstance(direction, SortDirection) else SortDirection(str(direction).upper())
        self.sorts.append(Sort(validate_identifier(field, allow_schema=True), direction_value, nulls_last))
        return self

    def group_by(self, *fields: str) -> "SelectQuery":
        self.group_fields.extend(validate_identifier(field, allow_schema=True) for field in fields)
        return self

    def having(self, *filters: Filter) -> "SelectQuery":
        self.having_filters.extend(filters)
        return self

    def paginate(self, limit: int = 100, offset: int = 0) -> "SelectQuery":
        self.pagination = Pagination(limit=limit, offset=offset).validate()
        return self

    def compile(self) -> CompiledQuery:
        self.reset_params()
        table_sql = quote_dotted_identifier(self.table)
        if self.alias:
            table_sql += f" AS {quote_identifier(self.alias)}"
        distinct_sql = "DISTINCT " if self.distinct_enabled else ""
        sql_parts = [f"SELECT {distinct_sql}{', '.join(self.columns)} FROM {table_sql}"]
        for join in self.joins:
            join_table = quote_dotted_identifier(join.table)
            if join.alias:
                join_table += f" AS {quote_identifier(join.alias)}"
            sql_parts.append(f"{join.join_type.value} {join_table} ON {join.on}")
        if self.filters:
            sql_parts.append("WHERE " + " AND ".join(self._compile_filter(item) for item in self.filters))
        if self.group_fields:
            sql_parts.append("GROUP BY " + ", ".join(quote_dotted_identifier(item) for item in self.group_fields))
        if self.having_filters:
            sql_parts.append("HAVING " + " AND ".join(self._compile_filter(item) for item in self.having_filters))
        if self.sorts:
            order_parts = []
            for sort in self.sorts:
                nulls = " NULLS LAST" if sort.nulls_last else ""
                order_parts.append(f"{quote_dotted_identifier(sort.field)} {sort.direction.value}{nulls}")
            sql_parts.append("ORDER BY " + ", ".join(order_parts))
        if self.pagination:
            sql_parts.append(f"LIMIT {self.new_param(self.pagination.limit, 'limit')} OFFSET {self.new_param(self.pagination.offset, 'offset')}")
        sql = " ".join(sql_parts)
        return CompiledQuery(sql=sql, params=dict(self.params), query_id=query_id(sql, self.params), metadata={"type": "select", "table": self.table})

    def _compile_filter(self, item: Filter) -> str:
        field = quote_dotted_identifier(validate_identifier(item.field, allow_schema=True))
        op = item.operator
        if op in {QueryOperator.IS_NULL, QueryOperator.IS_NOT_NULL}:
            return f"{field} {op.value}"
        if op == QueryOperator.BETWEEN:
            if item.value is None or item.value_to is None:
                raise QueryValidationError("BETWEEN requer value e value_to")
            return f"{field} BETWEEN {self.new_param(item.value)} AND {self.new_param(item.value_to)}"
        if op in {QueryOperator.IN, QueryOperator.NOT_IN}:
            if not isinstance(item.value, (list, tuple, set)):
                raise QueryValidationError("IN/NOT_IN requer lista")
            values = list(item.value)
            if not values:
                return "FALSE" if op == QueryOperator.IN else "TRUE"
            placeholders = ", ".join(self.new_param(value) for value in values)
            return f"{field} {op.value} ({placeholders})"
        return f"{field} {op.value} {self.new_param(item.value)}"


class InsertQuery(QueryBuilder):
    def __init__(self, table: str) -> None:
        super().__init__()
        self.table = validate_identifier(table, allow_schema=True)
        self.rows: List[Dict[str, Any]] = []
        self.returning_fields: List[str] = []
        self.conflict_fields: List[str] = []
        self.conflict_action: Optional[ConflictAction] = None
        self.update_fields: List[str] = []

    def values(self, rows: Union[Mapping[str, Any], Sequence[Mapping[str, Any]]]) -> "InsertQuery":
        if isinstance(rows, Mapping):
            self.rows = [dict(rows)]
        else:
            self.rows = [dict(row) for row in rows]
        if not self.rows:
            raise QueryValidationError("insert requer pelo menos uma linha")
        return self

    def returning(self, *fields: str) -> "InsertQuery":
        self.returning_fields = [validate_identifier(field, allow_schema=True) for field in fields]
        return self

    def on_conflict(self, fields: Sequence[str], action: ConflictAction = ConflictAction.NOTHING, update_fields: Optional[Sequence[str]] = None) -> "InsertQuery":
        self.conflict_fields = [validate_identifier(field) for field in fields]
        self.conflict_action = action
        self.update_fields = [validate_identifier(field) for field in update_fields or []]
        return self

    def compile(self) -> CompiledQuery:
        self.reset_params()
        if not self.rows:
            raise QueryValidationError("insert sem linhas")
        columns = list(self.rows[0].keys())
        for column in columns:
            validate_identifier(column)
        for row in self.rows:
            if set(row.keys()) != set(columns):
                raise QueryValidationError("todas as linhas do insert devem ter as mesmas colunas")
        values_sql = []
        for row in self.rows:
            values_sql.append("(" + ", ".join(self.new_param(row[column]) for column in columns) + ")")
        sql = f"INSERT INTO {quote_dotted_identifier(self.table)} ({', '.join(quote_identifier(column) for column in columns)}) VALUES {', '.join(values_sql)}"
        if self.conflict_action:
            if not self.conflict_fields:
                raise QueryValidationError("on_conflict requer fields")
            sql += f" ON CONFLICT ({', '.join(quote_identifier(field) for field in self.conflict_fields)}) {self.conflict_action.value}"
            if self.conflict_action == ConflictAction.UPDATE:
                fields = self.update_fields or [column for column in columns if column not in self.conflict_fields]
                assignments = ", ".join(f"{quote_identifier(field)} = EXCLUDED.{quote_identifier(field)}" for field in fields)
                sql += f" SET {assignments}"
        if self.returning_fields:
            sql += " RETURNING " + ", ".join(quote_dotted_identifier(field) for field in self.returning_fields)
        return CompiledQuery(sql=sql, params=dict(self.params), query_id=query_id(sql, self.params), metadata={"type": "insert", "table": self.table, "rows": len(self.rows)})


class UpdateQuery(QueryBuilder):
    def __init__(self, table: str) -> None:
        super().__init__()
        self.table = validate_identifier(table, allow_schema=True)
        self.payload: Dict[str, Any] = {}
        self.filters: List[Filter] = []
        self.returning_fields: List[str] = []

    def set(self, values: Mapping[str, Any]) -> "UpdateQuery":
        self.payload = dict(values)
        if not self.payload:
            raise QueryValidationError("update requer payload")
        for field_name in self.payload:
            validate_identifier(field_name)
        return self

    def where(self, *filters: Filter) -> "UpdateQuery":
        self.filters.extend(filters)
        return self

    def returning(self, *fields: str) -> "UpdateQuery":
        self.returning_fields = [validate_identifier(field, allow_schema=True) for field in fields]
        return self

    def compile(self) -> CompiledQuery:
        self.reset_params()
        if not self.payload:
            raise QueryValidationError("update sem payload")
        if not self.filters:
            raise QueryValidationError("update sem WHERE bloqueado por segurança")
        assignments = ", ".join(f"{quote_identifier(key)} = {self.new_param(value)}" for key, value in self.payload.items())
        select_helper = SelectQuery(self.table)
        select_helper.params = self.params
        select_helper._param_counter = self._param_counter
        where_sql = " AND ".join(select_helper._compile_filter(item) for item in self.filters)
        self.params = select_helper.params
        self._param_counter = select_helper._param_counter
        sql = f"UPDATE {quote_dotted_identifier(self.table)} SET {assignments} WHERE {where_sql}"
        if self.returning_fields:
            sql += " RETURNING " + ", ".join(quote_dotted_identifier(field) for field in self.returning_fields)
        return CompiledQuery(sql=sql, params=dict(self.params), query_id=query_id(sql, self.params), metadata={"type": "update", "table": self.table})


class DeleteQuery(QueryBuilder):
    def __init__(self, table: str) -> None:
        super().__init__()
        self.table = validate_identifier(table, allow_schema=True)
        self.filters: List[Filter] = []
        self.returning_fields: List[str] = []

    def where(self, *filters: Filter) -> "DeleteQuery":
        self.filters.extend(filters)
        return self

    def returning(self, *fields: str) -> "DeleteQuery":
        self.returning_fields = [validate_identifier(field, allow_schema=True) for field in fields]
        return self

    def compile(self) -> CompiledQuery:
        self.reset_params()
        if not self.filters:
            raise QueryValidationError("delete sem WHERE bloqueado por segurança")
        select_helper = SelectQuery(self.table)
        where_sql = " AND ".join(select_helper._compile_filter(item) for item in self.filters)
        self.params = select_helper.params
        sql = f"DELETE FROM {quote_dotted_identifier(self.table)} WHERE {where_sql}"
        if self.returning_fields:
            sql += " RETURNING " + ", ".join(quote_dotted_identifier(field) for field in self.returning_fields)
        return CompiledQuery(sql=sql, params=dict(self.params), query_id=query_id(sql, self.params), metadata={"type": "delete", "table": self.table})


@dataclass(frozen=True)
class QueryTemplate:
    name: str
    sql: str
    required_params: Sequence[str] = field(default_factory=list)
    optional_params: Sequence[str] = field(default_factory=list)
    description: str = ""

    def render(self, params: Mapping[str, Any]) -> CompiledQuery:
        missing = [item for item in self.required_params if item not in params]
        if missing:
            raise QueryValidationError(f"Parâmetros obrigatórios ausentes: {', '.join(missing)}")
        allowed = set(self.required_params) | set(self.optional_params)
        clean_params = {key: value for key, value in params.items() if key in allowed}
        return CompiledQuery(sql=self.sql, params=clean_params, query_id=query_id(self.sql, clean_params), metadata={"type": "template", "name": self.name})


QUERY_TEMPLATES: Dict[str, QueryTemplate] = {
    "health_check": QueryTemplate(
        name="health_check",
        sql="SELECT 1 AS ok, NOW() AS checked_at",
        description="Database liveness query",
    ),
    "audit_insert": QueryTemplate(
        name="audit_insert",
        sql=(
            "INSERT INTO audit_logs (audit_id, actor, action, entity_type, entity_id, metadata, created_at) "
            "VALUES (%(audit_id)s, %(actor)s, %(action)s, %(entity_type)s, %(entity_id)s, %(metadata)s, NOW())"
        ),
        required_params=["audit_id", "actor", "action", "entity_type", "entity_id", "metadata"],
        description="Insert audit log event",
    ),
}


def get_template(name: str) -> QueryTemplate:
    template = QUERY_TEMPLATES.get(name)
    if template is None:
        raise QueryValidationError(f"Template não encontrado: {name}")
    return template


def register_template(template: QueryTemplate, overwrite: bool = False) -> None:
    if template.name in QUERY_TEMPLATES and not overwrite:
        raise QueryValidationError(f"Template já existe: {template.name}")
    QUERY_TEMPLATES[template.name] = template


def soft_delete_query(table: str, id_field: str, id_value: Any, actor: Optional[str] = None) -> CompiledQuery:
    payload = {"deleted_at": datetime.now(tz=DEFAULT_TIMEZONE), "is_deleted": True}
    if actor:
        payload["deleted_by"] = actor
    return UpdateQuery(table).set(payload).where(Filter(id_field, QueryOperator.EQ, id_value)).returning("*").compile()


def audit_insert_query(actor: str, action: str, entity_type: str, entity_id: str, metadata: Optional[Mapping[str, Any]] = None) -> CompiledQuery:
    return get_template("audit_insert").render({
        "audit_id": f"aud_{uuid.uuid4().hex[:20]}",
        "actor": actor,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "metadata": dict(metadata or {}),
    })


def count_query(table: str, filters: Optional[Sequence[Filter]] = None) -> CompiledQuery:
    query = SelectQuery(table).select("COUNT(*) AS total")
    if filters:
        query.where(*filters)
    return query.compile()


def exists_query(table: str, filters: Sequence[Filter]) -> CompiledQuery:
    return SelectQuery(table).select("1").where(*filters).paginate(1, 0).compile()


def validate_identifier(value: Optional[str], allow_schema: bool = False) -> str:
    if value is None or not str(value).strip():
        raise QueryValidationError("Identificador vazio")
    text = str(value).strip()
    pattern = _DOTTED_IDENTIFIER_RE if allow_schema else _IDENTIFIER_RE
    if not pattern.match(text):
        raise QueryValidationError(f"Identificador inválido: {value}")
    return text


def validate_select_expression(value: str) -> str:
    text = str(value).strip()
    if text == "*":
        return text
    # Allow controlled aggregate/expression aliases used internally, still block semicolons/comments.
    if ";" in text or "--" in text or "/*" in text or "*/" in text:
        raise QueryValidationError(f"Expressão SELECT inválida: {value}")
    allowed = re.compile(r"^[A-Za-z0-9_.*(),\s]+(?:\s+AS\s+[A-Za-z_][A-Za-z0-9_]*)?$", re.IGNORECASE)
    if not allowed.match(text):
        raise QueryValidationError(f"Expressão SELECT inválida: {value}")
    return text


def validate_join_condition(value: str) -> str:
    text = str(value).strip()
    if not text or ";" in text or "--" in text or "/*" in text or "*/" in text:
        raise QueryValidationError("Condição JOIN inválida")
    allowed = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*\s*=\s*[A-Za-z_][A-Za-z0-9_.]*$")
    if not allowed.match(text):
        raise QueryValidationError("Condição JOIN deve ser no formato tabela.campo = tabela.campo")
    return text


def quote_identifier(value: str) -> str:
    validate_identifier(value)
    return '"' + value.replace('"', '""') + '"'


def quote_dotted_identifier(value: str) -> str:
    validate_identifier(value, allow_schema=True)
    return ".".join(quote_identifier(part) for part in value.split("."))


def query_id(sql: str, params: Mapping[str, Any]) -> str:
    raw = sql + "|" + repr(sorted(params.items()))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def queries_metadata() -> Dict[str, Any]:
    return {
        "version": QUERIES_VERSION,
        "templates": sorted(QUERY_TEMPLATES.keys()),
        "operators": [item.name for item in QueryOperator],
    }


__all__ = [
    "QUERIES_VERSION",
    "QueryOperator",
    "SortDirection",
    "JoinType",
    "ConflictAction",
    "SqlParam",
    "Filter",
    "Sort",
    "Join",
    "Pagination",
    "CompiledQuery",
    "QueryError",
    "QueryValidationError",
    "QueryBuilder",
    "SelectQuery",
    "InsertQuery",
    "UpdateQuery",
    "DeleteQuery",
    "QueryTemplate",
    "QUERY_TEMPLATES",
    "get_template",
    "register_template",
    "soft_delete_query",
    "audit_insert_query",
    "count_query",
    "exists_query",
    "validate_identifier",
    "quote_identifier",
    "quote_dotted_identifier",
    "queries_metadata",
]

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


IGNORE_DIRS = {
    ".git",
    ".github",
    ".next",
    ".vercel",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".venv",
    "venv",
    "reports",
    "governance/reports",
}

FRONTEND_EXTS = {".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte"}
CODE_EXTS = {".ts", ".tsx", ".js", ".jsx", ".py", ".sql"}
SQL_EXTS = {".sql"}

SUPABASE_TABLE_PATTERNS = [
    r"\.from\(\s*['\"]([^'\"]+)['\"]\s*\)",
    r"supabase\s*\.\s*from\(\s*['\"]([^'\"]+)['\"]\s*\)",
]

SUPABASE_RPC_PATTERNS = [
    r"\.rpc\(\s*['\"]([^'\"]+)['\"]\s*\)",
    r"supabase\s*\.\s*rpc\(\s*['\"]([^'\"]+)['\"]\s*\)",
]

SQL_TABLE_PATTERNS = [
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?([a-zA-Z_][a-zA-Z0-9_]*)",
    r"alter\s+table\s+(?:if\s+exists\s+)?(?:public\.)?([a-zA-Z_][a-zA-Z0-9_]*)",
]

SQL_POLICY_PATTERNS = [
    r"create\s+policy\s+['\"]?([^'\"]+)['\"]?\s+on\s+(?:public\.)?([a-zA-Z_][a-zA-Z0-9_]*)",
]

SQL_INDEX_PATTERNS = [
    r"create\s+(?:unique\s+)?index\s+(?:if\s+not\s+exists\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s+on\s+(?:public\.)?([a-zA-Z_][a-zA-Z0-9_]*)",
]

AUTH_PATTERNS = [
    r"useAuth\s*\(",
    r"requireAuth",
    r"ProtectedRoute",
    r"PrivateRoute",
    r"session",
    r"getUser\s*\(",
    r"auth\.getUser",
    r"auth\.getSession",
]

MOCK_PATTERNS = [
    r"mockData",
    r"mock\s*:",
    r"fakeData",
    r"dummyData",
    r"sampleData",
    r"TODO",
    r"FIXME",
    r"placeholder",
    r"hardcoded",
    r"console\.log",
]

DB_CONNECTION_PATTERNS = [
    r"supabase",
    r"\.from\(",
    r"\.rpc\(",
    r"fetch\(",
    r"axios\.",
    r"useQuery",
    r"useMutation",
    r"api/",
    r"edge_function",
]


@dataclass
class Evidence:
    file: str
    line: int
    snippet: str


@dataclass
class Finding:
    severity: str
    category: str
    title: str
    file: str
    line: int
    evidence: str
    recommendation: str


@dataclass
class AuditReport:
    project_root: str
    generated_at: str
    summary: dict[str, Any]
    findings: list[Finding] = field(default_factory=list)
    sql_tables: list[str] = field(default_factory=list)
    supabase_tables_used: list[str] = field(default_factory=list)
    supabase_rpcs_used: list[str] = field(default_factory=list)
    orphan_sql_tables: list[str] = field(default_factory=list)
    duplicated_temporal_tables: dict[str, list[str]] = field(default_factory=dict)
    table_policy_map: dict[str, int] = field(default_factory=dict)
    table_index_map: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": self.project_root,
            "generated_at": self.generated_at,
            "summary": self.summary,
            "findings": [asdict(item) for item in self.findings],
            "sql_tables": self.sql_tables,
            "supabase_tables_used": self.supabase_tables_used,
            "supabase_rpcs_used": self.supabase_rpcs_used,
            "orphan_sql_tables": self.orphan_sql_tables,
            "duplicated_temporal_tables": self.duplicated_temporal_tables,
            "table_policy_map": self.table_policy_map,
            "table_index_map": self.table_index_map,
        }


def should_ignore(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    ignored = {item.lower() for item in IGNORE_DIRS}
    return bool(parts & ignored)


def iter_files(root: Path, extensions: set[str]) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in extensions and not should_ignore(path):
            yield path


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def line_number(text: str, index: int) -> int:
    return text[:index].count("\n") + 1


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def normalize_table(name: str) -> str:
    return name.strip().lower()


def extract_sql_metadata(root: Path) -> tuple[set[str], dict[str, int], dict[str, int]]:
    tables: set[str] = set()
    policies: dict[str, int] = {}
    indexes: dict[str, int] = {}

    for path in iter_files(root, SQL_EXTS):
        content = read_text(path)

        for pattern in SQL_TABLE_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                table = normalize_table(match.group(1))
                tables.add(table)

        for pattern in SQL_POLICY_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                table = normalize_table(match.group(2))
                policies[table] = policies.get(table, 0) + 1

        for pattern in SQL_INDEX_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                table = normalize_table(match.group(2))
                indexes[table] = indexes.get(table, 0) + 1

    return tables, policies, indexes


def extract_supabase_usage(root: Path) -> tuple[dict[str, list[Evidence]], dict[str, list[Evidence]]]:
    tables: dict[str, list[Evidence]] = {}
    rpcs: dict[str, list[Evidence]] = {}

    for path in iter_files(root, CODE_EXTS):
        content = read_text(path)
        relative = rel(path, root)

        for pattern in SUPABASE_TABLE_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                table = normalize_table(match.group(1))
                tables.setdefault(table, []).append(
                    Evidence(
                        file=relative,
                        line=line_number(content, match.start()),
                        snippet=match.group(0)[:180],
                    )
                )

        for pattern in SUPABASE_RPC_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                rpc = normalize_table(match.group(1))
                rpcs.setdefault(rpc, []).append(
                    Evidence(
                        file=relative,
                        line=line_number(content, match.start()),
                        snippet=match.group(0)[:180],
                    )
                )

    return tables, rpcs


def detect_frontend_candidates(root: Path) -> list[Path]:
    bases = [
        root / "app",
        root / "pages",
        root / "src" / "app",
        root / "src" / "pages",
        root / "src" / "routes",
        root / "src" / "components",
        root / "src" / "modules",
        root / "src" / "features",
    ]

    found: list[Path] = []

    for base in bases:
        if not base.exists():
            continue

        for path in iter_files(base, FRONTEND_EXTS):
            relative = str(path).replace("\\", "/")
            name = path.name.lower()

            if (
                "page." in name
                or "dashboard" in name
                or "menu" in relative.lower()
                or "sidebar" in relative.lower()
                or "nav" in relative.lower()
                or "/pages/" in relative.lower()
                or "/app/" in relative.lower()
                or "/routes/" in relative.lower()
            ):
                found.append(path)

    return sorted(set(found))


def detect_service_candidates(root: Path) -> list[Path]:
    bases = [
        root / "services",
        root / "src" / "services",
        root / "src" / "api",
        root / "src" / "lib",
        root / "src" / "modules",
        root / "src" / "features",
        root / "supabase" / "edge_function",
        root / "supabase" / "functions",
    ]

    found: list[Path] = []

    for base in bases:
        if not base.exists():
            continue

        for path in iter_files(base, FRONTEND_EXTS):
            relative = str(path).replace("\\", "/").lower()

            if any(token in relative for token in ["service", "api", "repository", "crud", "edge_function", "function"]):
                found.append(path)

    return sorted(set(found))


def has_pattern(content: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, content, re.IGNORECASE) for pattern in patterns)


def referenced_tables(content: str) -> set[str]:
    refs: set[str] = set()
    for pattern in SUPABASE_TABLE_PATTERNS:
        for match in re.finditer(pattern, content, re.IGNORECASE):
            refs.add(normalize_table(match.group(1)))
    return refs


def temporal_base_name(table: str) -> str:
    return re.sub(r"_20\d{2}_\d{2}_\d{2}$", "", table)


def is_temporal_table(table: str) -> bool:
    return bool(re.search(r"_20\d{2}_\d{2}_\d{2}$", table))


def detect_temporal_duplicates(tables: Iterable[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}

    for table in tables:
        if is_temporal_table(table):
            grouped.setdefault(temporal_base_name(table), []).append(table)

    return {
        base: sorted(items)
        for base, items in grouped.items()
        if len(items) >= 2
    }


def add_finding(
    findings: list[Finding],
    severity: str,
    category: str,
    title: str,
    file: str,
    line: int,
    evidence: str,
    recommendation: str,
) -> None:
    findings.append(
        Finding(
            severity=severity,
            category=category,
            title=title,
            file=file,
            line=line,
            evidence=evidence,
            recommendation=recommendation,
        )
    )


def run_audit(root: Path) -> AuditReport:
    sql_tables, table_policy_map, table_index_map = extract_sql_metadata(root)
    supabase_tables, supabase_rpcs = extract_supabase_usage(root)

    frontend_candidates = detect_frontend_candidates(root)
    service_candidates = detect_service_candidates(root)

    findings: list[Finding] = []

    for path in frontend_candidates:
        content = read_text(path)
        relative = rel(path, root)

        if not has_pattern(content, DB_CONNECTION_PATTERNS):
            add_finding(
                findings,
                "high",
                "frontend_without_datasource",
                "Página/menu sem conexão detectável com Supabase/API",
                relative,
                1,
                "Não foi encontrado supabase, fetch, axios, useQuery, useMutation ou API.",
                "Conectar esta página a um service/repository real ou remover do menu se for módulo não ativo.",
            )

        if has_pattern(content, MOCK_PATTERNS):
            add_finding(
                findings,
                "medium",
                "mock_or_placeholder",
                "Possível uso de mock, placeholder ou TODO no frontend",
                relative,
                1,
                "Foram encontrados padrões como mockData, fakeData, TODO, FIXME, placeholder ou console.log.",
                "Substituir mocks por queries reais, criar tabela/service ou marcar explicitamente como demo.",
            )

        if not has_pattern(content, AUTH_PATTERNS) and any(
            token in relative.lower()
            for token in ["dashboard", "admin", "settings", "billing", "finance", "payroll", "security", "iam"]
        ):
            add_finding(
                findings,
                "high",
                "frontend_without_auth_guard",
                "Página sensível sem proteção de autenticação detectável",
                relative,
                1,
                "Não foi detectado useAuth, ProtectedRoute, session ou auth.getUser.",
                "Adicionar guarda de autenticação/autorização e validar tenant_id antes de carregar dados.",
            )

        for table in referenced_tables(content):
            if sql_tables and table not in sql_tables:
                add_finding(
                    findings,
                    "critical",
                    "table_reference_without_migration",
                    f"Frontend referencia tabela sem migration SQL: {table}",
                    relative,
                    1,
                    f"Referência Supabase encontrada para `{table}`, mas tabela não apareceu nos arquivos SQL.",
                    f"Criar migration para `{table}` ou corrigir nome da tabela no frontend.",
                )

    for path in service_candidates:
        content = read_text(path)
        relative = rel(path, root)

        if not has_pattern(content, DB_CONNECTION_PATTERNS):
            add_finding(
                findings,
                "high",
                "service_without_datasource",
                "Service/API sem conexão detectável com Supabase/API/banco",
                relative,
                1,
                "Arquivo parece service/api/repository/edge function, mas não tem datasource detectável.",
                "Conectar ao Supabase/PostgreSQL, delegar para repository real ou remover se estiver obsoleto.",
            )

        for table in referenced_tables(content):
            if sql_tables and table not in sql_tables:
                add_finding(
                    findings,
                    "critical",
                    "service_table_reference_without_migration",
                    f"Service referencia tabela sem migration SQL: {table}",
                    relative,
                    1,
                    f"Referência Supabase encontrada para `{table}`, mas tabela não apareceu nos arquivos SQL.",
                    f"Criar migration Supabase para `{table}` ou corrigir nome da tabela no service.",
                )

    orphan_sql_tables = sorted(sql_tables - set(supabase_tables.keys()))

    for table in orphan_sql_tables:
        if table in {"public"}:
            continue

        severity = "medium"
        if not is_temporal_table(table):
            severity = "high"

        add_finding(
            findings,
            severity,
            "orphan_sql_table",
            f"Tabela SQL sem uso detectado no código: {table}",
            "supabase/migrations",
            1,
            f"A tabela `{table}` existe em SQL, mas não foi encontrada em `.from('{table}')`.",
            "Confirmar se é tabela futura, obsoleta ou se falta service/frontend usando esta tabela.",
        )

    duplicated_temporal_tables = detect_temporal_duplicates(sql_tables)

    for base, tables in duplicated_temporal_tables.items():
        add_finding(
            findings,
            "critical",
            "temporal_table_duplication",
            f"Tabelas versionadas duplicadas para domínio: {base}",
            "supabase/migrations",
            1,
            ", ".join(tables[:20]),
            f"Consolidar em tabela canônica `{base}` e usar colunas created_at, updated_at, schema_version e migration_version.",
        )

    for table in sorted(sql_tables):
        if table in {"public"}:
            continue

        if table_policy_map.get(table, 0) == 0 and not table.startswith(("schema_", "pg_", "qa_")):
            add_finding(
                findings,
                "high",
                "missing_rls_policy",
                f"Tabela sem policy RLS detectada: {table}",
                "supabase/migrations",
                1,
                f"Nenhum CREATE POLICY encontrado para `{table}`.",
                "Criar policies RLS por tenant_id/organization_id e perfis de acesso.",
            )

        if table_index_map.get(table, 0) == 0 and not table.startswith(("schema_", "qa_")):
            add_finding(
                findings,
                "medium",
                "missing_index",
                f"Tabela sem índice explícito detectado: {table}",
                "supabase/migrations",
                1,
                f"Nenhum CREATE INDEX encontrado para `{table}`.",
                "Criar índices para tenant_id, organization_id, created_at, status e foreign keys principais.",
            )

    severity_count = {
        "critical": sum(1 for item in findings if item.severity == "critical"),
        "high": sum(1 for item in findings if item.severity == "high"),
        "medium": sum(1 for item in findings if item.severity == "medium"),
        "low": sum(1 for item in findings if item.severity == "low"),
    }

    summary = {
        "sql_tables_found": len(sql_tables),
        "supabase_tables_used": len(supabase_tables),
        "supabase_rpcs_used": len(supabase_rpcs),
        "frontend_candidates_scanned": len(frontend_candidates),
        "service_candidates_scanned": len(service_candidates),
        "orphan_sql_tables": len(orphan_sql_tables),
        "temporal_duplicate_groups": len(duplicated_temporal_tables),
        "findings": len(findings),
        "critical": severity_count["critical"],
        "high": severity_count["high"],
        "medium": severity_count["medium"],
        "low": severity_count["low"],
        "architecture_status": "failed" if severity_count["critical"] > 0 else "warning" if findings else "passed",
    }

    return AuditReport(
        project_root=str(root),
        generated_at=datetime.now(timezone.utc).isoformat(),
        summary=summary,
        findings=findings,
        sql_tables=sorted(sql_tables),
        supabase_tables_used=sorted(supabase_tables.keys()),
        supabase_rpcs_used=sorted(supabase_rpcs.keys()),
        orphan_sql_tables=orphan_sql_tables,
        duplicated_temporal_tables=duplicated_temporal_tables,
        table_policy_map=dict(sorted(table_policy_map.items())),
        table_index_map=dict(sorted(table_index_map.items())),
    )


def render_markdown(report: AuditReport) -> str:
    status = report.summary["architecture_status"]
    icon = "✅" if status == "passed" else "⚠️" if status == "warning" else "❌"

    lines = [
        "# Enterprise Architecture Audit",
        "",
        f"**Status:** {icon} `{status.upper()}`",
        f"**Generated at:** `{report.generated_at}`",
        f"**Project root:** `{report.project_root}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]

    for key, value in report.summary.items():
        lines.append(f"| `{key}` | `{value}` |")

    lines.extend([
        "",
        "## Critical and High Findings",
        "",
        "| Severity | Category | Title | File | Recommendation |",
        "|---|---|---|---|---|",
    ])

    selected = [
        item for item in report.findings
        if item.severity in {"critical", "high"}
    ]

    for item in selected[:300]:
        lines.append(
            f"| `{item.severity}` | `{item.category}` | "
            f"{escape_md(item.title)} | `{item.file}:{item.line}` | "
            f"{escape_md(item.recommendation)} |"
        )

    if len(selected) > 300:
        lines.append(f"| ... | ... | Mais {len(selected) - 300} findings ocultados | ... | ... |")

    lines.extend([
        "",
        "## Temporal Table Duplication",
        "",
    ])

    if not report.duplicated_temporal_tables:
        lines.append("Nenhuma duplicação temporal detectada.")
    else:
        for base, tables in report.duplicated_temporal_tables.items():
            lines.append(f"- `{base}`: {', '.join(f'`{table}`' for table in tables)}")

    lines.extend([
        "",
        "## Orphan SQL Tables",
        "",
    ])

    if not report.orphan_sql_tables:
        lines.append("Nenhuma tabela órfã detectada.")
    else:
        for table in report.orphan_sql_tables[:200]:
            lines.append(f"- `{table}`")

    return "\n".join(lines) + "\n"


def escape_md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def write_report(report: AuditReport, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "architecture-audit.json"
    md_path = out_dir / "architecture-audit.md"
    critical_path = out_dir / "CRITICAL_FAILURE"

    json_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md_path.write_text(render_markdown(report), encoding="utf-8")

    if report.summary["critical"] > 0:
        critical_path.write_text(
            "Critical enterprise architecture issues found.\n",
            encoding="utf-8",
        )
    else:
        critical_path.unlink(missing_ok=True)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enterprise architecture audit for KwanzaControl."
    )
    parser.add_argument("--root", default=".", help="Raiz do projeto.")
    parser.add_argument(
        "--out-dir",
        default="governance/reports",
        help="Pasta de saída dos relatórios.",
    )
    parser.add_argument(
        "--fail-on-critical",
        action="store_true",
        help="Retorna exit code 1 se houver finding crítico.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)

    root = Path(args.root).resolve()
    out_dir = Path(args.out_dir)

    report = run_audit(root)
    write_report(report, out_dir)

    print(json.dumps(report.summary, ensure_ascii=False, indent=2))
    print(f"\nRelatórios gerados em: {out_dir}")
    print(f"- {out_dir / 'architecture-audit.json'}")
    print(f"- {out_dir / 'architecture-audit.md'}")

    if report.summary["critical"] > 0:
        print(f"- {out_dir / 'CRITICAL_FAILURE'}")

    if args.fail_on_critical and report.summary["critical"] > 0:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
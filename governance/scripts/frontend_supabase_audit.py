#!/usr/bin/env python3
# =========================================================
# GOVERNANCE / SCRIPTS / frontend_supabase_audit.py
# KWANZACONTROL - CFO AI ENTERPRISE
# Frontend ↔ Supabase/PostgreSQL Integration Audit
# =========================================================

"""
Auditoria automática para identificar páginas, módulos e serviços do frontend
que podem estar sem integração real com Supabase/PostgreSQL.

Varre:
- frontend/pages
- frontend/app
- frontend/components
- frontend/modules
- frontend/services

Gera:
- governance/reports/frontend_supabase_audit.md
- governance/reports/frontend_supabase_audit.json

Classificação:
- OK       : usa Supabase/API real
- ALERTA   : usa mock/fake/dummy/hardcoded
- RISCO    : componente/página sem evidência de integração
- CRÍTICO  : página/módulo operacional sem persistência detectada
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Sequence


# =========================================================
# CONFIG
# =========================================================

DEFAULT_SCAN_DIRS = [
    "frontend/pages",
    "frontend/app",
    "frontend/components",
    "frontend/modules",
    "frontend/services",
]

DEFAULT_EXTENSIONS = {
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
}

IGNORE_DIRS = {
    "node_modules",
    ".next",
    "dist",
    "build",
    "coverage",
    ".git",
    ".turbo",
    ".vercel",
    "__snapshots__",
}

SUPABASE_PATTERNS = [
    r"\bsupabase\b",
    r"\bcreateClient\s*\(",
    r"\.from\s*\(",
    r"\.select\s*\(",
    r"\.insert\s*\(",
    r"\.update\s*\(",
    r"\.delete\s*\(",
    r"\.upsert\s*\(",
    r"\.rpc\s*\(",
    r"\.auth\b",
    r"\.storage\b",
    r"@supabase/supabase-js",
]

API_PATTERNS = [
    r"\bfetch\s*\(",
    r"\baxios\b",
    r"\bapiClient\b",
    r"\bhttpClient\b",
    r"\buseQuery\s*\(",
    r"\buseMutation\s*\(",
    r"\bSWR\b",
    r"\buseSWR\s*\(",
    r"/api/",
]

MOCK_PATTERNS = [
    r"\bmock\b",
    r"\bmocks\b",
    r"\bfake\b",
    r"\bdummy\b",
    r"\bfixture\b",
    r"\bfixtures\b",
    r"\bplaceholder\b",
    r"\bhardcoded\b",
    r"\bstaticData\b",
    r"\bsampleData\b",
    r"\bmockData\b",
    r"\bdemoData\b",
    r"\bTODO\b",
    r"\bFIXME\b",
    r"\btemporar",
    r"\bprovis[oó]rio",
    r"\bsem banco\b",
    r"\bsem integra",
]

LOCAL_STATE_PATTERNS = [
    r"\buseState\s*\(",
    r"\buseReducer\s*\(",
    r"\blocalStorage\b",
    r"\bsessionStorage\b",
]

WRITE_INTENT_PATTERNS = [
    r"\bform\b",
    r"\bonSubmit\b",
    r"\bhandleSubmit\b",
    r"\bsave\b",
    r"\bsalvar\b",
    r"\bcreate\b",
    r"\bcriar\b",
    r"\bupdate\b",
    r"\batualizar\b",
    r"\bdelete\b",
    r"\bexcluir\b",
    r"\bremove\b",
    r"\bremover\b",
    r"\bButton\b",
    r"\binput\b",
    r"\btextarea\b",
    r"\bselect\b",
]

OPERATIONAL_KEYWORDS = [
    "dashboard",
    "finance",
    "financial",
    "cashflow",
    "cash-flow",
    "fluxo",
    "caixa",
    "invoice",
    "fatura",
    "payment",
    "pagamento",
    "customer",
    "cliente",
    "supplier",
    "fornecedor",
    "transaction",
    "transacao",
    "transação",
    "report",
    "relatorio",
    "relatório",
    "analytics",
    "forecast",
    "budget",
    "orcamento",
    "orçamento",
    "account",
    "conta",
    "bank",
    "banco",
    "reconciliation",
    "conciliacao",
    "conciliação",
    "settings",
    "config",
    "user",
    "usuario",
    "usuário",
    "tenant",
    "company",
    "empresa",
]


# =========================================================
# MODELS
# =========================================================

@dataclass
class FileAudit:
    path: str
    status: str
    score: int
    severity: str
    uses_supabase: bool
    uses_api: bool
    has_mock_or_fake: bool
    has_local_state: bool
    has_write_intent: bool
    is_operational: bool
    matched_patterns: Dict[str, List[str]] = field(default_factory=dict)
    recommendation: str = ""


@dataclass
class AuditSummary:
    generated_at: str
    root: str
    scanned_files: int
    ok: int
    alerta: int
    risco: int
    critico: int
    files: List[FileAudit]


# =========================================================
# HELPERS
# =========================================================

def read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1", errors="ignore")
    except Exception:
        return ""


def compile_patterns(patterns: Sequence[str]) -> List[re.Pattern]:
    return [
        re.compile(pattern, re.IGNORECASE | re.MULTILINE)
        for pattern in patterns
    ]


SUPABASE_RE = compile_patterns(SUPABASE_PATTERNS)
API_RE = compile_patterns(API_PATTERNS)
MOCK_RE = compile_patterns(MOCK_PATTERNS)
LOCAL_STATE_RE = compile_patterns(LOCAL_STATE_PATTERNS)
WRITE_INTENT_RE = compile_patterns(WRITE_INTENT_PATTERNS)
OPERATIONAL_RE = compile_patterns(OPERATIONAL_KEYWORDS)


def matches(patterns: Sequence[re.Pattern], text: str) -> List[str]:
    found: List[str] = []

    for pattern in patterns:
        if pattern.search(text):
            found.append(pattern.pattern)

    return found


def should_ignore(path: Path) -> bool:
    return any(part in IGNORE_DIRS for part in path.parts)


def iter_source_files(root: Path, scan_dirs: Sequence[str], extensions: set[str]) -> Iterable[Path]:
    for scan_dir in scan_dirs:
        base = root / scan_dir

        if not base.exists():
            continue

        for path in base.rglob("*"):
            if not path.is_file():
                continue

            if should_ignore(path):
                continue

            if path.suffix.lower() not in extensions:
                continue

            yield path


def is_operational_file(path: Path, text: str) -> bool:
    joined = f"{path.as_posix()}\n{text[:3000]}"
    return bool(matches(OPERATIONAL_RE, joined))


def classify_file(path: Path, text: str, root: Path) -> FileAudit:
    supabase_hits = matches(SUPABASE_RE, text)
    api_hits = matches(API_RE, text)
    mock_hits = matches(MOCK_RE, text)
    local_state_hits = matches(LOCAL_STATE_RE, text)
    write_hits = matches(WRITE_INTENT_RE, text)

    uses_supabase = bool(supabase_hits)
    uses_api = bool(api_hits)
    has_mock_or_fake = bool(mock_hits)
    has_local_state = bool(local_state_hits)
    has_write_intent = bool(write_hits)
    operational = is_operational_file(path, text)

    score = 0

    if uses_supabase:
        score += 45

    if uses_api:
        score += 25

    if has_mock_or_fake:
        score -= 40

    if has_local_state and not uses_supabase and not uses_api:
        score -= 15

    if has_write_intent and not uses_supabase and not uses_api:
        score -= 25

    if operational and not uses_supabase and not uses_api:
        score -= 35

    if uses_supabase or uses_api:
        if has_mock_or_fake:
            status = "ALERTA"
            severity = "medium"
            recommendation = (
                "Arquivo possui integração, mas também contém indícios de mock/fake/TODO. "
                "Remover dados simulados ou isolar em ambiente de teste."
            )
        else:
            status = "OK"
            severity = "low"
            recommendation = "Integração com Supabase/API detectada."
    else:
        if operational or has_write_intent:
            status = "CRÍTICO"
            severity = "high"
            recommendation = (
                "Tela/módulo operacional sem evidência de persistência. "
                "Adicionar camada de serviço Supabase/PostgreSQL ou API real."
            )
        else:
            status = "RISCO"
            severity = "medium"
            recommendation = (
                "Arquivo sem evidência de integração com banco/API. "
                "Validar se é apenas componente visual ou se precisa persistência."
            )

    if has_mock_or_fake and not uses_supabase and not uses_api:
        status = "ALERTA" if status != "CRÍTICO" else status
        severity = "high" if status == "CRÍTICO" else "medium"

    relative_path = path.relative_to(root).as_posix()

    return FileAudit(
        path=relative_path,
        status=status,
        score=int(score),
        severity=severity,
        uses_supabase=uses_supabase,
        uses_api=uses_api,
        has_mock_or_fake=has_mock_or_fake,
        has_local_state=has_local_state,
        has_write_intent=has_write_intent,
        is_operational=operational,
        matched_patterns={
            "supabase": supabase_hits,
            "api": api_hits,
            "mock": mock_hits,
            "local_state": local_state_hits,
            "write_intent": write_hits,
        },
        recommendation=recommendation,
    )


# =========================================================
# REPORTS
# =========================================================

def build_summary(root: Path, files: List[FileAudit]) -> AuditSummary:
    return AuditSummary(
        generated_at=datetime.now(timezone.utc).isoformat(),
        root=root.as_posix(),
        scanned_files=len(files),
        ok=sum(1 for item in files if item.status == "OK"),
        alerta=sum(1 for item in files if item.status == "ALERTA"),
        risco=sum(1 for item in files if item.status == "RISCO"),
        critico=sum(1 for item in files if item.status == "CRÍTICO"),
        files=files,
    )


def write_json_report(summary: AuditSummary, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(
        json.dumps(asdict(summary), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def write_markdown_report(summary: AuditSummary, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    sorted_files = sorted(
        summary.files,
        key=lambda item: (
            {"CRÍTICO": 0, "ALERTA": 1, "RISCO": 2, "OK": 3}.get(item.status, 9),
            item.path,
        ),
    )

    lines: List[str] = [
        "# Frontend ↔ Supabase/PostgreSQL Audit",
        "",
        f"- Generated at: `{summary.generated_at}`",
        f"- Root: `{summary.root}`",
        f"- Scanned files: `{summary.scanned_files}`",
        "",
        "## Summary",
        "",
        "| Status | Count |",
        "|---|---:|",
        f"| OK | {summary.ok} |",
        f"| ALERTA | {summary.alerta} |",
        f"| RISCO | {summary.risco} |",
        f"| CRÍTICO | {summary.critico} |",
        "",
        "## Priority Findings",
        "",
    ]

    priority = [
        item for item in sorted_files
        if item.status in {"CRÍTICO", "ALERTA", "RISCO"}
    ]

    if not priority:
        lines.append("Nenhum risco relevante encontrado.")
    else:
        lines.extend([
            "| Status | Severity | Score | File | Supabase | API | Mock/Fake | Write Intent | Operational |",
            "|---|---|---:|---|---:|---:|---:|---:|---:|",
        ])

        for item in priority:
            lines.append(
                "| {status} | {severity} | {score} | `{path}` | {supabase} | {api} | {mock} | {write} | {operational} |".format(
                    status=item.status,
                    severity=item.severity,
                    score=item.score,
                    path=item.path,
                    supabase="yes" if item.uses_supabase else "no",
                    api="yes" if item.uses_api else "no",
                    mock="yes" if item.has_mock_or_fake else "no",
                    write="yes" if item.has_write_intent else "no",
                    operational="yes" if item.is_operational else "no",
                )
            )

    lines.extend([
        "",
        "## Detailed Recommendations",
        "",
    ])

    for item in priority:
        lines.extend([
            f"### `{item.path}`",
            "",
            f"- Status: **{item.status}**",
            f"- Severity: `{item.severity}`",
            f"- Score: `{item.score}`",
            f"- Uses Supabase: `{item.uses_supabase}`",
            f"- Uses API: `{item.uses_api}`",
            f"- Mock/Fake/TODO detected: `{item.has_mock_or_fake}`",
            f"- Local state detected: `{item.has_local_state}`",
            f"- Write intent detected: `{item.has_write_intent}`",
            f"- Operational module/page: `{item.is_operational}`",
            f"- Recommendation: {item.recommendation}",
            "",
        ])

    lines.extend([
        "## All Files",
        "",
        "| Status | File | Recommendation |",
        "|---|---|---|",
    ])

    for item in sorted_files:
        lines.append(
            f"| {item.status} | `{item.path}` | {item.recommendation} |"
        )

    output_path.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


# =========================================================
# MAIN
# =========================================================

def run_audit(
    root: Path,
    scan_dirs: Sequence[str],
    output_dir: Path,
) -> AuditSummary:
    files: List[FileAudit] = []

    for path in iter_source_files(root, scan_dirs, DEFAULT_EXTENSIONS):
        text = read_text_safe(path)
        files.append(
            classify_file(
                path=path,
                text=text,
                root=root,
            )
        )

    summary = build_summary(root, files)

    write_markdown_report(
        summary,
        output_dir / "frontend_supabase_audit.md",
    )

    write_json_report(
        summary,
        output_dir / "frontend_supabase_audit.json",
    )

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit frontend files for Supabase/PostgreSQL integration gaps.",
    )

    parser.add_argument(
        "--root",
        default=".",
        help="Project root directory. Default: current directory.",
    )

    parser.add_argument(
        "--output-dir",
        default="governance/reports",
        help="Directory where reports will be written.",
    )

    parser.add_argument(
        "--scan-dir",
        action="append",
        dest="scan_dirs",
        help="Directory to scan. Can be passed multiple times.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    root = Path(args.root).resolve()

    scan_dirs = args.scan_dirs or DEFAULT_SCAN_DIRS

    output_dir = root / args.output_dir

    summary = run_audit(
        root=root,
        scan_dirs=scan_dirs,
        output_dir=output_dir,
    )

    print("\nFRONTEND ↔ SUPABASE AUDIT COMPLETE")
    print("----------------------------------")
    print(f"Scanned files : {summary.scanned_files}")
    print(f"OK            : {summary.ok}")
    print(f"ALERTA        : {summary.alerta}")
    print(f"RISCO         : {summary.risco}")
    print(f"CRÍTICO       : {summary.critico}")
    print("")
    print(f"Markdown report: {output_dir / 'frontend_supabase_audit.md'}")
    print(f"JSON report    : {output_dir / 'frontend_supabase_audit.json'}")
    print("")

    return 1 if summary.critico > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# =========================================================
# GOVERNANCE / SCRIPTS / frontend_supabase_audit_v2.py
# KWANZACONTROL - CFO AI ENTERPRISE
# Frontend ↔ Supabase/PostgreSQL Integration Audit V2
# =========================================================

"""
Auditoria automática para identificar páginas, módulos, stores, hooks e services
do frontend que podem estar sem integração real com Supabase/PostgreSQL.

V2:
- Adiciona classificação UI_ONLY para componentes visuais puros.
- Reduz falsos positivos em src/components/ui/*.
- Mantém CRÍTICO para pages/stores/services/hooks operacionais sem integração.
- Gera Markdown e JSON.
- Retorna exit code 1 se houver CRÍTICO real.

Varre por padrão:
- src/pages
- src/components
- src/modules
- src/services
- src/api
- src/integrations
- src/hooks
- src/stores

Classificação:
- OK       : usa Supabase/API real sem mock/fake relevante
- ALERTA   : usa Supabase/API, mas contém mock/fake/TODO ou serviço mock
- RISCO    : arquivo sem integração, mas não claramente operacional
- CRÍTICO  : page/store/service/hook/módulo operacional sem persistência detectada
- UI_ONLY  : componente visual puro, sem responsabilidade de banco/API
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence


# =========================================================
# CONFIG
# =========================================================

DEFAULT_SCAN_DIRS = [
    "src/pages",
    "src/components",
    "src/modules",
    "src/services",
    "src/api",
    "src/integrations",
    "src/hooks",
    "src/stores",
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

# Diretórios geralmente visuais. V2 classifica como UI_ONLY quando não houver
# indícios fortes de domínio/operacionalidade.
UI_ONLY_PATH_PREFIXES = [
    "src/components/ui/",
]

UI_ONLY_FILE_NAMES = {
    "accordion.tsx",
    "alert.tsx",
    "alert-dialog.tsx",
    "aspect-ratio.tsx",
    "avatar.tsx",
    "badge.tsx",
    "breadcrumb.tsx",
    "button.tsx",
    "calendar.tsx",
    "card.tsx",
    "carousel.tsx",
    "checkbox.tsx",
    "collapsible.tsx",
    "command.tsx",
    "context-menu.tsx",
    "dialog.tsx",
    "drawer.tsx",
    "dropdown-menu.tsx",
    "form.tsx",
    "hover-card.tsx",
    "input.tsx",
    "input-otp.tsx",
    "label.tsx",
    "menubar.tsx",
    "navigation-menu.tsx",
    "popover.tsx",
    "progress.tsx",
    "radio-group.tsx",
    "resizable.tsx",
    "scroll-area.tsx",
    "select.tsx",
    "separator.tsx",
    "sheet.tsx",
    "skeleton.tsx",
    "slider.tsx",
    "sonner.tsx",
    "switch.tsx",
    "table.tsx",
    "tabs.tsx",
    "textarea.tsx",
    "toast.tsx",
    "toaster.tsx",
    "toggle.tsx",
    "toggle-group.tsx",
    "tooltip.tsx",
    "use-toast.ts",
}

PURE_VISUAL_COMPONENT_NAMES = {
    "EmptyState.tsx",
    "EmptyStates.tsx",
    "ErrorBoundary.tsx",
    "OptimizedImage.tsx",
    "Toast.tsx",
    "NotificationBadge.tsx",
    "Cards.tsx",
    "Charts.tsx",
    "Tables.tsx",
    "VirtualList.tsx",
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
    r"\bzustand\b",
    r"\bcreate\s*\(",
]

WRITE_INTENT_PATTERNS = [
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
    r"\bsubmit\b",
    r"\bmutate\b",
]

UI_CONTROL_PATTERNS = [
    r"\bButton\b",
    r"\binput\b",
    r"\btextarea\b",
    r"\bselect\b",
    r"\bDialog\b",
    r"\bDropdown\b",
    r"\bTooltip\b",
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
    "role",
    "roles",
    "rbac",
    "iam",
    "pam",
    "security",
    "audit",
    "compliance",
    "payroll",
    "employee",
    "employees",
    "hr",
    "notification",
    "quota",
    "performance",
    "metric",
    "metrics",
    "inventory",
    "category",
    "categories",
    "contract",
    "costcenter",
    "cost-center",
    "cost center",
    "agt",
    "tax",
    "billing",
]

SERVICE_OR_STORE_PATH_PATTERNS = [
    r"^src/services/",
    r"^src/stores/",
    r"^src/hooks/use",
]

PAGE_PATH_PATTERNS = [
    r"^src/pages/",
    r"^src/modules/",
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
    has_ui_controls: bool
    is_operational: bool
    is_ui_only_candidate: bool
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
    ui_only: int
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
UI_CONTROL_RE = compile_patterns(UI_CONTROL_PATTERNS)
OPERATIONAL_RE = compile_patterns(OPERATIONAL_KEYWORDS)
SERVICE_OR_STORE_RE = compile_patterns(SERVICE_OR_STORE_PATH_PATTERNS)
PAGE_RE = compile_patterns(PAGE_PATH_PATTERNS)


def matches(patterns: Sequence[re.Pattern], text: str) -> List[str]:
    found: List[str] = []

    for pattern in patterns:
        if pattern.search(text):
            found.append(pattern.pattern)

    return found


def should_ignore(path: Path) -> bool:
    return any(part in IGNORE_DIRS for part in path.parts)


def normalize_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


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


def is_page_or_module(relative_path: str) -> bool:
    return bool(matches(PAGE_RE, relative_path))


def is_service_store_or_hook(relative_path: str) -> bool:
    return bool(matches(SERVICE_OR_STORE_RE, relative_path))


def is_ui_only_candidate(relative_path: str, path: Path, text: str) -> bool:
    lower_path = relative_path.lower()

    if any(lower_path.startswith(prefix.lower()) for prefix in UI_ONLY_PATH_PREFIXES):
        return True

    if path.name in UI_ONLY_FILE_NAMES:
        return True

    if path.name in PURE_VISUAL_COMPONENT_NAMES:
        return True

    # Componentes genéricos em src/components sem palavras de domínio no path.
    if lower_path.startswith("src/components/"):
        path_has_domain_word = bool(matches(OPERATIONAL_RE, lower_path))
        text_has_strong_domain_word = bool(matches(OPERATIONAL_RE, text[:1500]))

        if not path_has_domain_word and not text_has_strong_domain_word:
            return True

    return False


def is_operational_file(relative_path: str, text: str) -> bool:
    joined = f"{relative_path}\n{text[:5000]}"
    return bool(matches(OPERATIONAL_RE, joined))


def classify_file(path: Path, text: str, root: Path) -> FileAudit:
    relative_path = normalize_path(path, root)

    supabase_hits = matches(SUPABASE_RE, text)
    api_hits = matches(API_RE, text)
    mock_hits = matches(MOCK_RE, text)
    local_state_hits = matches(LOCAL_STATE_RE, text)
    write_hits = matches(WRITE_INTENT_RE, text)
    ui_control_hits = matches(UI_CONTROL_RE, text)

    uses_supabase = bool(supabase_hits)
    uses_api = bool(api_hits)
    has_mock_or_fake = bool(mock_hits)
    has_local_state = bool(local_state_hits)
    has_write_intent = bool(write_hits)
    has_ui_controls = bool(ui_control_hits)
    operational = is_operational_file(relative_path, text)
    ui_only_candidate = is_ui_only_candidate(relative_path, path, text)

    page_or_module = is_page_or_module(relative_path)
    service_store_hook = is_service_store_or_hook(relative_path)

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

    # UI_ONLY ganha classificação própria antes dos críticos,
    # desde que não seja page/store/service/hook e não use mock crítico de domínio.
    if (
        ui_only_candidate
        and not page_or_module
        and not service_store_hook
        and not uses_supabase
        and not uses_api
    ):
        status = "UI_ONLY"
        severity = "none"
        recommendation = (
            "Componente visual puro. Não precisa acessar Supabase/PostgreSQL diretamente. "
            "Manter dados via props/hooks externos."
        )

        if has_mock_or_fake:
            status = "ALERTA"
            severity = "medium"
            recommendation = (
                "Componente visual contém indícios de mock/fake/TODO. "
                "Remover exemplos internos ou mover para storybook/fixtures."
            )

    elif uses_supabase or uses_api:
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
        if page_or_module or service_store_hook or operational or has_write_intent:
            status = "CRÍTICO"
            severity = "high"
            recommendation = (
                "Arquivo operacional sem evidência de Supabase/API. "
                "Criar service/hook real, conectar ao Supabase/PostgreSQL e remover dados locais."
            )
        else:
            status = "RISCO"
            severity = "medium"
            recommendation = (
                "Arquivo sem evidência de integração com banco/API. "
                "Validar se é componente visual puro ou se precisa persistência."
            )

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
        has_ui_controls=has_ui_controls,
        is_operational=operational,
        is_ui_only_candidate=ui_only_candidate,
        matched_patterns={
            "supabase": supabase_hits,
            "api": api_hits,
            "mock": mock_hits,
            "local_state": local_state_hits,
            "write_intent": write_hits,
            "ui_controls": ui_control_hits,
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
        ui_only=sum(1 for item in files if item.status == "UI_ONLY"),
        files=files,
    )


def write_json_report(summary: AuditSummary, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(
        json.dumps(asdict(summary), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def status_sort_key(status: str) -> int:
    return {
        "CRÍTICO": 0,
        "ALERTA": 1,
        "RISCO": 2,
        "OK": 3,
        "UI_ONLY": 4,
    }.get(status, 9)


def write_markdown_report(summary: AuditSummary, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    sorted_files = sorted(
        summary.files,
        key=lambda item: (
            status_sort_key(item.status),
            item.path,
        ),
    )

    lines: List[str] = [
        "# Frontend ↔ Supabase/PostgreSQL Audit V2",
        "",
        f"- Generated at: `{summary.generated_at}`",
        f"- Root: `{summary.root}`",
        f"- Scanned files: `{summary.scanned_files}`",
        "",
        "## Summary",
        "",
        "| Status | Count | Meaning |",
        "|---|---:|---|",
        f"| OK | {summary.ok} | Integração real detectada |",
        f"| ALERTA | {summary.alerta} | Integração parcial ou mocks/TODO |",
        f"| RISCO | {summary.risco} | Revisão manual necessária |",
        f"| CRÍTICO | {summary.critico} | Operacional sem Supabase/API |",
        f"| UI_ONLY | {summary.ui_only} | Componente visual puro |",
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
            "| Status | Severity | Score | File | Supabase | API | Mock/Fake | Write Intent | Operational | UI Candidate |",
            "|---|---|---:|---|---:|---:|---:|---:|---:|---:|",
        ])

        for item in priority:
            lines.append(
                "| {status} | {severity} | {score} | `{path}` | {supabase} | {api} | {mock} | {write} | {operational} | {ui_candidate} |".format(
                    status=item.status,
                    severity=item.severity,
                    score=item.score,
                    path=item.path,
                    supabase="yes" if item.uses_supabase else "no",
                    api="yes" if item.uses_api else "no",
                    mock="yes" if item.has_mock_or_fake else "no",
                    write="yes" if item.has_write_intent else "no",
                    operational="yes" if item.is_operational else "no",
                    ui_candidate="yes" if item.is_ui_only_candidate else "no",
                )
            )

    lines.extend([
        "",
        "## Critical Resolution Plan",
        "",
        "1. Corrigir primeiro `src/stores/*` e `src/hooks/use*.ts`, pois controlam sessão, tenant, quota e estado global.",
        "2. Corrigir páginas em `src/pages/*` sem Supabase/API, criando services reais em `src/services/*`.",
        "3. Corrigir services sem integração, evitando lógica fake/local em produção.",
        "4. Converter componentes operacionais para receber dados via props/hooks em vez de manter dados locais.",
        "5. Manter arquivos `UI_ONLY` sem acesso direto ao banco. Eles devem continuar puros.",
        "6. Remover `mock`, `fake`, `dummy`, `sampleData`, `TODO` de arquivos classificados como ALERTA.",
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
            f"- UI controls detected: `{item.has_ui_controls}`",
            f"- Operational module/page: `{item.is_operational}`",
            f"- UI-only candidate: `{item.is_ui_only_candidate}`",
            f"- Recommendation: {item.recommendation}",
            "",
        ])

    lines.extend([
        "## UI_ONLY Files",
        "",
    ])

    ui_files = [item for item in sorted_files if item.status == "UI_ONLY"]

    if not ui_files:
        lines.append("Nenhum arquivo UI_ONLY classificado.")
    else:
        lines.extend([
            "| File | Recommendation |",
            "|---|---|",
        ])

        for item in ui_files:
            lines.append(
                f"| `{item.path}` | {item.recommendation} |"
            )

    lines.extend([
        "",
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
        output_dir / "frontend_supabase_audit_v2.md",
    )

    write_json_report(
        summary,
        output_dir / "frontend_supabase_audit_v2.json",
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

    print("\nFRONTEND ↔ SUPABASE AUDIT V2 COMPLETE")
    print("-------------------------------------")
    print(f"Scanned files : {summary.scanned_files}")
    print(f"OK            : {summary.ok}")
    print(f"ALERTA        : {summary.alerta}")
    print(f"RISCO         : {summary.risco}")
    print(f"CRÍTICO       : {summary.critico}")
    print(f"UI_ONLY       : {summary.ui_only}")
    print("")
    print(f"Markdown report: {output_dir / 'frontend_supabase_audit_v2.md'}")
    print(f"JSON report    : {output_dir / 'frontend_supabase_audit_v2.json'}")
    print("")

    return 1 if summary.critico > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())

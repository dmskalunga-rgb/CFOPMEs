#!/usr/bin/env python3
"""
reports/performance/sla_compliance_report.py

Enterprise-grade SLA Compliance Report generator.

Objetivo:
- Ler incidentes/eventos de SLA a partir de CSV ou JSON.
- Calcular conformidade por serviço, severidade e período.
- Gerar relatório em JSON, CSV e HTML.
- Ser seguro para automação CI/CD, cron, Airflow, GitHub Actions ou execução manual.

Exemplo de execução:
    python reports/performance/sla_compliance_report.py \
        --input data/incidents.csv \
        --output-dir reports/output \
        --period monthly \
        --target-sla 99.5 \
        --format html,json,csv

Formato esperado do CSV/JSON:
    incident_id,service,severity,status,opened_at,resolved_at,downtime_minutes,response_minutes,resolution_minutes

Campos mínimos:
    incident_id: string
    service: string
    severity: critical|high|medium|low
    status: resolved|closed|open|in_progress
    opened_at: ISO datetime
    resolved_at: ISO datetime ou vazio
    downtime_minutes: número, opcional
    response_minutes: número, opcional
    resolution_minutes: número, opcional

Observação:
- Se downtime_minutes vier vazio, o script tenta calcular pela diferença entre opened_at e resolved_at.
- Incidentes abertos entram como violação potencial e usam o horário atual para cálculo.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import html
import json
import logging
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Sequence, Tuple


APP_NAME = "sla_compliance_report"
DEFAULT_TARGET_SLA = 99.5
DEFAULT_TIMEZONE = timezone.utc


class ReportFormat(str, Enum):
    JSON = "json"
    CSV = "csv"
    HTML = "html"


class Period(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    ALL = "all"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class IncidentStatus(str, Enum):
    RESOLVED = "resolved"
    CLOSED = "closed"
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SlaPolicy:
    """SLA policy thresholds by severity."""

    target_sla_percent: float = DEFAULT_TARGET_SLA
    response_target_minutes_by_severity: Dict[Severity, int] = dataclasses.field(
        default_factory=lambda: {
            Severity.CRITICAL: 15,
            Severity.HIGH: 30,
            Severity.MEDIUM: 120,
            Severity.LOW: 480,
            Severity.UNKNOWN: 480,
        }
    )
    resolution_target_minutes_by_severity: Dict[Severity, int] = dataclasses.field(
        default_factory=lambda: {
            Severity.CRITICAL: 240,
            Severity.HIGH: 480,
            Severity.MEDIUM: 1440,
            Severity.LOW: 4320,
            Severity.UNKNOWN: 4320,
        }
    )


@dataclass(frozen=True)
class Incident:
    incident_id: str
    service: str
    severity: Severity
    status: IncidentStatus
    opened_at: datetime
    resolved_at: Optional[datetime]
    downtime_minutes: float
    response_minutes: Optional[float]
    resolution_minutes: Optional[float]
    raw: Dict[str, Any]


@dataclass(frozen=True)
class GroupKey:
    service: str
    severity: Severity
    period_label: str


@dataclass
class SlaMetrics:
    service: str
    severity: str
    period: str
    total_incidents: int
    open_incidents: int
    resolved_incidents: int
    total_downtime_minutes: float
    availability_percent: float
    sla_target_percent: float
    sla_met: bool
    response_breaches: int
    resolution_breaches: int
    breach_count: int
    avg_response_minutes: Optional[float]
    p95_response_minutes: Optional[float]
    avg_resolution_minutes: Optional[float]
    p95_resolution_minutes: Optional[float]
    mttr_minutes: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class ReportError(Exception):
    """Base exception for report failures."""


class InputValidationError(ReportError):
    """Raised when input data is invalid."""


class FileLoader:
    @staticmethod
    def load(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            raise InputValidationError(f"Arquivo de entrada não encontrado: {path}")
        if not path.is_file():
            raise InputValidationError(f"Caminho de entrada não é arquivo: {path}")

        suffix = path.suffix.lower()
        if suffix == ".csv":
            return FileLoader._load_csv(path)
        if suffix == ".json":
            return FileLoader._load_json(path)

        raise InputValidationError("Formato não suportado. Use .csv ou .json")

    @staticmethod
    def _load_csv(path: Path) -> List[Dict[str, Any]]:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            return [dict(row) for row in reader]

    @staticmethod
    def _load_json(path: Path) -> List[Dict[str, Any]]:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)

        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("incidents"), list):
            return payload["incidents"]

        raise InputValidationError("JSON inválido. Esperado lista ou objeto com chave 'incidents'.")


class IncidentParser:
    @staticmethod
    def parse_many(rows: Iterable[Dict[str, Any]], now: datetime) -> List[Incident]:
        incidents: List[Incident] = []
        errors: List[str] = []

        for index, row in enumerate(rows, start=1):
            try:
                incidents.append(IncidentParser.parse(row, now=now))
            except Exception as exc:  # noqa: BLE001 - agrega erro por linha para relatório claro
                errors.append(f"linha={index}: {exc}")

        if errors:
            preview = "\n".join(errors[:20])
            extra = "" if len(errors) <= 20 else f"\n... e mais {len(errors) - 20} erro(s)."
            raise InputValidationError(f"Falha ao validar incidentes:\n{preview}{extra}")

        return incidents

    @staticmethod
    def parse(row: Dict[str, Any], now: datetime) -> Incident:
        incident_id = IncidentParser._required_str(row, "incident_id")
        service = IncidentParser._required_str(row, "service")
        severity = IncidentParser._parse_severity(row.get("severity"))
        status = IncidentParser._parse_status(row.get("status"))
        opened_at = IncidentParser._parse_datetime(IncidentParser._required_str(row, "opened_at"))
        resolved_at_raw = IncidentParser._optional_str(row, "resolved_at")
        resolved_at = IncidentParser._parse_datetime(resolved_at_raw) if resolved_at_raw else None

        if resolved_at and resolved_at < opened_at:
            raise ValueError("resolved_at não pode ser menor que opened_at")

        downtime_minutes = IncidentParser._optional_float(row, "downtime_minutes")
        if downtime_minutes is None:
            end = resolved_at or now
            downtime_minutes = max((end - opened_at).total_seconds() / 60, 0)

        response_minutes = IncidentParser._optional_float(row, "response_minutes")
        resolution_minutes = IncidentParser._optional_float(row, "resolution_minutes")
        if resolution_minutes is None and resolved_at is not None:
            resolution_minutes = max((resolved_at - opened_at).total_seconds() / 60, 0)

        return Incident(
            incident_id=incident_id,
            service=service,
            severity=severity,
            status=status,
            opened_at=opened_at,
            resolved_at=resolved_at,
            downtime_minutes=float(downtime_minutes),
            response_minutes=response_minutes,
            resolution_minutes=resolution_minutes,
            raw=row,
        )

    @staticmethod
    def _required_str(row: Dict[str, Any], key: str) -> str:
        value = row.get(key)
        if value is None or str(value).strip() == "":
            raise ValueError(f"campo obrigatório ausente: {key}")
        return str(value).strip()

    @staticmethod
    def _optional_str(row: Dict[str, Any], key: str) -> Optional[str]:
        value = row.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _optional_float(row: Dict[str, Any], key: str) -> Optional[float]:
        value = row.get(key)
        if value is None or str(value).strip() == "":
            return None
        try:
            return float(str(value).replace(",", "."))
        except ValueError as exc:
            raise ValueError(f"campo {key} precisa ser numérico") from exc

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(f"datetime inválido: {value}") from exc

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=DEFAULT_TIMEZONE)
        return parsed.astimezone(DEFAULT_TIMEZONE)

    @staticmethod
    def _parse_severity(value: Any) -> Severity:
        text = str(value or "unknown").strip().lower()
        aliases = {
            "sev1": Severity.CRITICAL,
            "p1": Severity.CRITICAL,
            "critical": Severity.CRITICAL,
            "critico": Severity.CRITICAL,
            "crítico": Severity.CRITICAL,
            "sev2": Severity.HIGH,
            "p2": Severity.HIGH,
            "high": Severity.HIGH,
            "alto": Severity.HIGH,
            "alta": Severity.HIGH,
            "sev3": Severity.MEDIUM,
            "p3": Severity.MEDIUM,
            "medium": Severity.MEDIUM,
            "medio": Severity.MEDIUM,
            "médio": Severity.MEDIUM,
            "media": Severity.MEDIUM,
            "média": Severity.MEDIUM,
            "sev4": Severity.LOW,
            "p4": Severity.LOW,
            "low": Severity.LOW,
            "baixo": Severity.LOW,
            "baixa": Severity.LOW,
        }
        return aliases.get(text, Severity.UNKNOWN)

    @staticmethod
    def _parse_status(value: Any) -> IncidentStatus:
        text = str(value or "unknown").strip().lower()
        aliases = {
            "resolved": IncidentStatus.RESOLVED,
            "resolvido": IncidentStatus.RESOLVED,
            "closed": IncidentStatus.CLOSED,
            "fechado": IncidentStatus.CLOSED,
            "open": IncidentStatus.OPEN,
            "aberto": IncidentStatus.OPEN,
            "in_progress": IncidentStatus.IN_PROGRESS,
            "progress": IncidentStatus.IN_PROGRESS,
            "em_andamento": IncidentStatus.IN_PROGRESS,
            "andamento": IncidentStatus.IN_PROGRESS,
        }
        return aliases.get(text, IncidentStatus.UNKNOWN)


class PeriodGrouper:
    @staticmethod
    def label_for(dt: datetime, period: Period) -> str:
        if period == Period.DAILY:
            return dt.strftime("%Y-%m-%d")
        if period == Period.WEEKLY:
            year, week, _ = dt.isocalendar()
            return f"{year}-W{week:02d}"
        if period == Period.MONTHLY:
            return dt.strftime("%Y-%m")
        if period == Period.QUARTERLY:
            quarter = ((dt.month - 1) // 3) + 1
            return f"{dt.year}-Q{quarter}"
        if period == Period.YEARLY:
            return str(dt.year)
        return "all"

    @staticmethod
    def minutes_in_period(period_label: str, period: Period, fallback_minutes: int = 30 * 24 * 60) -> int:
        if period == Period.DAILY:
            return 24 * 60
        if period == Period.WEEKLY:
            return 7 * 24 * 60
        if period == Period.MONTHLY:
            # Aproximação consistente para dashboard executivo.
            return 30 * 24 * 60
        if period == Period.QUARTERLY:
            return 90 * 24 * 60
        if period == Period.YEARLY:
            return 365 * 24 * 60
        return fallback_minutes


class SlaCalculator:
    def __init__(self, policy: SlaPolicy, period: Period) -> None:
        self.policy = policy
        self.period = period

    def calculate(self, incidents: Sequence[Incident]) -> List[SlaMetrics]:
        groups: DefaultDict[GroupKey, List[Incident]] = defaultdict(list)

        for incident in incidents:
            period_label = PeriodGrouper.label_for(incident.opened_at, self.period)
            key = GroupKey(
                service=incident.service,
                severity=incident.severity,
                period_label=period_label,
            )
            groups[key].append(incident)

        metrics = [self._calculate_group(key, group) for key, group in groups.items()]
        return sorted(metrics, key=lambda item: (item.period, item.service, item.severity))

    def _calculate_group(self, key: GroupKey, incidents: Sequence[Incident]) -> SlaMetrics:
        total_incidents = len(incidents)
        open_incidents = sum(1 for item in incidents if item.status in {IncidentStatus.OPEN, IncidentStatus.IN_PROGRESS})
        resolved_incidents = sum(1 for item in incidents if item.status in {IncidentStatus.RESOLVED, IncidentStatus.CLOSED})
        total_downtime = sum(max(item.downtime_minutes, 0) for item in incidents)

        period_minutes = PeriodGrouper.minutes_in_period(key.period_label, self.period)
        availability = max(0.0, min(100.0, ((period_minutes - total_downtime) / period_minutes) * 100))

        response_target = self.policy.response_target_minutes_by_severity.get(key.severity, 480)
        resolution_target = self.policy.resolution_target_minutes_by_severity.get(key.severity, 4320)

        response_values = [item.response_minutes for item in incidents if item.response_minutes is not None]
        resolution_values = [item.resolution_minutes for item in incidents if item.resolution_minutes is not None]

        response_breaches = sum(
            1 for item in incidents if item.response_minutes is not None and item.response_minutes > response_target
        )
        resolution_breaches = sum(
            1 for item in incidents if item.resolution_minutes is not None and item.resolution_minutes > resolution_target
        )

        breach_count = response_breaches + resolution_breaches + (0 if availability >= self.policy.target_sla_percent else 1)

        return SlaMetrics(
            service=key.service,
            severity=key.severity.value,
            period=key.period_label,
            total_incidents=total_incidents,
            open_incidents=open_incidents,
            resolved_incidents=resolved_incidents,
            total_downtime_minutes=round(total_downtime, 2),
            availability_percent=round(availability, 4),
            sla_target_percent=self.policy.target_sla_percent,
            sla_met=availability >= self.policy.target_sla_percent and response_breaches == 0 and resolution_breaches == 0,
            response_breaches=response_breaches,
            resolution_breaches=resolution_breaches,
            breach_count=breach_count,
            avg_response_minutes=round_optional(mean(response_values), 2),
            p95_response_minutes=round_optional(percentile(response_values, 95), 2),
            avg_resolution_minutes=round_optional(mean(resolution_values), 2),
            p95_resolution_minutes=round_optional(percentile(resolution_values, 95), 2),
            mttr_minutes=round_optional(mean(resolution_values), 2),
        )


class ReportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, metrics: Sequence[SlaMetrics], formats: Sequence[ReportFormat], generated_at: datetime) -> List[Path]:
        paths: List[Path] = []
        base_name = f"sla_compliance_{generated_at.strftime('%Y%m%d_%H%M%S')}"

        for report_format in formats:
            if report_format == ReportFormat.JSON:
                paths.append(self._write_json(metrics, base_name, generated_at))
            elif report_format == ReportFormat.CSV:
                paths.append(self._write_csv(metrics, base_name))
            elif report_format == ReportFormat.HTML:
                paths.append(self._write_html(metrics, base_name, generated_at))
            else:
                raise ReportError(f"Formato de saída não suportado: {report_format}")

        return paths

    def _write_json(self, metrics: Sequence[SlaMetrics], base_name: str, generated_at: datetime) -> Path:
        path = self.output_dir / f"{base_name}.json"
        payload = {
            "report": "SLA Compliance Report",
            "generated_at": generated_at.isoformat(),
            "summary": build_summary(metrics),
            "metrics": [metric.to_dict() for metric in metrics],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _write_csv(self, metrics: Sequence[SlaMetrics], base_name: str) -> Path:
        path = self.output_dir / f"{base_name}.csv"
        rows = [metric.to_dict() for metric in metrics]
        fieldnames = list(rows[0].keys()) if rows else list(SlaMetrics.__dataclass_fields__.keys())

        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        return path

    def _write_html(self, metrics: Sequence[SlaMetrics], base_name: str, generated_at: datetime) -> Path:
        path = self.output_dir / f"{base_name}.html"
        summary = build_summary(metrics)
        html_content = render_html_report(metrics, summary, generated_at)
        path.write_text(html_content, encoding="utf-8")
        return path


def mean(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return float(statistics.mean(values))


def percentile(values: Sequence[float], percent: int) -> Optional[float]:
    if not values:
        return None
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    index = (len(sorted_values) - 1) * (percent / 100)
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = index - lower
    return float(sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight)


def round_optional(value: Optional[float], ndigits: int) -> Optional[float]:
    if value is None:
        return None
    return round(value, ndigits)


def build_summary(metrics: Sequence[SlaMetrics]) -> Dict[str, Any]:
    total_groups = len(metrics)
    compliant_groups = sum(1 for item in metrics if item.sla_met)
    non_compliant_groups = total_groups - compliant_groups
    total_incidents = sum(item.total_incidents for item in metrics)
    total_downtime = sum(item.total_downtime_minutes for item in metrics)

    worst_groups = sorted(metrics, key=lambda item: (item.availability_percent, -item.breach_count))[:5]

    return {
        "total_groups": total_groups,
        "compliant_groups": compliant_groups,
        "non_compliant_groups": non_compliant_groups,
        "compliance_rate_percent": round((compliant_groups / total_groups) * 100, 2) if total_groups else 100.0,
        "total_incidents": total_incidents,
        "total_downtime_minutes": round(total_downtime, 2),
        "worst_groups": [item.to_dict() for item in worst_groups],
    }


def render_html_report(metrics: Sequence[SlaMetrics], summary: Dict[str, Any], generated_at: datetime) -> str:
    rows = "\n".join(render_metric_row(metric) for metric in metrics)
    worst_rows = "\n".join(render_metric_row(SlaMetrics(**item)) for item in summary.get("worst_groups", []))

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SLA Compliance Report</title>
  <style>
    body {{
      font-family: Arial, Helvetica, sans-serif;
      margin: 0;
      background: #f6f7f9;
      color: #1f2937;
    }}
    header {{
      background: #111827;
      color: white;
      padding: 24px 32px;
    }}
    main {{
      padding: 24px 32px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .card {{
      background: white;
      border-radius: 12px;
      padding: 18px;
      box-shadow: 0 1px 5px rgba(0,0,0,.08);
    }}
    .label {{
      color: #6b7280;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .04em;
    }}
    .value {{
      font-size: 26px;
      font-weight: 700;
      margin-top: 8px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 1px 5px rgba(0,0,0,.08);
      margin-bottom: 28px;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid #e5e7eb;
      text-align: left;
      font-size: 13px;
      white-space: nowrap;
    }}
    th {{
      background: #f3f4f6;
      color: #374151;
    }}
    .ok {{
      color: #047857;
      font-weight: 700;
    }}
    .fail {{
      color: #b91c1c;
      font-weight: 700;
    }}
    h2 {{ margin-top: 32px; }}
    footer {{
      color: #6b7280;
      font-size: 12px;
      padding: 0 32px 24px;
    }}
  </style>
</head>
<body>
<header>
  <h1>SLA Compliance Report</h1>
  <p>Gerado em {html.escape(generated_at.isoformat())}</p>
</header>
<main>
  <section class="grid">
    <div class="card"><div class="label">Grupos analisados</div><div class="value">{summary['total_groups']}</div></div>
    <div class="card"><div class="label">Conformes</div><div class="value">{summary['compliant_groups']}</div></div>
    <div class="card"><div class="label">Não conformes</div><div class="value">{summary['non_compliant_groups']}</div></div>
    <div class="card"><div class="label">Taxa de conformidade</div><div class="value">{summary['compliance_rate_percent']}%</div></div>
    <div class="card"><div class="label">Incidentes</div><div class="value">{summary['total_incidents']}</div></div>
    <div class="card"><div class="label">Downtime total</div><div class="value">{summary['total_downtime_minutes']} min</div></div>
  </section>

  <h2>Piores grupos</h2>
  <table>
    {render_table_header()}
    <tbody>{worst_rows}</tbody>
  </table>

  <h2>Detalhamento completo</h2>
  <table>
    {render_table_header()}
    <tbody>{rows}</tbody>
  </table>
</main>
<footer>
  Relatório gerado por {APP_NAME}. Use os arquivos JSON/CSV para auditoria e integração com BI.
</footer>
</body>
</html>
"""


def render_table_header() -> str:
    return """
    <thead>
      <tr>
        <th>Período</th>
        <th>Serviço</th>
        <th>Severidade</th>
        <th>Incidentes</th>
        <th>Abertos</th>
        <th>Downtime</th>
        <th>Disponibilidade</th>
        <th>Target</th>
        <th>SLA</th>
        <th>Resp. Breaches</th>
        <th>Resol. Breaches</th>
        <th>MTTR</th>
        <th>P95 Resp.</th>
        <th>P95 Resol.</th>
      </tr>
    </thead>
    """


def render_metric_row(metric: SlaMetrics) -> str:
    status_class = "ok" if metric.sla_met else "fail"
    status_label = "OK" if metric.sla_met else "NÃO OK"
    return f"""
      <tr>
        <td>{escape(metric.period)}</td>
        <td>{escape(metric.service)}</td>
        <td>{escape(metric.severity)}</td>
        <td>{metric.total_incidents}</td>
        <td>{metric.open_incidents}</td>
        <td>{metric.total_downtime_minutes}</td>
        <td>{metric.availability_percent}%</td>
        <td>{metric.sla_target_percent}%</td>
        <td class="{status_class}">{status_label}</td>
        <td>{metric.response_breaches}</td>
        <td>{metric.resolution_breaches}</td>
        <td>{format_optional(metric.mttr_minutes)}</td>
        <td>{format_optional(metric.p95_response_minutes)}</td>
        <td>{format_optional(metric.p95_resolution_minutes)}</td>
      </tr>
    """


def escape(value: Any) -> str:
    return html.escape(str(value))


def format_optional(value: Optional[float]) -> str:
    return "-" if value is None else str(value)


def parse_formats(value: str) -> List[ReportFormat]:
    formats: List[ReportFormat] = []
    for item in value.split(","):
        normalized = item.strip().lower()
        if not normalized:
            continue
        try:
            formats.append(ReportFormat(normalized))
        except ValueError as exc:
            allowed = ", ".join(item.value for item in ReportFormat)
            raise argparse.ArgumentTypeError(f"Formato inválido: {normalized}. Permitidos: {allowed}") from exc
    return formats or [ReportFormat.HTML]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Gera relatório enterprise de conformidade SLA por serviço, severidade e período.",
    )
    parser.add_argument("--input", required=True, type=Path, help="Arquivo CSV ou JSON com incidentes.")
    parser.add_argument("--output-dir", default=Path("reports/output"), type=Path, help="Diretório de saída.")
    parser.add_argument("--period", default=Period.MONTHLY.value, choices=[item.value for item in Period])
    parser.add_argument("--target-sla", default=DEFAULT_TARGET_SLA, type=float, help="SLA alvo em porcentagem.")
    parser.add_argument("--format", default="html,json,csv", type=parse_formats, help="Formatos: html,json,csv")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args(argv)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)
    logger = logging.getLogger(APP_NAME)

    try:
        generated_at = datetime.now(tz=DEFAULT_TIMEZONE)
        logger.info("Carregando incidentes de %s", args.input)
        rows = FileLoader.load(args.input)

        logger.info("Validando %s registro(s)", len(rows))
        incidents = IncidentParser.parse_many(rows, now=generated_at)

        policy = SlaPolicy(target_sla_percent=args.target_sla)
        calculator = SlaCalculator(policy=policy, period=Period(args.period))

        logger.info("Calculando métricas SLA")
        metrics = calculator.calculate(incidents)

        writer = ReportWriter(args.output_dir)
        paths = writer.write(metrics=metrics, formats=args.format, generated_at=generated_at)

        logger.info("Relatório gerado com sucesso")
        for path in paths:
            print(path)

        return 0

    except ReportError as exc:
        logger.error("Erro de relatório: %s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001 - fallback seguro para CLI enterprise
        logger.exception("Erro inesperado: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(run())

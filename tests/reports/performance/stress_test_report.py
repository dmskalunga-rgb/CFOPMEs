#!/usr/bin/env python3
"""
reports/performance/stress_test_report.py

Enterprise-grade Stress Test Report generator.

Objetivo:
- Ler resultados de testes de carga/stress a partir de CSV ou JSON.
- Calcular métricas de performance por endpoint, método, cenário e janela de tempo.
- Gerar relatório executivo e técnico em JSON, CSV e HTML.
- Ser adequado para pipelines CI/CD, auditoria, SRE, QA, observabilidade e capacity planning.

Exemplo de execução:
    python reports/performance/stress_test_report.py \
        --input data/stress_results.csv \
        --output-dir reports/output \
        --format html,json,csv \
        --latency-threshold-ms 800 \
        --error-rate-threshold 1.0 \
        --throughput-drop-threshold 20

Formato esperado do CSV/JSON:
    request_id,scenario,service,endpoint,method,status_code,success,started_at,finished_at,latency_ms,bytes_sent,bytes_received,virtual_users,error_type

Campos mínimos:
    request_id: string
    scenario: string
    service: string
    endpoint: string
    method: GET|POST|PUT|PATCH|DELETE|...
    status_code: inteiro, opcional
    success: true|false|1|0|yes|no
    started_at: ISO datetime
    finished_at: ISO datetime ou vazio
    latency_ms: número, opcional; se vazio, calculado por finished_at - started_at
    virtual_users: número, opcional

JSON aceito:
    [ { ... }, { ... } ]
    ou
    { "results": [ { ... }, { ... } ] }
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import html
import json
import logging
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Sequence, Tuple


APP_NAME = "stress_test_report"
DEFAULT_TIMEZONE = timezone.utc
DEFAULT_LATENCY_THRESHOLD_MS = 800.0
DEFAULT_ERROR_RATE_THRESHOLD = 1.0
DEFAULT_THROUGHPUT_DROP_THRESHOLD = 20.0


class ReportFormat(str, Enum):
    JSON = "json"
    CSV = "csv"
    HTML = "html"


class HealthStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class ReportPolicy:
    latency_threshold_ms: float = DEFAULT_LATENCY_THRESHOLD_MS
    error_rate_threshold_percent: float = DEFAULT_ERROR_RATE_THRESHOLD
    throughput_drop_threshold_percent: float = DEFAULT_THROUGHPUT_DROP_THRESHOLD
    min_sample_size_for_strict_eval: int = 30


@dataclass(frozen=True)
class StressResult:
    request_id: str
    scenario: str
    service: str
    endpoint: str
    method: str
    status_code: Optional[int]
    success: bool
    started_at: datetime
    finished_at: Optional[datetime]
    latency_ms: float
    bytes_sent: Optional[float]
    bytes_received: Optional[float]
    virtual_users: Optional[int]
    error_type: Optional[str]
    raw: Dict[str, Any]


@dataclass(frozen=True)
class GroupKey:
    scenario: str
    service: str
    method: str
    endpoint: str


@dataclass
class StressMetrics:
    scenario: str
    service: str
    method: str
    endpoint: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    error_rate_percent: float
    throughput_rps: float
    avg_latency_ms: Optional[float]
    min_latency_ms: Optional[float]
    max_latency_ms: Optional[float]
    median_latency_ms: Optional[float]
    p90_latency_ms: Optional[float]
    p95_latency_ms: Optional[float]
    p99_latency_ms: Optional[float]
    total_bytes_sent: float
    total_bytes_received: float
    avg_virtual_users: Optional[float]
    max_virtual_users: Optional[int]
    duration_seconds: float
    latency_threshold_ms: float
    error_rate_threshold_percent: float
    health_status: str
    bottleneck_score: float
    primary_risk: str

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class ReportError(Exception):
    """Base exception for stress report failures."""


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
        if isinstance(payload, dict) and isinstance(payload.get("results"), list):
            return payload["results"]

        raise InputValidationError("JSON inválido. Esperado lista ou objeto com chave 'results'.")


class StressResultParser:
    @staticmethod
    def parse_many(rows: Iterable[Dict[str, Any]]) -> List[StressResult]:
        results: List[StressResult] = []
        errors: List[str] = []

        for index, row in enumerate(rows, start=1):
            try:
                results.append(StressResultParser.parse(row))
            except Exception as exc:  # noqa: BLE001 - agrega erros por linha para diagnóstico claro
                errors.append(f"linha={index}: {exc}")

        if errors:
            preview = "\n".join(errors[:30])
            extra = "" if len(errors) <= 30 else f"\n... e mais {len(errors) - 30} erro(s)."
            raise InputValidationError(f"Falha ao validar resultados:\n{preview}{extra}")

        return results

    @staticmethod
    def parse(row: Dict[str, Any]) -> StressResult:
        request_id = StressResultParser._required_str(row, "request_id")
        scenario = StressResultParser._required_str(row, "scenario")
        service = StressResultParser._required_str(row, "service")
        endpoint = StressResultParser._required_str(row, "endpoint")
        method = StressResultParser._required_str(row, "method").upper()
        status_code = StressResultParser._optional_int(row, "status_code")
        success = StressResultParser._parse_bool(row.get("success"))
        started_at = StressResultParser._parse_datetime(StressResultParser._required_str(row, "started_at"))
        finished_at_raw = StressResultParser._optional_str(row, "finished_at")
        finished_at = StressResultParser._parse_datetime(finished_at_raw) if finished_at_raw else None

        if finished_at and finished_at < started_at:
            raise ValueError("finished_at não pode ser menor que started_at")

        latency_ms = StressResultParser._optional_float(row, "latency_ms")
        if latency_ms is None:
            if finished_at is None:
                raise ValueError("latency_ms é obrigatório quando finished_at está vazio")
            latency_ms = max((finished_at - started_at).total_seconds() * 1000, 0.0)

        bytes_sent = StressResultParser._optional_float(row, "bytes_sent")
        bytes_received = StressResultParser._optional_float(row, "bytes_received")
        virtual_users = StressResultParser._optional_int(row, "virtual_users")
        error_type = StressResultParser._optional_str(row, "error_type")

        return StressResult(
            request_id=request_id,
            scenario=scenario,
            service=service,
            endpoint=endpoint,
            method=method,
            status_code=status_code,
            success=success,
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=float(latency_ms),
            bytes_sent=bytes_sent,
            bytes_received=bytes_received,
            virtual_users=virtual_users,
            error_type=error_type,
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
    def _optional_int(row: Dict[str, Any], key: str) -> Optional[int]:
        value = row.get(key)
        if value is None or str(value).strip() == "":
            return None
        try:
            return int(float(str(value).replace(",", ".")))
        except ValueError as exc:
            raise ValueError(f"campo {key} precisa ser inteiro") from exc

    @staticmethod
    def _parse_bool(value: Any) -> bool:
        text = str(value).strip().lower()
        if text in {"true", "1", "yes", "y", "sim", "s", "ok", "success", "sucesso"}:
            return True
        if text in {"false", "0", "no", "n", "nao", "não", "fail", "failed", "erro", "error"}:
            return False
        raise ValueError("campo success precisa ser booleano")

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


class StressMetricsCalculator:
    def __init__(self, policy: ReportPolicy) -> None:
        self.policy = policy

    def calculate(self, results: Sequence[StressResult]) -> List[StressMetrics]:
        groups: DefaultDict[GroupKey, List[StressResult]] = defaultdict(list)

        for result in results:
            key = GroupKey(
                scenario=result.scenario,
                service=result.service,
                method=result.method,
                endpoint=result.endpoint,
            )
            groups[key].append(result)

        metrics = [self._calculate_group(key, group) for key, group in groups.items()]
        return sorted(metrics, key=lambda item: (item.scenario, item.service, item.endpoint, item.method))

    def _calculate_group(self, key: GroupKey, group: Sequence[StressResult]) -> StressMetrics:
        total_requests = len(group)
        successful_requests = sum(1 for item in group if item.success)
        failed_requests = total_requests - successful_requests
        error_rate = (failed_requests / total_requests) * 100 if total_requests else 0.0

        latencies = [max(item.latency_ms, 0.0) for item in group]
        start_time = min(item.started_at for item in group)
        end_candidates = [item.finished_at for item in group if item.finished_at is not None]
        end_time = max(end_candidates) if end_candidates else max(item.started_at for item in group)
        duration_seconds = max((end_time - start_time).total_seconds(), 0.001)
        throughput_rps = total_requests / duration_seconds

        bytes_sent = sum(item.bytes_sent or 0.0 for item in group)
        bytes_received = sum(item.bytes_received or 0.0 for item in group)
        virtual_users = [item.virtual_users for item in group if item.virtual_users is not None]

        health_status, primary_risk = self._evaluate_health(
            total_requests=total_requests,
            error_rate=error_rate,
            p95_latency=percentile(latencies, 95),
            throughput_rps=throughput_rps,
        )
        bottleneck_score = self._bottleneck_score(
            error_rate=error_rate,
            p95_latency=percentile(latencies, 95),
            throughput_rps=throughput_rps,
        )

        return StressMetrics(
            scenario=key.scenario,
            service=key.service,
            method=key.method,
            endpoint=key.endpoint,
            total_requests=total_requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            error_rate_percent=round(error_rate, 4),
            throughput_rps=round(throughput_rps, 4),
            avg_latency_ms=round_optional(mean(latencies), 2),
            min_latency_ms=round_optional(min(latencies) if latencies else None, 2),
            max_latency_ms=round_optional(max(latencies) if latencies else None, 2),
            median_latency_ms=round_optional(median(latencies), 2),
            p90_latency_ms=round_optional(percentile(latencies, 90), 2),
            p95_latency_ms=round_optional(percentile(latencies, 95), 2),
            p99_latency_ms=round_optional(percentile(latencies, 99), 2),
            total_bytes_sent=round(bytes_sent, 2),
            total_bytes_received=round(bytes_received, 2),
            avg_virtual_users=round_optional(mean([float(item) for item in virtual_users]), 2),
            max_virtual_users=max(virtual_users) if virtual_users else None,
            duration_seconds=round(duration_seconds, 4),
            latency_threshold_ms=self.policy.latency_threshold_ms,
            error_rate_threshold_percent=self.policy.error_rate_threshold_percent,
            health_status=health_status.value,
            bottleneck_score=round(bottleneck_score, 4),
            primary_risk=primary_risk,
        )

    def _evaluate_health(
        self,
        total_requests: int,
        error_rate: float,
        p95_latency: Optional[float],
        throughput_rps: float,
    ) -> Tuple[HealthStatus, str]:
        if total_requests < self.policy.min_sample_size_for_strict_eval:
            return HealthStatus.WARN, "sample_size_low"

        latency_breach = p95_latency is not None and p95_latency > self.policy.latency_threshold_ms
        error_breach = error_rate > self.policy.error_rate_threshold_percent
        low_throughput = throughput_rps <= 0

        if error_breach and latency_breach:
            return HealthStatus.FAIL, "high_error_rate_and_latency"
        if error_breach:
            return HealthStatus.FAIL, "high_error_rate"
        if latency_breach:
            return HealthStatus.WARN, "high_p95_latency"
        if low_throughput:
            return HealthStatus.FAIL, "zero_or_invalid_throughput"

        return HealthStatus.PASS, "none"

    def _bottleneck_score(self, error_rate: float, p95_latency: Optional[float], throughput_rps: float) -> float:
        latency_score = 0.0
        if p95_latency is not None and self.policy.latency_threshold_ms > 0:
            latency_score = min((p95_latency / self.policy.latency_threshold_ms) * 40.0, 40.0)

        error_score = min((error_rate / max(self.policy.error_rate_threshold_percent, 0.001)) * 40.0, 40.0)
        throughput_score = 20.0 if throughput_rps <= 0 else 0.0
        return min(latency_score + error_score + throughput_score, 100.0)


class ReportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, metrics: Sequence[StressMetrics], formats: Sequence[ReportFormat], generated_at: datetime) -> List[Path]:
        paths: List[Path] = []
        base_name = f"stress_test_{generated_at.strftime('%Y%m%d_%H%M%S')}"

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

    def _write_json(self, metrics: Sequence[StressMetrics], base_name: str, generated_at: datetime) -> Path:
        path = self.output_dir / f"{base_name}.json"
        payload = {
            "report": "Stress Test Report",
            "generated_at": generated_at.isoformat(),
            "summary": build_summary(metrics),
            "metrics": [metric.to_dict() for metric in metrics],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _write_csv(self, metrics: Sequence[StressMetrics], base_name: str) -> Path:
        path = self.output_dir / f"{base_name}.csv"
        rows = [metric.to_dict() for metric in metrics]
        fieldnames = list(rows[0].keys()) if rows else list(StressMetrics.__dataclass_fields__.keys())

        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        return path

    def _write_html(self, metrics: Sequence[StressMetrics], base_name: str, generated_at: datetime) -> Path:
        path = self.output_dir / f"{base_name}.html"
        summary = build_summary(metrics)
        html_content = render_html_report(metrics, summary, generated_at)
        path.write_text(html_content, encoding="utf-8")
        return path


def mean(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return float(statistics.mean(values))


def median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return float(statistics.median(values))


def percentile(values: Sequence[float], percent: int) -> Optional[float]:
    if not values:
        return None
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    index = (len(sorted_values) - 1) * (percent / 100)
    lower = math.floor(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = index - lower
    return float(sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight)


def round_optional(value: Optional[float], ndigits: int) -> Optional[float]:
    if value is None:
        return None
    return round(value, ndigits)


def build_summary(metrics: Sequence[StressMetrics]) -> Dict[str, Any]:
    total_groups = len(metrics)
    total_requests = sum(item.total_requests for item in metrics)
    total_failures = sum(item.failed_requests for item in metrics)
    pass_groups = sum(1 for item in metrics if item.health_status == HealthStatus.PASS.value)
    warn_groups = sum(1 for item in metrics if item.health_status == HealthStatus.WARN.value)
    fail_groups = sum(1 for item in metrics if item.health_status == HealthStatus.FAIL.value)

    weighted_error_rate = (total_failures / total_requests) * 100 if total_requests else 0.0
    total_duration = sum(item.duration_seconds for item in metrics)
    avg_throughput = mean([item.throughput_rps for item in metrics]) or 0.0
    avg_p95 = mean([item.p95_latency_ms for item in metrics if item.p95_latency_ms is not None])

    top_bottlenecks = sorted(metrics, key=lambda item: item.bottleneck_score, reverse=True)[:10]

    return {
        "total_groups": total_groups,
        "total_requests": total_requests,
        "total_failures": total_failures,
        "weighted_error_rate_percent": round(weighted_error_rate, 4),
        "pass_groups": pass_groups,
        "warn_groups": warn_groups,
        "fail_groups": fail_groups,
        "avg_throughput_rps": round(avg_throughput, 4),
        "avg_p95_latency_ms": round_optional(avg_p95, 2),
        "total_group_duration_seconds": round(total_duration, 4),
        "top_bottlenecks": [item.to_dict() for item in top_bottlenecks],
    }


def render_html_report(metrics: Sequence[StressMetrics], summary: Dict[str, Any], generated_at: datetime) -> str:
    all_rows = "\n".join(render_metric_row(metric) for metric in metrics)
    bottleneck_rows = "\n".join(render_metric_row(StressMetrics(**item)) for item in summary.get("top_bottlenecks", []))

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stress Test Report</title>
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
    .pass {{ color: #047857; font-weight: 700; }}
    .warn {{ color: #b45309; font-weight: 700; }}
    .fail {{ color: #b91c1c; font-weight: 700; }}
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
  <h1>Stress Test Report</h1>
  <p>Gerado em {html.escape(generated_at.isoformat())}</p>
</header>
<main>
  <section class="grid">
    <div class="card"><div class="label">Grupos analisados</div><div class="value">{summary['total_groups']}</div></div>
    <div class="card"><div class="label">Requests</div><div class="value">{summary['total_requests']}</div></div>
    <div class="card"><div class="label">Falhas</div><div class="value">{summary['total_failures']}</div></div>
    <div class="card"><div class="label">Error rate</div><div class="value">{summary['weighted_error_rate_percent']}%</div></div>
    <div class="card"><div class="label">Throughput médio</div><div class="value">{summary['avg_throughput_rps']} rps</div></div>
    <div class="card"><div class="label">P95 médio</div><div class="value">{format_optional(summary['avg_p95_latency_ms'])} ms</div></div>
    <div class="card"><div class="label">PASS</div><div class="value">{summary['pass_groups']}</div></div>
    <div class="card"><div class="label">WARN</div><div class="value">{summary['warn_groups']}</div></div>
    <div class="card"><div class="label">FAIL</div><div class="value">{summary['fail_groups']}</div></div>
  </section>

  <h2>Principais gargalos</h2>
  <table>
    {render_table_header()}
    <tbody>{bottleneck_rows}</tbody>
  </table>

  <h2>Detalhamento completo</h2>
  <table>
    {render_table_header()}
    <tbody>{all_rows}</tbody>
  </table>
</main>
<footer>
  Relatório gerado por {APP_NAME}. Use JSON/CSV para integração com BI, APM, CI/CD ou auditoria técnica.
</footer>
</body>
</html>
"""


def render_table_header() -> str:
    return """
    <thead>
      <tr>
        <th>Status</th>
        <th>Cenário</th>
        <th>Serviço</th>
        <th>Método</th>
        <th>Endpoint</th>
        <th>Requests</th>
        <th>Falhas</th>
        <th>Error %</th>
        <th>RPS</th>
        <th>Avg ms</th>
        <th>P95 ms</th>
        <th>P99 ms</th>
        <th>VU médio</th>
        <th>VU máx.</th>
        <th>Score</th>
        <th>Risco</th>
      </tr>
    </thead>
    """


def render_metric_row(metric: StressMetrics) -> str:
    status_class = metric.health_status
    return f"""
      <tr>
        <td class="{status_class}">{escape(metric.health_status.upper())}</td>
        <td>{escape(metric.scenario)}</td>
        <td>{escape(metric.service)}</td>
        <td>{escape(metric.method)}</td>
        <td>{escape(metric.endpoint)}</td>
        <td>{metric.total_requests}</td>
        <td>{metric.failed_requests}</td>
        <td>{metric.error_rate_percent}%</td>
        <td>{metric.throughput_rps}</td>
        <td>{format_optional(metric.avg_latency_ms)}</td>
        <td>{format_optional(metric.p95_latency_ms)}</td>
        <td>{format_optional(metric.p99_latency_ms)}</td>
        <td>{format_optional(metric.avg_virtual_users)}</td>
        <td>{format_optional(metric.max_virtual_users)}</td>
        <td>{metric.bottleneck_score}</td>
        <td>{escape(metric.primary_risk)}</td>
      </tr>
    """


def escape(value: Any) -> str:
    return html.escape(str(value))


def format_optional(value: Any) -> str:
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
        description="Gera relatório enterprise de stress test por cenário, serviço e endpoint.",
    )
    parser.add_argument("--input", required=True, type=Path, help="Arquivo CSV ou JSON com resultados do stress test.")
    parser.add_argument("--output-dir", default=Path("reports/output"), type=Path, help="Diretório de saída.")
    parser.add_argument("--format", default="html,json,csv", type=parse_formats, help="Formatos: html,json,csv")
    parser.add_argument("--latency-threshold-ms", default=DEFAULT_LATENCY_THRESHOLD_MS, type=float)
    parser.add_argument("--error-rate-threshold", default=DEFAULT_ERROR_RATE_THRESHOLD, type=float)
    parser.add_argument("--throughput-drop-threshold", default=DEFAULT_THROUGHPUT_DROP_THRESHOLD, type=float)
    parser.add_argument("--min-sample-size", default=30, type=int)
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
        logger.info("Carregando resultados de %s", args.input)
        rows = FileLoader.load(args.input)

        logger.info("Validando %s registro(s)", len(rows))
        results = StressResultParser.parse_many(rows)

        policy = ReportPolicy(
            latency_threshold_ms=args.latency_threshold_ms,
            error_rate_threshold_percent=args.error_rate_threshold,
            throughput_drop_threshold_percent=args.throughput_drop_threshold,
            min_sample_size_for_strict_eval=args.min_sample_size,
        )
        calculator = StressMetricsCalculator(policy=policy)

        logger.info("Calculando métricas de stress test")
        metrics = calculator.calculate(results)

        writer = ReportWriter(args.output_dir)
        paths = writer.write(metrics=metrics, formats=args.format, generated_at=generated_at)

        logger.info("Relatório gerado com sucesso")
        for path in paths:
            print(path)

        return 0

    except ReportError as exc:
        logger.error("Erro de relatório: %s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001 - fallback seguro para execução CLI enterprise
        logger.exception("Erro inesperado: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(run())

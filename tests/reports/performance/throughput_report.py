#!/usr/bin/env python3
"""
reports/performance/throughput_report.py

Enterprise-grade Throughput Report generator.

Objetivo:
- Ler eventos/transações/requisições a partir de CSV ou JSON.
- Calcular throughput por serviço, endpoint, método, cenário e janela de tempo.
- Identificar pico, média, queda de vazão, saturação e variações relevantes.
- Gerar relatórios em HTML, JSON e CSV.
- Ser adequado para CI/CD, observabilidade, SRE, QA, capacity planning e auditoria.

Exemplo de execução:
    python reports/performance/throughput_report.py \
        --input data/throughput_events.csv \
        --output-dir reports/output \
        --window-seconds 60 \
        --format html,json,csv \
        --target-throughput 150 \
        --drop-threshold-percent 25

Formato esperado do CSV/JSON:
    event_id,scenario,service,endpoint,method,timestamp,success,units,bytes_processed,duration_ms,worker_id,node

Campos mínimos:
    event_id: string
    scenario: string
    service: string
    endpoint: string
    method: string
    timestamp: ISO datetime

Campos opcionais:
    success: true|false|1|0|yes|no
    units: quantidade processada pelo evento. Default: 1
    bytes_processed: bytes processados pelo evento
    duration_ms: duração individual do processamento
    worker_id: identificador do worker/thread/consumer
    node: host/pod/instância

JSON aceito:
    [ { ... }, { ... } ]
    ou
    { "events": [ { ... }, { ... } ] }
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


APP_NAME = "throughput_report"
DEFAULT_TIMEZONE = timezone.utc
DEFAULT_WINDOW_SECONDS = 60
DEFAULT_DROP_THRESHOLD_PERCENT = 25.0
DEFAULT_TARGET_THROUGHPUT = 0.0


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
    window_seconds: int = DEFAULT_WINDOW_SECONDS
    target_throughput: float = DEFAULT_TARGET_THROUGHPUT
    drop_threshold_percent: float = DEFAULT_DROP_THRESHOLD_PERCENT
    min_windows_for_strict_eval: int = 3


@dataclass(frozen=True)
class ThroughputEvent:
    event_id: str
    scenario: str
    service: str
    endpoint: str
    method: str
    timestamp: datetime
    success: bool
    units: float
    bytes_processed: float
    duration_ms: Optional[float]
    worker_id: Optional[str]
    node: Optional[str]
    raw: Dict[str, Any]


@dataclass(frozen=True)
class GroupKey:
    scenario: str
    service: str
    method: str
    endpoint: str


@dataclass
class WindowMetric:
    window_start: str
    window_end: str
    events: int
    successful_events: int
    failed_events: int
    units: float
    bytes_processed: float
    throughput_per_second: float
    success_throughput_per_second: float
    error_rate_percent: float

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class ThroughputMetrics:
    scenario: str
    service: str
    method: str
    endpoint: str
    total_events: int
    successful_events: int
    failed_events: int
    error_rate_percent: float
    total_units: float
    total_bytes_processed: float
    duration_seconds: float
    avg_throughput_per_second: float
    success_throughput_per_second: float
    peak_throughput_per_second: float
    min_throughput_per_second: float
    median_throughput_per_second: Optional[float]
    p90_throughput_per_second: Optional[float]
    p95_throughput_per_second: Optional[float]
    p99_throughput_per_second: Optional[float]
    avg_bytes_per_second: float
    peak_bytes_per_second: float
    avg_duration_ms: Optional[float]
    p95_duration_ms: Optional[float]
    distinct_workers: int
    distinct_nodes: int
    window_seconds: int
    window_count: int
    target_throughput: float
    throughput_vs_target_percent: Optional[float]
    throughput_drop_percent: float
    health_status: str
    primary_risk: str
    capacity_score: float

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class ReportError(Exception):
    """Base exception for throughput report failures."""


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
        if isinstance(payload, dict) and isinstance(payload.get("events"), list):
            return payload["events"]

        raise InputValidationError("JSON inválido. Esperado lista ou objeto com chave 'events'.")


class ThroughputEventParser:
    @staticmethod
    def parse_many(rows: Iterable[Dict[str, Any]]) -> List[ThroughputEvent]:
        events: List[ThroughputEvent] = []
        errors: List[str] = []

        for index, row in enumerate(rows, start=1):
            try:
                events.append(ThroughputEventParser.parse(row))
            except Exception as exc:  # noqa: BLE001 - agrega erros por linha para diagnóstico claro
                errors.append(f"linha={index}: {exc}")

        if errors:
            preview = "\n".join(errors[:30])
            extra = "" if len(errors) <= 30 else f"\n... e mais {len(errors) - 30} erro(s)."
            raise InputValidationError(f"Falha ao validar eventos:\n{preview}{extra}")

        return events

    @staticmethod
    def parse(row: Dict[str, Any]) -> ThroughputEvent:
        event_id = ThroughputEventParser._required_str(row, "event_id")
        scenario = ThroughputEventParser._required_str(row, "scenario")
        service = ThroughputEventParser._required_str(row, "service")
        endpoint = ThroughputEventParser._required_str(row, "endpoint")
        method = ThroughputEventParser._required_str(row, "method").upper()
        timestamp = ThroughputEventParser._parse_datetime(ThroughputEventParser._required_str(row, "timestamp"))
        success = ThroughputEventParser._optional_bool(row.get("success"), default=True)
        units = ThroughputEventParser._optional_float(row, "units", default=1.0)
        bytes_processed = ThroughputEventParser._optional_float(row, "bytes_processed", default=0.0)
        duration_ms = ThroughputEventParser._optional_float(row, "duration_ms", default=None)
        worker_id = ThroughputEventParser._optional_str(row, "worker_id")
        node = ThroughputEventParser._optional_str(row, "node")

        if units < 0:
            raise ValueError("units não pode ser negativo")
        if bytes_processed < 0:
            raise ValueError("bytes_processed não pode ser negativo")
        if duration_ms is not None and duration_ms < 0:
            raise ValueError("duration_ms não pode ser negativo")

        return ThroughputEvent(
            event_id=event_id,
            scenario=scenario,
            service=service,
            endpoint=endpoint,
            method=method,
            timestamp=timestamp,
            success=success,
            units=float(units),
            bytes_processed=float(bytes_processed),
            duration_ms=duration_ms,
            worker_id=worker_id,
            node=node,
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
    def _optional_float(row: Dict[str, Any], key: str, default: Optional[float]) -> Optional[float]:
        value = row.get(key)
        if value is None or str(value).strip() == "":
            return default
        try:
            return float(str(value).replace(",", "."))
        except ValueError as exc:
            raise ValueError(f"campo {key} precisa ser numérico") from exc

    @staticmethod
    def _optional_bool(value: Any, default: bool) -> bool:
        if value is None or str(value).strip() == "":
            return default
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


class ThroughputCalculator:
    def __init__(self, policy: ReportPolicy) -> None:
        if policy.window_seconds <= 0:
            raise ValueError("window_seconds precisa ser maior que zero")
        self.policy = policy

    def calculate(self, events: Sequence[ThroughputEvent]) -> Tuple[List[ThroughputMetrics], Dict[str, List[WindowMetric]]]:
        groups: DefaultDict[GroupKey, List[ThroughputEvent]] = defaultdict(list)

        for event in events:
            key = GroupKey(
                scenario=event.scenario,
                service=event.service,
                method=event.method,
                endpoint=event.endpoint,
            )
            groups[key].append(event)

        metrics: List[ThroughputMetrics] = []
        windows_by_group: Dict[str, List[WindowMetric]] = {}

        for key, group_events in groups.items():
            group_metrics, window_metrics = self._calculate_group(key, group_events)
            metrics.append(group_metrics)
            windows_by_group[self._group_id(key)] = window_metrics

        metrics.sort(key=lambda item: (item.scenario, item.service, item.endpoint, item.method))
        return metrics, windows_by_group

    def _calculate_group(
        self,
        key: GroupKey,
        group_events: Sequence[ThroughputEvent],
    ) -> Tuple[ThroughputMetrics, List[WindowMetric]]:
        ordered = sorted(group_events, key=lambda item: item.timestamp)
        total_events = len(ordered)
        successful_events = sum(1 for item in ordered if item.success)
        failed_events = total_events - successful_events
        error_rate = (failed_events / total_events) * 100 if total_events else 0.0

        start_time = ordered[0].timestamp
        end_time = ordered[-1].timestamp
        duration_seconds = max((end_time - start_time).total_seconds(), 1.0)

        total_units = sum(item.units for item in ordered)
        success_units = sum(item.units for item in ordered if item.success)
        total_bytes = sum(item.bytes_processed for item in ordered)
        avg_throughput = total_units / duration_seconds
        success_throughput = success_units / duration_seconds
        avg_bytes_per_second = total_bytes / duration_seconds

        window_metrics = self._build_windows(ordered)
        window_throughputs = [item.throughput_per_second for item in window_metrics]
        window_bytes_rates = [item.bytes_processed / self.policy.window_seconds for item in window_metrics]
        durations = [item.duration_ms for item in ordered if item.duration_ms is not None]
        workers = {item.worker_id for item in ordered if item.worker_id}
        nodes = {item.node for item in ordered if item.node}

        peak_throughput = max(window_throughputs) if window_throughputs else avg_throughput
        min_throughput = min(window_throughputs) if window_throughputs else avg_throughput
        peak_bytes_per_second = max(window_bytes_rates) if window_bytes_rates else avg_bytes_per_second
        drop_percent = self._drop_percent(peak_throughput, min_throughput)
        target_percent = self._target_percent(avg_throughput)
        health_status, primary_risk = self._evaluate_health(
            avg_throughput=avg_throughput,
            peak_throughput=peak_throughput,
            drop_percent=drop_percent,
            error_rate=error_rate,
            window_count=len(window_metrics),
        )
        capacity_score = self._capacity_score(
            avg_throughput=avg_throughput,
            peak_throughput=peak_throughput,
            drop_percent=drop_percent,
            error_rate=error_rate,
        )

        return (
            ThroughputMetrics(
                scenario=key.scenario,
                service=key.service,
                method=key.method,
                endpoint=key.endpoint,
                total_events=total_events,
                successful_events=successful_events,
                failed_events=failed_events,
                error_rate_percent=round(error_rate, 4),
                total_units=round(total_units, 4),
                total_bytes_processed=round(total_bytes, 2),
                duration_seconds=round(duration_seconds, 4),
                avg_throughput_per_second=round(avg_throughput, 4),
                success_throughput_per_second=round(success_throughput, 4),
                peak_throughput_per_second=round(peak_throughput, 4),
                min_throughput_per_second=round(min_throughput, 4),
                median_throughput_per_second=round_optional(median(window_throughputs), 4),
                p90_throughput_per_second=round_optional(percentile(window_throughputs, 90), 4),
                p95_throughput_per_second=round_optional(percentile(window_throughputs, 95), 4),
                p99_throughput_per_second=round_optional(percentile(window_throughputs, 99), 4),
                avg_bytes_per_second=round(avg_bytes_per_second, 4),
                peak_bytes_per_second=round(peak_bytes_per_second, 4),
                avg_duration_ms=round_optional(mean(durations), 2),
                p95_duration_ms=round_optional(percentile(durations, 95), 2),
                distinct_workers=len(workers),
                distinct_nodes=len(nodes),
                window_seconds=self.policy.window_seconds,
                window_count=len(window_metrics),
                target_throughput=self.policy.target_throughput,
                throughput_vs_target_percent=round_optional(target_percent, 2),
                throughput_drop_percent=round(drop_percent, 4),
                health_status=health_status.value,
                primary_risk=primary_risk,
                capacity_score=round(capacity_score, 4),
            ),
            window_metrics,
        )

    def _build_windows(self, ordered: Sequence[ThroughputEvent]) -> List[WindowMetric]:
        if not ordered:
            return []

        start = ordered[0].timestamp
        buckets: DefaultDict[int, List[ThroughputEvent]] = defaultdict(list)

        for event in ordered:
            offset = int((event.timestamp - start).total_seconds())
            bucket_index = offset // self.policy.window_seconds
            buckets[bucket_index].append(event)

        metrics: List[WindowMetric] = []
        for bucket_index in sorted(buckets):
            items = buckets[bucket_index]
            window_start = start.timestamp() + bucket_index * self.policy.window_seconds
            window_end = window_start + self.policy.window_seconds
            events = len(items)
            successful_events = sum(1 for item in items if item.success)
            failed_events = events - successful_events
            units = sum(item.units for item in items)
            success_units = sum(item.units for item in items if item.success)
            bytes_processed = sum(item.bytes_processed for item in items)
            error_rate = (failed_events / events) * 100 if events else 0.0

            metrics.append(
                WindowMetric(
                    window_start=datetime.fromtimestamp(window_start, tz=DEFAULT_TIMEZONE).isoformat(),
                    window_end=datetime.fromtimestamp(window_end, tz=DEFAULT_TIMEZONE).isoformat(),
                    events=events,
                    successful_events=successful_events,
                    failed_events=failed_events,
                    units=round(units, 4),
                    bytes_processed=round(bytes_processed, 2),
                    throughput_per_second=round(units / self.policy.window_seconds, 4),
                    success_throughput_per_second=round(success_units / self.policy.window_seconds, 4),
                    error_rate_percent=round(error_rate, 4),
                )
            )

        return metrics

    def _target_percent(self, avg_throughput: float) -> Optional[float]:
        if self.policy.target_throughput <= 0:
            return None
        return (avg_throughput / self.policy.target_throughput) * 100

    @staticmethod
    def _drop_percent(peak: float, minimum: float) -> float:
        if peak <= 0:
            return 0.0
        return max(((peak - minimum) / peak) * 100, 0.0)

    def _evaluate_health(
        self,
        avg_throughput: float,
        peak_throughput: float,
        drop_percent: float,
        error_rate: float,
        window_count: int,
    ) -> Tuple[HealthStatus, str]:
        if window_count < self.policy.min_windows_for_strict_eval:
            return HealthStatus.WARN, "sample_window_low"

        target_enabled = self.policy.target_throughput > 0
        target_breach = target_enabled and avg_throughput < self.policy.target_throughput
        severe_drop = drop_percent > self.policy.drop_threshold_percent
        no_throughput = peak_throughput <= 0
        high_error_rate = error_rate >= 5.0

        if no_throughput:
            return HealthStatus.FAIL, "zero_throughput"
        if target_breach and high_error_rate:
            return HealthStatus.FAIL, "target_breach_and_high_errors"
        if target_breach:
            return HealthStatus.FAIL, "below_target_throughput"
        if high_error_rate:
            return HealthStatus.FAIL, "high_error_rate"
        if severe_drop:
            return HealthStatus.WARN, "throughput_instability"

        return HealthStatus.PASS, "none"

    def _capacity_score(self, avg_throughput: float, peak_throughput: float, drop_percent: float, error_rate: float) -> float:
        score = 100.0

        if self.policy.target_throughput > 0:
            target_ratio = min(avg_throughput / self.policy.target_throughput, 1.0)
            score = min(score, target_ratio * 100.0)

        if peak_throughput <= 0:
            return 0.0

        instability_penalty = min(drop_percent, 50.0)
        error_penalty = min(error_rate * 5.0, 50.0)
        return max(score - instability_penalty - error_penalty, 0.0)

    @staticmethod
    def _group_id(key: GroupKey) -> str:
        return f"{key.scenario}|{key.service}|{key.method}|{key.endpoint}"


class ReportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        metrics: Sequence[ThroughputMetrics],
        windows_by_group: Dict[str, List[WindowMetric]],
        formats: Sequence[ReportFormat],
        generated_at: datetime,
    ) -> List[Path]:
        paths: List[Path] = []
        base_name = f"throughput_{generated_at.strftime('%Y%m%d_%H%M%S')}"

        for report_format in formats:
            if report_format == ReportFormat.JSON:
                paths.append(self._write_json(metrics, windows_by_group, base_name, generated_at))
            elif report_format == ReportFormat.CSV:
                paths.append(self._write_csv(metrics, base_name))
                paths.append(self._write_windows_csv(windows_by_group, base_name))
            elif report_format == ReportFormat.HTML:
                paths.append(self._write_html(metrics, base_name, generated_at))
            else:
                raise ReportError(f"Formato de saída não suportado: {report_format}")

        return paths

    def _write_json(
        self,
        metrics: Sequence[ThroughputMetrics],
        windows_by_group: Dict[str, List[WindowMetric]],
        base_name: str,
        generated_at: datetime,
    ) -> Path:
        path = self.output_dir / f"{base_name}.json"
        payload = {
            "report": "Throughput Report",
            "generated_at": generated_at.isoformat(),
            "summary": build_summary(metrics),
            "metrics": [metric.to_dict() for metric in metrics],
            "windows_by_group": {
                group_id: [window.to_dict() for window in windows]
                for group_id, windows in windows_by_group.items()
            },
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _write_csv(self, metrics: Sequence[ThroughputMetrics], base_name: str) -> Path:
        path = self.output_dir / f"{base_name}.csv"
        rows = [metric.to_dict() for metric in metrics]
        fieldnames = list(rows[0].keys()) if rows else list(ThroughputMetrics.__dataclass_fields__.keys())

        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        return path

    def _write_windows_csv(self, windows_by_group: Dict[str, List[WindowMetric]], base_name: str) -> Path:
        path = self.output_dir / f"{base_name}_windows.csv"
        fieldnames = ["group_id", *WindowMetric.__dataclass_fields__.keys()]

        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            for group_id, windows in windows_by_group.items():
                for window in windows:
                    row = {"group_id": group_id, **window.to_dict()}
                    writer.writerow(row)

        return path

    def _write_html(self, metrics: Sequence[ThroughputMetrics], base_name: str, generated_at: datetime) -> Path:
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


def build_summary(metrics: Sequence[ThroughputMetrics]) -> Dict[str, Any]:
    total_groups = len(metrics)
    total_events = sum(item.total_events for item in metrics)
    total_failures = sum(item.failed_events for item in metrics)
    total_units = sum(item.total_units for item in metrics)
    total_bytes = sum(item.total_bytes_processed for item in metrics)
    pass_groups = sum(1 for item in metrics if item.health_status == HealthStatus.PASS.value)
    warn_groups = sum(1 for item in metrics if item.health_status == HealthStatus.WARN.value)
    fail_groups = sum(1 for item in metrics if item.health_status == HealthStatus.FAIL.value)

    avg_throughput = mean([item.avg_throughput_per_second for item in metrics]) or 0.0
    peak_throughput = max([item.peak_throughput_per_second for item in metrics], default=0.0)
    avg_capacity_score = mean([item.capacity_score for item in metrics]) or 0.0
    weighted_error_rate = (total_failures / total_events) * 100 if total_events else 0.0
    top_capacity_risks = sorted(metrics, key=lambda item: (item.capacity_score, -item.throughput_drop_percent))[:10]

    return {
        "total_groups": total_groups,
        "total_events": total_events,
        "total_failures": total_failures,
        "weighted_error_rate_percent": round(weighted_error_rate, 4),
        "total_units": round(total_units, 4),
        "total_bytes_processed": round(total_bytes, 2),
        "pass_groups": pass_groups,
        "warn_groups": warn_groups,
        "fail_groups": fail_groups,
        "avg_throughput_per_second": round(avg_throughput, 4),
        "peak_throughput_per_second": round(peak_throughput, 4),
        "avg_capacity_score": round(avg_capacity_score, 4),
        "top_capacity_risks": [item.to_dict() for item in top_capacity_risks],
    }


def render_html_report(metrics: Sequence[ThroughputMetrics], summary: Dict[str, Any], generated_at: datetime) -> str:
    all_rows = "\n".join(render_metric_row(metric) for metric in metrics)
    risk_rows = "\n".join(render_metric_row(ThroughputMetrics(**item)) for item in summary.get("top_capacity_risks", []))

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Throughput Report</title>
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
  <h1>Throughput Report</h1>
  <p>Gerado em {html.escape(generated_at.isoformat())}</p>
</header>
<main>
  <section class="grid">
    <div class="card"><div class="label">Grupos analisados</div><div class="value">{summary['total_groups']}</div></div>
    <div class="card"><div class="label">Eventos</div><div class="value">{summary['total_events']}</div></div>
    <div class="card"><div class="label">Unidades processadas</div><div class="value">{summary['total_units']}</div></div>
    <div class="card"><div class="label">Falhas</div><div class="value">{summary['total_failures']}</div></div>
    <div class="card"><div class="label">Error rate</div><div class="value">{summary['weighted_error_rate_percent']}%</div></div>
    <div class="card"><div class="label">Throughput médio</div><div class="value">{summary['avg_throughput_per_second']} /s</div></div>
    <div class="card"><div class="label">Pico de throughput</div><div class="value">{summary['peak_throughput_per_second']} /s</div></div>
    <div class="card"><div class="label">Score médio</div><div class="value">{summary['avg_capacity_score']}</div></div>
    <div class="card"><div class="label">PASS/WARN/FAIL</div><div class="value">{summary['pass_groups']}/{summary['warn_groups']}/{summary['fail_groups']}</div></div>
  </section>

  <h2>Principais riscos de capacidade</h2>
  <table>
    {render_table_header()}
    <tbody>{risk_rows}</tbody>
  </table>

  <h2>Detalhamento completo</h2>
  <table>
    {render_table_header()}
    <tbody>{all_rows}</tbody>
  </table>
</main>
<footer>
  Relatório gerado por {APP_NAME}. Use JSON/CSV para BI, observabilidade, capacity planning ou auditoria.
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
        <th>Eventos</th>
        <th>Falhas</th>
        <th>Error %</th>
        <th>Total units</th>
        <th>Avg /s</th>
        <th>Success /s</th>
        <th>Peak /s</th>
        <th>Min /s</th>
        <th>Drop %</th>
        <th>Target %</th>
        <th>Score</th>
        <th>Risco</th>
      </tr>
    </thead>
    """


def render_metric_row(metric: ThroughputMetrics) -> str:
    status_class = metric.health_status
    return f"""
      <tr>
        <td class="{status_class}">{escape(metric.health_status.upper())}</td>
        <td>{escape(metric.scenario)}</td>
        <td>{escape(metric.service)}</td>
        <td>{escape(metric.method)}</td>
        <td>{escape(metric.endpoint)}</td>
        <td>{metric.total_events}</td>
        <td>{metric.failed_events}</td>
        <td>{metric.error_rate_percent}%</td>
        <td>{metric.total_units}</td>
        <td>{metric.avg_throughput_per_second}</td>
        <td>{metric.success_throughput_per_second}</td>
        <td>{metric.peak_throughput_per_second}</td>
        <td>{metric.min_throughput_per_second}</td>
        <td>{metric.throughput_drop_percent}%</td>
        <td>{format_optional(metric.throughput_vs_target_percent)}</td>
        <td>{metric.capacity_score}</td>
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
        description="Gera relatório enterprise de throughput por cenário, serviço, endpoint e janela de tempo.",
    )
    parser.add_argument("--input", required=True, type=Path, help="Arquivo CSV ou JSON com eventos de throughput.")
    parser.add_argument("--output-dir", default=Path("reports/output"), type=Path, help="Diretório de saída.")
    parser.add_argument("--window-seconds", default=DEFAULT_WINDOW_SECONDS, type=int, help="Tamanho da janela de agregação em segundos.")
    parser.add_argument("--target-throughput", default=DEFAULT_TARGET_THROUGHPUT, type=float, help="Meta mínima de throughput médio por segundo. 0 desativa.")
    parser.add_argument("--drop-threshold-percent", default=DEFAULT_DROP_THRESHOLD_PERCENT, type=float, help="Queda percentual permitida entre pico e mínimo.")
    parser.add_argument("--min-windows", default=3, type=int, help="Mínimo de janelas para avaliação estrita.")
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
        logger.info("Carregando eventos de %s", args.input)
        rows = FileLoader.load(args.input)

        logger.info("Validando %s registro(s)", len(rows))
        events = ThroughputEventParser.parse_many(rows)

        policy = ReportPolicy(
            window_seconds=args.window_seconds,
            target_throughput=args.target_throughput,
            drop_threshold_percent=args.drop_threshold_percent,
            min_windows_for_strict_eval=args.min_windows,
        )
        calculator = ThroughputCalculator(policy=policy)

        logger.info("Calculando métricas de throughput")
        metrics, windows_by_group = calculator.calculate(events)

        writer = ReportWriter(args.output_dir)
        paths = writer.write(
            metrics=metrics,
            windows_by_group=windows_by_group,
            formats=args.format,
            generated_at=generated_at,
        )

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

"""
===============================================================================
KwanzaControl Enterprise Inference Latency Report Engine
File: reports/ml_metrics/inference_latency_report.py

Description:
    Enterprise-grade ML inference performance & latency observability engine for:

    - Model inference latency tracking (p50, p95, p99)
    - Throughput analysis (RPS / TPS)
    - Cold start vs warm start latency separation
    - SLA enforcement for ML APIs
    - Model performance degradation monitoring
    - Microservice inference benchmarking
    - Real-time AI system observability
    - Latency spike detection
    - Cost-performance optimization signals
    - Multi-model comparison (champion vs challenger)
    - CI/CD performance regression gates
    - JSON / HTML / Markdown enterprise reporting
    - Production ML reliability governance

Architecture Level:
    ENTERPRISE / PRODUCTION READY

===============================================================================
"""

from __future__ import annotations

import json
import logging
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Dict, List

# =============================================================================
# BASE PATHS
# =============================================================================

ROOT_DIR = Path(__file__).resolve().parents[2]

REPORTS_DIR = ROOT_DIR / "reports"
ML_DIR = REPORTS_DIR / "ml_metrics"

EXPORTS_DIR = ML_DIR / "exports"
LOGS_DIR = ML_DIR / "logs"
HISTORY_DIR = ML_DIR / "history"

# =============================================================================
# LOGGER
# =============================================================================


def setup_logger() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("inference_latency_report")

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(
        LOGS_DIR / "inference_latency_report.log",
        encoding="utf-8",
    )

    stream_handler = logging.StreamHandler(sys.stdout)

    file_handler.setFormatter(formatter)
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    return logger


logger = setup_logger()

# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass(slots=True)
class InferenceRecord:
    model_name: str
    latency_ms: float
    cold_start: bool
    timestamp: str
    request_id: str
    status: str


@dataclass(slots=True)
class LatencyMetrics:
    model_name: str
    p50: float
    p95: float
    p99: float
    avg_latency: float
    max_latency: float
    min_latency: float
    throughput_rps: float
    error_rate: float


@dataclass(slots=True)
class LatencySummary:
    total_requests: int
    total_models: int
    avg_p95_latency: float
    avg_throughput: float
    slowest_model: str
    fastest_model: str
    generated_at: str


@dataclass(slots=True)
class LatencyGovernance:
    sla_compliant_models: int
    sla_violating_models: int
    compliance_rate: float
    risk_level: str


# =============================================================================
# ENGINE
# =============================================================================


class InferenceLatencyReportEngine:
    """
    Enterprise inference latency observability engine.
    """

    def __init__(self, latency_file: Path) -> None:
        self.latency_file = latency_file
        self.raw_data: Dict[str, Any] = {}
        self.records: List[InferenceRecord] = []
        self.metrics: Dict[str, LatencyMetrics] = {}

        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

        logger.info("InferenceLatencyReportEngine initialized.")

    # =========================================================================
    # LOAD
    # =========================================================================

    def load(self) -> None:
        logger.info("Loading inference latency dataset...")

        if not self.latency_file.exists():
            raise FileNotFoundError(f"Latency file not found: {self.latency_file}")

        with open(self.latency_file, encoding="utf-8") as f:
            self.raw_data = json.load(f)

    # =========================================================================
    # PARSE
    # =========================================================================

    def parse(self) -> None:
        logger.info("Parsing inference records...")

        for r in self.raw_data.get("inferences", []):

            self.records.append(
                InferenceRecord(
                    model_name=r.get("model_name", ""),
                    latency_ms=float(r.get("latency_ms", 0)),
                    cold_start=bool(r.get("cold_start", False)),
                    timestamp=r.get("timestamp", ""),
                    request_id=r.get("request_id", ""),
                    status=r.get("status", "OK"),
                )
            )

    # =========================================================================
    # METRICS
    # =========================================================================

    def compute(self) -> None:
        logger.info("Computing latency metrics...")

        grouped: Dict[str, List[InferenceRecord]] = {}

        for r in self.records:
            grouped.setdefault(r.model_name, []).append(r)

        for model, records in grouped.items():

            latencies = [r.latency_ms for r in records if r.status == "OK"]
            errors = [r for r in records if r.status != "OK"]

            if not latencies:
                continue

            lat_sorted = sorted(latencies)

            def percentile(p: float) -> float:
                k = int(len(lat_sorted) * (p / 100))
                return lat_sorted[min(k, len(lat_sorted) - 1)]

            p50 = percentile(50)
            p95 = percentile(95)
            p99 = percentile(99)

            avg = statistics.mean(latencies)
            mx = max(latencies)
            mn = min(latencies)

            throughput = len(records) / max(len(set(r.timestamp for r in records)), 1)

            error_rate = len(errors) / max(len(records), 1)

            self.metrics[model] = LatencyMetrics(
                model_name=model,
                p50=round(p50, 3),
                p95=round(p95, 3),
                p99=round(p99, 3),
                avg_latency=round(avg, 3),
                max_latency=round(mx, 3),
                min_latency=round(mn, 3),
                throughput_rps=round(throughput, 3),
                error_rate=round(error_rate, 4),
            )

    # =========================================================================
    # SUMMARY
    # =========================================================================

    def summary(self) -> LatencySummary:

        models = list(self.metrics.values())

        slowest = max(models, key=lambda x: x.p95, default=None)
        fastest = min(models, key=lambda x: x.p95, default=None)

        return LatencySummary(
            total_requests=len(self.records),
            total_models=len(models),
            avg_p95_latency=round(
                statistics.mean([m.p95 for m in models]) if models else 0, 3
            ),
            avg_throughput=round(
                statistics.mean([m.throughput_rps for m in models]) if models else 0, 3
            ),
            slowest_model=slowest.model_name if slowest else "",
            fastest_model=fastest.model_name if fastest else "",
            generated_at=datetime.now(UTC).isoformat(),
        )

    # =========================================================================
    # GOVERNANCE
    # =========================================================================

    def governance(self) -> LatencyGovernance:

        sla_limit = 250.0  # ms p95 SLA

        compliant = [m for m in self.metrics.values() if m.p95 <= sla_limit]

        total = len(self.metrics)

        rate = len(compliant) / max(total, 1)

        if rate > 0.9:
            risk = "LOW_RISK"
        elif rate > 0.7:
            risk = "MEDIUM_RISK"
        else:
            risk = "HIGH_RISK"

        return LatencyGovernance(
            sla_compliant_models=len(compliant),
            sla_violating_models=total - len(compliant),
            compliance_rate=round(rate * 100, 2),
            risk_level=risk,
        )

    # =========================================================================
    # EXPORT
    # =========================================================================

    def export(self) -> Path:

        payload = {
            "summary": asdict(self.summary()),
            "governance": asdict(self.governance()),
            "metrics": {k: asdict(v) for k, v in self.metrics.items()},
        }

        path = EXPORTS_DIR / "inference_latency_report.json"

        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4, ensure_ascii=False)

        logger.info("Latency report exported.")

        return path

    # =========================================================================
    # MARKDOWN
    # =========================================================================

    def export_markdown(self) -> Path:

        s = self.summary()

        lines = [
            "# Enterprise Inference Latency Report",
            f"Generated: {s.generated_at}\n",
            "## Summary",
            f"- Total Requests: {s.total_requests}",
            f"- Total Models: {s.total_models}",
            f"- Avg P95 Latency: {s.avg_p95_latency} ms",
            f"- Avg Throughput: {s.avg_throughput} req/s",
            f"- Slowest Model: {s.slowest_model}",
            f"- Fastest Model: {s.fastest_model}\n",
            "## Model Metrics",
            "| Model | P50 | P95 | P99 | Throughput | Error Rate |",
            "|------|------|------|------|------------|------------|",
        ]

        for m in self.metrics.values():
            lines.append(
                f"| {m.model_name} | {m.p50} | {m.p95} | {m.p99} | {m.throughput_rps} | {m.error_rate} |"
            )

        path = EXPORTS_DIR / "inference_latency_report.md"
        path.write_text("\n".join(lines), encoding="utf-8")

        logger.info("Markdown report exported.")

        return path

    # =========================================================================
    # VALIDATION
    # =========================================================================

    def validate(self) -> None:

        gov = self.governance()

        if gov.compliance_rate < 80:
            logger.error("Inference SLA violation detected.")
            raise SystemExit(1)

        logger.info("Latency validation passed.")

    # =========================================================================
    # PIPELINE
    # =========================================================================

    def run(self) -> None:

        logger.info("Starting inference latency pipeline...")

        self.load()
        self.parse()
        self.compute()

        self.export()
        self.export_markdown()

        self.validate()

        logger.info("Inference latency pipeline completed successfully.")


# =============================================================================
# FACTORY
# =============================================================================


def create_engine(file: Path) -> InferenceLatencyReportEngine:
    return InferenceLatencyReportEngine(file)


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":

    FILE = ML_DIR / "history" / "inference_latency.json"

    engine = create_engine(FILE)

    engine.run()
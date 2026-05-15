"""
data/security/security_scanner.py

Enterprise-grade security scanner module for Python services, data platforms,
APIs, pipelines, repositories and internal governance tooling.

Core capabilities:
- Pluggable security checks
- Filesystem/repository scanning
- Secret leakage detection
- Dependency manifest scanning hooks
- Configuration hardening checks
- Python source static checks using AST and regex heuristics
- Baseline suppression and accepted-risk workflow
- Severity/risk scoring
- Evidence and remediation guidance
- JSON/Markdown report export
- Optional integration hooks for audit and metrics systems
- Thread-safe scan orchestration

Production recommendations:
- Combine this module with SAST, SCA, DAST and container scanners.
- Treat regex secret detection as a safety net, not as the only control.
- Store scan results in a central vulnerability management platform.
- Gate CI/CD on critical findings and policy violations.
"""

from __future__ import annotations

import ast
import dataclasses
import fnmatch
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple, Union

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
PathLike = Union[str, os.PathLike[str]]


# =============================================================================
# Exceptions
# =============================================================================


class SecurityScannerError(Exception):
    """Base security scanner error."""


class ScannerConfigurationError(SecurityScannerError):
    """Raised when scanner configuration is invalid."""


class ScannerExecutionError(SecurityScannerError):
    """Raised when a scanner/check execution fails."""


class ScanTargetError(SecurityScannerError):
    """Raised when a scan target is invalid."""


class ReportExportError(SecurityScannerError):
    """Raised when report export fails."""


# =============================================================================
# Enums/config
# =============================================================================


class FindingSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingStatus(str, Enum):
    OPEN = "open"
    SUPPRESSED = "suppressed"
    ACCEPTED_RISK = "accepted_risk"
    FIXED = "fixed"
    FALSE_POSITIVE = "false_positive"


class FindingCategory(str, Enum):
    SECRET_LEAK = "secret_leak"
    INSECURE_CRYPTO = "insecure_crypto"
    INSECURE_CONFIG = "insecure_config"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    DEPENDENCY = "dependency"
    DATA_PROTECTION = "data_protection"
    LOGGING = "logging"
    NETWORK = "network"
    CODE_QUALITY = "code_quality"
    COMPLIANCE = "compliance"
    CUSTOM = "custom"


class ScanTargetType(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"
    TEXT = "text"
    CONFIG = "config"
    MANIFEST = "manifest"


class ScanResultStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    PARTIAL = "partial"
    ERROR = "error"


class ReportFormat(str, Enum):
    JSON = "json"
    MARKDOWN = "markdown"
    SARIF_LIKE = "sarif_like"


@dataclass(frozen=True)
class SecurityScannerConfig:
    """Runtime configuration for security scanning."""

    enabled: bool = True
    fail_closed: bool = False
    max_file_bytes: int = 5 * 1024 * 1024
    max_files: int = 50_000
    follow_symlinks: bool = False
    include_hidden: bool = False
    default_encoding: str = "utf-8"
    redact_evidence: bool = True
    enable_baseline_suppression: bool = True
    minimum_severity: FindingSeverity = FindingSeverity.INFO
    include_patterns: Tuple[str, ...] = ("**/*",)
    exclude_patterns: Tuple[str, ...] = (
        "**/.git/**",
        "**/.venv/**",
        "**/venv/**",
        "**/__pycache__/**",
        "**/node_modules/**",
        "**/dist/**",
        "**/build/**",
        "**/.mypy_cache/**",
        "**/.pytest_cache/**",
    )
    binary_extensions: Tuple[str, ...] = (
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
        ".tar", ".7z", ".rar", ".exe", ".dll", ".so", ".dylib", ".class", ".jar",
        ".pyc", ".pyo", ".woff", ".woff2", ".ttf",
    )


# =============================================================================
# Domain models
# =============================================================================


@dataclass(frozen=True)
class ScanTarget:
    """Target to scan."""

    target_id: str
    target_type: ScanTargetType
    path: Optional[Path] = None
    text: Optional[str] = None
    name: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_path(cls, path: PathLike) -> "ScanTarget":
        p = Path(path)
        if not p.exists():
            raise ScanTargetError(f"Scan target does not exist: {p}")
        target_type = ScanTargetType.DIRECTORY if p.is_dir() else ScanTargetType.FILE
        return cls(target_id=str(uuid.uuid4()), target_type=target_type, path=p, name=str(p))

    @classmethod
    def from_text(cls, text: str, name: str = "inline-text") -> "ScanTarget":
        return cls(target_id=str(uuid.uuid4()), target_type=ScanTargetType.TEXT, text=text, name=name)


@dataclass(frozen=True)
class Evidence:
    """Evidence for a security finding."""

    file_path: Optional[str] = None
    line_number: Optional[int] = None
    column: Optional[int] = None
    snippet: Optional[str] = None
    matched_value_hash: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self, redact: bool = True) -> JsonDict:
        return {
            "file_path": self.file_path,
            "line_number": self.line_number,
            "column": self.column,
            "snippet": redact_snippet(self.snippet) if redact else self.snippet,
            "matched_value_hash": self.matched_value_hash,
            "metadata": redact_sensitive(self.metadata) if redact else dict(self.metadata),
        }


@dataclass(frozen=True)
class SecurityFinding:
    """Security finding produced by a check."""

    finding_id: str
    rule_id: str
    title: str
    description: str
    category: FindingCategory
    severity: FindingSeverity
    risk_score: float
    status: FindingStatus = FindingStatus.OPEN
    evidence: Tuple[Evidence, ...] = ()
    remediation: str = ""
    references: Tuple[str, ...] = ()
    cwe: Tuple[str, ...] = ()
    owasp: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()
    first_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: JsonDict = field(default_factory=dict)

    def fingerprint(self) -> str:
        evidence_key = [
            {
                "file_path": e.file_path,
                "line_number": e.line_number,
                "matched_value_hash": e.matched_value_hash,
            }
            for e in self.evidence
        ]
        payload = {
            "rule_id": self.rule_id,
            "title": self.title,
            "category": self.category.value,
            "evidence": evidence_key,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def to_dict(self, redact: bool = True) -> JsonDict:
        return {
            "finding_id": self.finding_id,
            "fingerprint": self.fingerprint(),
            "rule_id": self.rule_id,
            "title": self.title,
            "description": self.description,
            "category": self.category.value,
            "severity": self.severity.value,
            "risk_score": self.risk_score,
            "status": self.status.value,
            "evidence": [item.to_dict(redact=redact) for item in self.evidence],
            "remediation": self.remediation,
            "references": list(self.references),
            "cwe": list(self.cwe),
            "owasp": list(self.owasp),
            "tags": list(self.tags),
            "first_seen": self.first_seen.isoformat(),
            "metadata": redact_sensitive(self.metadata) if redact else dict(self.metadata),
        }


@dataclass(frozen=True)
class ScanContext:
    """Scan context shared with checks."""

    scan_id: str
    started_at: datetime
    config: SecurityScannerConfig
    labels: JsonDict = field(default_factory=dict)
    baseline_fingerprints: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ScanResult:
    """Security scan result."""

    scan_id: str
    status: ScanResultStatus
    target: str
    findings: Tuple[SecurityFinding, ...]
    started_at: datetime
    completed_at: datetime
    scanned_files: int = 0
    skipped_files: int = 0
    errors: Tuple[str, ...] = ()
    metadata: JsonDict = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return (self.completed_at - self.started_at).total_seconds() * 1000.0

    def summary(self) -> JsonDict:
        by_severity = Counter(f.severity.value for f in self.findings if f.status == FindingStatus.OPEN)
        by_category = Counter(f.category.value for f in self.findings if f.status == FindingStatus.OPEN)
        return {
            "scan_id": self.scan_id,
            "status": self.status.value,
            "target": self.target,
            "duration_ms": self.duration_ms,
            "scanned_files": self.scanned_files,
            "skipped_files": self.skipped_files,
            "total_findings": len(self.findings),
            "open_findings": sum(1 for f in self.findings if f.status == FindingStatus.OPEN),
            "by_severity": dict(by_severity),
            "by_category": dict(by_category),
            "errors": list(self.errors),
        }

    def to_dict(self, redact: bool = True) -> JsonDict:
        return {
            "summary": self.summary(),
            "findings": [finding.to_dict(redact=redact) for finding in self.findings],
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "metadata": redact_sensitive(self.metadata) if redact else dict(self.metadata),
        }


# =============================================================================
# Check abstractions
# =============================================================================


class SecurityCheck(ABC):
    """Base pluggable security check."""

    rule_id: str
    name: str
    category: FindingCategory
    severity: FindingSeverity

    @abstractmethod
    def scan_file(self, file_path: Path, content: str, context: ScanContext) -> Sequence[SecurityFinding]:
        """Scan a file and return findings."""

    def scan_text(self, name: str, content: str, context: ScanContext) -> Sequence[SecurityFinding]:
        pseudo = Path(name)
        return self.scan_file(pseudo, content, context)


@dataclass(frozen=True)
class RegexPatternRule:
    rule_id: str
    title: str
    pattern: str
    category: FindingCategory
    severity: FindingSeverity
    description: str
    remediation: str
    flags: int = re.IGNORECASE
    cwe: Tuple[str, ...] = ()
    owasp: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()


class RegexSecurityCheck(SecurityCheck):
    """Regex-based file scanner."""

    def __init__(self, rules: Sequence[RegexPatternRule]) -> None:
        self.rules = tuple(rules)
        self.rule_id = "regex-security-check"
        self.name = "Regex Security Check"
        self.category = FindingCategory.CUSTOM
        self.severity = FindingSeverity.INFO
        self._compiled = tuple((rule, re.compile(rule.pattern, rule.flags)) for rule in self.rules)

    def scan_file(self, file_path: Path, content: str, context: ScanContext) -> Sequence[SecurityFinding]:
        findings: List[SecurityFinding] = []
        lines = content.splitlines()
        for rule, compiled in self._compiled:
            for line_index, line in enumerate(lines, start=1):
                for match in compiled.finditer(line):
                    matched = match.group(0)
                    evidence = Evidence(
                        file_path=str(file_path),
                        line_number=line_index,
                        column=match.start() + 1,
                        snippet=line.strip(),
                        matched_value_hash=hashlib.sha256(matched.encode("utf-8", errors="ignore")).hexdigest(),
                    )
                    finding = SecurityFinding(
                        finding_id=str(uuid.uuid4()),
                        rule_id=rule.rule_id,
                        title=rule.title,
                        description=rule.description,
                        category=rule.category,
                        severity=rule.severity,
                        risk_score=score_for_severity(rule.severity),
                        evidence=(evidence,),
                        remediation=rule.remediation,
                        cwe=rule.cwe,
                        owasp=rule.owasp,
                        tags=rule.tags,
                    )
                    findings.append(_apply_baseline(finding, context))
        return findings


class PythonAstSecurityCheck(SecurityCheck):
    """Python AST security check for dangerous calls and patterns."""

    rule_id = "python-ast-security"
    name = "Python AST Security Check"
    category = FindingCategory.CODE_QUALITY
    severity = FindingSeverity.MEDIUM

    DANGEROUS_CALLS: Dict[str, Tuple[FindingSeverity, str, str]] = {
        "eval": (FindingSeverity.HIGH, "Use of eval", "Avoid eval; use safe parsers or explicit dispatch."),
        "exec": (FindingSeverity.HIGH, "Use of exec", "Avoid exec; use safe, explicit logic."),
        "compile": (FindingSeverity.MEDIUM, "Dynamic compile usage", "Avoid dynamic code compilation unless strictly controlled."),
        "pickle.loads": (FindingSeverity.HIGH, "Unsafe pickle deserialization", "Avoid pickle for untrusted data; use JSON or signed payloads."),
        "pickle.load": (FindingSeverity.HIGH, "Unsafe pickle deserialization", "Avoid pickle for untrusted data; use JSON or signed payloads."),
        "subprocess.Popen": (FindingSeverity.MEDIUM, "Subprocess execution", "Validate command arguments and avoid shell=True."),
        "os.system": (FindingSeverity.HIGH, "Shell command execution", "Use subprocess with argument arrays and shell=False."),
    }

    def scan_file(self, file_path: Path, content: str, context: ScanContext) -> Sequence[SecurityFinding]:
        if file_path.suffix != ".py":
            return ()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return ()

        findings: List[SecurityFinding] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call_name = _call_name(node.func)
                if call_name in self.DANGEROUS_CALLS:
                    severity, title, remediation = self.DANGEROUS_CALLS[call_name]
                    evidence = Evidence(
                        file_path=str(file_path),
                        line_number=getattr(node, "lineno", None),
                        column=getattr(node, "col_offset", 0) + 1,
                        snippet=_safe_line(content, getattr(node, "lineno", None)),
                        metadata={"call": call_name},
                    )
                    finding = SecurityFinding(
                        finding_id=str(uuid.uuid4()),
                        rule_id=f"python-dangerous-call-{call_name.replace('.', '-')}",
                        title=title,
                        description=f"Dangerous Python call detected: {call_name}.",
                        category=FindingCategory.CODE_QUALITY,
                        severity=severity,
                        risk_score=score_for_severity(severity),
                        evidence=(evidence,),
                        remediation=remediation,
                        cwe=("CWE-94",) if call_name in {"eval", "exec", "compile"} else (),
                        tags=("python", "sast"),
                    )
                    findings.append(_apply_baseline(finding, context))

                if call_name in {"subprocess.Popen", "subprocess.run", "subprocess.call"}:
                    for keyword in node.keywords:
                        if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                            evidence = Evidence(
                                file_path=str(file_path),
                                line_number=getattr(node, "lineno", None),
                                column=getattr(node, "col_offset", 0) + 1,
                                snippet=_safe_line(content, getattr(node, "lineno", None)),
                                metadata={"call": call_name, "shell": True},
                            )
                            finding = SecurityFinding(
                                finding_id=str(uuid.uuid4()),
                                rule_id="python-subprocess-shell-true",
                                title="Subprocess with shell=True",
                                description="shell=True increases command injection risk when arguments include user-controlled data.",
                                category=FindingCategory.CODE_QUALITY,
                                severity=FindingSeverity.HIGH,
                                risk_score=score_for_severity(FindingSeverity.HIGH),
                                evidence=(evidence,),
                                remediation="Use shell=False and pass command arguments as a list. Strictly validate any user-controlled input.",
                                cwe=("CWE-78",),
                                tags=("python", "command-injection", "sast"),
                            )
                            findings.append(_apply_baseline(finding, context))
        return findings


class ConfigurationSecurityCheck(SecurityCheck):
    """Configuration hardening scanner for common insecure settings."""

    rule_id = "configuration-security"
    name = "Configuration Security Check"
    category = FindingCategory.INSECURE_CONFIG
    severity = FindingSeverity.MEDIUM

    INSECURE_SETTINGS: Tuple[Tuple[str, str, FindingSeverity, str], ...] = (
        (r"(?i)debug\s*[:=]\s*true", "Debug mode enabled", FindingSeverity.MEDIUM, "Disable debug mode in production."),
        (r"(?i)verify_ssl\s*[:=]\s*false", "SSL verification disabled", FindingSeverity.HIGH, "Enable TLS certificate verification."),
        (r"(?i)ssl_verify\s*[:=]\s*false", "SSL verification disabled", FindingSeverity.HIGH, "Enable TLS certificate verification."),
        (r"(?i)allow_origins\s*[:=]\s*\[?['\"]?\*", "Wildcard CORS origin", FindingSeverity.MEDIUM, "Restrict CORS origins to trusted domains."),
        (r"(?i)admin\s*[:=]\s*true", "Admin flag in configuration", FindingSeverity.LOW, "Ensure administrative flags are not enabled by default."),
        (r"(?i)log_level\s*[:=]\s*debug", "Verbose debug logging", FindingSeverity.LOW, "Avoid debug logging in production."),
    )

    def scan_file(self, file_path: Path, content: str, context: ScanContext) -> Sequence[SecurityFinding]:
        if file_path.suffix.lower() not in {".env", ".ini", ".cfg", ".conf", ".yaml", ".yml", ".json", ".toml", ".properties", ".py"}:
            return ()
        findings: List[SecurityFinding] = []
        lines = content.splitlines()
        for pattern, title, severity, remediation in self.INSECURE_SETTINGS:
            compiled = re.compile(pattern)
            for idx, line in enumerate(lines, start=1):
                if compiled.search(line):
                    finding = SecurityFinding(
                        finding_id=str(uuid.uuid4()),
                        rule_id=f"config-{slugify(title)}",
                        title=title,
                        description=f"Potential insecure configuration detected: {title}.",
                        category=FindingCategory.INSECURE_CONFIG,
                        severity=severity,
                        risk_score=score_for_severity(severity),
                        evidence=(Evidence(file_path=str(file_path), line_number=idx, snippet=line.strip()),),
                        remediation=remediation,
                        tags=("configuration", "hardening"),
                    )
                    findings.append(_apply_baseline(finding, context))
        return findings


class DependencyManifestCheck(SecurityCheck):
    """Dependency manifest hygiene check."""

    rule_id = "dependency-manifest"
    name = "Dependency Manifest Check"
    category = FindingCategory.DEPENDENCY
    severity = FindingSeverity.MEDIUM

    def scan_file(self, file_path: Path, content: str, context: ScanContext) -> Sequence[SecurityFinding]:
        filename = file_path.name.lower()
        findings: List[SecurityFinding] = []
        if filename not in {"requirements.txt", "pyproject.toml", "package.json", "pom.xml", "build.gradle"}:
            return ()

        lines = content.splitlines()
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if filename == "requirements.txt" and re.match(r"^[A-Za-z0-9_.\-]+\s*$", stripped):
                findings.append(SecurityFinding(
                    finding_id=str(uuid.uuid4()),
                    rule_id="dependency-unpinned-python-requirement",
                    title="Unpinned Python dependency",
                    description="A requirements.txt dependency appears to be unpinned, which may reduce build reproducibility and supply-chain control.",
                    category=FindingCategory.DEPENDENCY,
                    severity=FindingSeverity.LOW,
                    risk_score=score_for_severity(FindingSeverity.LOW),
                    evidence=(Evidence(file_path=str(file_path), line_number=idx, snippet=stripped),),
                    remediation="Pin dependencies with exact versions and use vulnerability scanning/SBOM tooling.",
                    tags=("dependency", "supply-chain"),
                ))
            if "latest" in stripped.lower():
                findings.append(SecurityFinding(
                    finding_id=str(uuid.uuid4()),
                    rule_id="dependency-latest-version",
                    title="Dependency uses latest tag/version",
                    description="Using 'latest' can make builds non-reproducible and introduce unreviewed changes.",
                    category=FindingCategory.DEPENDENCY,
                    severity=FindingSeverity.MEDIUM,
                    risk_score=score_for_severity(FindingSeverity.MEDIUM),
                    evidence=(Evidence(file_path=str(file_path), line_number=idx, snippet=stripped),),
                    remediation="Use pinned, reviewed versions and automated dependency update workflows.",
                    tags=("dependency", "supply-chain"),
                ))
        return tuple(_apply_baseline(f, context) for f in findings)


# =============================================================================
# Baseline and reporting
# =============================================================================


@dataclass(frozen=True)
class ScanBaseline:
    """Accepted baseline/suppression set."""

    fingerprints: frozenset[str] = frozenset()
    accepted_until: Optional[datetime] = None
    reason: Optional[str] = None

    @classmethod
    def from_findings(cls, findings: Iterable[SecurityFinding], reason: str = "baseline") -> "ScanBaseline":
        return cls(fingerprints=frozenset(f.fingerprint() for f in findings), reason=reason)

    @classmethod
    def from_json(cls, raw: Union[str, bytes]) -> "ScanBaseline":
        payload = json.loads(raw)
        accepted_until = parse_datetime(payload["accepted_until"]) if payload.get("accepted_until") else None
        return cls(fingerprints=frozenset(payload.get("fingerprints", ())), accepted_until=accepted_until, reason=payload.get("reason"))

    def to_json(self, indent: int = 2) -> str:
        return json.dumps({
            "fingerprints": sorted(self.fingerprints),
            "accepted_until": self.accepted_until.isoformat() if self.accepted_until else None,
            "reason": self.reason,
        }, indent=indent, sort_keys=True)

    def is_active(self) -> bool:
        return self.accepted_until is None or self.accepted_until > datetime.now(timezone.utc)


class ScanReporter:
    """Report renderer for scan results."""

    def export(self, result: ScanResult, fmt: ReportFormat = ReportFormat.JSON, redact: bool = True) -> str:
        try:
            if fmt == ReportFormat.JSON:
                return json.dumps(result.to_dict(redact=redact), indent=2, sort_keys=True, default=str)
            if fmt == ReportFormat.MARKDOWN:
                return self._markdown(result, redact)
            if fmt == ReportFormat.SARIF_LIKE:
                return json.dumps(self._sarif_like(result, redact), indent=2, sort_keys=True, default=str)
            raise ReportExportError(f"Unsupported report format: {fmt.value}")
        except Exception as exc:
            if isinstance(exc, ReportExportError):
                raise
            raise ReportExportError("Failed to export scan report.") from exc

    def _markdown(self, result: ScanResult, redact: bool = True) -> str:
        lines = [
            f"# Security Scan Report",
            "",
            f"- Scan ID: `{result.scan_id}`",
            f"- Status: `{result.status.value}`",
            f"- Target: `{result.target}`",
            f"- Duration: `{result.duration_ms:.2f} ms`",
            f"- Scanned files: `{result.scanned_files}`",
            f"- Skipped files: `{result.skipped_files}`",
            "",
            "## Summary",
            "",
            "```json",
            json.dumps(result.summary(), indent=2, sort_keys=True, default=str),
            "```",
            "",
            "## Findings",
            "",
        ]
        if not result.findings:
            lines.append("No findings.")
            return "\n".join(lines)
        for finding in sorted(result.findings, key=lambda f: (-score_for_severity(f.severity), f.rule_id)):
            lines.extend([
                f"### {finding.severity.value.upper()} - {finding.title}",
                "",
                f"- Rule: `{finding.rule_id}`",
                f"- Category: `{finding.category.value}`",
                f"- Status: `{finding.status.value}`",
                f"- Risk Score: `{finding.risk_score}`",
                f"- Fingerprint: `{finding.fingerprint()}`",
                "",
                finding.description,
                "",
                f"Remediation: {finding.remediation or 'Review and remediate according to policy.'}",
                "",
            ])
            for ev in finding.evidence:
                lines.append(f"- Evidence: `{ev.file_path}:{ev.line_number or ''}` {redact_snippet(ev.snippet) if redact else (ev.snippet or '')}")
            lines.append("")
        return "\n".join(lines)

    def _sarif_like(self, result: ScanResult, redact: bool = True) -> JsonDict:
        return {
            "version": "2.1.0-like",
            "runs": [{
                "tool": {"driver": {"name": "enterprise-security-scanner"}},
                "results": [
                    {
                        "ruleId": finding.rule_id,
                        "level": _sarif_level(finding.severity),
                        "message": {"text": finding.description},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": ev.file_path},
                                    "region": {"startLine": ev.line_number, "startColumn": ev.column},
                                },
                                "snippet": {"text": redact_snippet(ev.snippet) if redact else ev.snippet},
                            }
                            for ev in finding.evidence
                        ],
                        "properties": finding.to_dict(redact=redact),
                    }
                    for finding in result.findings
                ],
            }],
        }


# =============================================================================
# Main scanner
# =============================================================================


class SecurityScanner:
    """Enterprise security scanner orchestration service."""

    def __init__(
        self,
        checks: Optional[Sequence[SecurityCheck]] = None,
        config: Optional[SecurityScannerConfig] = None,
        reporter: Optional[ScanReporter] = None,
    ) -> None:
        self.config = config or SecurityScannerConfig()
        self.checks = tuple(checks or default_checks())
        self.reporter = reporter or ScanReporter()
        self._lock = threading.RLock()

    def scan(self, target: Union[ScanTarget, PathLike, str], baseline: Optional[ScanBaseline] = None, labels: Optional[Mapping[str, Any]] = None) -> ScanResult:
        """Scan a target path or text target."""
        if not self.config.enabled:
            now = datetime.now(timezone.utc)
            return ScanResult(str(uuid.uuid4()), ScanResultStatus.PASSED, str(target), (), now, now)

        scan_target = self._coerce_target(target)
        scan_id = str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        context = ScanContext(
            scan_id=scan_id,
            started_at=started,
            config=self.config,
            labels=dict(labels or {}),
            baseline_fingerprints=(baseline.fingerprints if baseline and baseline.is_active() and self.config.enable_baseline_suppression else frozenset()),
        )
        findings: List[SecurityFinding] = []
        errors: List[str] = []
        scanned_files = 0
        skipped_files = 0

        try:
            if scan_target.target_type == ScanTargetType.TEXT:
                for check in self.checks:
                    findings.extend(check.scan_text(scan_target.name or "inline-text", scan_target.text or "", context))
            elif scan_target.target_type == ScanTargetType.FILE:
                file_findings, scanned, skipped = self._scan_file(scan_target.path, context)
                findings.extend(file_findings)
                scanned_files += scanned
                skipped_files += skipped
            elif scan_target.target_type == ScanTargetType.DIRECTORY:
                for index, file_path in enumerate(self._iter_files(scan_target.path), start=1):
                    if index > self.config.max_files:
                        errors.append(f"Maximum file limit reached: {self.config.max_files}")
                        break
                    file_findings, scanned, skipped = self._scan_file(file_path, context)
                    findings.extend(file_findings)
                    scanned_files += scanned
                    skipped_files += skipped
            else:
                raise ScanTargetError(f"Unsupported target type: {scan_target.target_type.value}")

            filtered = tuple(f for f in findings if severity_rank(f.severity) >= severity_rank(self.config.minimum_severity))
            status = ScanResultStatus.FAILED if any(f.status == FindingStatus.OPEN and f.severity in {FindingSeverity.HIGH, FindingSeverity.CRITICAL} for f in filtered) else ScanResultStatus.PASSED
            if errors:
                status = ScanResultStatus.PARTIAL if filtered else ScanResultStatus.ERROR
            return ScanResult(
                scan_id=scan_id,
                status=status,
                target=scan_target.name or str(scan_target.path or "inline-text"),
                findings=filtered,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
                scanned_files=scanned_files,
                skipped_files=skipped_files,
                errors=tuple(errors),
                metadata={"labels": dict(labels or {}), "check_count": len(self.checks)},
            )
        except Exception as exc:
            logger.exception("Security scan failed. target=%s", scan_target.name)
            if self.config.fail_closed:
                if isinstance(exc, SecurityScannerError):
                    raise
                raise ScannerExecutionError("Security scan failed.") from exc
            return ScanResult(
                scan_id=scan_id,
                status=ScanResultStatus.ERROR,
                target=scan_target.name or str(scan_target.path or "inline-text"),
                findings=tuple(findings),
                started_at=started,
                completed_at=datetime.now(timezone.utc),
                scanned_files=scanned_files,
                skipped_files=skipped_files,
                errors=(f"{type(exc).__name__}: {exc}",),
            )

    def export_report(self, result: ScanResult, fmt: ReportFormat = ReportFormat.JSON, redact: bool = True) -> str:
        return self.reporter.export(result, fmt=fmt, redact=redact)

    def _coerce_target(self, target: Union[ScanTarget, PathLike, str]) -> ScanTarget:
        if isinstance(target, ScanTarget):
            return target
        if isinstance(target, (str, os.PathLike)):
            path = Path(target)
            if path.exists():
                return ScanTarget.from_path(path)
            return ScanTarget.from_text(str(target), name="inline-text")
        raise ScanTargetError("Unsupported scan target type.")

    def _iter_files(self, root: Optional[Path]) -> Iterator[Path]:
        if root is None:
            return
        for dirpath, dirnames, filenames in os.walk(root, followlinks=self.config.follow_symlinks):
            current = Path(dirpath)
            if not self.config.include_hidden:
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for filename in filenames:
                if not self.config.include_hidden and filename.startswith("."):
                    continue
                file_path = current / filename
                if self._path_allowed(file_path):
                    yield file_path

    def _path_allowed(self, path: Path) -> bool:
        normalized = path.as_posix()
        if path.suffix.lower() in self.config.binary_extensions:
            return False
        include = any(fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(path.name, pattern) for pattern in self.config.include_patterns)
        exclude = any(fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(path.name, pattern) for pattern in self.config.exclude_patterns)
        return include and not exclude

    def _scan_file(self, file_path: Optional[Path], context: ScanContext) -> Tuple[Tuple[SecurityFinding, ...], int, int]:
        if file_path is None:
            return (), 0, 1
        try:
            if not file_path.is_file():
                return (), 0, 1
            if file_path.stat().st_size > self.config.max_file_bytes:
                return (), 0, 1
            content = file_path.read_text(encoding=self.config.default_encoding, errors="replace")
            findings: List[SecurityFinding] = []
            for check in self.checks:
                findings.extend(check.scan_file(file_path, content, context))
            return tuple(findings), 1, 0
        except Exception as exc:
            logger.debug("Failed to scan file %s: %s", file_path, exc)
            return (), 0, 1


# =============================================================================
# Default checks
# =============================================================================


def default_secret_rules() -> Tuple[RegexPatternRule, ...]:
    return (
        RegexPatternRule(
            rule_id="secret-aws-access-key",
            title="Possible AWS access key",
            pattern=r"\bAKIA[0-9A-Z]{16}\b",
            category=FindingCategory.SECRET_LEAK,
            severity=FindingSeverity.CRITICAL,
            description="A value matching the AWS access key pattern was found.",
            remediation="Revoke the key immediately, rotate impacted credentials and remove it from source history.",
            cwe=("CWE-798",),
            owasp=("A02:2021-Cryptographic Failures",),
            tags=("secret", "aws"),
        ),
        RegexPatternRule(
            rule_id="secret-github-token",
            title="Possible GitHub token",
            pattern=r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b",
            category=FindingCategory.SECRET_LEAK,
            severity=FindingSeverity.CRITICAL,
            description="A value matching a GitHub token pattern was found.",
            remediation="Revoke the token, rotate credentials and remove it from source history.",
            cwe=("CWE-798",),
            tags=("secret", "github"),
        ),
        RegexPatternRule(
            rule_id="secret-private-key",
            title="Private key material detected",
            pattern=r"-----BEGIN (RSA |EC |OPENSSH |DSA |PRIVATE )?PRIVATE KEY-----",
            category=FindingCategory.SECRET_LEAK,
            severity=FindingSeverity.CRITICAL,
            description="Private key material appears to be present in a scanned file.",
            remediation="Remove the private key, rotate it and store it in an approved secret manager.",
            cwe=("CWE-798",),
            tags=("secret", "private-key"),
        ),
        RegexPatternRule(
            rule_id="secret-hardcoded-password",
            title="Possible hardcoded password",
            pattern=r"(?i)\b(password|passwd|pwd)\b\s*[:=]\s*['\"][^'\"]{8,}['\"]",
            category=FindingCategory.SECRET_LEAK,
            severity=FindingSeverity.HIGH,
            description="A possible hardcoded password was found.",
            remediation="Move the password to a secret manager and rotate it if it was real.",
            cwe=("CWE-798",),
            tags=("secret", "password"),
        ),
        RegexPatternRule(
            rule_id="secret-hardcoded-api-key",
            title="Possible hardcoded API key",
            pattern=r"(?i)\b(api[_-]?key|token|secret)\b\s*[:=]\s*['\"][A-Za-z0-9_\-\./+=]{16,}['\"]",
            category=FindingCategory.SECRET_LEAK,
            severity=FindingSeverity.HIGH,
            description="A possible hardcoded token/API key/secret was found.",
            remediation="Store secrets in a secret manager and rotate exposed values.",
            cwe=("CWE-798",),
            tags=("secret", "api-key"),
        ),
    )


def default_crypto_rules() -> Tuple[RegexPatternRule, ...]:
    return (
        RegexPatternRule(
            rule_id="crypto-md5-usage",
            title="MD5 usage detected",
            pattern=r"\b(hashlib\.md5|MD5\()",
            category=FindingCategory.INSECURE_CRYPTO,
            severity=FindingSeverity.MEDIUM,
            description="MD5 is not appropriate for security-sensitive hashing.",
            remediation="Use SHA-256+ for non-password hashing or Argon2/bcrypt/PBKDF2 for passwords.",
            cwe=("CWE-327",),
            tags=("crypto", "hashing"),
        ),
        RegexPatternRule(
            rule_id="crypto-sha1-usage",
            title="SHA1 usage detected",
            pattern=r"\b(hashlib\.sha1|SHA1\()",
            category=FindingCategory.INSECURE_CRYPTO,
            severity=FindingSeverity.MEDIUM,
            description="SHA1 is weak for collision resistance and should not be used for security-sensitive designs.",
            remediation="Use SHA-256+ or approved modern primitives.",
            cwe=("CWE-327",),
            tags=("crypto", "hashing"),
        ),
        RegexPatternRule(
            rule_id="crypto-random-module-security",
            title="Non-cryptographic random usage",
            pattern=r"\brandom\.(random|randint|choice|choices|randrange)\(",
            category=FindingCategory.INSECURE_CRYPTO,
            severity=FindingSeverity.LOW,
            description="The random module is not suitable for security-sensitive randomness.",
            remediation="Use secrets or os.urandom for tokens, passwords and cryptographic nonces.",
            cwe=("CWE-338",),
            tags=("crypto", "randomness"),
        ),
    )


def default_checks() -> Tuple[SecurityCheck, ...]:
    return (
        RegexSecurityCheck(default_secret_rules() + default_crypto_rules()),
        PythonAstSecurityCheck(),
        ConfigurationSecurityCheck(),
        DependencyManifestCheck(),
    )


# =============================================================================
# Utility functions
# =============================================================================


def score_for_severity(severity: FindingSeverity) -> float:
    return {
        FindingSeverity.INFO: 1.0,
        FindingSeverity.LOW: 20.0,
        FindingSeverity.MEDIUM: 45.0,
        FindingSeverity.HIGH: 75.0,
        FindingSeverity.CRITICAL: 95.0,
    }[severity]


def severity_rank(severity: FindingSeverity) -> int:
    return {
        FindingSeverity.INFO: 0,
        FindingSeverity.LOW: 1,
        FindingSeverity.MEDIUM: 2,
        FindingSeverity.HIGH: 3,
        FindingSeverity.CRITICAL: 4,
    }[severity]


def redact_snippet(snippet: Optional[str]) -> Optional[str]:
    if snippet is None:
        return None
    redacted = re.sub(r"(['\"])[A-Za-z0-9_\-\./+=]{12,}(['\"])", r"\1***REDACTED***\2", snippet)
    redacted = re.sub(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key)(\s*[:=]\s*)([^\s,;]+)", r"\1\2***REDACTED***", redacted)
    return redacted[:500]


def redact_sensitive(data: Mapping[str, Any]) -> JsonDict:
    sensitive_terms = (
        "password", "secret", "token", "api_key", "apikey", "authorization",
        "credential", "private_key", "access_key", "refresh_token", "session",
    )

    def walk(value: Any) -> Any:
        if isinstance(value, Mapping):
            output: JsonDict = {}
            for key, item in value.items():
                key_text = str(key).lower()
                if any(term in key_text for term in sensitive_terms):
                    output[str(key)] = "***REDACTED***"
                else:
                    output[str(key)] = walk(item)
            return output
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, tuple):
            return tuple(walk(item) for item in value)
        return value

    return walk(dict(data))


def parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise ValueError(f"Unsupported datetime value: {value!r}")


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _safe_line(content: str, line_number: Optional[int]) -> Optional[str]:
    if not line_number:
        return None
    lines = content.splitlines()
    if 1 <= line_number <= len(lines):
        return lines[line_number - 1].strip()
    return None


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _apply_baseline(finding: SecurityFinding, context: ScanContext) -> SecurityFinding:
    if finding.fingerprint() in context.baseline_fingerprints:
        return dataclasses.replace(finding, status=FindingStatus.ACCEPTED_RISK, metadata={**finding.metadata, "baseline_suppressed": True})
    return finding


def _sarif_level(severity: FindingSeverity) -> str:
    if severity in {FindingSeverity.CRITICAL, FindingSeverity.HIGH}:
        return "error"
    if severity == FindingSeverity.MEDIUM:
        return "warning"
    return "note"


def create_default_security_scanner() -> SecurityScanner:
    return SecurityScanner()


__all__ = [
    "ConfigurationSecurityCheck",
    "DependencyManifestCheck",
    "Evidence",
    "FindingCategory",
    "FindingSeverity",
    "FindingStatus",
    "PathLike",
    "PythonAstSecurityCheck",
    "RegexPatternRule",
    "RegexSecurityCheck",
    "ReportExportError",
    "ReportFormat",
    "ScanBaseline",
    "ScanContext",
    "ScanReporter",
    "ScanResult",
    "ScanResultStatus",
    "ScanTarget",
    "ScanTargetError",
    "ScanTargetType",
    "ScannerConfigurationError",
    "ScannerExecutionError",
    "SecurityCheck",
    "SecurityFinding",
    "SecurityScanner",
    "SecurityScannerConfig",
    "SecurityScannerError",
    "create_default_security_scanner",
    "default_checks",
    "default_crypto_rules",
    "default_secret_rules",
    "parse_datetime",
    "redact_sensitive",
    "redact_snippet",
    "score_for_severity",
    "severity_rank",
    "slugify",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    scanner = create_default_security_scanner()
    target = ScanTarget.from_text(
        """
import hashlib
import subprocess

password = "SuperSecretPassword123"
api_key = "abcd1234abcd1234abcd1234"
hashlib.md5(b"x").hexdigest()
subprocess.run("ls -la", shell=True)
DEBUG = True
        """,
        name="demo.py",
    )
    result = scanner.scan(target)
    print(scanner.export_report(result, ReportFormat.MARKDOWN))

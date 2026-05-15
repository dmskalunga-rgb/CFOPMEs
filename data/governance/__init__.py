"""
data.governance
===============

Enterprise-grade data governance package.

This package centralizes governance primitives for modern data platforms:

- Data catalog and metadata management
- Data lineage and impact analysis
- Data quality governance
- Policy enforcement
- Privacy and consent governance
- Retention and lifecycle controls
- Access governance
- Stewardship workflows
- Compliance evidence and audit readiness
- Ownership, classification and business glossary support

Design goals
------------
- Stable public API for governance services and pipelines.
- Lazy imports to keep package initialization lightweight.
- Clear module registry for capability discovery and health checks.
- Pluggable architecture for enterprise tools such as OpenMetadata, DataHub,
  Collibra, Apache Atlas, Great Expectations, dbt artifacts, IAM systems,
  SIEM platforms and lakehouse catalogs.

Usage
-----
>>> from data.governance import describe_package, available_modules
>>> describe_package()["title"]
'data.governance'

The package is intentionally import-friendly. Optional heavy dependencies are
only required by modules that use them directly.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

__title__ = "data.governance"
__description__ = "Enterprise-grade data governance toolkit"
__version__ = "1.0.0"
__author__ = "Data Platform Team"
__license__ = "Proprietary"


@dataclass(frozen=True)
class GovernanceModuleInfo:
    """Metadata describing a governance module exposed by this package."""

    name: str
    module_path: str
    description: str
    domain: str = "governance"
    optional_dependencies: Tuple[str, ...] = field(default_factory=tuple)
    public_symbols: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "module_path": self.module_path,
            "description": self.description,
            "domain": self.domain,
            "optional_dependencies": list(self.optional_dependencies),
            "public_symbols": list(self.public_symbols),
        }


MODULE_REGISTRY: Dict[str, GovernanceModuleInfo] = {
    "access_governance": GovernanceModuleInfo(
        name="access_governance",
        module_path="data.governance.access_governance",
        description="Access review, entitlement governance and least-privilege controls.",
        domain="access",
        public_symbols=("AccessGovernanceEngine", "AccessPolicy", "AccessReviewResult"),
    ),
    "audit_evidence": GovernanceModuleInfo(
        name="audit_evidence",
        module_path="data.governance.audit_evidence",
        description="Compliance evidence collection, packaging and audit-readiness workflows.",
        domain="audit",
        public_symbols=("AuditEvidenceCollector", "EvidenceRecord", "EvidenceBundle"),
    ),
    "business_glossary": GovernanceModuleInfo(
        name="business_glossary",
        module_path="data.governance.business_glossary",
        description="Business glossary, term ownership, definitions and semantic mapping.",
        domain="metadata",
        public_symbols=("BusinessGlossary", "GlossaryTerm", "GlossaryRelationship"),
    ),
    "catalog_manager": GovernanceModuleInfo(
        name="catalog_manager",
        module_path="data.governance.catalog_manager",
        description="Dataset catalog registration, metadata enrichment and discovery APIs.",
        domain="catalog",
        optional_dependencies=("pydantic",),
        public_symbols=("DataCatalogManager", "CatalogAsset", "CatalogSearchResult"),
    ),
    "classification_engine": GovernanceModuleInfo(
        name="classification_engine",
        module_path="data.governance.classification_engine",
        description="Data classification, sensitivity tagging and PII detection orchestration.",
        domain="privacy",
        optional_dependencies=("pandas",),
        public_symbols=("ClassificationEngine", "ClassificationRule", "ClassificationResult"),
    ),
    "compliance_manager": GovernanceModuleInfo(
        name="compliance_manager",
        module_path="data.governance.compliance_manager",
        description="Regulatory control mapping, compliance checks and governance reporting.",
        domain="compliance",
        public_symbols=("ComplianceManager", "ComplianceControl", "ComplianceAssessment"),
    ),
    "consent_manager": GovernanceModuleInfo(
        name="consent_manager",
        module_path="data.governance.consent_manager",
        description="Consent tracking, purpose limitation and data subject preference governance.",
        domain="privacy",
        public_symbols=("ConsentManager", "ConsentRecord", "ConsentDecision"),
    ),
    "data_contracts": GovernanceModuleInfo(
        name="data_contracts",
        module_path="data.governance.data_contracts",
        description="Data contract definitions, compatibility checks and schema expectations.",
        domain="quality",
        optional_dependencies=("jsonschema",),
        public_symbols=("DataContract", "ContractValidator", "ContractValidationResult"),
    ),
    "data_lineage": GovernanceModuleInfo(
        name="data_lineage",
        module_path="data.governance.data_lineage",
        description="Column/table lineage, dependency graphs and impact analysis.",
        domain="lineage",
        public_symbols=("LineageGraph", "LineageNode", "LineageEdge", "ImpactAnalysisResult"),
    ),
    "data_ownership": GovernanceModuleInfo(
        name="data_ownership",
        module_path="data.governance.data_ownership",
        description="Data ownership, stewardship assignments and accountability workflows.",
        domain="stewardship",
        public_symbols=("OwnershipRegistry", "DataOwner", "StewardAssignment"),
    ),
    "data_quality_governance": GovernanceModuleInfo(
        name="data_quality_governance",
        module_path="data.governance.data_quality_governance",
        description="Quality scorecards, SLOs, certification and quality governance policies.",
        domain="quality",
        optional_dependencies=("pandas",),
        public_symbols=("DataQualityGovernance", "QualityPolicy", "QualityScorecard"),
    ),
    "lifecycle_manager": GovernanceModuleInfo(
        name="lifecycle_manager",
        module_path="data.governance.lifecycle_manager",
        description="Dataset lifecycle, archival, deprecation and retention-state workflows.",
        domain="lifecycle",
        public_symbols=("DataLifecycleManager", "LifecyclePolicy", "LifecycleState"),
    ),
    "metadata_registry": GovernanceModuleInfo(
        name="metadata_registry",
        module_path="data.governance.metadata_registry",
        description="Technical metadata registry, asset attributes and schema metadata APIs.",
        domain="metadata",
        public_symbols=("MetadataRegistry", "MetadataRecord", "MetadataQuery"),
    ),
    "policy_engine": GovernanceModuleInfo(
        name="policy_engine",
        module_path="data.governance.policy_engine",
        description="Governance policy evaluation, rule orchestration and decision auditing.",
        domain="policy",
        public_symbols=("GovernancePolicyEngine", "GovernancePolicy", "PolicyDecision"),
    ),
    "privacy_governance": GovernanceModuleInfo(
        name="privacy_governance",
        module_path="data.governance.privacy_governance",
        description="Privacy controls, lawful basis, purpose limitation and privacy risk workflows.",
        domain="privacy",
        public_symbols=("PrivacyGovernanceEngine", "PrivacyRiskAssessment", "PrivacyControl"),
    ),
    "retention_manager": GovernanceModuleInfo(
        name="retention_manager",
        module_path="data.governance.retention_manager",
        description="Retention policies, legal hold and disposal eligibility decisions.",
        domain="retention",
        public_symbols=("RetentionManager", "RetentionPolicy", "RetentionDecision"),
    ),
    "stewardship_workflow": GovernanceModuleInfo(
        name="stewardship_workflow",
        module_path="data.governance.stewardship_workflow",
        description="Stewardship task routing, approval workflow and remediation tracking.",
        domain="stewardship",
        public_symbols=("StewardshipWorkflow", "StewardshipTask", "WorkflowDecision"),
    ),
}


_LAZY_SYMBOLS: Dict[str, Tuple[str, str]] = {
    # access_governance
    "AccessGovernanceEngine": ("data.governance.access_governance", "AccessGovernanceEngine"),
    "AccessPolicy": ("data.governance.access_governance", "AccessPolicy"),
    "AccessReviewResult": ("data.governance.access_governance", "AccessReviewResult"),
    # audit_evidence
    "AuditEvidenceCollector": ("data.governance.audit_evidence", "AuditEvidenceCollector"),
    "EvidenceRecord": ("data.governance.audit_evidence", "EvidenceRecord"),
    "EvidenceBundle": ("data.governance.audit_evidence", "EvidenceBundle"),
    # business_glossary
    "BusinessGlossary": ("data.governance.business_glossary", "BusinessGlossary"),
    "GlossaryTerm": ("data.governance.business_glossary", "GlossaryTerm"),
    "GlossaryRelationship": ("data.governance.business_glossary", "GlossaryRelationship"),
    # catalog_manager
    "DataCatalogManager": ("data.governance.catalog_manager", "DataCatalogManager"),
    "CatalogAsset": ("data.governance.catalog_manager", "CatalogAsset"),
    "CatalogSearchResult": ("data.governance.catalog_manager", "CatalogSearchResult"),
    # classification_engine
    "ClassificationEngine": ("data.governance.classification_engine", "ClassificationEngine"),
    "ClassificationRule": ("data.governance.classification_engine", "ClassificationRule"),
    "ClassificationResult": ("data.governance.classification_engine", "ClassificationResult"),
    # compliance_manager
    "ComplianceManager": ("data.governance.compliance_manager", "ComplianceManager"),
    "ComplianceControl": ("data.governance.compliance_manager", "ComplianceControl"),
    "ComplianceAssessment": ("data.governance.compliance_manager", "ComplianceAssessment"),
    # consent_manager
    "ConsentManager": ("data.governance.consent_manager", "ConsentManager"),
    "ConsentRecord": ("data.governance.consent_manager", "ConsentRecord"),
    "ConsentDecision": ("data.governance.consent_manager", "ConsentDecision"),
    # data_contracts
    "DataContract": ("data.governance.data_contracts", "DataContract"),
    "ContractValidator": ("data.governance.data_contracts", "ContractValidator"),
    "ContractValidationResult": ("data.governance.data_contracts", "ContractValidationResult"),
    # data_lineage
    "LineageGraph": ("data.governance.data_lineage", "LineageGraph"),
    "LineageNode": ("data.governance.data_lineage", "LineageNode"),
    "LineageEdge": ("data.governance.data_lineage", "LineageEdge"),
    "ImpactAnalysisResult": ("data.governance.data_lineage", "ImpactAnalysisResult"),
    # data_ownership
    "OwnershipRegistry": ("data.governance.data_ownership", "OwnershipRegistry"),
    "DataOwner": ("data.governance.data_ownership", "DataOwner"),
    "StewardAssignment": ("data.governance.data_ownership", "StewardAssignment"),
    # data_quality_governance
    "DataQualityGovernance": ("data.governance.data_quality_governance", "DataQualityGovernance"),
    "QualityPolicy": ("data.governance.data_quality_governance", "QualityPolicy"),
    "QualityScorecard": ("data.governance.data_quality_governance", "QualityScorecard"),
    # lifecycle_manager
    "DataLifecycleManager": ("data.governance.lifecycle_manager", "DataLifecycleManager"),
    "LifecyclePolicy": ("data.governance.lifecycle_manager", "LifecyclePolicy"),
    "LifecycleState": ("data.governance.lifecycle_manager", "LifecycleState"),
    # metadata_registry
    "MetadataRegistry": ("data.governance.metadata_registry", "MetadataRegistry"),
    "MetadataRecord": ("data.governance.metadata_registry", "MetadataRecord"),
    "MetadataQuery": ("data.governance.metadata_registry", "MetadataQuery"),
    # policy_engine
    "GovernancePolicyEngine": ("data.governance.policy_engine", "GovernancePolicyEngine"),
    "GovernancePolicy": ("data.governance.policy_engine", "GovernancePolicy"),
    "PolicyDecision": ("data.governance.policy_engine", "PolicyDecision"),
    # privacy_governance
    "PrivacyGovernanceEngine": ("data.governance.privacy_governance", "PrivacyGovernanceEngine"),
    "PrivacyRiskAssessment": ("data.governance.privacy_governance", "PrivacyRiskAssessment"),
    "PrivacyControl": ("data.governance.privacy_governance", "PrivacyControl"),
    # retention_manager
    "RetentionManager": ("data.governance.retention_manager", "RetentionManager"),
    "RetentionPolicy": ("data.governance.retention_manager", "RetentionPolicy"),
    "RetentionDecision": ("data.governance.retention_manager", "RetentionDecision"),
    # stewardship_workflow
    "StewardshipWorkflow": ("data.governance.stewardship_workflow", "StewardshipWorkflow"),
    "StewardshipTask": ("data.governance.stewardship_workflow", "StewardshipTask"),
    "WorkflowDecision": ("data.governance.stewardship_workflow", "WorkflowDecision"),
}


__all__ = [
    "__title__",
    "__description__",
    "__version__",
    "GovernanceModuleInfo",
    "MODULE_REGISTRY",
    "available_modules",
    "available_domains",
    "describe_package",
    "load_module",
    "load_symbol",
    "check_optional_dependencies",
    "GovernanceFacade",
    "facade",
    *_LAZY_SYMBOLS.keys(),
]


def __getattr__(name: str) -> Any:
    """
    Lazy public symbol loader.

    Keeps package import lightweight and avoids importing optional dependencies
    until a specific governance capability is requested.
    """
    if name in _LAZY_SYMBOLS:
        module_path, symbol_name = _LAZY_SYMBOLS[name]
        symbol = load_symbol(module_path, symbol_name)
        globals()[name] = symbol
        return symbol
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def available_modules(domain: Optional[str] = None) -> List[str]:
    """Return registered governance module names, optionally filtered by domain."""
    if domain is None:
        return sorted(MODULE_REGISTRY.keys())
    return sorted(name for name, info in MODULE_REGISTRY.items() if info.domain == domain)


def available_domains() -> List[str]:
    """Return governance domains represented in the package registry."""
    return sorted({info.domain for info in MODULE_REGISTRY.values()})


def describe_package() -> Dict[str, Any]:
    """Return package metadata and registered governance capabilities."""
    return {
        "title": __title__,
        "description": __description__,
        "version": __version__,
        "domains": available_domains(),
        "modules": {name: info.to_dict() for name, info in MODULE_REGISTRY.items()},
        "public_symbols": sorted(_LAZY_SYMBOLS.keys()),
    }


def load_module(name_or_path: str) -> Any:
    """
    Import and return a governance module by registry name or full module path.

    Parameters
    ----------
    name_or_path:
        Either a key from MODULE_REGISTRY, e.g. 'policy_engine', or a full module
        path, e.g. 'data.governance.policy_engine'.
    """
    module_path = MODULE_REGISTRY[name_or_path].module_path if name_or_path in MODULE_REGISTRY else name_or_path
    try:
        return importlib.import_module(module_path)
    except Exception as exc:
        logger.debug("Failed to import governance module %s", module_path, exc_info=True)
        raise ImportError(f"Could not import governance module '{module_path}': {exc}") from exc


def load_symbol(module_path: str, symbol_name: str) -> Any:
    """Import a symbol from a governance module with clear operational errors."""
    module = load_module(module_path)
    try:
        return getattr(module, symbol_name)
    except AttributeError as exc:
        raise ImportError(f"Module '{module_path}' does not expose symbol '{symbol_name}'") from exc


def check_optional_dependencies(module_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Check whether optional dependencies for one or all governance modules are importable.

    Returns a serializable report useful for startup health checks, CI and deployment
    readiness gates.
    """
    modules: Mapping[str, GovernanceModuleInfo]
    if module_name:
        if module_name not in MODULE_REGISTRY:
            raise KeyError(f"Unknown governance module: {module_name}")
        modules = {module_name: MODULE_REGISTRY[module_name]}
    else:
        modules = MODULE_REGISTRY

    dependency_status: Dict[str, Dict[str, bool]] = {}
    for name, info in modules.items():
        status: Dict[str, bool] = {}
        for dependency in info.optional_dependencies:
            import_name = _dependency_import_name(dependency)
            try:
                importlib.import_module(import_name)
            except Exception:
                status[dependency] = False
            else:
                status[dependency] = True
        dependency_status[name] = status

    return {
        "ok": all(all(values.values()) for values in dependency_status.values()),
        "modules": dependency_status,
    }


def _dependency_import_name(package_name: str) -> str:
    aliases = {
        "scikit-learn": "sklearn",
        "beautifulsoup4": "bs4",
        "pyyaml": "yaml",
    }
    return aliases.get(package_name, package_name)


class GovernanceFacade:
    """
    Convenience facade for dynamic enterprise governance composition.

    Useful when governance capabilities are configured from YAML/JSON or enabled
    by deployment profile, domain, tenant or compliance scope.
    """

    def __init__(self) -> None:
        self._module_cache: Dict[str, Any] = {}

    def module(self, name_or_path: str) -> Any:
        if name_or_path not in self._module_cache:
            self._module_cache[name_or_path] = load_module(name_or_path)
        return self._module_cache[name_or_path]

    def symbol(self, symbol_name: str) -> Any:
        if symbol_name not in _LAZY_SYMBOLS:
            raise KeyError(f"Unknown public governance symbol: {symbol_name}")
        module_path, exported_name = _LAZY_SYMBOLS[symbol_name]
        return load_symbol(module_path, exported_name)

    def modules_by_domain(self, domain: str) -> Dict[str, GovernanceModuleInfo]:
        return {name: info for name, info in MODULE_REGISTRY.items() if info.domain == domain}

    def describe(self) -> Dict[str, Any]:
        return describe_package()

    def health(self) -> Dict[str, Any]:
        return check_optional_dependencies()


facade = GovernanceFacade()

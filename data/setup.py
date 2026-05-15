"""
setup.py

Enterprise setup script for the `data` platform package.

This file provides a robust packaging configuration for local development,
CI/CD builds, internal artifact registries, and production deployments.

Recommended modern usage:
    pip install -e .[dev]
    python -m build
    twine upload dist/*

For long-term maintainability, you may later move static metadata to
`pyproject.toml`, keeping this file as a compatibility bridge.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List

from setuptools import find_packages, setup


ROOT_DIR = Path(__file__).resolve().parent
PACKAGE_NAME = "data"


def read_file(filename: str, default: str = "") -> str:
    path = ROOT_DIR / filename
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8")


def read_version() -> str:
    """Read package version from data/__init__.py or APP_VERSION."""
    env_version = os.getenv("APP_VERSION")
    if env_version:
        return normalize_version(env_version)

    init_file = ROOT_DIR / PACKAGE_NAME / "__init__.py"
    if init_file.exists():
        content = init_file.read_text(encoding="utf-8")
        match = re.search(r"__version__\s*=\s*[\"']([^\"']+)[\"']", content)
        if match:
            return normalize_version(match.group(1))

    return "1.0.0"


def normalize_version(version: str) -> str:
    """Normalize a version string enough for common PEP 440-compatible builds."""
    version = version.strip()
    if re.fullmatch(r"\d+\.\d+\.\d+(?:[a-zA-Z0-9\.\-\+]+)?", version):
        return version
    sanitized = re.sub(r"[^0-9a-zA-Z\.]+", ".", version).strip(".")
    return sanitized or "1.0.0"


def parse_requirements(filename: str) -> List[str]:
    """Parse requirement files while ignoring comments/options."""
    requirements: List[str] = []
    path = ROOT_DIR / filename
    if not path.exists():
        return requirements

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r", "--requirement", "-c", "--constraint", "--index-url", "--extra-index-url")):
            continue
        requirements.append(line)
    return requirements


LONG_DESCRIPTION = read_file(
    "README.md",
    default=(
        "Enterprise-grade modular data platform package for ingestion, processing, "
        "validation, quality, governance, security, observability, orchestration, "
        "and AI-enabled data workflows."
    ),
)

BASE_REQUIREMENTS = parse_requirements("requirements.txt") or [
    "python-dotenv>=1.0.0,<2.0.0",
    "pydantic>=2.7.0,<3.0.0",
    "typing-extensions>=4.10.0",
]

EXTRAS_REQUIRE: Dict[str, List[str]] = {
    "dataframe": [
        "pandas>=2.2.0,<3.0.0",
        "numpy>=1.26.0,<3.0.0",
        "pyarrow>=15.0.0,<22.0.0",
        "openpyxl>=3.1.0,<4.0.0",
    ],
    "database": [
        "SQLAlchemy>=2.0.0,<3.0.0",
        "psycopg2-binary>=2.9.9,<3.0.0",
        "alembic>=1.13.0,<2.0.0",
    ],
    "api": [
        "fastapi>=0.110.0,<1.0.0",
        "uvicorn[standard]>=0.29.0,<1.0.0",
        "httpx>=0.27.0,<1.0.0",
        "orjson>=3.10.0,<4.0.0",
    ],
    "streaming": [
        "confluent-kafka>=2.4.0,<3.0.0",
        "redis>=5.0.0,<6.0.0",
    ],
    "observability": [
        "prometheus-client>=0.20.0,<1.0.0",
        "opentelemetry-api>=1.24.0,<2.0.0",
        "opentelemetry-sdk>=1.24.0,<2.0.0",
        "structlog>=24.1.0,<26.0.0",
    ],
    "quality": [
        "pandas>=2.2.0,<3.0.0",
        "numpy>=1.26.0,<3.0.0",
        "python-dateutil>=2.9.0,<3.0.0",
    ],
    "ai": [
        "openai>=1.30.0,<2.0.0",
        "tiktoken>=0.7.0,<1.0.0",
        "scikit-learn>=1.4.0,<2.0.0",
    ],
    "dev": [
        "pytest>=8.0.0,<9.0.0",
        "pytest-cov>=5.0.0,<7.0.0",
        "pytest-mock>=3.14.0,<4.0.0",
        "ruff>=0.4.0,<1.0.0",
        "mypy>=1.10.0,<2.0.0",
        "black>=24.0.0,<26.0.0",
        "build>=1.2.0,<2.0.0",
        "twine>=5.0.0,<7.0.0",
        "pre-commit>=3.7.0,<5.0.0",
    ],
    "docs": [
        "mkdocs>=1.6.0,<2.0.0",
        "mkdocs-material>=9.5.0,<11.0.0",
        "mkdocstrings[python]>=0.25.0,<1.0.0",
    ],
}

EXTRAS_REQUIRE["all"] = sorted(
    set(dep for extra, deps in EXTRAS_REQUIRE.items() if extra not in {"dev", "docs"} for dep in deps)
)
EXTRAS_REQUIRE["full"] = sorted(set(dep for deps in EXTRAS_REQUIRE.values() for dep in deps))

setup(
    name=PACKAGE_NAME,
    version=read_version(),
    description="Enterprise-grade modular data platform package.",
    long_description=LONG_DESCRIPTION,
    long_description_content_type="text/markdown",
    author=os.getenv("APP_AUTHOR", "Data Platform Team"),
    author_email=os.getenv("APP_AUTHOR_EMAIL", "data-platform@example.com"),
    maintainer=os.getenv("APP_MAINTAINER", "Data Platform Team"),
    maintainer_email=os.getenv("APP_MAINTAINER_EMAIL", "data-platform@example.com"),
    url=os.getenv("APP_HOMEPAGE", ""),
    project_urls={
        "Documentation": os.getenv("APP_DOCS_URL", ""),
        "Source": os.getenv("APP_SOURCE_URL", ""),
        "Issue Tracker": os.getenv("APP_ISSUES_URL", ""),
    },
    license=os.getenv("APP_LICENSE", "Proprietary"),
    packages=find_packages(
        include=[
            "data",
            "data.*",
        ],
        exclude=[
            "tests",
            "tests.*",
            "docs",
            "docs.*",
            "examples",
            "examples.*",
        ],
    ),
    include_package_data=True,
    package_data={
        "data": [
            "py.typed",
            "config/*.json",
            "config/*.yaml",
            "config/*.yml",
            "schemas/*.json",
            "templates/*.json",
            "templates/*.yaml",
            "templates/*.yml",
        ]
    },
    python_requires=">=3.10",
    install_requires=BASE_REQUIREMENTS,
    extras_require=EXTRAS_REQUIRE,
    entry_points={
        "console_scripts": [
            "data-platform=data.cli:main",
            "data-quality=data.quality.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: Information Technology",
        "Intended Audience :: System Administrators",
        "License :: Other/Proprietary License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Database",
        "Topic :: Scientific/Engineering :: Information Analysis",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: System :: Monitoring",
        "Typing :: Typed",
    ],
    keywords=[
        "data-platform",
        "data-quality",
        "data-governance",
        "data-observability",
        "lakehouse",
        "etl",
        "elt",
        "orchestration",
        "ingestion",
        "validation",
        "profiling",
        "schema-drift",
        "rag",
        "enterprise",
    ],
    zip_safe=False,
)

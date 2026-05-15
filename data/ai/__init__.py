"""
data/ai/__init__.py

Pacote enterprise para componentes de Inteligência Artificial, Machine Learning,
LLM, embeddings, inferência, avaliação, feature engineering e orquestração de IA.

Objetivos do pacote:
- Centralizar APIs públicas do domínio data.ai.
- Fornecer versionamento e metadados do pacote.
- Definir contratos/base classes reutilizáveis.
- Evitar imports pesados no carregamento inicial com lazy imports.
- Padronizar exceções, tipos, status e configurações globais.
- Manter compatibilidade para evolução modular da arquitetura.

Sugestão de estrutura futura:

data/ai/
    __init__.py
    config.py
    exceptions.py
    registry.py
    schemas.py
    prompts.py
    embeddings.py
    vector_store.py
    llm_client.py
    inference.py
    evaluation.py
    feature_engineering.py
    model_registry.py
    model_serving.py
    pipelines.py
    agents.py
    guardrails.py
    observability.py
"""

from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Optional, Protocol, Sequence, Tuple, TypeVar


# =============================================================================
# Package metadata
# =============================================================================

__title__ = "data.ai"
__description__ = "Enterprise AI package for ML, LLM, embeddings, inference and evaluation."
__version__ = "1.0.0"
__author__ = "Data Platform Team"
__license__ = "Proprietary"
__package_name__ = "data.ai"


# =============================================================================
# Logging
# =============================================================================

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# =============================================================================
# Public constants
# =============================================================================

DEFAULT_ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
DEFAULT_SERVICE_NAME = os.getenv("SERVICE_NAME", "data-ai")
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("DATA_AI_DEFAULT_TIMEOUT_SECONDS", "60"))
DEFAULT_MAX_RETRIES = int(os.getenv("DATA_AI_DEFAULT_MAX_RETRIES", "3"))
DEFAULT_BATCH_SIZE = int(os.getenv("DATA_AI_DEFAULT_BATCH_SIZE", "100"))


# =============================================================================
# Enums
# =============================================================================


class AIProvider(str, Enum):
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    AWS_BEDROCK = "aws_bedrock"
    HUGGINGFACE = "huggingface"
    LOCAL = "local"
    CUSTOM = "custom"


class TaskType(str, Enum):
    CHAT = "chat"
    COMPLETION = "completion"
    EMBEDDING = "embedding"
    CLASSIFICATION = "classification"
    REGRESSION = "regression"
    RANKING = "ranking"
    SUMMARIZATION = "summarization"
    EXTRACTION = "extraction"
    TRANSLATION = "translation"
    RERANKING = "reranking"
    IMAGE_GENERATION = "image_generation"
    AUDIO_TRANSCRIPTION = "audio_transcription"
    MODERATION = "moderation"
    CUSTOM = "custom"


class ModelLifecycleStage(str, Enum):
    EXPERIMENTAL = "experimental"
    STAGING = "staging"
    PRODUCTION = "production"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class InferenceStatus(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class EvaluationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


# =============================================================================
# Exceptions
# =============================================================================


class DataAIError(Exception):
    """Exceção base do pacote data.ai."""


class AIConfigurationError(DataAIError):
    """Erro de configuração de IA."""


class AIProviderError(DataAIError):
    """Erro retornado por provider externo/interno."""


class AIInferenceError(DataAIError):
    """Erro durante inferência."""


class AIValidationError(DataAIError):
    """Erro de validação de payload, schema ou contrato."""


class AIModelNotFoundError(DataAIError):
    """Modelo não encontrado em registry ou provider."""


class AIRateLimitError(AIProviderError):
    """Limite de requisições atingido."""


class AITimeoutError(AIProviderError):
    """Timeout em operação de IA."""


# =============================================================================
# Dataclasses base
# =============================================================================


@dataclass(frozen=True)
class AIModelRef:
    """Referência padronizada para modelos de IA/ML."""

    name: str
    provider: AIProvider = AIProvider.CUSTOM
    version: Optional[str] = None
    task_type: TaskType = TaskType.CUSTOM
    stage: ModelLifecycleStage = ModelLifecycleStage.EXPERIMENTAL
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        version = f":{self.version}" if self.version else ""
        return f"{self.provider.value}/{self.name}{version}"


@dataclass(frozen=True)
class AIRequestContext:
    """Contexto operacional para chamadas de IA."""

    request_id: str
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    correlation_id: Optional[str] = None
    trace_id: Optional[str] = None
    environment: str = DEFAULT_ENVIRONMENT
    service_name: str = DEFAULT_SERVICE_NAME
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class AIUsage:
    """Uso/custo lógico de uma operação de IA."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Optional[float] = None
    latency_ms: Optional[float] = None


@dataclass
class AIResponse:
    """Resposta padronizada para operações de IA."""

    status: InferenceStatus
    output: Any = None
    model: Optional[AIModelRef] = None
    usage: Optional[AIUsage] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == InferenceStatus.SUCCEEDED and self.error is None


@dataclass(frozen=True)
class RetryConfig:
    max_retries: int = DEFAULT_MAX_RETRIES
    base_seconds: float = 0.5
    max_seconds: float = 30.0
    jitter: bool = True


@dataclass(frozen=True)
class AIClientConfig:
    provider: AIProvider = AIProvider.CUSTOM
    model: Optional[str] = None
    api_key: Optional[str] = None
    endpoint: Optional[str] = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    batch_size: int = DEFAULT_BATCH_SIZE
    extra: Mapping[str, Any] = field(default_factory=dict)


# =============================================================================
# Protocols públicos
# =============================================================================


class AIClient(Protocol):
    """Contrato mínimo para clients de IA."""

    def infer(self, payload: Mapping[str, Any], context: Optional[AIRequestContext] = None) -> AIResponse:
        """Executa inferência síncrona."""


class EmbeddingClient(Protocol):
    """Contrato mínimo para geração de embeddings."""

    def embed(self, texts: Sequence[str], context: Optional[AIRequestContext] = None) -> Sequence[Sequence[float]]:
        """Gera embeddings para uma sequência de textos."""


class ModelEvaluator(Protocol):
    """Contrato mínimo para avaliação de modelos/prompts."""

    def evaluate(self, dataset: Iterable[Mapping[str, Any]], context: Optional[AIRequestContext] = None) -> Mapping[str, Any]:
        """Avalia um modelo, prompt ou pipeline."""


class ModelRegistry(Protocol):
    """Contrato mínimo para registry de modelos."""

    def get_model(self, name: str, version: Optional[str] = None) -> AIModelRef:
        """Busca uma referência de modelo."""

    def register_model(self, model: AIModelRef) -> None:
        """Registra uma referência de modelo."""


# =============================================================================
# Lazy imports
# =============================================================================

_T = TypeVar("_T")

_LAZY_IMPORTS: Dict[str, str] = {
    # Exemplos para módulos futuros:
    # "AISettings": "data.ai.config",
    # "PromptTemplate": "data.ai.prompts",
    # "EmbeddingService": "data.ai.embeddings",
    # "VectorStore": "data.ai.vector_store",
    # "LLMClient": "data.ai.llm_client",
    # "InferencePipeline": "data.ai.inference",
    # "EvaluationRunner": "data.ai.evaluation",
    # "FeatureEngineer": "data.ai.feature_engineering",
    # "GuardrailEngine": "data.ai.guardrails",
}


def __getattr__(name: str) -> Any:
    """
    Lazy import para evitar carregar dependências pesadas no import do pacote.

    Exemplo futuro:
        from data.ai import LLMClient
    """

    module_name = _LAZY_IMPORTS.get(name)
    if not module_name:
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> Sequence[str]:
    return sorted(list(globals().keys()) + list(_LAZY_IMPORTS.keys()))


# =============================================================================
# Helpers públicos
# =============================================================================


def get_package_info() -> Dict[str, Any]:
    """Retorna metadados básicos do pacote."""

    return {
        "title": __title__,
        "description": __description__,
        "version": __version__,
        "author": __author__,
        "license": __license__,
        "package_name": __package_name__,
        "environment": DEFAULT_ENVIRONMENT,
        "service_name": DEFAULT_SERVICE_NAME,
    }


def build_model_ref(
    name: str,
    provider: Union[AIProvider, str] = AIProvider.CUSTOM,
    version: Optional[str] = None,
    task_type: Union[TaskType, str] = TaskType.CUSTOM,
    stage: Union[ModelLifecycleStage, str] = ModelLifecycleStage.EXPERIMENTAL,
    metadata: Optional[Mapping[str, Any]] = None,
) -> AIModelRef:
    """Cria uma referência padronizada de modelo."""

    return AIModelRef(
        name=name,
        provider=provider if isinstance(provider, AIProvider) else AIProvider(provider),
        version=version,
        task_type=task_type if isinstance(task_type, TaskType) else TaskType(task_type),
        stage=stage if isinstance(stage, ModelLifecycleStage) else ModelLifecycleStage(stage),
        metadata=metadata or {},
    )


def build_success_response(
    output: Any,
    model: Optional[AIModelRef] = None,
    usage: Optional[AIUsage] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> AIResponse:
    """Cria resposta padronizada de sucesso."""

    return AIResponse(
        status=InferenceStatus.SUCCEEDED,
        output=output,
        model=model,
        usage=usage,
        metadata=dict(metadata or {}),
    )


def build_error_response(
    error: Union[str, Exception],
    status: InferenceStatus = InferenceStatus.FAILED,
    model: Optional[AIModelRef] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> AIResponse:
    """Cria resposta padronizada de erro."""

    return AIResponse(
        status=status,
        model=model,
        error=str(error),
        metadata=dict(metadata or {}),
    )


def validate_required_keys(payload: Mapping[str, Any], required_keys: Sequence[str]) -> None:
    """Valida presença de chaves obrigatórias em um payload."""

    missing = [key for key in required_keys if payload.get(key) in (None, "")]
    if missing:
        raise AIValidationError(f"Campos obrigatórios ausentes: {missing}")


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "__title__",
    "__description__",
    "__version__",
    "__author__",
    "__license__",
    "__package_name__",
    "DEFAULT_ENVIRONMENT",
    "DEFAULT_SERVICE_NAME",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_BATCH_SIZE",
    "AIProvider",
    "TaskType",
    "ModelLifecycleStage",
    "InferenceStatus",
    "EvaluationStatus",
    "DataAIError",
    "AIConfigurationError",
    "AIProviderError",
    "AIInferenceError",
    "AIValidationError",
    "AIModelNotFoundError",
    "AIRateLimitError",
    "AITimeoutError",
    "AIModelRef",
    "AIRequestContext",
    "AIUsage",
    "AIResponse",
    "RetryConfig",
    "AIClientConfig",
    "AIClient",
    "EmbeddingClient",
    "ModelEvaluator",
    "ModelRegistry",
    "get_package_info",
    "build_model_ref",
    "build_success_response",
    "build_error_response",
    "validate_required_keys",
]

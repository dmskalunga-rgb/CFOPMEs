"""
data/utils/dependency_injection.py

Enterprise-grade dependency injection container.

Este módulo fornece um container de injeção de dependências leve, tipado e
robusto para aplicações de dados, validação, ingestão, IA, pipelines, serviços
internos e jobs batch/streaming.

Capacidades principais:
- Registro por nome, tipo/interface ou chave composta.
- Lifetimes: singleton, transient e scoped.
- Factories, providers e instâncias prontas.
- Resolução automática por type hints no construtor/função.
- Escopos isolados para execução de pipelines, requests ou jobs.
- Overrides seguros para testes.
- Hooks de lifecycle: on_create, on_resolve e on_dispose.
- Detecção de dependência circular.
- Validação de registros obrigatórios.
- Thread-safe.
- Sem dependências externas obrigatórias.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    Generic,
    Iterable,
    Iterator,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    Type,
    TypeVar,
    Union,
    get_type_hints,
)


logger = logging.getLogger(__name__)

T = TypeVar("T")
Provider = Callable[["Container"], Any]
LifecycleHook = Callable[[Any], None]
ResolveKey = Union[str, Type[Any], Tuple[str, Type[Any]]]


class ServiceLifetime(str, Enum):
    """Tempo de vida da dependência registrada."""

    SINGLETON = "SINGLETON"
    TRANSIENT = "TRANSIENT"
    SCOPED = "SCOPED"


class RegistrationKind(str, Enum):
    """Tipo de registro no container."""

    INSTANCE = "INSTANCE"
    FACTORY = "FACTORY"
    CLASS = "CLASS"
    ALIAS = "ALIAS"


class DIError(Exception):
    """Erro base de dependency injection."""


class ServiceNotRegisteredError(DIError):
    """Dependência não registrada."""


class CircularDependencyError(DIError):
    """Dependência circular detectada."""


class ServiceRegistrationError(DIError):
    """Registro inválido."""


class ScopeDisposedError(DIError):
    """Uso de escopo já descartado."""


class Disposable(Protocol):
    """Contrato opcional para recursos descartáveis."""

    def dispose(self) -> None:
        """Libera recursos."""


@dataclass(frozen=True)
class ServiceKey:
    """Chave normalizada de serviço."""

    name: Optional[str] = None
    service_type: Optional[Type[Any]] = None

    def __post_init__(self) -> None:
        if self.name is None and self.service_type is None:
            raise ServiceRegistrationError("ServiceKey requires name or service_type")

    @staticmethod
    def from_value(value: ResolveKey) -> "ServiceKey":
        if isinstance(value, tuple):
            name, service_type = value
            return ServiceKey(name=name, service_type=service_type)
        if isinstance(value, str):
            return ServiceKey(name=value)
        if inspect.isclass(value):
            return ServiceKey(service_type=value)
        raise ServiceRegistrationError(f"Invalid service key: {value!r}")

    def label(self) -> str:
        if self.name and self.service_type:
            return f"{self.name}:{self.service_type.__module__}.{self.service_type.__qualname__}"
        if self.name:
            return self.name
        assert self.service_type is not None
        return f"{self.service_type.__module__}.{self.service_type.__qualname__}"


@dataclass
class ServiceDescriptor:
    """Descrição de um serviço registrado."""

    key: ServiceKey
    kind: RegistrationKind
    lifetime: ServiceLifetime
    provider: Optional[Provider] = None
    implementation_type: Optional[Type[Any]] = None
    instance: Optional[Any] = None
    alias_to: Optional[ServiceKey] = None
    on_create: Optional[LifecycleHook] = None
    on_resolve: Optional[LifecycleHook] = None
    on_dispose: Optional[LifecycleHook] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def validate(self) -> None:
        if self.kind == RegistrationKind.INSTANCE and self.instance is None:
            raise ServiceRegistrationError(f"INSTANCE registration requires instance: {self.key.label()}")
        if self.kind == RegistrationKind.FACTORY and self.provider is None:
            raise ServiceRegistrationError(f"FACTORY registration requires provider: {self.key.label()}")
        if self.kind == RegistrationKind.CLASS and self.implementation_type is None:
            raise ServiceRegistrationError(f"CLASS registration requires implementation_type: {self.key.label()}")
        if self.kind == RegistrationKind.ALIAS and self.alias_to is None:
            raise ServiceRegistrationError(f"ALIAS registration requires alias_to: {self.key.label()}")


@dataclass(frozen=True)
class ResolutionContext:
    """Contexto interno de resolução."""

    stack: Tuple[str, ...] = field(default_factory=tuple)

    def push(self, key: ServiceKey) -> "ResolutionContext":
        label = key.label()
        if label in self.stack:
            chain = " -> ".join((*self.stack, label))
            raise CircularDependencyError(f"Circular dependency detected: {chain}")
        return ResolutionContext(stack=(*self.stack, label))


class Scope:
    """Escopo de resolução para dependências scoped."""

    def __init__(self, container: "Container", scope_id: Optional[str] = None) -> None:
        self.container = container
        self.scope_id = scope_id or str(uuid.uuid4())
        self._instances: MutableMapping[str, Any] = {}
        self._disposed = False
        self._lock = threading.RLock()

    @property
    def disposed(self) -> bool:
        return self._disposed

    def resolve(self, key: ResolveKey, *, default: Any = None, required: bool = True) -> Any:
        if self._disposed:
            raise ScopeDisposedError(f"Scope already disposed: {self.scope_id}")
        return self.container.resolve(key, scope=self, default=default, required=required)

    def get_or_create(self, descriptor: ServiceDescriptor, factory: Callable[[], Any]) -> Any:
        if self._disposed:
            raise ScopeDisposedError(f"Scope already disposed: {self.scope_id}")
        label = descriptor.key.label()
        with self._lock:
            if label not in self._instances:
                self._instances[label] = factory()
            return self._instances[label]

    def dispose(self) -> None:
        with self._lock:
            if self._disposed:
                return
            for key, instance in reversed(list(self._instances.items())):
                self.container._dispose_instance(instance, descriptor_key=key)
            self._instances.clear()
            self._disposed = True

    def __enter__(self) -> "Scope":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.dispose()


class Container:
    """Container enterprise de injeção de dependências."""

    def __init__(self, *, parent: Optional["Container"] = None, name: str = "root") -> None:
        self.parent = parent
        self.name = name
        self._descriptors: MutableMapping[str, ServiceDescriptor] = {}
        self._singletons: MutableMapping[str, Any] = {}
        self._overrides: MutableMapping[str, ServiceDescriptor] = {}
        self._lock = threading.RLock()
        self._disposed = False

    def register_instance(
        self,
        key: ResolveKey,
        instance: Any,
        *,
        on_resolve: Optional[LifecycleHook] = None,
        on_dispose: Optional[LifecycleHook] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "Container":
        service_key = ServiceKey.from_value(key)
        descriptor = ServiceDescriptor(
            key=service_key,
            kind=RegistrationKind.INSTANCE,
            lifetime=ServiceLifetime.SINGLETON,
            instance=instance,
            on_resolve=on_resolve,
            on_dispose=on_dispose,
            metadata=metadata or {},
        )
        return self._register(descriptor)

    def register_factory(
        self,
        key: ResolveKey,
        provider: Provider,
        *,
        lifetime: ServiceLifetime = ServiceLifetime.TRANSIENT,
        on_create: Optional[LifecycleHook] = None,
        on_resolve: Optional[LifecycleHook] = None,
        on_dispose: Optional[LifecycleHook] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "Container":
        service_key = ServiceKey.from_value(key)
        descriptor = ServiceDescriptor(
            key=service_key,
            kind=RegistrationKind.FACTORY,
            lifetime=lifetime,
            provider=provider,
            on_create=on_create,
            on_resolve=on_resolve,
            on_dispose=on_dispose,
            metadata=metadata or {},
        )
        return self._register(descriptor)

    def register_class(
        self,
        key: ResolveKey,
        implementation_type: Optional[Type[Any]] = None,
        *,
        lifetime: ServiceLifetime = ServiceLifetime.TRANSIENT,
        on_create: Optional[LifecycleHook] = None,
        on_resolve: Optional[LifecycleHook] = None,
        on_dispose: Optional[LifecycleHook] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "Container":
        service_key = ServiceKey.from_value(key)
        impl = implementation_type or service_key.service_type
        if impl is None:
            raise ServiceRegistrationError("implementation_type is required when key is not a class")
        descriptor = ServiceDescriptor(
            key=service_key,
            kind=RegistrationKind.CLASS,
            lifetime=lifetime,
            implementation_type=impl,
            on_create=on_create,
            on_resolve=on_resolve,
            on_dispose=on_dispose,
            metadata=metadata or {},
        )
        return self._register(descriptor)

    def register_alias(self, alias: ResolveKey, target: ResolveKey) -> "Container":
        descriptor = ServiceDescriptor(
            key=ServiceKey.from_value(alias),
            kind=RegistrationKind.ALIAS,
            lifetime=ServiceLifetime.TRANSIENT,
            alias_to=ServiceKey.from_value(target),
        )
        return self._register(descriptor)

    def resolve(self, key: ResolveKey, *, scope: Optional[Scope] = None, default: Any = None, required: bool = True) -> Any:
        if self._disposed:
            raise DIError(f"Container already disposed: {self.name}")
        service_key = ServiceKey.from_value(key)
        try:
            return self._resolve(service_key, scope=scope, context=ResolutionContext())
        except ServiceNotRegisteredError:
            if required:
                raise
            return default

    def try_resolve(self, key: ResolveKey, *, scope: Optional[Scope] = None, default: Any = None) -> Any:
        return self.resolve(key, scope=scope, default=default, required=False)

    def create_scope(self, scope_id: Optional[str] = None) -> Scope:
        return Scope(self, scope_id=scope_id)

    @contextlib.contextmanager
    def scoped(self, scope_id: Optional[str] = None) -> Iterator[Scope]:
        scope = self.create_scope(scope_id=scope_id)
        try:
            yield scope
        finally:
            scope.dispose()

    @contextlib.contextmanager
    def override(self, key: ResolveKey, instance_or_provider: Any, *, lifetime: ServiceLifetime = ServiceLifetime.SINGLETON) -> Iterator[None]:
        service_key = ServiceKey.from_value(key)
        label = service_key.label()
        if callable(instance_or_provider) and not inspect.isclass(instance_or_provider):
            descriptor = ServiceDescriptor(
                key=service_key,
                kind=RegistrationKind.FACTORY,
                lifetime=lifetime,
                provider=instance_or_provider,
            )
        elif inspect.isclass(instance_or_provider):
            descriptor = ServiceDescriptor(
                key=service_key,
                kind=RegistrationKind.CLASS,
                lifetime=lifetime,
                implementation_type=instance_or_provider,
            )
        else:
            descriptor = ServiceDescriptor(
                key=service_key,
                kind=RegistrationKind.INSTANCE,
                lifetime=ServiceLifetime.SINGLETON,
                instance=instance_or_provider,
            )
        descriptor.validate()
        with self._lock:
            previous = self._overrides.get(label)
            self._overrides[label] = descriptor
            self._singletons.pop(label, None)
        try:
            yield
        finally:
            with self._lock:
                if previous is None:
                    self._overrides.pop(label, None)
                else:
                    self._overrides[label] = previous
                self._singletons.pop(label, None)

    def inject(self, func: Callable[..., T]) -> Callable[..., T]:
        """Decorator que injeta argumentos ausentes com base em type hints."""
        signature = inspect.signature(func)
        type_hints = get_type_hints(func)

        def wrapper(*args: Any, **kwargs: Any) -> T:
            bound = signature.bind_partial(*args, **kwargs)
            for param_name, parameter in signature.parameters.items():
                if param_name in bound.arguments:
                    continue
                annotation = type_hints.get(param_name)
                if annotation is None:
                    continue
                if parameter.default is not inspect.Parameter.empty:
                    required = False
                    default = parameter.default
                else:
                    required = True
                    default = None
                resolved = self.resolve(annotation, default=default, required=required)
                if resolved is not None or required:
                    kwargs[param_name] = resolved
            return func(*args, **kwargs)

        wrapper.__name__ = getattr(func, "__name__", "injected")
        wrapper.__doc__ = getattr(func, "__doc__", None)
        wrapper.__module__ = getattr(func, "__module__", __name__)
        return wrapper

    def build(self, cls: Type[T], *, scope: Optional[Scope] = None) -> T:
        """Constrói uma classe resolvendo dependências do __init__."""
        return self._construct_type(cls, scope=scope, context=ResolutionContext())

    def contains(self, key: ResolveKey) -> bool:
        service_key = ServiceKey.from_value(key)
        label = service_key.label()
        with self._lock:
            if label in self._descriptors or label in self._overrides:
                return True
        return self.parent.contains(key) if self.parent else False

    def validate_required(self, keys: Sequence[ResolveKey]) -> None:
        missing = [ServiceKey.from_value(key).label() for key in keys if not self.contains(key)]
        if missing:
            raise ServiceNotRegisteredError(f"Missing required services: {missing}")

    def descriptors(self) -> Tuple[ServiceDescriptor, ...]:
        with self._lock:
            return tuple(self._descriptors.values())

    def dispose(self) -> None:
        with self._lock:
            if self._disposed:
                return
            for key, instance in reversed(list(self._singletons.items())):
                self._dispose_instance(instance, descriptor_key=key)
            self._singletons.clear()
            self._disposed = True

    def _register(self, descriptor: ServiceDescriptor) -> "Container":
        descriptor.validate()
        with self._lock:
            self._descriptors[descriptor.key.label()] = descriptor
        return self

    def _resolve(self, key: ServiceKey, *, scope: Optional[Scope], context: ResolutionContext) -> Any:
        context = context.push(key)
        descriptor = self._find_descriptor(key)
        if descriptor is None:
            raise ServiceNotRegisteredError(f"Service not registered: {key.label()}")

        if descriptor.kind == RegistrationKind.ALIAS:
            assert descriptor.alias_to is not None
            return self._resolve(descriptor.alias_to, scope=scope, context=context)

        label = descriptor.key.label()
        if descriptor.lifetime == ServiceLifetime.SINGLETON:
            with self._lock:
                if label not in self._singletons:
                    self._singletons[label] = self._create_instance(descriptor, scope=scope, context=context)
                instance = self._singletons[label]
        elif descriptor.lifetime == ServiceLifetime.SCOPED:
            if scope is None:
                raise ServiceRegistrationError(f"Scoped service requires scope: {label}")
            instance = scope.get_or_create(descriptor, lambda: self._create_instance(descriptor, scope=scope, context=context))
        else:
            instance = self._create_instance(descriptor, scope=scope, context=context)

        if descriptor.on_resolve:
            descriptor.on_resolve(instance)
        return instance

    def _find_descriptor(self, key: ServiceKey) -> Optional[ServiceDescriptor]:
        label = key.label()
        with self._lock:
            if label in self._overrides:
                return self._overrides[label]
            if label in self._descriptors:
                return self._descriptors[label]
        if self.parent:
            return self.parent._find_descriptor(key)
        return None

    def _create_instance(self, descriptor: ServiceDescriptor, *, scope: Optional[Scope], context: ResolutionContext) -> Any:
        if descriptor.kind == RegistrationKind.INSTANCE:
            instance = descriptor.instance
        elif descriptor.kind == RegistrationKind.FACTORY:
            assert descriptor.provider is not None
            instance = descriptor.provider(self)
        elif descriptor.kind == RegistrationKind.CLASS:
            assert descriptor.implementation_type is not None
            instance = self._construct_type(descriptor.implementation_type, scope=scope, context=context)
        else:
            raise ServiceRegistrationError(f"Cannot create instance for descriptor kind: {descriptor.kind}")

        if descriptor.on_create and instance is not None:
            descriptor.on_create(instance)
        return instance

    def _construct_type(self, cls: Type[T], *, scope: Optional[Scope], context: ResolutionContext) -> T:
        signature = inspect.signature(cls.__init__)
        type_hints = get_type_hints(cls.__init__)
        kwargs: Dict[str, Any] = {}
        for param_name, parameter in signature.parameters.items():
            if param_name == "self":
                continue
            if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
                continue
            annotation = type_hints.get(param_name)
            if annotation is None:
                if parameter.default is inspect.Parameter.empty:
                    raise ServiceRegistrationError(
                        f"Cannot autowire parameter without type hint: {cls.__name__}.{param_name}"
                    )
                continue
            required = parameter.default is inspect.Parameter.empty
            try:
                kwargs[param_name] = self._resolve(ServiceKey.from_value(annotation), scope=scope, context=context)
            except ServiceNotRegisteredError:
                if required:
                    raise
        return cls(**kwargs)

    def _dispose_instance(self, instance: Any, *, descriptor_key: str) -> None:
        descriptor = self._descriptors.get(descriptor_key) or self._overrides.get(descriptor_key)
        try:
            if descriptor and descriptor.on_dispose:
                descriptor.on_dispose(instance)
            elif hasattr(instance, "dispose") and callable(instance.dispose):
                instance.dispose()
            elif hasattr(instance, "close") and callable(instance.close):
                instance.close()
        except Exception:
            logger.exception("Failed to dispose service instance: %s", descriptor_key)


class ContainerBuilder:
    """Builder fluente para montar containers."""

    def __init__(self, *, name: str = "root", parent: Optional[Container] = None) -> None:
        self.container = Container(parent=parent, name=name)

    def instance(self, key: ResolveKey, value: Any, **kwargs: Any) -> "ContainerBuilder":
        self.container.register_instance(key, value, **kwargs)
        return self

    def factory(self, key: ResolveKey, provider: Provider, **kwargs: Any) -> "ContainerBuilder":
        self.container.register_factory(key, provider, **kwargs)
        return self

    def singleton(self, key: ResolveKey, implementation_type: Optional[Type[Any]] = None, **kwargs: Any) -> "ContainerBuilder":
        self.container.register_class(key, implementation_type, lifetime=ServiceLifetime.SINGLETON, **kwargs)
        return self

    def transient(self, key: ResolveKey, implementation_type: Optional[Type[Any]] = None, **kwargs: Any) -> "ContainerBuilder":
        self.container.register_class(key, implementation_type, lifetime=ServiceLifetime.TRANSIENT, **kwargs)
        return self

    def scoped(self, key: ResolveKey, implementation_type: Optional[Type[Any]] = None, **kwargs: Any) -> "ContainerBuilder":
        self.container.register_class(key, implementation_type, lifetime=ServiceLifetime.SCOPED, **kwargs)
        return self

    def alias(self, alias: ResolveKey, target: ResolveKey) -> "ContainerBuilder":
        self.container.register_alias(alias, target)
        return self

    def build(self) -> Container:
        return self.container


def create_container(*, name: str = "root", parent: Optional[Container] = None) -> Container:
    """Cria um novo container vazio."""
    return Container(parent=parent, name=name)


def create_builder(*, name: str = "root", parent: Optional[Container] = None) -> ContainerBuilder:
    """Cria um builder de container."""
    return ContainerBuilder(name=name, parent=parent)


GLOBAL_CONTAINER = Container(name="global")


def get_global_container() -> Container:
    """Retorna container global da aplicação."""
    return GLOBAL_CONTAINER


def reset_global_container() -> Container:
    """Descarta e recria registros do container global.

    Útil apenas para testes ou inicialização controlada.
    """
    global GLOBAL_CONTAINER
    GLOBAL_CONTAINER.dispose()
    GLOBAL_CONTAINER = Container(name="global")
    return GLOBAL_CONTAINER


def inject(func: Callable[..., T]) -> Callable[..., T]:
    """Decorator usando o container global."""
    return GLOBAL_CONTAINER.inject(func)


def resolve(key: ResolveKey, *, default: Any = None, required: bool = True) -> Any:
    """Resolve serviço no container global."""
    return GLOBAL_CONTAINER.resolve(key, default=default, required=required)


__all__ = [
    "CircularDependencyError",
    "Container",
    "ContainerBuilder",
    "DIError",
    "Disposable",
    "GLOBAL_CONTAINER",
    "LifecycleHook",
    "Provider",
    "RegistrationKind",
    "ResolutionContext",
    "ResolveKey",
    "Scope",
    "ScopeDisposedError",
    "ServiceDescriptor",
    "ServiceKey",
    "ServiceLifetime",
    "ServiceNotRegisteredError",
    "ServiceRegistrationError",
    "create_builder",
    "create_container",
    "get_global_container",
    "inject",
    "reset_global_container",
    "resolve",
]

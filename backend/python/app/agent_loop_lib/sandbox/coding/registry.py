"""Pluggable sandbox backend registry — one ``SandboxBackendFactory`` per
provider (local, docker, e2b, daytona, …), looked up by name through
``SandboxBackendRegistry``.

Replaces the inline ``if backend == "local": … elif backend == "e2b": …``
in ``ControlPlane.start()`` and ``sandbox_bridge.build_coding_sandbox_manager()``
with an Open/Closed dispatch: adding a new provider means adding a factory
module + one ``registry.register()`` call, never editing the manager,
tools, bridge, or ControlPlane.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.agent_loop_lib.sandbox.coding.base import (
    CodingSandboxBackend,
    SandboxCapabilities,
    SandboxContext,
    SandboxRef,
)

__all__ = [
    "BackendHealth",
    "SandboxMiddlewareSpec",
    "SandboxBackendFactory",
    "SandboxBackendRegistry",
    "build_default_registry",
]

logger = logging.getLogger(__name__)

CODING_SANDBOX_TOOL_PATTERN = "/toolsets/coding_sandbox/**"

# Only used when a metered backend declares no `max_timeout_s` of its own.
_DEFAULT_METERED_MAX_TIMEOUT_S = 120.0

# Mirrors `CodeRequest.timeout` — what a `run_code` call costs when the model
# names no timeout of its own.
_DEFAULT_SANDBOX_TIMEOUT_S = 30.0


class BackendHealth(BaseModel):
    """Result of a factory's ``health_check()``."""

    available: bool
    reason: str | None = None
    latency_ms: float | None = None


@dataclass(frozen=True)
class SandboxMiddlewareSpec:
    """One hook a backend needs registered on its behalf.

    ``name`` matches the corresponding entry in ``ControlPlaneConfig.hooks``
    so a hook an operator configured explicitly is not also auto-registered
    from the factory — two copies of, say, the metered guard would run two
    independent budgets and cap the same timeout twice.
    """

    name: str
    event: str
    middleware: Any
    path_pattern: str | None = None


class SandboxBackendFactory(ABC):
    """One per provider — owns the provider's config schema, health probes,
    sandbox construction, optional middleware, and (for remote providers)
    reconnect from a serialised ``SandboxRef``.

    Subclasses MUST set ``backend_name`` (the config key: ``"local"``,
    ``"docker"``, ``"e2b"``, …) and ``config_model``."""

    backend_name: str
    config_model: type[BaseModel]

    def __init__(self, config: BaseModel, shared: Any | None = None) -> None:
        """``config`` is the factory-specific config (validated instance of
        ``config_model``); ``shared`` is the shared ``CodingSandboxConfig``
        (passed for fields like ``package_allowlist`` that apply to every
        backend)."""
        self._config = config
        self._shared = shared

    @property
    def config(self) -> BaseModel:
        return self._config

    @property
    def shared(self) -> Any | None:
        return self._shared

    @abstractmethod
    def is_installed(self) -> bool:
        """Cheap sync check — can the provider's dependencies be imported?"""
        ...

    @abstractmethod
    async def health_check(self) -> BackendHealth:
        """I/O: daemon ping, API auth, image present, etc."""
        ...

    async def warmup(self) -> None:
        """Optional: pull image, pre-auth.  Default no-op."""

    @abstractmethod
    def create(self, ctx: SandboxContext) -> CodingSandboxBackend:
        """Construct a new, un-provisioned sandbox instance."""
        ...

    async def reconnect(self, ref: SandboxRef) -> CodingSandboxBackend:
        """Reconnect to a live sandbox from a serialised ``SandboxRef``.
        Default: not supported (local/Docker don't survive process restart)."""
        raise NotImplementedError(
            f"{self.backend_name!r} backend does not support reconnect"
        )

    def middleware(self) -> list[SandboxMiddlewareSpec]:
        """Hooks this backend needs registered on the kernel.

        Derived from ``capabilities()`` rather than hard-coded per provider:
        any backend that declares ``is_metered`` gets the billing/timeout
        guard, so a new metered provider cannot forget it by omitting an
        override. Providers with genuinely bespoke needs still override.
        """
        caps = self.capabilities()
        if not caps.is_metered:
            return []

        from app.agent_loop_lib.hooks.middleware.builtin.metered_sandbox_guard import (
            metered_sandbox_guard,
        )

        return [
            SandboxMiddlewareSpec(
                name="metered_sandbox_guard",
                event="pre_tool_use",
                path_pattern=CODING_SANDBOX_TOOL_PATTERN,
                middleware=metered_sandbox_guard(
                    max_timeout=caps.max_timeout_s or _DEFAULT_METERED_MAX_TIMEOUT_S,
                    # What a call that omits `timeout` will actually cost —
                    # the tool substitutes this and the provider bills it.
                    default_timeout=getattr(
                        self._shared, "default_timeout", _DEFAULT_SANDBOX_TIMEOUT_S,
                    ),
                ),
            ),
        ]

    @abstractmethod
    def capabilities(self) -> SandboxCapabilities:
        """Declare what this backend supports — used by metered guard,
        manager, and future UI."""
        ...


class SandboxBackendRegistry:
    """Name → ``SandboxBackendFactory`` mapping with duplicate-name
    rejection and health aggregation."""

    def __init__(self) -> None:
        self._factories: dict[str, SandboxBackendFactory] = {}

    def register(self, factory: SandboxBackendFactory) -> None:
        """Register a factory.  ``ValueError`` on duplicate name."""
        name = factory.backend_name
        if name in self._factories:
            raise ValueError(
                f"sandbox backend {name!r} already registered "
                f"(existing: {self._factories[name].__class__.__name__})"
            )
        self._factories[name] = factory
        logger.info("SandboxBackendRegistry: registered %r (%s)", name, type(factory).__name__)

    def get(self, name: str) -> SandboxBackendFactory:
        """Look up by name.  ``ValueError`` lists known names on miss."""
        factory = self._factories.get(name)
        if factory is None:
            known = sorted(self._factories.keys())
            raise ValueError(
                f"unknown sandbox backend {name!r}; "
                f"registered backends: {known}"
            )
        if not factory.is_installed():
            raise ValueError(
                f"sandbox backend {name!r} is registered but its dependencies "
                f"are not installed; install them or choose another backend "
                f"from {sorted(self._factories.keys())}"
            )
        return factory

    def names(self) -> list[str]:
        """All registered backend names, sorted."""
        return sorted(self._factories.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._factories

    async def available(self) -> dict[str, BackendHealth]:
        """Run ``health_check()`` on every registered factory."""
        results: dict[str, BackendHealth] = {}
        for name, factory in self._factories.items():
            try:
                results[name] = await factory.health_check()
            except Exception as exc:
                results[name] = BackendHealth(
                    available=False, reason=f"{type(exc).__name__}: {exc}"
                )
        return results


def build_default_registry(
    shared_config: Any | None = None,
    *,
    backend_options: dict[str, dict[str, Any]] | None = None,
) -> SandboxBackendRegistry:
    """Build a registry with every built-in factory registered.

    Registration does NOT depend on `is_installed()`. An uninstalled
    backend that is simply absent from the registry surfaces later as
    "unknown sandbox backend 'e2b'", which sends the operator looking for
    a typo instead of a missing package — so it is registered, and
    `get()`/`health_check()` report the real reason.

    A `ValidationError` from a backend's own options is likewise not
    swallowed: config the operator wrote and got wrong has to be visible,
    with its field errors, at startup.

    `shared_config` is the `CodingSandboxConfig` (or None standalone);
    `backend_options` keys matching no factory are ignored, leaving room
    for plugin-registered backends.
    """
    from app.agent_loop_lib.sandbox.coding.factories import (
        BUILTIN_FACTORIES,
        build_factory,
    )

    registry = SandboxBackendRegistry()
    opts = backend_options or {}

    for cls in BUILTIN_FACTORIES:
        name = cls.backend_name
        registry.register(
            build_factory(
                name,
                backend_options=opts.get(name, {}),
                shared_config=shared_config,
            )
        )
    return registry

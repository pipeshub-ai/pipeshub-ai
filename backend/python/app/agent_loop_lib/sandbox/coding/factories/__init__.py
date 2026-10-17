"""Concrete ``SandboxBackendFactory`` implementations — one per provider.

Adding a provider is one module plus one entry in ``BUILTIN_FACTORIES``.
Everything else — name lookup, config validation, registration, health,
middleware — is driven off the ``backend_name`` and ``config_model`` the
factory class already declares, so there is no dispatch chain to extend
and no second list to keep in sync.
"""

from __future__ import annotations

from typing import Any

from app.agent_loop_lib.sandbox.coding.factories.docker import (
    DockerCodingSandboxFactory,
)
from app.agent_loop_lib.sandbox.coding.factories.e2b import E2BCodingSandboxFactory
from app.agent_loop_lib.sandbox.coding.factories.local import LocalCodingSandboxFactory
from app.agent_loop_lib.sandbox.coding.registry import SandboxBackendFactory

__all__ = ["BUILTIN_FACTORIES", "build_factory", "builtin_factory_names"]

BUILTIN_FACTORIES: tuple[type[SandboxBackendFactory], ...] = (
    LocalCodingSandboxFactory,
    DockerCodingSandboxFactory,
    E2BCodingSandboxFactory,
)


def builtin_factory_names() -> list[str]:
    return sorted(cls.backend_name for cls in BUILTIN_FACTORIES)


def build_factory(
    backend_name: str,
    *,
    backend_options: dict[str, Any] | None = None,
    shared_config: Any | None = None,
) -> SandboxBackendFactory:
    """Construct the factory for ``backend_name``.

    A ``ValidationError`` from ``config_model`` propagates untouched: bad
    backend options are a deployment mistake the operator has to see with
    its field errors intact, not something to be reported later as an
    unrelated "unknown backend".
    """
    for cls in BUILTIN_FACTORIES:
        if cls.backend_name == backend_name:
            config = cls.config_model(**(backend_options or {}))
            return cls(config=config, shared=shared_config)
    raise ValueError(
        f"unknown sandbox backend {backend_name!r}; "
        f"built-in backends are {builtin_factory_names()}"
    )

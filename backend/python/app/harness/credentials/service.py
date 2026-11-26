"""The PipesHub credential service: one handle table per process over the platform's secret sources.

Handles are resolved in the process that issued them (the worker or query
service for tools, the model gateway for model keys); none crosses to Node or
to another process. The dedicated process role of REPORT D22 replaces this
binding with a remote adapter of the same port.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.control_plane.runtime.bindings import BINDING_KIND
from app.harness.credentials.bindings import BindingDocuments, BindingSecretSource
from app.harness.credentials.sources import (
    MCP_KIND,
    MODEL_KIND,
    TOOLSET_KIND,
    McpSecretSource,
    ModelKeySource,
    ToolsetSecretSource,
)
from app.harness.credentials.vault import VAULT_KIND, VaultSecretStore
from harness_core.clock import SystemClock
from harness_core.credentials.service import HandleCredentialService

if TYPE_CHECKING:
    from harness_core.clock import Clock
    from harness_core.credentials.ports import SecretSource
    from harness_core.ids import IdSource


_PROCESS: dict[int, tuple[object, HandleCredentialService]] = {}


def pipeshub_sources(config: Any, clock: Clock) -> dict[str, SecretSource]:  # noqa: ANN401
    vault = VaultSecretStore(config, clock)
    bindings = BindingDocuments(vault)
    return {
        VAULT_KIND: vault,
        BINDING_KIND: BindingSecretSource(bindings),
        TOOLSET_KIND: ToolsetSecretSource(config, bindings),
        MCP_KIND: McpSecretSource(config, bindings),
        MODEL_KIND: ModelKeySource(config),
    }


def pipeshub_credential_service(
    config: Any,  # noqa: ANN401
    *,
    clock: Clock | None = None,
    ids: IdSource | None = None,
) -> HandleCredentialService:
    clock = clock or SystemClock()
    return HandleCredentialService(pipeshub_sources(config, clock), clock, ids=ids)


def credential_service_for(config: Any) -> HandleCredentialService:  # noqa: ANN401
    """The process's service for this configuration service (one per process in practice)."""
    held = _PROCESS.get(id(config))
    if held is not None and held[0] is config:
        return held[1]
    service = pipeshub_credential_service(config)
    _PROCESS[id(config)] = (config, service)
    return service


__all__ = ["VAULT_KIND", "credential_service_for", "pipeshub_credential_service", "pipeshub_sources"]

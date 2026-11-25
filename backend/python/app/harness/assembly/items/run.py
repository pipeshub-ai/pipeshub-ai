"""A run's item runtimes, built for the attempt from what its sources loaded (CPD-7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.control_plane.runtime.types import RunContext as ItemRunContext
from app.harness.assembly.items.mcp import McpConnector, McpItemRuntime
from app.harness.assembly.items.pins import pinned_items
from app.harness.assembly.items.registry import ItemRuntimes
from app.harness.assembly.items.skills import GraphSkillConnector, SkillsItemRuntime
from app.harness.assembly.items.toolsets import RunToolsetConnector, ToolsetItemRuntime
from app.harness.credentials.bindings import BindingCredentialBroker, BindingDocuments
from app.harness.credentials.service import credential_service_for
from app.harness.credentials.vault import VaultSecretStore
from harness_core.clock import SystemClock
from harness_core.credentials.errors import CredentialUnavailable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from app.control_plane.contracts.generated.effective_set import PinnedBinding
    from app.control_plane.runtime.types import ItemLoad
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.descriptor_pins import ToolDescriptorSource
    from app.harness.assembly.items.credentials import (
        PinnedBindingLeases,
        RunCredentialBroker,
    )
    from app.harness.assembly.services import RunServices
    from app.harness.assembly.tool_sources import SourcedTool
    from harness_core.clock import Clock
    from harness_core.credentials.types import (
        CredentialPurpose,
        Secret,
        ShortLivedCredential,
    )


class PinnedCredentialsUnavailable:
    """The broker of a run admitted without a pin: no effective set names a binding for its items, so their
    credentials are resolved where today's toolset and MCP code resolves them, and a runtime that asks this
    broker for one is refused, never handed a secret no pin names. A pinned run gets ``BindingCredentialBroker``
    (``item_credentials``)."""

    async def lease(self, binding: PinnedBinding, purpose: CredentialPurpose, ttl_s: int) -> ShortLivedCredential:
        raise CredentialUnavailable("the run has no pin, so no binding is named for its items")

    async def resolve(self, handle: ShortLivedCredential, purpose: CredentialPurpose) -> Secret:
        raise CredentialUnavailable("the run has no pin, so no binding is named for its items")

    async def release(self, handle: ShortLivedCredential) -> None:
        return None

    async def close(self) -> int:
        return 0


def item_credentials(run: RunContext, services: RunServices, clock: Clock | None = None) -> RunCredentialBroker:
    """One attempt's broker for its items: a pinned run's leases its pinned bindings from the process's credential
    service over the vault's binding documents (CPD-8); a run without a pin has none to lease."""
    if run.resolved is None:
        return PinnedCredentialsUnavailable()
    config = services.config_service()
    return BindingCredentialBroker(
        credential_service_for(config), BindingDocuments(VaultSecretStore(config, clock or SystemClock()))
    )


class SkillReader:
    """Whether a run's pinned skill still holds, read through ``PinnedSkills`` as the loop's skill reads are."""

    def __init__(
        self,
        graph: Callable[[], Awaitable[object]],
        agent_key: str | None,
        config: Callable[[], object] | None = None,
        *,
        viewers: tuple[str | None, ...] = (),
    ) -> None:
        self._graph = graph
        self._agent_key = agent_key
        self._config = config
        self._viewers = viewers

    async def holds(self, org_id: str, name: str, digest: str) -> bool:
        from app.agents.agent_loop.skills.bundle_source import PinnedSkills
        from app.harness.supply_chain.skills import SkillTrust
        from harness_core.skills import SkillBundleError

        # Without the review store an imported skill does not hold (``FirstPartyOnly``): never trusted on a guess.
        trust = SkillTrust(self._config()) if self._config is not None else None  # type: ignore[arg-type]
        try:
            _ = await PinnedSkills(await self._graph(), org_id, self._agent_key, viewers=self._viewers, trust=trust).files(name, digest)  # type: ignore[arg-type]
        except SkillBundleError:
            return False
        return True


def run_item_runtimes(
    run: RunContext,
    *,
    descriptors: ToolDescriptorSource,
    sourced: Sequence[SourcedTool],
    skills: SkillReader,
    close_mcp_session: Callable[[str], Awaitable[None]] | None,
    leases: PinnedBindingLeases | None = None,
) -> ItemRuntimes:
    names = {tool.tool_id: tool.name for tool in sourced}
    server_pins = {pin.ref: pin.digest for pin in run.descriptor_pins if pin.kind == "mcp"}
    return ItemRuntimes(
        [
            ToolsetItemRuntime(RunToolsetConnector(descriptors, names, leases)),
            McpItemRuntime(McpConnector(descriptors, names, close_mcp_session, leases), server_pins),
            SkillsItemRuntime(GraphSkillConnector(skills.holds)),
        ]
    )


async def lease_pinned_bindings(
    run: RunContext, leases: PinnedBindingLeases, credentials: RunCredentialBroker, attempt_id: str | None
) -> None:
    """Leases the binding of every bound item of the run, before anything creates a client from one."""
    await leases.lease_all(pinned_items(run), ItemRunContext(run.org_id, run.run_id, attempt_id), credentials)  # type: ignore[arg-type]


async def load_pinned_items(
    run: RunContext,
    runtimes: ItemRuntimes,
    attempt_id: str | None,
    credentials: RunCredentialBroker | None = None,
) -> ItemLoad:
    """Exactly the run's pinned items at their pinned digests; ``CapabilityLost`` names whatever moved.

    The same for the loop and an engine (FB-11): a pinned skill holds while it is in force for the run and its
    pinned content is in the skill's history, which is where both executors then read it from.
    """
    return await runtimes.load(
        pinned_items(run),
        ItemRunContext(run.org_id, run.run_id, attempt_id),
        credentials if credentials is not None else PinnedCredentialsUnavailable(),  # type: ignore[arg-type]
    )


__all__ = [
    "PinnedCredentialsUnavailable",
    "SkillReader",
    "item_credentials",
    "lease_pinned_bindings",
    "load_pinned_items",
    "run_item_runtimes",
]

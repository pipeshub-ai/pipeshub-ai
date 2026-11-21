"""How a harness run's MCP tools are pinned at admission and held at the claim (D-MCP-5, WP84-FU-1).

This build's runs are pinned to what each server lists for the run's credential owner at admission (``LIVE_MCP_PINNING``),
less the tools the description scanner flags, and the claim withholds a tool its server lists at another digest. An
edition's ``McpToolAdmission`` may pin an org's runs another way through ``run_pinning``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from app.agents.run_definition import AgentRunDefinition
    from app.harness.assembly.descriptor_pins import ToolDescriptorSource
    from app.harness.mcp_hub.manifests import ManifestConfig


@dataclass(frozen=True, slots=True)
class McpListing:
    """What the run's servers listed for its credential owner now: each answering server's tool digests, the servers
    that answered, and why each other one named nothing (``{instanceId, reason}``)."""

    live: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    answered: frozenset[str] = frozenset()
    failures: Sequence[Mapping[str, Any]] = ()




class McpRunPinning(Protocol):
    @property
    def holds_claim_to_admission_listing(self) -> bool:
        """The claim holds each MCP tool to the digest its admission listing pinned."""
        ...

    async def agent_definition(
        self,
        definition: AgentRunDefinition,
        base: ToolDescriptorSource,
        list_servers: Callable[[], Awaitable[McpListing]] | None,
    ) -> AgentRunDefinition:
        """A saved agent's definition with each granted tool's descriptor digest."""
        ...

    async def assistant_servers(
        self,
        servers: list[dict[str, Any]],
        base: ToolDescriptorSource,
        *,
        first_use: bool,
        list_servers: Callable[[], Awaitable[McpListing]] | None,
    ) -> tuple[list[dict[str, Any]], ToolDescriptorSource]:
        """The assistant's servers with the tools a pin can name, and the source its definition is pinned from."""
        ...


class LiveMcpPinning:
    @property
    def holds_claim_to_admission_listing(self) -> bool:
        return True

    async def agent_definition(
        self,
        definition: AgentRunDefinition,
        base: ToolDescriptorSource,
        list_servers: Callable[[], Awaitable[McpListing]] | None,
    ) -> AgentRunDefinition:
        from app.agents.run_definition import hold_flagged_tools, pin_tool_descriptors
        from app.harness.assembly.descriptor_pins import LiveMcpDescriptors

        if list_servers is None:
            return await pin_tool_descriptors(definition, base, mcp_approvals=False)
        listing = await list_servers()
        # A tool the description scanner flags is held back here, named, so the run is never pinned to it.
        definition = hold_flagged_tools(definition, listing.failures)
        return await pin_tool_descriptors(definition, LiveMcpDescriptors(base, listing.live), mcp_approvals=True)

    async def assistant_servers(
        self,
        servers: list[dict[str, Any]],
        base: ToolDescriptorSource,
        *,
        first_use: bool,
        list_servers: Callable[[], Awaitable[McpListing]] | None,
    ) -> tuple[list[dict[str, Any]], ToolDescriptorSource]:
        from app.agents.run_definition import assistant_mcp_servers
        from app.harness.assembly.descriptor_pins import LiveMcpDescriptors

        if first_use:
            # D-LAZY-2: each server granted whole; its worker lists and pins it at the run's first use of it.
            return [{**server, "tools": [], "atFirstUse": True} for server in servers], base
        listing = await list_servers() if list_servers is not None else McpListing()
        descriptors = LiveMcpDescriptors(base, listing.live)
        return await assistant_mcp_servers(servers, descriptors, failures=listing.failures), descriptors


LIVE_MCP_PINNING = LiveMcpPinning()


async def mcp_run_pinning(org_id: str, config_service: ManifestConfig, *, hub: bool) -> McpRunPinning:
    """The edition's pinning for the org's harness runs where the MCP hub governs them (``hub``); else the live
    listing's."""
    from app.harness.mcp_hub.tool_admission import mcp_tool_admission

    if not hub:
        return LIVE_MCP_PINNING
    return await mcp_tool_admission().run_pinning(org_id, config_service)


__all__ = ["LIVE_MCP_PINNING", "LiveMcpPinning", "McpListing", "McpRunPinning", "mcp_run_pinning"]

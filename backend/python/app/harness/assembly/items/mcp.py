"""MCP items: a server's pinned tools at the digests an admin approved, and its session closed with the load."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from app.harness.assembly.items.base import (
    ItemConnector,
    LiveTool,
    OpenedItem,
    PinnedItemRuntime,
    tool_name,
)
from app.harness.assembly.items.pins import MCP
from harness_core.mcp_hub.errors import ManifestUnavailable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from app.control_plane.contracts.generated.effective_set import EffectiveItem
    from app.control_plane.runtime.ports import CredentialBroker
    from app.control_plane.runtime.types import Closer, RunContext
    from app.harness.assembly.descriptor_pins import ToolDescriptorSource
    from app.harness.assembly.items.credentials import PinnedBindingLeases


class McpItemRuntime(PinnedItemRuntime):
    """Also checks the server's granted manifest when the run pinned one (``mcp:<instance>``, CPS PN-6)."""

    kind = MCP

    def __init__(self, connector: ItemConnector, server_pins: Mapping[str, str] | None = None) -> None:
        super().__init__(connector)
        # Instance id -> the granted-manifest digest the run pinned for it.
        self._server_pins = dict(server_pins or {})

    @override
    def item_changes(self, item: EffectiveItem, opened: OpenedItem) -> list[str]:
        instance = item.installation.root
        pinned = self._server_pins.get(instance)
        return [f"mcp:{instance}"] if pinned is not None and opened.set_digest != pinned else []


class McpConnector:
    """An MCP item's tools at the hub's approved digests, served by the run's MCP tools; closing drops its session.
    A server whose item names a binding leases it when it opens (CPD-10)."""

    def __init__(
        self,
        descriptors: ToolDescriptorSource,
        names: Mapping[str, str],
        close_session: Callable[[str], Awaitable[None]] | None = None,
        leases: PinnedBindingLeases | None = None,
    ) -> None:
        self._descriptors = descriptors
        self._names = names
        self._close_session = close_session
        self._leases = leases

    async def open(self, item: EffectiveItem, ctx: RunContext, credentials: CredentialBroker) -> OpenedItem:
        if self._leases is not None:
            await self._leases.lease(item, ctx, credentials)
        instance = item.installation.root
        close: Closer | None = None
        if self._close_session is not None:
            close_session = self._close_session

            async def close_instance() -> None:
                await close_session(instance)

            close = close_instance
        try:
            approved = await self._descriptors.approved_mcp_tools(instance)
        except ManifestUnavailable:
            return OpenedItem(live={}, close=close, unreadable=True)
        names = [tool_name(pinned.tool_id) for pinned in item.tools]
        live = {
            pinned.tool_id: LiveTool(
                approved.tools.get(tool_name(pinned.tool_id)) if approved is not None else None,
                handle=self._names.get(pinned.tool_id),
            )
            for pinned in item.tools
        }
        set_digest = approved.granted_digest(names) if approved is not None else None
        return OpenedItem(live=live, close=close, set_digest=set_digest)


__all__ = ["McpConnector", "McpItemRuntime"]

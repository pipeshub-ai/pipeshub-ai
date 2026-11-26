"""Toolset items: the pinned tools of a configured toolset instance, at the descriptor its code declares."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.harness.assembly.items.base import (
    LiveTool,
    OpenedItem,
    PinnedItemRuntime,
    tool_name,
)
from app.harness.assembly.items.pins import TOOLSET

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.control_plane.contracts.generated.effective_set import EffectiveItem
    from app.control_plane.runtime.ports import CredentialBroker
    from app.control_plane.runtime.types import RunContext
    from app.harness.assembly.descriptor_pins import ToolDescriptorSource
    from app.harness.assembly.items.credentials import PinnedBindingLeases


class ToolsetItemRuntime(PinnedItemRuntime):
    kind = TOOLSET


class RunToolsetConnector:
    """A toolset item's tools as the run's loader registered them (``PipesHubToolLoader``), digested as declared.

    Opening an instance opens no client: those are the loader's, and the run's teardown closes them. An
    instance whose item names a binding leases it here (CPD-10), so a credential the pin no longer holds is
    refused before the run starts.
    """

    def __init__(
        self,
        descriptors: ToolDescriptorSource,
        names: Mapping[str, str],
        leases: PinnedBindingLeases | None = None,
    ) -> None:
        self._descriptors = descriptors
        # Pinned tool id -> the registry name the run serves it under.
        self._names = names
        self._leases = leases

    async def open(self, item: EffectiveItem, ctx: RunContext, credentials: CredentialBroker) -> OpenedItem:
        if self._leases is not None:
            await self._leases.lease(item, ctx, credentials)
        live: dict[str, LiveTool] = {}
        for pinned in item.tools:
            toolset = pinned.tool_id.split("@", 1)[0]
            live[pinned.tool_id] = LiveTool(
                self._descriptors.toolset_tool(toolset, tool_name(pinned.tool_id)),
                handle=self._names.get(pinned.tool_id),
            )
        return OpenedItem(live=live)


__all__ = ["RunToolsetConnector", "ToolsetItemRuntime"]

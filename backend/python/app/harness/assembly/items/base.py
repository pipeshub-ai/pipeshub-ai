"""One way to turn pinned items into a run's tools, whatever their kind (03-interfaces §3.2, CPD-7).

``PinnedItemRuntime`` is the ``ItemRuntime`` every kind shares: it opens each ready item of its kind through
the kind's ``ItemConnector``, keeps exactly the pinned tools whose live descriptor digest is the pinned one,
and refuses the rest as ``CapabilityLost`` (a tool no longer there or changed: ``changed``; a store that
cannot answer: ``missing``, so the claim re-checks on the same pin). What it opened is closed when the load
is, or at once when the load is refused (R2 X-1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from app.control_plane.runtime.problems import problem
from app.control_plane.runtime.types import Closer, ItemLoad, LoadedTool
from harness_core.driver import CapabilityLost

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app.control_plane.contracts.generated.effective_set import (
        EffectiveItem,
        PinnedTool,
    )
    from app.control_plane.contracts.generated.problem import Problem
    from app.control_plane.runtime.ports import CredentialBroker
    from app.control_plane.runtime.types import LiveDescriptor, RunContext


@dataclass(frozen=True, slots=True)
class LiveTool:
    """A tool as the item serves it now: its descriptor digest (None: it has none to compare) and the handle."""

    digest: str | None
    handle: object = field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class OpenedItem:
    """One opened item: its live tools by tool id, what closes it, and whether its store could answer."""

    live: Mapping[str, LiveTool]
    close: Closer | None = field(default=None, repr=False)
    unreadable: bool = False
    # A digest over the item's pinned tools as approved now (an MCP server's granted manifest), if the kind has one.
    set_digest: str | None = None


class ItemConnector(Protocol):
    """How one kind reaches an item's live tools; the runtime decides what of it the run may use."""

    async def open(self, item: EffectiveItem, ctx: RunContext, credentials: CredentialBroker) -> OpenedItem: ...


def tool_name(tool_id: str) -> str:
    return tool_id.rsplit("/", 1)[-1]


class PinnedItemRuntime:
    """``ItemRuntime`` over an ``ItemConnector``: the pinned tools of ready items, at their pinned digests only."""

    kind: str

    def __init__(self, connector: ItemConnector) -> None:
        self._connector = connector

    def key(self, item: EffectiveItem, tool: PinnedTool) -> str:
        """How a refusal names a pinned tool (the claim's ``CapabilityLost`` keys)."""
        del item
        return f"tool:{tool.tool_id}"

    def item_changes(self, item: EffectiveItem, opened: OpenedItem) -> list[str]:
        """What else of the item moved since the pin, beyond its tools (none for most kinds)."""
        del item, opened
        return []

    async def load(self, items: Sequence[EffectiveItem], ctx: RunContext, credentials: CredentialBroker) -> ItemLoad:
        ready = [i for i in items if i.state == "ready" and i.kind is not None and i.kind.root == self.kind]
        opened: list[OpenedItem] = []
        tools: list[LoadedTool] = []
        missing: list[str] = []
        changed: list[str] = []
        try:
            for item in ready:
                live = await self._connector.open(item, ctx, credentials)
                opened.append(live)
                for pinned in item.tools:
                    key, digest = self.key(item, pinned), pinned.descriptor_digest.root
                    if live.unreadable:
                        missing.append(key)
                        continue
                    found = live.live.get(pinned.tool_id)
                    if found is None or found.digest != digest:
                        changed.append(key)
                        continue
                    tools.append(LoadedTool(pinned.tool_id, digest, handle=found.handle))
                if not live.unreadable:
                    changed.extend(self.item_changes(item, live))
            if missing or changed:
                raise CapabilityLost(missing=missing, changed=changed)
        except BaseException:
            await ItemLoad(closers=tuple(o.close for o in opened if o.close is not None)).close()
            raise
        return ItemLoad(tools=tuple(tools), closers=tuple(o.close for o in opened if o.close is not None))

    def verify(self, item: EffectiveItem, live: LiveDescriptor) -> Problem | None:
        pinned = {tool.tool_id: tool.descriptor_digest.root for tool in item.tools}
        if pinned.get(live.tool_id) == live.digest:
            return None
        return problem(
            "CP_DESCRIPTOR_CHANGED",
            "This tool changed since the run started; an admin must approve the new version.",
            installation=item.installation.root,
        )


__all__ = ["ItemConnector", "LiveTool", "OpenedItem", "PinnedItemRuntime", "tool_name"]

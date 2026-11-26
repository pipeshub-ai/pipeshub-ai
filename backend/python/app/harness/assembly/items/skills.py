"""Skill items: a pinned skill is loaded only at the bundle digest admission pinned (SKL-G6), on every executor."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from app.harness.assembly.items.base import LiveTool, OpenedItem, PinnedItemRuntime
from app.harness.assembly.items.pins import SKILL

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.control_plane.contracts.generated.effective_set import (
        EffectiveItem,
        PinnedTool,
    )
    from app.control_plane.runtime.ports import CredentialBroker
    from app.control_plane.runtime.types import RunContext


class SkillsItemRuntime(PinnedItemRuntime):
    kind = SKILL

    @override
    def key(self, item: EffectiveItem, tool: PinnedTool) -> str:
        return f"skill:{tool.tool_id.rsplit('/', 1)[-1]}"


class GraphSkillConnector:
    """A pinned skill is live while the run may still use it and some stored content matches its pin (FB-11):
    an edit since admission keeps it (the run reads the admitted content from history), a deletion, disabling or
    unassignment does not."""

    def __init__(self, holds: Callable[[str, str, str], Awaitable[bool]]) -> None:
        self._holds = holds

    async def open(self, item: EffectiveItem, ctx: RunContext, credentials: CredentialBroker) -> OpenedItem:
        del credentials
        live: dict[str, LiveTool] = {}
        for pinned in item.tools:
            name, digest = pinned.tool_id.rsplit("/", 1)[-1], pinned.descriptor_digest.root
            if await self._holds(ctx.org_id, name, digest):
                live[pinned.tool_id] = LiveTool(digest, handle=name)
        return OpenedItem(live=live)


__all__ = ["GraphSkillConnector", "SkillsItemRuntime"]

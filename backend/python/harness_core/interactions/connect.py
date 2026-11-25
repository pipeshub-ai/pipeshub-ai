"""A call that needs an item its run's owner has not connected (G-12, GA-07).

Instead of failing the call with "connect it, then ask again", the run waits
for the owner as it waits on an approval (``ix.connect.<invocationId>``,
kind ``connect``): they connect the item and say so (``approved``), and the
call runs once on the attempt that follows, which assembles the run's tools
again and so sees the new credential. Nothing of the call is journaled before
then, so it runs exactly once. Declined or expired, the call is refused with
the next step in words.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from pydantic import JsonValue

    from harness_core.journal.types import JournalEvent
    from harness_core.kernel.core.messages import ToolCall

ItemKind = Literal["toolset", "mcp"]


@dataclass(frozen=True)
class ConnectNeed:
    """The item a call waits on: as the person names it, its kind and its instance."""

    item: str
    item_kind: ItemKind
    instance_id: str
    # ``sign_in_rejected``: it was connected, and its vendor rejected the saved sign-in during the run (WP-64).
    reason: Literal["sign_in_rejected"] | None = None

    def journaled(self) -> dict[str, JsonValue]:
        journaled: dict[str, JsonValue] = {"item": self.item[:256], "itemKind": self.item_kind, "instanceId": self.instance_id}
        if self.reason is not None:
            journaled["reason"] = self.reason
        return journaled


@runtime_checkable
class ConnectGate(Protocol):
    """What the attempt's tools say of a call: the item it needs that is not connected, or ``None``."""

    def need(self, call: ToolCall) -> ConnectNeed | None: ...

    def declined(self, call: ToolCall) -> str:
        """The model's text when the owner did not connect it: the call was not made, and what to tell them."""
        ...


def run_tree(events: Iterable[JournalEvent], run_id: str) -> set[str]:
    """``run_id``'s run tree in the session's journal: its root (up through ``child.spawned``) and every run below."""
    parent_of: dict[str, str] = {}
    for event in events:
        if event.type == "child.spawned" and event.run_id is not None:
            child = event.data.get("childRunId")
            if isinstance(child, str):
                parent_of[child] = event.run_id
    root = run_id
    seen = {root}
    while root in parent_of and parent_of[root] not in seen:
        root = parent_of[root]
        seen.add(root)
    tree = {root}
    grew = True
    while grew:
        below = {child for child, parent in parent_of.items() if parent in tree} - tree
        tree |= below
        grew = bool(below)
    return tree


def skipped_connects(events: Sequence[JournalEvent], run_id: str) -> set[str]:
    """The items (instance ids) the person chose not to connect, or did not in time, anywhere in ``run_id``'s run
    tree (WP-64): one connect card per item per task, so neither a later call nor the parent asks again."""
    tree = run_tree(events, run_id)
    asked: dict[str, str] = {}
    skipped: set[str] = set()
    for event in events:
        if event.run_id not in tree:
            continue
        if event.type == "interaction.requested" and event.data.get("kind") == "connect":
            connect = event.data.get("connect")
            ix = event.data.get("interactionId")
            if isinstance(connect, dict) and isinstance(ix, str) and isinstance(connect.get("instanceId"), str):
                asked[ix] = str(connect["instanceId"])
        elif event.type == "interaction.resolved":
            ix = event.data.get("interactionId")
            if isinstance(ix, str) and ix in asked and event.data.get("decision") != "approved":
                skipped.add(asked[ix])
    return skipped


__all__ = ["ConnectGate", "ConnectNeed", "ItemKind", "run_tree", "skipped_connects"]

"""Governed memory for PipesHub runs (P7.3): Node's memory routes as a ``MemorySource``,
and what the run journals about what it was given.

Node decided at admission, with the actor's own credential, which memories the
run may be given and which scopes it may propose to (``x.pipeshub.memory``);
the worker only asks for the pinned memories' content and posts proposals, with
its attempt token. Content never reaches a log line.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_MODELS
from app.harness.journal_node.client import error_code
from harness_core.contracts import validate_contract
from harness_core.journal.types import JournalUnavailable, NewEvent
from harness_core.memories.recall import MEMORY_SLOT
from harness_core.memories.types import (
    MEMORY_KINDS,
    MEMORY_SCOPES,
    MemoryProposal,
    MemoryRefused,
    MemoryUnavailable,
    ProposedMemory,
    RecalledMemory,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    import httpx
    from pydantic import BaseModel, JsonValue

    from app.harness.journal_node.client import AttemptRoute

LOGGER = "app.harness.memories"
RECALLED_EVENT = "x.pipeshub.memory.recalled"
_PATH = "/memories"


def _recalled(item: Mapping[str, JsonValue]) -> RecalledMemory:
    scope, kind = item.get("scope"), item.get("kind")
    label = item.get("label")
    if scope not in MEMORY_SCOPES or kind not in MEMORY_KINDS or not isinstance(label, dict):
        raise MemoryUnavailable("invalid memory from Node")
    return RecalledMemory(
        memory_id=str(item["memoryId"]),
        scope=scope,
        kind=kind,
        content=str(item["content"]),
        label=cast("dict[str, JsonValue]", label),
    )


class NodeMemorySource:
    """One attempt's memory: the run's pinned memories and its proposals, through Node."""

    def __init__(self, route: AttemptRoute) -> None:
        self._route = route
        self._log = route.log(LOGGER)

    async def recall(self) -> Sequence[RecalledMemory]:
        response = await self._call(lambda: self._route.get(_PATH, operation="memory.recall"))
        if response.status_code != 200:
            raise self._refusal(response, "memory.recall")
        try:
            items = cast("list[dict[str, JsonValue]]", response.json()["memories"])
            return [_recalled(item) for item in items]
        except (KeyError, TypeError, ValueError) as exc:
            self._log.warning("memory.unavailable", reason=MemoryUnavailable.reason_code, operation="memory.recall")
            raise MemoryUnavailable("invalid recall answer from Node") from exc

    async def propose(self, proposal: MemoryProposal) -> ProposedMemory:
        body: dict[str, JsonValue] = {"content": proposal.content, "kind": proposal.kind, "scope": proposal.scope}
        if proposal.replaces is not None:
            body["replaces"] = proposal.replaces
        response = await self._call(lambda: self._route.post(_PATH, body=body, operation="memory.propose"))
        if response.status_code != 200:
            raise self._refusal(response, "memory.propose")
        try:
            data = response.json()
            proposed = ProposedMemory(str(data["memoryId"]), str(data["status"]), bool(data["created"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise MemoryUnavailable("invalid proposal answer from Node") from exc
        self._log.info(
            "memory.proposed",
            memoryId=proposed.memory_id,
            scope=proposal.scope,
            kind=proposal.kind,
            created=proposed.created,
            replaces=proposal.replaces,
        )
        return proposed

    async def _call(self, send: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response:
        try:
            return await send()
        except JournalUnavailable as exc:
            raise MemoryUnavailable("Node unreachable") from exc

    def _refusal(self, response: httpx.Response, operation: str) -> Exception:
        code = error_code(response)
        status = response.status_code
        if code.startswith("MEMORY_"):
            self._log.warning("memory.refused", reason=MemoryRefused.reason_code, detail=code, operation=operation)
            return MemoryRefused(code)
        self._log.warning(
            "memory.unavailable", reason=MemoryUnavailable.reason_code, status=status, detail=code or None, operation=operation
        )
        return MemoryUnavailable(f"memory route answered {status}")


def recalled_data(slots: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    """``x.pipeshub.memory.recalled`` data from the pre-run slot: what the run was given (ids, scope, kind,
    label; never the content), for the timeline and the run's own-work label. None: it was given nothing."""
    slot = slots.get(MEMORY_SLOT)
    if not isinstance(slot, dict) or not slot.get("entries"):
        return None
    return {
        "entries": slot["entries"],
        "chars": slot.get("chars", 0),
        "truncated": bool(slot.get("truncated", False)),
    }


def recalled_event_for(memories: Sequence[RecalledMemory], chars: int, *, truncated: bool) -> NewEvent | None:
    """The event for a run given memories outside the pre-run (an engine run's first turn), schema-checked."""
    if not memories:
        return None
    entries: list[JsonValue] = [
        {"memoryId": m.memory_id, "scope": m.scope, "kind": m.kind, "label": dict(m.label)} for m in memories
    ]
    data: dict[str, JsonValue] = {"entries": entries, "chars": chars, "truncated": truncated}
    model = EVENT_MODELS[RECALLED_EVENT]
    _ = validate_contract(cast("type[BaseModel]", model.model_fields["data"].annotation), data)
    return NewEvent(RECALLED_EVENT, data, critical=False)


def source_free(memories: Sequence[RecalledMemory]) -> list[RecalledMemory]:
    """The memories derived from no record: the only ones an engine run is given (its files would carry the
    rest to viewers its label cannot name, P2.5a FW-4)."""
    return [m for m in memories if m.label.get("complete") is True and m.label.get("sources") == []]


__all__ = ["RECALLED_EVENT", "NodeMemorySource", "recalled_data", "recalled_event_for", "source_free"]

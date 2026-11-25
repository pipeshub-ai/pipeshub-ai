"""Recalled memory in a durable run (P7.3): fetched once, journaled, given to every turn.

``MemoryRecallStep`` is a pre-run step: the first attempt asks the run's
``MemorySource`` and journals what it rendered as the slot ``pre_run.memory``;
a later attempt reads the slot back and never asks again, so every attempt
sends the model the same text. ``MemoryContextShaper`` puts that text right
before the run's input on each turn, as an injected user message (data, not
the system prompt), so the earlier history keeps its cache-stable prefix.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX
from harness_core.memories.render import (
    MemoryRenderLimits,
    RenderedMemories,
    render_memories,
)
from harness_core.memories.types import MemoryUnavailable

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.context.types import HostBands
    from harness_core.kernel.durable.context_compiler import ContextHost
    from harness_core.kernel.durable.ports import ModelCall, TurnShaper
    from harness_core.kernel.fold import RunState
    from harness_core.logs import HarnessLog
    from harness_core.memories.ports import MemorySource

MEMORY_STEP = "memory"
MEMORY_SLOT = PRE_RUN_SLOT_PREFIX + MEMORY_STEP


def memory_slot(rendered: RenderedMemories, *, unavailable: bool = False) -> dict[str, JsonValue]:
    """What the pre-run commit journals: the text and, per memory shown, its id, scope, kind and label."""
    entries: list[JsonValue] = [
        {"memoryId": m.memory_id, "scope": m.scope, "kind": m.kind, "label": dict(m.label)} for m in rendered.shown
    ]
    slot: dict[str, JsonValue] = {
        "text": rendered.text,
        "entries": entries,
        "chars": rendered.chars,
        "truncated": rendered.truncated,
    }
    if unavailable:
        slot["unavailable"] = True
    return slot


def memory_text(slots: Mapping[str, JsonValue]) -> str:
    """The rendered text a run journaled; empty when it recalled nothing."""
    slot = slots.get(MEMORY_SLOT)
    if not isinstance(slot, dict):
        return ""
    text = slot.get("text")
    return text if isinstance(text, str) else ""


class MemoryRecallStep:
    """Pre-run step ``memory``: recall, render, journal."""

    key = MEMORY_STEP

    def __init__(self, source: MemorySource, *, limits: MemoryRenderLimits | None = None, log: HarnessLog) -> None:
        self._source = source
        self._limits = limits
        self._log = log

    async def run(self, input_text: str) -> JsonValue:
        try:
            recalled = await self._source.recall()
        except MemoryUnavailable as exc:
            # A run without its memories is still the user's run: it goes on, and says so.
            self._log.warning("memory.recall_unavailable", reason=MemoryUnavailable.reason_code, errorType=type(exc).__name__)
            return memory_slot(render_memories(()), unavailable=True)
        rendered = render_memories(recalled, self._limits)
        self._log.info(
            "memory.recalled",
            pinned=len(recalled),
            shown=len(rendered.shown),
            chars=rendered.chars,
            truncated=rendered.truncated,
        )
        return memory_slot(rendered)


class MemoryContextShaper:
    """Wraps the product's turn shaper; adds the recalled text before the run's input."""

    def __init__(self, inner: TurnShaper) -> None:
        self._inner = inner

    async def shape(self, call: ModelCall, turn: int, state: RunState) -> ModelCall:
        shaped = await self._inner.shape(call, turn, state)
        text = memory_text(shaped.slots)
        if not text:
            return shaped
        messages = list(shaped.messages)
        # Without a known input position the notes go first, never between a tool call and its result.
        at = shaped.input_index if shaped.input_index is not None else 0
        messages.insert(at, UserMessage(content=text, injected=True))
        index = at + 1 if shaped.input_index is not None else None
        return replace(shaped, messages=messages, input_index=index)

    async def enter(self, turn: int, state: RunState) -> None:
        await self._inner.enter(turn, state)

    def __getattr__(self, name: str) -> object:
        return cast("object", getattr(self._inner, name))


class MemoryContextHost:
    """With the context compiler on (P4): the recalled text is the first part of the volatile tail.

    A run's memories differ from the session's earlier runs', so in the pinned band
    (a system block ahead of the history) they would break the cached prefix at every
    run; the tail comes after the history and is never cached. It is still a function
    of the journal (the slot), so every turn and every attempt sends the same text.
    """

    def __init__(self, inner: ContextHost) -> None:
        self._inner = inner

    async def bands(self, call: ModelCall, turn: int, state: RunState) -> HostBands:
        bands = await self._inner.bands(call, turn, state)
        text = memory_text({**state.slots, **call.slots})
        if not text:
            return bands
        return replace(bands, tail=(text, *bands.tail))

    def __getattr__(self, name: str) -> object:
        return cast("object", getattr(self._inner, name))


def with_memory(instructions: str, rendered: RenderedMemories) -> str:
    """An engine's instructions with the recalled text appended: input text, never a file it can rewrite."""
    if not rendered.text:
        return instructions
    return f"{instructions}\n\n{rendered.text}" if instructions else rendered.text


__all__ = [
    "MEMORY_SLOT",
    "MEMORY_STEP",
    "MemoryContextHost",
    "MemoryContextShaper",
    "MemoryRecallStep",
    "memory_slot",
    "memory_text",
    "with_memory",
]

"""A compaction as the journal records it for the chat, whichever executor compacted.

The person's ``/compact [focus]`` (SLASH2 C2): the run that takes the request compacts before its first turn and
journals one ``x.pipeshub.context.compacted``: the durable loop after its ``context.compacted``, an engine run with
the turn that followed its engine's own compaction. A compaction nobody asked for (LC2FIX-3): the loop's ladder when
the provider counted a request past its threshold, or an engine's own auto-compaction mid-turn, journaled with
``trigger`` ``auto`` when the claim's journal accepts it (``CONTEXT_COMPACTED_AUTO``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from harness_core.journal.types import NewEvent

if TYPE_CHECKING:
    from pydantic import JsonValue

CONTEXT_COMPACTED_EVENT = "x.pipeshub.context.compacted"
# The loop's record that the request was honoured, journaled with the event: a later attempt does not compact again.
USER_COMPACT_SLOT = "context.userCompact"
# Everything before the last response is summarised: the reply the person is answering stays word for word.
USER_COMPACT_KEEP_TURNS = 1
FOCUS_MAX_CHARS = 500

CompactOutcome = Literal["compacted", "nothing_to_compact", "unsupported", "failed"]
CompactTrigger = Literal["user", "auto"]


@dataclass(frozen=True, slots=True)
class CompactReport:
    """What a compaction did; token counts are estimates, ``None`` when the executor does not say."""

    outcome: CompactOutcome
    before_tokens: int | None = None
    after_tokens: int | None = None
    user_messages_kept: bool = False


def compacted_event(
    executor: str, report: CompactReport, focus: str | None, *, trigger: CompactTrigger = "user"
) -> NewEvent:
    data: dict[str, JsonValue] = {
        "trigger": trigger,
        "executor": executor,
        "outcome": report.outcome,
        "beforeTokens": report.before_tokens,
        "afterTokens": report.after_tokens,
        "userMessagesKept": report.user_messages_kept,
    }
    if focus:
        data["focus"] = focus[:FOCUS_MAX_CHARS]
    return NewEvent(CONTEXT_COMPACTED_EVENT, data, critical=False)


__all__ = [
    "CONTEXT_COMPACTED_EVENT",
    "FOCUS_MAX_CHARS",
    "USER_COMPACT_KEEP_TURNS",
    "USER_COMPACT_SLOT",
    "CompactOutcome",
    "CompactReport",
    "CompactTrigger",
    "compacted_event",
]

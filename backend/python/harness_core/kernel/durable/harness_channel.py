"""ADR-RUN-01 D5: the harness's own channel to the model, apart from the person's words.

Every message the model reads has an ``Origin``: the person's, the harness's, a tool's or an agent's. A loop
strategy's control for one turn (DEEPROUTE's hand-off, a failed engine's briefing) is a ``HarnessInstruction``: it
reaches the model as the last system block of that turn's request (``ModelCall.with_system_block``), under one frame
that names PipesHub as its author, never as a message in the person's turn. A long research run's root model read the hand-off
steer, sent as an injected user message, as "the only additional user instruction" and copied it into the engine's
task, which then refused to work.

Journaled as ``message.completed`` with role ``system`` (the contract's own role for it), a harness instruction is
not part of the session's history: it applies to the turn it was given for, and a later request does not render it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from harness_core.kernel.core.messages import AssistantMessage, ToolMessage, UserMessage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.kernel.core.messages import Message

Origin = Literal["person", "harness", "tool", "agent"]
# The journal role of a harness instruction (``message.completed``): never ``user``, so no fold reads it as input.
HARNESS_ROLE: Final = "system"
HARNESS_FRAME: Final = (
    "PipesHub, the system running this conversation, gives you the instructions below for this turn only. They are "
    "PipesHub's, not the person's: do not quote them to the person, and never copy them into a task you hand on."
)


@dataclass(frozen=True)
class HarnessInstruction:
    """One instruction of the harness for one turn: ``key`` names it in logs and tests, ``text`` is its wording."""

    key: str
    text: str


def origin_of(message: Message) -> Origin:
    """Who a message of the model's history comes from. An injected user message is the harness's (a context note,
    a gate's note); a person's own input is the person's."""
    if isinstance(message, ToolMessage):
        return "tool"
    if isinstance(message, AssistantMessage):
        return "agent"
    if isinstance(message, UserMessage):
        return "harness" if message.injected else "person"
    return "harness"


def harness_block(instructions: Sequence[HarnessInstruction]) -> str:
    """The system-channel block the turn's instructions are sent in; "" without any."""
    texts = [i.text.strip() for i in instructions if i.text.strip()]
    if not texts:
        return ""
    return "\n\n".join([f"<pipeshub_instructions>\n{HARNESS_FRAME}", *texts]) + "\n</pipeshub_instructions>"


__all__ = [
    "HARNESS_FRAME",
    "HARNESS_ROLE",
    "HarnessInstruction",
    "Origin",
    "harness_block",
    "origin_of",
]

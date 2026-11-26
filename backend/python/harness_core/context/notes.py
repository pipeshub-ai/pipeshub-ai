"""The host's notes (P4.11): context that holds until it changes, journaled as history when it is new.

A provider's prompt cache reuses a prefix only where an earlier request ended (P4.11's simulator, checked against
P4.10's sample). A volatile message after the history, which each next request removes, therefore leaves nothing
reusable but the system and tools. A note instead is journaled once, where it first applies (before the run's input
when the run starts, after the history when it appears mid-run), and every later request repeats its bytes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.kernel.core.messages import TextPart, UserMessage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

    from harness_core.kernel.core.messages import Message

# Loop slot holding the note texts this run journaled, in order.
NOTES_SLOT = "context.notes"
# Notes ride in a user-role message only because the history has no other role for them; they are the host's words.
NOTE_HEADER = "Context from the system, not a message from the user:"


def note_message(texts: Sequence[str]) -> str:
    """The texts as one note; empty when there are none."""
    joined = "\n\n".join(text for text in texts if text)
    return f"{NOTE_HEADER}\n\n{joined}" if joined else ""


def journaled_notes(value: JsonValue | None) -> tuple[str, ...]:
    return tuple(str(text) for text in value) if isinstance(value, list) else ()


def held(history: Sequence[Message], text: str) -> bool:
    """Whether a note carrying ``text`` is in ``history`` (a compaction may have summarised it away)."""
    return any(isinstance(m, UserMessage) and m.injected and text in _text(m) for m in history)


def new_notes(notes: Sequence[str], journaled: Sequence[str]) -> tuple[str, ...]:
    """The host's notes this run has not journaled yet, in the host's order."""
    seen = set(journaled)
    return tuple(dict.fromkeys(text for text in notes if text and text not in seen))


def _text(message: Message) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(p.text for p in content if isinstance(p, TextPart))


__all__ = ["NOTES_SLOT", "NOTE_HEADER", "held", "journaled_notes", "new_notes", "note_message"]

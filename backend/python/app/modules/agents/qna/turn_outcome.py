"""How an earlier turn that ended without a full answer is replayed to a model.

Node sends a turn that failed before an answer as a `bot_response` with `status: "failed"`, and
one the person stopped with `status: "stopped"`. Replayed without a marker, the failed question
reads as still open: the model answers it instead of the current one, and providers that merge
consecutive user turns (Anthropic, Gemini) put both questions in one turn.
"""

from __future__ import annotations

FAILED = "failed"
STOPPED = "stopped"

FAILED_ANSWER = "(This turn failed before an answer.)"
STOPPED_ANSWER = "(Stopped by the user.)"
# For payloads that carry no status (a Node that still drops error rows): two questions in a row
# mean the first got no answer, but not why.
UNANSWERED_ANSWER = "(No answer was recorded for this turn.)"

_MARKERS = {FAILED: FAILED_ANSWER, STOPPED: STOPPED_ANSWER}


def replayed_answer(content: str, status: str | None) -> str:
    """The answer text a model sees for an earlier turn: what was said, then how it ended."""
    marker = _MARKERS.get(status or "")
    if marker is None:
        return content
    return f"{content}\n\n{marker}" if content else marker


__all__ = [
    "FAILED",
    "FAILED_ANSWER",
    "STOPPED",
    "STOPPED_ANSWER",
    "UNANSWERED_ANSWER",
    "replayed_answer",
]

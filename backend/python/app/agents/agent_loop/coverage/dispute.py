"""The dispute checker: a small-model read of the question and the draft, without
the sources, for a draft that disputes a detail the question states or a reading
the source plainly supports. It only reports the quotes; it never rewrites.

The prompt names six kinds of failure so the model can sort what it sees, but
only the dispute kind is acted on: rewriting for the others changed nothing or
broke an answer that was right.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agents.agent_loop.coverage.llm import complete_json
from app.agents.agent_loop.coverage.opening import plain_text

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import BaseMessage

_MAX_DRAFT_CHARS = 9000
_DISPUTE_TYPES = frozenset({"b", "disputes_stated_fact"})

DISPUTE_SYSTEM = (
    "You review draft answers from an enterprise knowledge-base assistant before they are sent. "
    "Reply with JSON only."
)
DISPUTE_USER = """Check the draft answer against the user's question. You do not see the sources, so do not decide whether facts are true; decide only whether the answer, as written, fails the question in one of these ways:

A. opening_concedes: the first one or two sentences lead with what could not be found, that there is "no single answer", that the documents conflict, that it cannot tell which item is meant, or with a status/delivery caveat, instead of leading with the answer it then gives.
B. disputes_stated_fact: it questions or corrects a detail the question states or a reading the source plainly supports (e.g. "the log calls it paused, not cancelled", "not a verbatim quote from him", "does not say whether 2 hours is per batch or overall", "no record that it was approved") when the caveat does not change the answer.
C. rival_value_mixed: it presents a second, conflicting value, list or process from another document alongside or instead of the one the question is about, leaving the reader with two answers.
D. anchor_unlinked: the question names specific symptoms, conditions or terms (an error, a metric, a scope) and the answer never ties its answer to them.
E. missing_part: the question has several explicit parts or asks for a list/set, and the answer leaves an explicit part unanswered or gives one item where several were asked.
F. internal_error: the answer contradicts itself (a count that does not match its own list, an attribution it then reverses) or hedges its central claim as a guess.

Flag only clear cases; long, detailed answers with extra context are fine. Question:
{q}

Draft answer:
{a}

Reply as {{"violations": [{{"type": "<A-F code name>", "quote": "<short quote from the answer>", "fix": "<one sentence>"}}], "verdict": "send" or "revise"}}"""


async def disputed_quotes(
    question: str,
    draft: str,
    llm: "BaseChatModel | None",
    *,
    timeout: float,
    on_usage: "Callable[[BaseMessage], None] | None" = None,
) -> list[str]:
    """The draft's quotes the checker reads as disputing a stated detail; none
    on a timeout, an error or an unreadable reply."""
    reply = await complete_json(
        llm, DISPUTE_SYSTEM, DISPUTE_USER.format(q=question, a=plain_text(draft)[:_MAX_DRAFT_CHARS]),
        timeout=timeout, purpose="dispute check", on_usage=on_usage,
    )
    violations = (reply or {}).get("violations")
    if not isinstance(violations, list):
        return []
    quotes = []
    for violation in violations:
        if not isinstance(violation, dict):
            continue
        kind = str(violation.get("type") or "").strip().lower()
        quote = " ".join(str(violation.get("quote") or "").split())
        if kind in _DISPUTE_TYPES and quote and quote not in quotes:
            quotes.append(quote)
    return quotes


__all__ = ["DISPUTE_SYSTEM", "DISPUTE_USER", "disputed_quotes"]

"""A loop run's settled outcome when a limit cut it short (ADR-RUN-01 D1, WP-100).

An engine run settles its outcome in its kernel; a loop run that hands its work to an engine presents the child's
answer and relays the child's outcome. A loop run cut short on its own answers either with what its model wrote in
the answer-only turn, or, when no model call could write one, with the presenter's no-model answer
(``cut_short_fallback``). Both settle as ``Partial(reason)``, so the chat words the outcome in the reader's language,
as it does every other run's: the no-model answer's prose is what the run did (``OutcomePresenter.gathered``: the
files it saved, what it gathered, the pages it read), an answer the model wrote keeps its words, and either carries
the presenter's closing, the English a surface with no UI appends. A completed loop run settles nothing: its answer
is its words.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.engines.outcome.ledger import OUTCOME_SETTLED
from harness_core.engines.outcome.outcome import Delivered, Partial, settled_event
from harness_core.engines.outcome.presenter import PRESENTER, Framed

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.engines.outcome.deliverable import Deliverable
    from harness_core.engines.outcome.ledger import OutcomeLedger
    from harness_core.journal.types import NewEvent
    from harness_core.kernel.durable.ports import RunEnding


@dataclass(frozen=True, slots=True)
class LoopSettled:
    """The cut-short loop answer framed (the prose the chat shows, the closing it words itself) and the event that
    journals its outcome."""

    framed: Framed
    event: NewEvent


def loop_settled(
    ending: RunEnding, run_id: str, ledger: OutcomeLedger, deliverable: Deliverable, *, saved: Sequence[str] = ()
) -> LoopSettled | None:
    """``ending``'s settled outcome when a limit cut the loop run short; ``None`` for any other ending, and for a
    run that already journaled one (a hand-off that relayed its child's). ``ledger``: what the run gathered (the
    session's provenance ledger's sources). The run's conversation keeps every tool result it gathered, so a
    "continue" carries on from them: the work counts as kept when it called a tool. ``saved``: the names of the
    files the run saved; a no-model answer names them and the pages it read (R36COVER-FU-5), as a job's cut does."""
    reason = ending.truncated_by
    if ending.outcome != "answered" or reason is None or ending.answer is None:
        return None
    if any(e.type == OUTCOME_SETTLED and e.run_id == run_id for e in ending.events):
        return None
    outcome = Partial(reason, Delivered(None, ledger.sources, ledger.notes, kept=bool(ending.tool_calls)))
    answer = ending.answer.strip()
    if PRESENTER.is_no_answer(answer) and _owes_file(answer):
        framed = Framed("", answer)
    elif PRESENTER.is_no_answer(answer):
        # R50JFIX-1: what it did is the answer's prose, so the web chat, the Slack bot and an MCP client all show the
        # files it saved and what it gathered; the chat words the stop and the action from the outcome, as for any
        # cut-short answer.
        done = PRESENTER.gathered([call.name for call in ending.tool_calls], saved=saved, sources=ledger.sources)
        framed = PRESENTER.frame(outcome, body=done)
    else:
        framed = PRESENTER.frame(outcome, body=answer)
    return LoopSettled(framed, settled_event(outcome, deliverable, closing=framed.closing, opening=framed.opening))


def _owes_file(answer: str) -> bool:
    """A no-model answer that owes its requested file (WP-70) keeps its own words: it already names the file."""
    first = answer.lstrip().split("\n", 1)[0]
    return first.startswith("**")


__all__ = ["LoopSettled", "loop_settled"]

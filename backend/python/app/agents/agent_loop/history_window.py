"""How much of the conversation so far a run replays, and in what shape.

Node sends every saved turn on every request, and replaying all of it made a
long chat (or one large attachment on an early turn) fill the model's window
before the current request had any room: the run then failed with
`ContextBudgetExceeded`, or compaction spent the budget on old turns.

The window is planned per exchange: one question the person asked and
everything up to their next question (the answer, its tool calls, card and
approval answers). Newest exchanges are kept whole. Once the next one does not
fit, it and every older exchange are reduced to the question, the opening of
the answer and the names of the tools it used. Exchanges that do not fit even
reduced are left out, and the replay says how many. The plan is pure and
depends only on its inputs, so a given history replays the same way on every
request.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

# Replayed history (docs/context-engineering.md, §4) may use at most this
# share of the message budget, so the current request and the run's own tool
# results always have the rest.
HISTORY_SHARE = 0.30
# Of the history share, what earlier turns' attachments may use. The rest keeps
# the conversation itself readable when an early turn attached a large file.
EARLIER_ATTACHMENT_SHARE = 0.5
# What the current turn's attachments may use of the message budget. With the
# history share, this leaves 30% for the tool results the run is about to read.
CURRENT_ATTACHMENT_SHARE = 0.40

REDUCED_QUESTION_CHARS = 1_000
REDUCED_ANSWER_CHARS = 600

# `core/tokens.py`'s estimate, applied to the rows before they become messages.
_CHARS_PER_TOKEN = 4
_MESSAGE_OVERHEAD_TOKENS = 4
# Room for what seeding adds to an exchange: a failed/stopped marker on the
# answer, or a placeholder answer for a question that got none.
_EXCHANGE_ALLOWANCE_TOKENS = 2 * _MESSAGE_OVERHEAD_TOKENS + 12
# A replayed artifact result is a pointer (id, tool, args, summary, hint), never
# the payload; this bounds its length.
_ARTIFACT_POINTER_CHARS = 800

_CUT = " […]"

Row = dict[str, Any]
Exchange = Sequence[Row]


def share_tokens(message_budget_tokens: int, share: float) -> int:
    return max(int(message_budget_tokens * share), 0)


def current_attachment_chars(message_budget_tokens: int, request_tokens: int = 0) -> int:
    """Characters the current turn's attachments may render into: their share
    of the message budget, less the person's words, which open the same message."""
    tokens = share_tokens(message_budget_tokens, CURRENT_ATTACHMENT_SHARE) - request_tokens
    return max(tokens, 0) * _CHARS_PER_TOKEN


def earlier_attachment_chars(history_tokens: int) -> int:
    """Characters earlier turns' attachments may render into, of the history share."""
    return share_tokens(history_tokens, EARLIER_ATTACHMENT_SHARE) * _CHARS_PER_TOKEN


def split_exchanges(rows: Sequence[Row], opens_question: Callable[[Row], bool]) -> list[list[Row]]:
    """`rows` (`previousConversations`, oldest first) grouped into exchanges.

    A row that answers a card or an approval belongs to the exchange it
    answers, so reducing or leaving out an exchange never separates them.
    """
    exchanges: list[list[Row]] = []
    for row in rows:
        if not exchanges or opens_question(row):
            exchanges.append([row])
        else:
            exchanges[-1].append(row)
    return exchanges


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + _CUT


def _tool_names(row: Row) -> list[str]:
    names: list[str] = []
    for entry in row.get("tool_results") or []:
        if isinstance(entry, dict):
            name = str(entry.get("tool_name") or "")
            if name and name not in names:
                names.append(name)
    return names


def reduce_exchange(exchange: Exchange) -> list[Row]:
    """`exchange` as a short record of what was asked and answered.

    Tool calls become their names in the answer: a call replayed without its
    result is not a valid tool turn, and the result is what is being dropped.
    Attachments stay on the question so the replay can name them.
    """
    reduced: list[Row] = []
    for row in exchange:
        content = str(row.get("content") or "").strip()
        if row.get("role") == "user_query":
            reduced.append({**row, "content": _cut(content, REDUCED_QUESTION_CHARS)})
        elif row.get("role") == "bot_response":
            answer = _cut(content, REDUCED_ANSWER_CHARS)
            names = _tool_names(row)
            if names:
                answer = f"{answer}\n\n(Tools used: {', '.join(names)}.)" if answer else f"(Tools used: {', '.join(names)}.)"
            kept = {key: value for key, value in row.items() if key != "tool_results"}
            reduced.append({**kept, "content": answer})
    return reduced


def _text_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


def exchange_tokens(exchange: Exchange) -> int:
    """Estimated tokens of `exchange` once replayed as messages."""
    total = _EXCHANGE_ALLOWANCE_TOKENS
    for row in exchange:
        total += _MESSAGE_OVERHEAD_TOKENS + _text_tokens(str(row.get("content") or ""))
        results = [entry for entry in row.get("tool_results") or [] if isinstance(entry, dict)]
        if results:
            total += _MESSAGE_OVERHEAD_TOKENS
        for entry in results:
            result = entry.get("result", "")
            result_text = result if isinstance(result, str) else json.dumps(result, default=str)
            args = entry.get("args")
            call_chars = len(str(entry.get("tool_name") or "")) + len(str(args if isinstance(args, dict) else {}))
            if entry.get("artifact_id"):
                result_text = result_text[:_ARTIFACT_POINTER_CHARS]
            total += _MESSAGE_OVERHEAD_TOKENS + _text_tokens(result_text) + call_chars // _CHARS_PER_TOKEN
    return total


@dataclass(frozen=True)
class HistoryPlan:
    """Which exchanges a run replays, oldest first, and in what form."""

    # Indices into the exchanges the plan was made from.
    kept: tuple[int, ...]
    # Indices replayed whole; every other kept index is reduced.
    whole: frozenset[int]
    omitted: int
    tokens: int

    @property
    def reduced(self) -> int:
        return len(self.kept) - len(self.whole)


def plan_history(
    exchanges: Sequence[Exchange],
    budget_tokens: int,
    extra_tokens: Sequence[int] | None = None,
) -> HistoryPlan:
    """Fit `exchanges` into `budget_tokens`, newest first.

    `extra_tokens[i]` is what exchange `i` adds only when kept whole (its
    rendered attachments). Exchanges kept whole are always the newest ones: an
    older exchange whole after a newer one reduced would replay a conversation
    that reads out of proportion.
    """
    extras = list(extra_tokens or [0] * len(exchanges))
    used = 0
    kept: list[int] = []
    whole: set[int] = set()
    reducing = False
    omitted = 0
    for index in range(len(exchanges) - 1, -1, -1):
        if not reducing:
            cost = exchange_tokens(exchanges[index]) + extras[index]
            if used + cost <= budget_tokens:
                used += cost
                kept.append(index)
                whole.add(index)
                continue
            reducing = True
        cost = exchange_tokens(reduce_exchange(exchanges[index]))
        if used + cost > budget_tokens:
            omitted = index + 1
            break
        used += cost
        kept.append(index)
    kept.reverse()
    return HistoryPlan(kept=tuple(kept), whole=frozenset(whole), omitted=omitted, tokens=used)


def omitted_note(omitted: int) -> str:
    turns = "turn" if omitted == 1 else "turns"
    return f"({omitted} earlier {turns} of this conversation are not shown here.)"


def attachment_stub(names: Sequence[str]) -> str:
    """What a replayed question says about attachments whose content is not repeated."""
    listed = ", ".join(names) if names else "a file"
    return (
        f"[Attached to this earlier question: {listed}. The content is not repeated here; "
        "read it with knowledgegraph__fetch_record.]"
    )


__all__ = [
    "CURRENT_ATTACHMENT_SHARE",
    "EARLIER_ATTACHMENT_SHARE",
    "HISTORY_SHARE",
    "REDUCED_ANSWER_CHARS",
    "REDUCED_QUESTION_CHARS",
    "HistoryPlan",
    "attachment_stub",
    "current_attachment_chars",
    "earlier_attachment_chars",
    "exchange_tokens",
    "omitted_note",
    "plan_history",
    "reduce_exchange",
    "share_tokens",
    "split_exchanges",
]

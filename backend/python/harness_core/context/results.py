"""One tool result's size cap (legacy L1 budget reduction, a compiler step since P4.8).

A single runaway result (a whole page scraped, a large record) is cut to a fixed number
of characters, keeping both ends: a result carries its instructions last (the citation
rule, the continuation hint). The cut is a function of the result alone, so a capped
result renders the same bytes on every turn and the history stays append-only.
Legacy's PRE_MODEL shaper and the compiler share it, so both paths cut the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.kernel.core.messages import TextPart

if TYPE_CHECKING:
    from harness_core.kernel.core.messages import Part, ToolMessage

RESULT_CAP_CHARS = 64_000
# Where a run's tool state carries its ``toolResults.maxChars`` (C4.10), read by every loop path that caps a result.
RESULT_CAP_STATE = "result_cap_chars"
# Loop slot holding the step footers a compiled request showed on results, by call id (P4.9): journaled
# when new, so a later run of the session renders an earlier run's results with the bytes they were sent with.
RESULT_FOOTERS_SLOT = "context.resultFooters"
# Loop slot holding a smaller per-result cap, journaled after the provider refused a request whose newest
# turns alone exceed its window (P4.9); halved per refusal down to the floor, then the run is refused.
RESULT_CAP_SLOT = "context.resultCap"
RESULT_CAP_FLOOR = 2_000
_MARKER = "\n[…truncated"
CAPPED_SUFFIX = "by budget_reduction]"

# BUDGETEND: the results of one batch (one response's calls) together take at most this share of the model's
# window, split evenly between them. Without it, one run kept three turns of Slack results at 64,000 characters
# each and every request after a compaction was still 108k-150k tokens of a 128k window.
BATCH_RESULT_SHARE = 0.12
# Characters per token of tool output as providers count it: JSON with ids counts about 2.8, not 4.
RESULT_CHARS_PER_TOKEN = 3
# No result of a batch is cut below this, however many calls the batch made.
BATCH_RESULT_FLOOR = 4_000


def batch_result_cap(batch_size: int, window_tokens: int | None, max_chars: int = RESULT_CAP_CHARS) -> int:
    """The per-result cap of a batch of ``batch_size`` results for a model with ``window_tokens``: a share of the
    window split between them, at most ``max_chars``, at least the floor. A function of the batch and the model
    alone, so a result renders the same bytes on every turn and the cached prefix holds."""
    if not window_tokens or batch_size <= 0:
        return max_chars
    share = int(window_tokens * BATCH_RESULT_SHARE * RESULT_CHARS_PER_TOKEN) // batch_size
    return min(max_chars, max(share, BATCH_RESULT_FLOOR))


def _marker(dropped: int) -> str:
    return f"{_MARKER} {dropped} chars {CAPPED_SUFFIX}\n"


def _read_more(message: ToolMessage) -> str:
    """Where a cut result's whole text is: its artifact, when the run registered one."""
    meta = message.artifact_meta
    if meta is None:
        return ""
    return f'\n[The whole result: retrieve_artifact_content(artifact_id="{meta.artifact_id}")]'


def _cut(length: int, limit: int) -> tuple[int, int, str]:
    """What a both-ends cut of ``length`` characters keeps within ``limit``, marker included: the head
    length, the tail length and the marker. Three quarters of what is kept come from the head."""
    if limit <= 0:
        return 0, 0, ""
    # Budgeted against the widest the dropped count can print, so the marker always fits.
    keep = limit - len(_marker(length))
    if keep <= 0:
        # No room for both; a fragment of the marker would say nothing.
        return limit, 0, ""
    keep_head = int(keep * 0.75)
    return keep_head, keep - keep_head, _marker(length - keep)


def cap_text(text: str, limit: int) -> str:
    """``text`` within ``limit`` characters, marker included: three quarters from the head, the rest from the tail."""
    head, tail, marker = _cut(len(text), limit)
    # Not `text[-tail:]`: at tail == 0 that returns the whole string.
    return text[:head] + marker + text[len(text) - tail :]


def cap_result(message: ToolMessage, max_chars: int = RESULT_CAP_CHARS) -> ToolMessage:
    """``message`` with its text cut to ``max_chars``; images are left alone, and a result
    already cut (its marker present) is not cut again."""
    content = message.content
    if isinstance(content, list):
        return _cap_parts(message, content, max_chars)
    if len(content) <= max_chars or CAPPED_SUFFIX in content:
        return message
    return message.model_copy(update={"content": cap_text(content, max_chars) + _read_more(message)})


def _cap_parts(message: ToolMessage, parts: list[Part], max_chars: int) -> ToolMessage:
    """The parts' text cut as one text, both ends kept: cutting part by part spent the whole allowance on an
    oversized first part and emptied the last one, where the closing instructions are."""
    texts = [p.text for p in parts if isinstance(p, TextPart)]
    total = sum(len(t) for t in texts)
    # The marker sits in whichever part the head stopped in.
    if total <= max_chars or any(CAPPED_SUFFIX in t for t in texts):
        return message
    head, tail, marker = _cut(total, max_chars)
    tail_from = total - tail
    capped: list[Part] = []
    offset = 0
    for part in parts:
        if not isinstance(part, TextPart):
            capped.append(part)
            continue
        start, end = offset, offset + len(part.text)
        offset = end
        kept = part.text[: max(0, min(end, head) - start)]
        # The marker sits where the head stops, in the part the cut starts in.
        if start <= head < end:
            kept += marker
            marker = ""
        kept += part.text[max(0, tail_from - start) :] if end > tail_from else ""
        capped.append(part.model_copy(update={"text": kept}))
    return message.model_copy(update={"content": capped})


def loop_result_cap(policy_cap: object) -> int:
    """The loop's cap on one result under the run's ``toolResults.maxChars`` (C4.10): lowered by it, never raised."""
    if not isinstance(policy_cap, int) or isinstance(policy_cap, bool) or policy_cap <= 0:
        return RESULT_CAP_CHARS
    return min(RESULT_CAP_CHARS, policy_cap)

__all__ = [
    "BATCH_RESULT_FLOOR",
    "BATCH_RESULT_SHARE",
    "CAPPED_SUFFIX",
    "RESULT_CHARS_PER_TOKEN",
    "batch_result_cap",
    "RESULT_CAP_CHARS",
    "RESULT_CAP_STATE",
    "loop_result_cap",
    "RESULT_CAP_FLOOR",
    "RESULT_CAP_SLOT",
    "RESULT_FOOTERS_SLOT",
    "cap_result",
    "cap_text",
]

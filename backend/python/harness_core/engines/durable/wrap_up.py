"""An engine run's wrap-up answer (ENGBUDGET): BUDGETEND's answer-only turn for a run a coding engine drives.

A loop run that reaches its spending limit answers from what it gathered on a reserve it held back (BUDGETEND). An
engine run had no such turn: the engine's next model call was refused at the gateway and the run ended with the
no-model fallback, although its tools had returned data (a Codex child made 22 Jira calls, then answered
"This reply stopped early ... before an answer could be written."). ``ModelEngineWrapUp`` gives it the same
turn: the run holds a ``WrapUpReserve`` on its ledger account before the engine spends, and when a limit stops the
engine with nothing written, one model call on that reserve answers the run's request from the tool results the
journal holds. The engine itself is not driven again: a follow-up turn would start a sandbox and resend the
engine's whole history, and its tool calls could spend the reserve on more work.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast

from harness_core.access.token_count import counter_for
from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.core.responses import StopReason
from harness_core.kernel.core.tokens import count_text_tokens
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from harness_core.budget.wrap_up import WrapUpReserve
    from harness_core.engines.durable.ports import GatheredResult
    from harness_core.kernel.core.turn_limit import CutShort
    from harness_core.models.transports.base import LLMTransport

_STOPPED_AT: dict[str, str] = {
    "unfinished": "the end of its turn, without the file it was asked for,",
    "budget": "its spending limit",
    "turns": "its step limit",
    "time": "its working-time limit",
    "stall": "a point where its steps kept repeating without progress",
    "provider": "a point where the AI model provider stayed unavailable",
    "engine_error": "an error in the coding engine it ran on",
    "secret_blocked": "a request PipesHub blocked because it looked like it contained a secret",
}

WRAP_UP_SYSTEM = (
    "You finish a task that another agent started. It stopped at {limit} before it wrote its answer. Write the "
    "answer to the request now, using only the notes and tool results below. When the request asks for a report or "
    "a file, write that file's whole content in Markdown: it is saved as the file. Do not ask to run anything and do "
    "not describe tools or these instructions. Cite each finding with the source it comes from, as a Markdown link "
    "to the result's URL right after the sentence; keep the links, keys and ids the results give, so the reader can "
    "open them, and do not invent sources. Where the results do not settle something, say so in one short sentence "
    "and mark it unverified. End with a short section headed 'Still to do' that lists what the request needs beyond "
    "these results, so the next run can pick up from there."
)
# SPENDCONT: a continuation's wrap-up answered from its own results alone, so each "continue" replaced the partial
# result instead of growing it.
EARLIER_HEADING = (
    "What earlier attempts at this request already produced (keep all of it in your answer, update it with the "
    "results below, and do not drop items):"
)
# WP-70: what the engine itself kept besides its tool results, which the wrap-up reads first.
ENGINE_NOTES = "engine notes"
ENGINE_DRAFT = "engine draft"
NOTES_HEADING = "Notes the agent kept while it worked:"
DRAFT_HEADING = "The agent's last message before it stopped (unfinished):"
# The prompt's frame (request, headings) beside the results, in tokens.
_FRAME_TOKENS = 600
# A single result keeps at least this many characters when the reserve is small.
_MIN_RESULT_CHARS = 400
# Each result's fair share is at least this (WP-70: 166 pages each cut to a 400-character JSON head say nothing).
_SHARE_FLOOR_CHARS = 1_200
# Of the room, the engine's notes take at most this share and its last message at most this many characters.
_NOTES_SHARE = 0.3
_DRAFT_MAX_CHARS = 2_000
_CHARS_PER_TOKEN = 3
# Holding the prompt to its reserve by the run's counter: at most this many rebuilds, each this far under the count.
_FIT_ROUNDS = 4
_FIT_MARGIN = 0.95
# A result's text fields worth reading, in a JSON result of any tool (product-neutral).
_TEXT_KEYS = ("title", "summary", "snippet", "description", "content", "text", "body")
_SCALAR_MAX_CHARS = 200


def readable(text: str) -> str:
    """A tool result as prose: a JSON result's text fields (titles, snippets, contents) one per line, else as is. A
    fetched page's JSON is mostly framing; its text is what the answer needs."""
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return stripped
    try:
        document = json.loads(stripped)
    except ValueError:
        return stripped
    lines: list[str] = []
    _collect(document, lines, depth=0)
    return "\n".join(lines) if lines else stripped


def _collect(value: object, lines: list[str], *, depth: int) -> None:
    """Text fields as prose, other short scalars as ``key: value`` (ids and keys stay), then what nests."""
    if depth > 6:
        return
    if isinstance(value, dict):
        entries = cast("dict[str, object]", value)
        for key in _TEXT_KEYS:
            field_value = entries.get(key)
            if isinstance(field_value, str) and field_value.strip():
                lines.append(" ".join(field_value.split()))
        for key, item in entries.items():
            if key in _TEXT_KEYS or isinstance(item, dict | list) or item is None or isinstance(item, bool):
                continue
            shown = str(item)
            if len(shown) <= _SCALAR_MAX_CHARS:
                lines.append(f"{key}: {shown}")
        for key, item in entries.items():
            if key not in _TEXT_KEYS and isinstance(item, dict | list):
                _collect(item, lines, depth=depth + 1)
    elif isinstance(value, list):
        for item in cast("list[object]", value):
            _collect(item, lines, depth=depth + 1)
    elif isinstance(value, str) and value.strip():
        lines.append(" ".join(value.split()))


def _heading(result: GatheredResult) -> str:
    return f"### {result.tool}" + (f" ({result.source})" if result.source else "")


def wrap_up_prompt(
    request: str,
    gathered: Sequence[GatheredResult],
    *,
    input_tokens: int,
    earlier: str | None = None,
    count: Callable[[str], int] | None = None,
) -> str:
    """The request, the partial result an earlier run left (at most half the room), the engine's own notes and its
    last message (WP-70), and as many of the newest tool results as fit ``input_tokens``, oldest first, each read as
    prose and given a fair share of the room.

    The room is first sized at ``_CHARS_PER_TOKEN``; with ``count`` (the run's own counter, R15-WRAP) the prompt is
    then held to ``input_tokens`` as that counter counts it, the reservation's own count: text that takes a token or
    more a character (Chinese, Japanese, Korean) would otherwise be sent at two to three times its reserve. ``count``
    may take milliseconds on a long prompt: call this off the event loop."""
    room = max(input_tokens - _FRAME_TOKENS - count_text_tokens(request), input_tokens // 4) * _CHARS_PER_TOKEN
    prompt = _prompt_in(request, gathered, room, earlier)
    if count is None:
        return prompt
    for _ in range(_FIT_ROUNDS):
        tokens = count(prompt)
        if tokens <= input_tokens or room <= _MIN_RESULT_CHARS:
            break
        room = max(_MIN_RESULT_CHARS, int(room * input_tokens / tokens * _FIT_MARGIN))
        prompt = _prompt_in(request, gathered, room, earlier)
    return prompt


def _counted_prompt(
    model: str, request: str, gathered: Sequence[GatheredResult], input_tokens: int, earlier: str | None
) -> str:
    """``wrap_up_prompt`` held to ``input_tokens`` by ``model``'s counter; on a worker thread."""
    return wrap_up_prompt(request, gathered, input_tokens=input_tokens, earlier=earlier, count=counter_for(model).text)


def _prompt_in(request: str, gathered: Sequence[GatheredResult], room: int, earlier: str | None) -> str:
    """The wrap-up prompt within ``room`` characters of gathered text."""
    earlier_text = earlier.strip()[: room // 2] if earlier else ""
    budget = room - len(earlier_text)
    notes = [g for g in gathered if g.tool == ENGINE_NOTES]
    drafts = [g for g in gathered if g.tool == ENGINE_DRAFT]
    results = [g for g in gathered if g.tool not in (ENGINE_NOTES, ENGINE_DRAFT)]
    draft_text = drafts[-1].text.strip()[:_DRAFT_MAX_CHARS] if drafts else ""
    budget -= len(draft_text)
    notes_left = int(budget * _NOTES_SHARE)
    note_blocks: list[str] = []
    for note in notes:
        text = note.text.strip()
        if notes_left < _MIN_RESULT_CHARS or not text:
            continue
        text = text if len(text) <= notes_left else text[:notes_left] + " [...]"
        block = f"{_heading(note)}\n{text}"
        note_blocks.append(block)
        notes_left -= len(block)
        budget -= len(block)
    share = max(_SHARE_FLOOR_CHARS, budget // max(len(results), 1))
    kept: list[str] = []
    for result in reversed(results):
        if budget < _MIN_RESULT_CHARS:
            break
        text = readable(result.text)
        limit = min(share, budget)
        if len(text) > limit:
            text = text[:limit] + " [...]"
        block = f"{_heading(result)}\n{text}"
        kept.append(block)
        budget -= len(block)
    dropped = len(results) - len(kept)
    parts = [f"Request:\n{request.strip()}"]
    if earlier_text:
        parts.append(f"{EARLIER_HEADING}\n{earlier_text}")
    if note_blocks:
        parts.append(NOTES_HEADING + "\n\n" + "\n\n".join(note_blocks))
    if draft_text:
        parts.append(f"{DRAFT_HEADING}\n{draft_text}")
    parts.append("Tool results (oldest first):")
    if dropped:
        parts.append(f"({dropped} earlier results did not fit and are left out.)")
    parts.extend(reversed(kept))
    return "\n\n".join(parts)


def whole_part(text: str) -> str:
    """An answer the output limit cut, up to its last whole paragraph, or its last whole sentence when it is one
    paragraph (WP-127: wrap-ups ended in half a table row); the run's notice says the rest is missing."""
    paragraph = text.rfind("\n\n")
    if paragraph > 0:
        return text[:paragraph].rstrip()
    sentence = max(text.rfind(end) for end in (". ", "! ", "? "))
    return text[: sentence + 1] if sentence > 0 else text


class ModelEngineWrapUp:
    """``EngineWrapUp`` over the run's metered transport (or a decorator over it, the host's classification guard),
    which draws on ``reserve`` when armed."""

    def __init__(self, *, reserve: WrapUpReserve, transport: LLMTransport, log: HarnessLog | None = None) -> None:
        self._reserve = reserve
        self._transport = transport
        self._log = log or HarnessLog.named("harness_core.engines.durable.wrap_up")

    async def hold(self) -> bool:
        return await self._reserve.hold()

    async def answer(
        self, request: str, gathered: Sequence[GatheredResult], reason: CutShort, *, earlier: str | None = None
    ) -> str | None:
        size = self._reserve.size
        # Off the event loop: the run's counter (WP-66) may load its BPE and reads a long prompt in milliseconds
        # (R14-LOOP).
        prompt = await asyncio.to_thread(
            _counted_prompt, self._reserve.pricing_model, request, gathered, size.input_tokens, earlier
        )
        armed = self._reserve.arm()
        try:
            response = await self._transport.complete(
                messages=[UserMessage(content=prompt)],
                system=WRAP_UP_SYSTEM.format(limit=_STOPPED_AT.get(reason, _STOPPED_AT["budget"])),
                max_output_tokens=size.output_tokens,
            )
        except Exception as exc:
            # The no-model answer stands in; a refused or failed call is never the run's failure.
            self._log.warning("engine.wrap_up.failed", reason=reason, armed=armed, errorType=type(exc).__name__)
            return None
        text = response.message.text.strip()
        cut = response.stop_reason == StopReason.MAX_TOKENS
        if cut:
            text = whole_part(text)
        self._log.info(
            "engine.wrap_up.answered",
            reason=reason,
            armed=armed,
            results=len(gathered),
            chars=len(text),
            inputTokens=response.usage.input_tokens,
            outputTokens=response.usage.output_tokens,
            cut=cut,
        )
        return text or None

    async def release(self) -> None:
        await self._reserve.release()


__all__ = [
    "EARLIER_HEADING",
    "ENGINE_DRAFT",
    "ENGINE_NOTES",
    "WRAP_UP_SYSTEM",
    "ModelEngineWrapUp",
    "readable",
    "whole_part",
    "wrap_up_prompt",
]

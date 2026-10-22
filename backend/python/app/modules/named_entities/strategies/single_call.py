"""One structured call per window. Valid items are kept; the rest are dropped."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from app.modules.named_entities.strategies.agent.prompt import STATIC_PREFIX
from app.modules.named_entities.strategies.agent.tools import (
    ExtractionSession,
    stage_wire_entity,
)
from app.modules.named_entities.strategies.base import (
    ExtractionContext,
    ExtractionDocument,
    StrategyResult,
)
from app.modules.named_entities.wire import EntitySubmission, WireEntity
from app.services.llm_gateway.gateway import (
    ProviderUnavailableError,
    get_llm_gateway,
    provider_key,
)
from app.telemetry.modules.named_entity_metrics import record_step_down

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain_core.language_models.chat_models import BaseChatModel

    from app.modules.named_entities.text import TextUnit

    _Reply = EntitySubmission | str | None

logger = logging.getLogger(__name__)

_WINDOW_CHARS = 4_000
# A window is at most 4k characters; a reply with more items than this is not reading them.
_MAX_ITEMS_PER_WINDOW = 200
_CALL_TIMEOUT = 30.0
# The calls one window may make: strict with its two reflection retries, then tool,
# then prompted JSON. Waiting for an indexing LLM slot is not counted.
WINDOW_TIMEOUT_SECONDS = 5 * _CALL_TIMEOUT
_WIRE_ADAPTER = TypeAdapter(WireEntity)
# Strict schema, then a non-strict tool, then prompted JSON. A model starts lower
# only after a lower mode answered a prompt a higher one failed on: an error every
# mode shares (a content filter, an outage, a 429) says nothing about the schema.
# The memory expires, so a model that misbehaved for a while gets strict back.
_MODES = ("strict", "tool", "prompt")
_STEP_DOWN_TTL_SECONDS = 3600.0
_STEP_DOWN: dict[tuple[str, str], tuple[str, float]] = {}
_NO_TOOLS = "\nNo tools are available in this mode. Reply with the JSON object only.\n"


def parse_submission_text(raw: str) -> list[WireEntity]:
    """Accept a full submission, or the complete items inside a truncated one."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].casefold() == "json":
            text = text[4:].strip()
    candidates = [text]
    try:
        import json_repair

        repaired = json_repair.repair_json(text)
        if isinstance(repaired, str) and repaired not in candidates:
            candidates.append(repaired)
    except Exception:
        pass
    for candidate in candidates:
        parsed = _parse_one(candidate)
        if parsed:
            return parsed
    return []


def _parse_one(candidate: str) -> list[WireEntity]:
    try:
        return list(EntitySubmission.model_validate_json(candidate).entities)
    except Exception:
        pass
    try:
        data = json.loads(candidate)
    except Exception:
        return _salvage(candidate)
    raw_items = data.get("entities") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        return []
    kept: list[WireEntity] = []
    for item in raw_items:
        try:
            kept.append(_WIRE_ADAPTER.validate_python(item))
        except Exception:
            continue
    return kept


def _salvage(text: str) -> list[WireEntity]:
    """Keep complete objects when the rest of the payload was cut off."""
    import re

    kept: list[WireEntity] = []
    for match in re.finditer(r"\{[^{}]*\}", text):
        try:
            kept.append(_WIRE_ADAPTER.validate_json(match.group(0)))
        except Exception:
            continue
    return kept


@dataclass(frozen=True)
class Piece:
    """``unit.text[start:end]``: all of a block, or one piece of a long one."""

    unit: TextUnit
    start: int
    end: int


def _cuts(text: str, size: int) -> list[tuple[int, int]]:
    """``text`` in pieces of at most ``size`` characters, each ending after a
    space where one falls in the second half, so a name is rarely split."""
    spans: list[tuple[int, int]] = []
    start = 0
    while len(text) - start > size:
        space = text.rfind(" ", start + size // 2, start + size)
        end = space + 1 if space != -1 else start + size
        spans.append((start, end))
        start = end
    spans.append((start, len(text)))
    return spans


def pack_windows(units: list[TextUnit], budget: int) -> list[list[Piece]]:
    """Consecutive blocks packed into windows of at most ``budget`` characters
    of text, in reading order."""
    budget = max(budget, 1)
    windows: list[list[Piece]] = []
    current: list[Piece] = []
    used = 0
    for unit in units:
        for start, end in _cuts(unit.text, budget):
            size = end - start
            if current and used + size > budget:
                windows.append(current)
                current, used = [], 0
            current.append(Piece(unit, start, end))
            used += size
    if current:
        windows.append(current)
    return windows


def _render(pieces: list[Piece]) -> str:
    lines = []
    for piece in pieces:
        text = piece.unit.text
        head = "…" if piece.start > 0 else ""
        tail = "…" if piece.end < len(text) else ""
        lines.append(f"[B{piece.unit.block_index}] {head}{text[piece.start:piece.end]}{tail}")
    return "\n".join(lines)


class SingleCallExtractionStrategy:
    name = "single_call"

    def __init__(self, complete: Callable[[str, str], Awaitable[_Reply]] | None = None) -> None:
        self._complete = complete

    async def extract(
        self, doc: ExtractionDocument, seed: list, ctx: ExtractionContext
    ) -> StrategyResult:
        del seed
        budgets = ctx.budgets
        limit = getattr(budgets, "max_llm_windows", 5)
        all_windows = pack_windows(doc.units, getattr(budgets, "max_window_chars", _WINDOW_CHARS))
        windows = all_windows[:limit]
        session = ExtractionSession(doc.units, enabled=ctx.enabled, norm_ctx=ctx.norm, seed_count=0)
        if not windows:
            return StrategyResult(termination_reason="single_call")
        kinds = ", ".join(sorted(kind.value for kind in ctx.enabled))
        failed = 0
        for window in windows:
            prompt = (
                f"Enabled kinds: {kinds}.\n"
                "Return JSON {\"entities\": [{\"text\", \"block\", \"kind\"}]}.\n"
                + session.spotlight_text(_render(window))
            )
            try:
                raw = await self._invoke(ctx, prompt)
            except Exception as exc:
                logger.warning("Named-entity single call failed: %s", type(exc).__name__)
                failed += 1
                continue
            items = raw.entities if isinstance(raw, EntitySubmission) else parse_submission_text(str(raw or ""))
            await session.staged(lambda items=items[:_MAX_ITEMS_PER_WINDOW]: [
                stage_wire_entity(session, index, item, extractor="single_call") for index, item in enumerate(items)
            ])
        coverage = {
            "incomplete": failed > 0 or len(all_windows) > len(windows),
            "windows_total": len(all_windows),
            "windows_run": len(windows),
            "windows_failed": failed,
        }
        if failed and not session.staging:
            return StrategyResult(termination_reason="llm_error", failed_before_first_turn=True, **coverage)
        return StrategyResult(
            mentions=session.staging.mentions(),
            termination_reason="single_call",
            submitted=session.staging.submitted,
            rejected=session.staging.rejected,
            rejected_reasons=dict(session.staging.rejected_reasons),
            **coverage,
        )

    async def _invoke(self, ctx: ExtractionContext, prompt: str) -> _Reply:
        if self._complete is not None:
            return await self._complete(STATIC_PREFIX, prompt)
        llm = ctx.llm
        if llm is None:
            raise RuntimeError("no llm")
        key = (ctx.provider_name, ctx.model_name)
        start = _start_mode(key)
        runners = {"strict": _strict, "tool": _function_call, "prompt": _prompted_json}
        failed: list[str] = []
        error: Exception | None = None
        for mode in _MODES[_MODES.index(start):]:
            try:
                result = await runners[mode](llm, prompt)
            except Exception as exc:
                error, result = exc, None
                # A call that ran out of time, or that the provider could not
                # serve (an open circuit, an outage), says nothing about the
                # schema: the next mode is only a retry, and must not become the
                # start mode.
                if isinstance(exc, (TimeoutError, ProviderUnavailableError)):
                    continue
            if result is None:
                failed.append(mode)
                continue
            if failed:
                logger.info("Named-entity %s output failed where %s worked; starting at %s", failed[0], mode, mode)
                _STEP_DOWN[key] = (mode, time.monotonic() + _STEP_DOWN_TTL_SECONDS)
                record_step_down(mode)
            return result
        raise error or ValueError("no structured result")


def _start_mode(key: tuple[str, str]) -> str:
    remembered = _STEP_DOWN.get(key)
    if remembered is None:
        return "strict"
    mode, expires = remembered
    if time.monotonic() >= expires:
        del _STEP_DOWN[key]
        return "strict"
    return mode


def _messages(prompt: str) -> list:
    from langchain_core.messages import HumanMessage, SystemMessage

    return [SystemMessage(content=STATIC_PREFIX + _NO_TOOLS), HumanMessage(content=prompt)]


async def _strict(llm: BaseChatModel, prompt: str) -> EntitySubmission | None:
    from app.utils.streaming import invoke_with_structured_output_and_reflection

    return await invoke_with_structured_output_and_reflection(
        llm, _messages(prompt), EntitySubmission, call_timeout=_CALL_TIMEOUT, raise_call_errors=True,
    )


async def _function_call(llm: BaseChatModel, prompt: str) -> EntitySubmission | str:
    bound = llm.with_structured_output(EntitySubmission, method="function_calling")
    result = await get_llm_gateway().invoke(
        bound, _messages(prompt), provider=provider_key(llm), call_site="named_entities", timeout=_CALL_TIMEOUT
    )
    if isinstance(result, EntitySubmission):
        return result
    if isinstance(result, dict):
        return json.dumps(result)
    raise ValueError("no structured result")


async def _prompted_json(llm: BaseChatModel, prompt: str) -> str:
    response = await get_llm_gateway().invoke(
        llm, _messages(prompt), provider=provider_key(llm), call_site="named_entities", timeout=_CALL_TIMEOUT
    )
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return "".join(str(getattr(part, "text", part)) for part in content)
    return str(content or "")


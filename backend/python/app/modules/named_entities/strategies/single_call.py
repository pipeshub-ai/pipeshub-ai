"""One structured call per window. Valid items are kept; the rest are dropped."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

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
from app.telemetry.modules.named_entity_metrics import record_step_down
from app.utils.concurrency import indexing_llm_slot

logger = logging.getLogger(__name__)

_WINDOW = 5
# A window is five blocks; a reply with more items than this is not reading them.
_MAX_ITEMS_PER_WINDOW = 200
_CALL_TIMEOUT = 30.0
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


def _windows(units: list, size: int) -> list[list]:
    return [units[i : i + size] for i in range(0, len(units), size)]


class SingleCallExtractionStrategy:
    name = "single_call"

    def __init__(self, complete=None) -> None:
        self._complete = complete

    async def extract(
        self, doc: ExtractionDocument, seed: list, ctx: ExtractionContext
    ) -> StrategyResult:
        del seed
        budgets = ctx.budgets
        limit = getattr(budgets, "max_llm_windows", 3)
        all_windows = _windows(doc.units, _WINDOW)
        windows = all_windows[:limit]
        session = ExtractionSession(doc.units, enabled=ctx.enabled, norm_ctx=ctx.norm, seed_count=0)
        if not windows:
            return StrategyResult(termination_reason="single_call")
        kinds = ", ".join(sorted(kind.value for kind in ctx.enabled))
        failed = 0
        for window in windows:
            prompt = (
                f"Enabled kinds: {kinds}.\n"
                "Return JSON {\"entities\": [{\"text\", \"block\", \"kind\", \"normalized\"}]}.\n"
                + session.spotlight(window)
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

    async def _invoke(self, ctx: ExtractionContext, prompt: str) -> Any:
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


async def _strict(llm, prompt: str) -> EntitySubmission | None:
    from app.utils.streaming import invoke_with_structured_output_and_reflection

    return await invoke_with_structured_output_and_reflection(
        llm, _messages(prompt), EntitySubmission, call_timeout=_CALL_TIMEOUT,
    )


async def _function_call(llm, prompt: str) -> EntitySubmission | str:
    bound = llm.with_structured_output(EntitySubmission, method="function_calling")
    async with indexing_llm_slot():
        result = await asyncio.wait_for(bound.ainvoke(_messages(prompt)), timeout=_CALL_TIMEOUT)
    if isinstance(result, EntitySubmission):
        return result
    if isinstance(result, dict):
        return json.dumps(result)
    raise ValueError("no structured result")


async def _prompted_json(llm, prompt: str) -> str:
    async with indexing_llm_slot():
        response = await asyncio.wait_for(llm.ainvoke(_messages(prompt)), timeout=_CALL_TIMEOUT)
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return "".join(str(getattr(part, "text", part)) for part in content)
    return str(content or "")


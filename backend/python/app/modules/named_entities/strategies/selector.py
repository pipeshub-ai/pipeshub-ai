"""Pick the agent, one structured call, or the deterministic pass only."""

from __future__ import annotations

from app.modules.named_entities.domain.kinds import semantic_kinds
from app.modules.named_entities.strategies.base import (
    ExtractionContext,
    ExtractionDocument,
)
from app.utils.concurrency import indexing_llm_slots_remaining

_TOOL_CAPABILITY: dict[tuple[str, str], bool] = {}


def remember_tool_capability(provider: str, model: str, supported: bool) -> None:
    _TOOL_CAPABILITY[(provider, model)] = supported


def clear_tool_capability() -> None:
    _TOOL_CAPABILITY.clear()


def model_supports_tools(llm, provider: str, model: str, override: bool | None) -> bool:
    if override is not None:
        return override
    key = (provider, model)
    cached = _TOOL_CAPABILITY.get(key)
    if cached is not None:
        return cached
    if llm is None:
        return True
    try:
        from langchain_core.tools import tool

        @tool
        def ping() -> str:
            """ping"""
            return "ok"

        llm.bind_tools([ping])
    except Exception:
        _TOOL_CAPABILITY[key] = False
        return False
    _TOOL_CAPABILITY[key] = True
    return True


class ExtractionStrategySelector:
    def choose(self, doc: ExtractionDocument, ctx: ExtractionContext) -> str:
        if not doc.units:
            return "single_call"
        if not semantic_kinds(ctx.enabled):
            return "deterministic"
        if ctx.under_pressure or indexing_llm_slots_remaining() <= 0:
            return "single_call"
        if not model_supports_tools(ctx.llm, ctx.provider_name, ctx.model_name, ctx.supports_tools):
            return "single_call"
        return "agent"

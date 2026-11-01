"""Searches the coverage loop runs in code, through the search tool's own
operation so the results are ranked, permission-checked, numbered for citation
and recorded as shown exactly as if the model had called the tool."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.retrieval_ledger import emit_retrieval_context

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

# Small enough that a round of them costs less than one search the model makes.
TARGETED_RESULT_CHARS = 16_000
_SEARCH_TIMEOUT_S = 60.0


@dataclass(frozen=True)
class TargetedResult:
    query: str
    text: str | None
    """The rendered result, or None when the search failed or found nothing."""


def _result_text(output: Any) -> str | None:  # noqa: ANN401 - str or a list of parts
    if isinstance(output, list):
        text = "".join(getattr(part, "text", "") or "" for part in output)
    elif isinstance(output, str):
        text = output
    else:
        return None
    if text.lstrip().startswith("{"):
        # The tool answers an empty or failed search with a JSON status.
        try:
            payload = json.loads(text)
        except ValueError:
            return text
        if isinstance(payload, dict) and not payload.get("results"):
            return None
    return text if text.strip() else None


async def run_targeted_searches(
    context: "AgentContext",
    queries: list[str],
    *,
    purpose: str,
    max_chars: int = TARGETED_RESULT_CHARS,
    timeout: float = _SEARCH_TIMEOUT_S,
) -> list[TargetedResult]:
    """Run ``queries`` concurrently; one retrieval frame reports what they added."""
    from app.agents.actions.knowledge_graph.ops.search import execute_search

    async def _one(query: str) -> TargetedResult:
        try:
            output = await asyncio.wait_for(
                execute_search(context.tool_state, query, max_result_chars=max_chars), timeout=timeout,
            )
        except Exception as exc:
            logger.warning("coverage_loop: %s search failed: %r", purpose, exc)
            return TargetedResult(query, None)
        return TargetedResult(query, _result_text(output))

    if not queries:
        return []
    results = list(await asyncio.gather(*[_one(query) for query in queries]))
    await emit_retrieval_context(
        context, source="tool", tool_name=f"coverage_loop.{purpose}", tool_call_id=f"coverage_loop.{purpose}",
    )
    return results


def render_results(results: list[TargetedResult]) -> str:
    blocks = []
    for result in results:
        body = result.text or "No records found."
        blocks.append(f'Search "{result.query}":\n{body}')
    return "\n\n".join(blocks)


__all__ = ["TARGETED_RESULT_CHARS", "TargetedResult", "render_results", "run_targeted_searches"]

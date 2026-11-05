"""Searches the coverage loop runs in code, through the search tool's own
operation so the results are ranked, permission-checked, numbered for citation
and recorded as shown exactly as if the model had called the tool."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from app.agents.agent_loop.retrieval_ledger import emit_retrieval_context

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext
    from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

# Small enough that a round of them costs less than one search the model makes.
TARGETED_RESULT_CHARS = 16_000
# Of each search's timeout, the most it waits for keyword matches once the
# semantic results are in; otherwise a slow grep could time out the whole search.
_KEYWORD_WAIT_SHARE = 0.25


@dataclass(frozen=True)
class TargetedResult:
    query: str
    text: str | None
    """The rendered result, or None when the search failed or found nothing."""


def _result_text(output: Any) -> str | None:  # noqa: ANN401 - str or a list of parts
    if isinstance(output, list):
        text = "".join(str(getattr(part, "text", "") or "") for part in cast("list[object]", output))
    elif isinstance(output, str):
        text = output
    else:
        return None
    if text.lstrip().startswith("{"):
        # The tool answers an empty or failed search with a JSON status.
        try:
            payload: object = json.loads(text)
        except ValueError:
            return text
        if isinstance(payload, dict) and not cast("dict[str, Any]", payload).get("results"):
            return None
    return text if text.strip() else None


async def run_targeted_searches(
    context: "AgentContext",
    queries: list[str],
    *,
    purpose: str,
    timeout: float,
    max_chars: int = TARGETED_RESULT_CHARS,
) -> list[TargetedResult]:
    """Run ``queries`` concurrently, each bounded by ``timeout`` seconds (a slower
    one counts as finding nothing); one retrieval frame reports what they added."""
    from app.agents.actions.knowledge_graph.ops.search import execute_search

    async def _one(query: str) -> TargetedResult:
        try:
            output = await asyncio.wait_for(
                execute_search(
                    cast("ChatState", context.tool_state), query,
                    max_result_chars=max_chars, keyword_wait_s=timeout * _KEYWORD_WAIT_SHARE,
                ),
                timeout=timeout,
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
    """The searches as one text block for a request to the model."""
    blocks: list[str] = []
    for result in results:
        body = result.text or "No records found."
        blocks.append(f'Search "{result.query}":\n{body}')
    return "\n\n".join(blocks)


__all__ = ["TARGETED_RESULT_CHARS", "TargetedResult", "render_results", "run_targeted_searches"]

"""`classify_route` (`app/modules/agents/qna/router.py`) — regression check
that extracting the tier rubric into `build_tier_rubric()` (so
`app.agents.agent_loop.intent.parse_intent_and_route` can reuse the exact
same quick/react/deep definitions) left `classify_route()`'s own composed
system prompt byte-identical to before the extraction.
"""

from __future__ import annotations

import logging
from typing import Any

from app.modules.agents.qna.router import RouteDecision, build_tier_rubric, classify_route

_LOGGER = logging.getLogger("test.modules.agents.qna.router")


class _CapturingStructuredModel:
    """Captures the messages `classify_route()` sends the structured LLM
    call without needing a real LangChain model."""

    def __init__(self) -> None:
        self.last_messages: list[Any] = []

    def with_structured_output(self, schema: Any) -> "_CapturingStructuredModel":  # noqa: ANN401
        return self

    async def ainvoke(self, messages: list, config: dict | None = None) -> RouteDecision:  # noqa: ANN401
        self.last_messages = messages
        return RouteDecision(reasoning="test", route="react")


def _query_info(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "query": "find my open tickets",
        "knowledge": [],
        "connector_configs": {},
        "filters": {},
        "toolsets": [],
        "previous_conversations": [],
        "attachments": [],
    }
    base.update(overrides)
    return base


class TestClassifyRoutePromptRegression:
    async def test_system_prompt_still_contains_capability_block(self) -> None:
        llm = _CapturingStructuredModel()

        await classify_route(_query_info(), _LOGGER, llm)

        system_message = llm.last_messages[0]
        assert "## Agent capabilities" in system_message.content

    async def test_system_prompt_tier_rubric_matches_build_tier_rubric_output(self) -> None:
        """The tier-definition body (from `## quick` onward) must be exactly
        what `build_tier_rubric()` produces for the same `n_knowledge` — the
        one piece of the prompt `parse_intent_and_route()` now reuses
        verbatim. Only the leading capability/SQL-override blocks (passed
        in as plain strings to `build_tier_rubric()`) legitimately differ
        between this direct call and `classify_route()`'s real capability
        context, so the comparison starts at `## quick`.
        """
        llm = _CapturingStructuredModel()

        await classify_route(_query_info(), _LOGGER, llm)

        system_message = llm.last_messages[0]
        actual_rubric_body = "## quick" + system_message.content.split("## quick", 1)[1]

        # query_info has no knowledge/filters configured -> n_knowledge == 0,
        # matching classify_route's own build_capability_context() result.
        expected_rubric = build_tier_rubric(capability_block="", sql_verify_override="", n_knowledge=0)
        expected_rubric_body = "## quick" + expected_rubric.split("## quick", 1)[1]

        assert actual_rubric_body == expected_rubric_body

    async def test_sql_toolset_override_still_forces_react_language_in_prompt(self) -> None:
        llm = _CapturingStructuredModel()
        query_info = _query_info(toolsets=[{"name": "mariadb-prod", "tools": []}])

        await classify_route(query_info, _LOGGER, llm)

        system_message = llm.last_messages[0]
        assert "## SQL override (highest priority)" in system_message.content

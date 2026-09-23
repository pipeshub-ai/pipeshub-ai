"""`ChatQuery.strictScope` on the agent-chat route (`app/api/routes/agent.py`).

Universal Agent Mode (`agentIdPlaceholder`) is the "Chat Assistant" path
Node's `chatMode=agent` routes through for project-scoped chats — see the
`is_placeholder` comment in `chat_stream()`. A project-scoped chat sets
`strictScope=true` so an empty effective apps/kb selection (e.g. before a
fresh project's first file upload) stays empty at retrieval time instead
of the graph provider falling back to "search everything the user can
access". This mirrors `tests/unit/api/routes/test_chatbot_strict_scope.py`
for `chatbot.py`'s equivalent field.

`TestStrictScopeFilterInjection` replicates the exact block from
`chat_stream()` (right after the `NO_KB_SELECTED_FILTER` sentinel is
applied, right before `_filter_knowledge_by_enabled_sources`) rather than
driving the full endpoint — same style as
`test_agent_capabilities_gate.py::TestCapabilityGatingLogic`, since
`chat_stream()` itself requires a live agent/MCP/DB stack to exercise
end-to-end.
"""
from __future__ import annotations

NO_KB_SELECTED_FILTER = "NO_KB_SELECTED"


class TestChatQueryStrictScopeField:
    def test_defaults_to_false(self) -> None:
        from app.api.routes.agent import ChatQuery

        q = ChatQuery(query="test")
        assert q.strictScope is False

    def test_accepts_explicit_true(self) -> None:
        from app.api.routes.agent import ChatQuery

        q = ChatQuery(query="test", strictScope=True)
        assert q.strictScope is True


class TestStrictScopeFilterInjection:
    """Replicates the filters-construction block from `chat_stream()`:

        if not filters.get("kb") and agent_id != "agentIdPlaceholder":
            filters["kb"] = [NO_KB_SELECTED_FILTER]

        if chat_query.strictScope:
            filters["strictScope"] = True
    """

    @staticmethod
    def _apply(filters: dict, *, agent_id: str, strict_scope: bool) -> dict:
        filters = dict(filters)
        if not filters.get("kb") and agent_id != "agentIdPlaceholder":
            filters["kb"] = [NO_KB_SELECTED_FILTER]
        if strict_scope:
            filters["strictScope"] = True
        return filters

    def test_strict_scope_true_added_for_placeholder_agent_with_empty_scope(self) -> None:
        """The `agentIdPlaceholder` (Chat Assistant / project chat) path
        skips the NO_KB_SELECTED sentinel, so a fresh project with zero
        sources reaches this block with `filters == {"apps": [], "kb": []}`
        -- strictScope must still land so retrieval doesn't widen back out."""
        result = self._apply(
            {"apps": [], "kb": []}, agent_id="agentIdPlaceholder", strict_scope=True,
        )
        assert result == {"apps": [], "kb": [], "strictScope": True}

    def test_strict_scope_false_leaves_filters_untouched(self) -> None:
        """A real Agent Builder agent (non-placeholder) never sets
        strictScope -- its own knowledge-derived filters must be unaffected."""
        result = self._apply(
            {"apps": ["confluence"], "kb": []}, agent_id="agent-123", strict_scope=False,
        )
        assert result == {"apps": ["confluence"], "kb": [NO_KB_SELECTED_FILTER]}
        assert "strictScope" not in result

    def test_strict_scope_true_with_non_empty_kb_still_sets_flag(self) -> None:
        """Once a project links its hidden KB, strictScope stays set too --
        it is a pass-through backstop, not conditioned on the scope being
        empty at this point in the pipeline."""
        result = self._apply(
            {"apps": [], "kb": ["hidden-kb-1"]}, agent_id="agentIdPlaceholder", strict_scope=True,
        )
        assert result == {"apps": [], "kb": ["hidden-kb-1"], "strictScope": True}

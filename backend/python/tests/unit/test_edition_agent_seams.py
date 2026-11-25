"""The agent loop's extension points in ``app.edition_services``: extra hooks, an extended tool grant, and how a
fetch that continues several records is read. Each OSS default leaves today's behaviour as it is."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import edition_services
from app.agent_loop_lib.hooks.events import HookEvent
from app.agents.agent_loop.factory import PipesHubAgentFactory
from tests.edition_overlay import edition_tree_present


def _post_model_names(hooks: Any) -> list[str]:  # noqa: ANN401 - a HookRegistry
    return [
        (getattr(mw, "__qualname__", None) or type(mw).__qualname__).split(".<locals>")[0]
        for _, mw in hooks.on(HookEvent.POST_MODEL)._stack  # pyright: ignore[reportPrivateUsage]
    ]


def _context() -> Any:  # noqa: ANN401
    from app.agents.agent_loop.context import AgentContext

    return AgentContext(org_id="org-1", user_id="user-1", user_email="u@example.com")


@pytest.mark.skipif(edition_tree_present(), reason="these hooks are another edition's in this checkout")
def test_the_defaults_add_no_hook_and_keep_the_grant() -> None:
    context = _context()
    hooks = MagicMock()

    edition_services.register_extra_agent_hooks(hooks, context)

    assert hooks.mock_calls == []
    assert edition_services.extend_tool_grant(["a", "b"], context, MagicMock()) == ["a", "b"]
    assert edition_services.split_record_continuation(["a", "b"], 40, {}) is None


def edition_review(_ctx: Any, next_fn: Any) -> Any:  # noqa: ANN401
    return next_fn()


def test_an_edition_hook_runs_after_the_completion_gate_and_before_the_last_check(monkeypatch) -> None:
    seen: list[Any] = []

    def extra(hooks: Any, context: Any) -> None:  # noqa: ANN401
        seen.append(context)
        hooks.on(HookEvent.POST_MODEL).use(edition_review)

    monkeypatch.setattr(edition_services, "register_extra_agent_hooks", extra)
    context = _context()

    names = _post_model_names(PipesHubAgentFactory._build_hooks(context, transport_registry=MagicMock(), model_name="m"))

    assert seen == [context]
    assert names.index("completion_gate") < names.index("edition_review") < names.index("ask_in_text_recovery")


class TestContinuingSeveralRecords:
    @staticmethod
    def _tool() -> Any:  # noqa: ANN401
        from app.agents.agent_loop.hooks.citations import (
            CitationCollector,
            _FetchFullRecordTool,
        )

        context = _context()
        return _FetchFullRecordTool(CitationCollector(context), context)

    async def test_with_no_plan_it_is_refused(self, monkeypatch) -> None:
        monkeypatch.setattr(edition_services, "split_record_continuation", lambda *_: None)

        output = await self._tool().execute(record_ids=["a", "b"], start_block=40)

        assert output.success is False and "one record at a time" in output.error

    async def test_an_edition_plan_reads_each_group_from_its_own_block(self, monkeypatch) -> None:
        asked: list[tuple[list[str], int]] = []

        def plan(record_ids: list[str], start_block: int, _state: dict[str, Any]) -> list[tuple[list[str], int]]:
            asked.append((record_ids, start_block))
            return [(["a"], start_block), (["b"], 0)]

        monkeypatch.setattr(edition_services, "split_record_continuation", plan)
        with patch(
            "app.agents.actions.knowledge_graph.ops.fetch.execute_fetch_record",
            new=AsyncMock(return_value=(MagicMock(), None)),
        ) as execute:
            output = await self._tool().execute(record_ids=["a", "b"], start_block=40)

        assert asked == [(["a", "b"], 40)]
        reads = [(c.kwargs["record_ids"], c.kwargs["start_block"]) for c in execute.await_args_list]
        assert reads == [(["a"], 40), (["b"], 0)]
        assert output.success is True

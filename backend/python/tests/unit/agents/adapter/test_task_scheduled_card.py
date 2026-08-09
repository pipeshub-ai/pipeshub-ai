"""`task_scheduled_card_sse` (`app/agents/agent_loop/hooks/task_scheduled_card.py`)
-- emits a `workflow_created` CUSTOM event the moment a `task_manage(action=
"create")` call succeeds and produced at least one trigger, so the chat UI
can render an inline `WorkflowCard` without waiting for the agent's own text
response."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.agent_loop_lib.tools.base import ToolOutput
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.task_scheduled_card import task_scheduled_card_sse
from tests.unit.agents.adapter.support.hook_helpers import run_post_tool

_TOOL_PATH = "/toolsets/tasks/task_manage"

_CREATE_RESULT = {
    "task_id": "task-1",
    "title": "Weekly digest",
    "status": "active",
    "triggers": [{"trigger_id": "trig-1", "kind": "cron", "next_run_at": "2026-01-01T00:00:00Z"}],
}


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def write(self, event: dict) -> bool:
        self.events.append(event)
        return True


def _make_context(**overrides) -> AgentContext:
    context = AgentContext(
        org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock(),
    )
    for key, value in overrides.items():
        setattr(context, key, value)
    return context


class TestTaskScheduledCardSSE:
    async def test_emits_single_workflow_created_event_for_create_with_triggers(self) -> None:
        sink = _RecordingSink()
        context = _make_context(event_sink=sink, has_ui_client=True)

        middleware = task_scheduled_card_sse(context)
        await run_post_tool(
            middleware, ToolOutput(success=True, data=dict(_CREATE_RESULT)), tool_path=_TOOL_PATH,
        )

        # Exactly one event is emitted -- the workflow card. Emitting both a
        # legacy `scheduled_task` and a `workflow_created` event for the same
        # action produced two cards in the chat UI for one action.
        assert len(sink.events) == 1
        task_event = sink.events[0]
        assert task_event["event"] == "ui_card"
        card = task_event["data"]
        assert card["cardType"] == "workflow_created"
        assert card["payload"]["workflowId"] == "task-1"
        assert card["payload"]["title"] == "Weekly digest"
        assert card["payload"]["status"] == "active"
        assert card["payload"]["triggers"] == [
            {"triggerId": "trig-1", "kind": "cron", "nextRunAt": "2026-01-01T00:00:00Z"}
        ]
        assert card["actions"][0]["href"] == "/workflows?workflowId=task-1"

    async def test_no_emission_when_create_has_no_triggers(self) -> None:
        """A run_now-only task (no schedule) isn't a 'scheduled' task."""
        sink = _RecordingSink()
        context = _make_context(event_sink=sink, has_ui_client=True)

        middleware = task_scheduled_card_sse(context)
        data = {**_CREATE_RESULT, "triggers": []}
        await run_post_tool(middleware, ToolOutput(success=True, data=data), tool_path=_TOOL_PATH)

        assert sink.events == []

    async def test_no_emission_for_non_create_actions(self) -> None:
        """`pause`/`resume`/`cancel`/`run_now`/`update`/`promote_to_agent`
        responses never carry a `triggers` key -- the gate naturally excludes
        them all without needing to inspect the original `action` argument."""
        sink = _RecordingSink()
        context = _make_context(event_sink=sink, has_ui_client=True)

        middleware = task_scheduled_card_sse(context)
        await run_post_tool(
            middleware,
            ToolOutput(success=True, data={"task_id": "task-1", "status": "paused"}),
            tool_path=_TOOL_PATH,
        )

        assert sink.events == []

    async def test_no_emission_on_failure(self) -> None:
        sink = _RecordingSink()
        context = _make_context(event_sink=sink, has_ui_client=True)

        middleware = task_scheduled_card_sse(context)
        await run_post_tool(
            middleware, ToolOutput(success=False, error="boom"), tool_path=_TOOL_PATH,
        )

        assert sink.events == []

    async def test_no_emission_when_no_ui_client(self) -> None:
        sink = _RecordingSink()
        context = _make_context(event_sink=sink, has_ui_client=False)

        middleware = task_scheduled_card_sse(context)
        await run_post_tool(
            middleware, ToolOutput(success=True, data=dict(_CREATE_RESULT)), tool_path=_TOOL_PATH,
        )

        assert sink.events == []

    async def test_no_emission_for_unrelated_tool(self) -> None:
        sink = _RecordingSink()
        context = _make_context(event_sink=sink, has_ui_client=True)

        middleware = task_scheduled_card_sse(context)
        await run_post_tool(
            middleware, ToolOutput(success=True, data=dict(_CREATE_RESULT)), tool_path="/connectors/jira/search",
        )

        assert sink.events == []

    async def test_no_event_sink_is_a_safe_noop(self) -> None:
        context = _make_context(event_sink=None, has_ui_client=True)

        middleware = task_scheduled_card_sse(context)
        await run_post_tool(
            middleware, ToolOutput(success=True, data=dict(_CREATE_RESULT)), tool_path=_TOOL_PATH,
        )

    async def test_non_dict_payload_is_a_safe_noop(self) -> None:
        sink = _RecordingSink()
        context = _make_context(event_sink=sink, has_ui_client=True)

        middleware = task_scheduled_card_sse(context)
        await run_post_tool(
            middleware, ToolOutput(success=True, data="not json"), tool_path=_TOOL_PATH,
        )

        assert sink.events == []

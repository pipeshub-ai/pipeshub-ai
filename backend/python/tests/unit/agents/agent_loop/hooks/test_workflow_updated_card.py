"""`workflow_updated_card_sse` (`app/agents/agent_loop/hooks/workflow_updated_card.py`)
-- emits a `workflow_updated` ui_card the moment a `workflow_manage(action=
"update")` call regenerates code, so the chat UI can link to the new version
without waiting for the agent's own text response."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.agent_loop_lib.tools.base import ToolOutput
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.workflow_updated_card import workflow_updated_card_sse
from tests.unit.agents.adapter.support.hook_helpers import run_post_tool

_TOOL_PATH = "/toolsets/tasks/workflow_manage"

_REGENERATED_RESULT = {
    "regenerated": True,
    "workflow_id": "wf-1",
    "workflow_version_id": "ver-2",
    "updated_fields": ["instructions", "tool_names"],
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


async def _emit(
    data, *, tool_path: str = _TOOL_PATH, success: bool = True, has_ui_client: bool = True,
):
    sink = _RecordingSink()
    context = _make_context(event_sink=sink, has_ui_client=has_ui_client)
    middleware = workflow_updated_card_sse(context)
    await run_post_tool(
        middleware, ToolOutput(success=success, data=data), tool_path=tool_path,
    )
    return sink.events


class TestWorkflowUpdatedCardSse:
    async def test_emits_one_card_on_successful_regeneration(self) -> None:
        events = await _emit(dict(_REGENERATED_RESULT))

        assert len(events) == 1
        card = events[0]["data"]
        assert card["cardType"] == "workflow_updated"
        assert card["payload"]["workflowId"] == "wf-1"
        assert card["payload"]["versionId"] == "ver-2"
        assert card["actions"][0]["href"] == "/workflows?workflowId=wf-1"

    async def test_reads_a_json_encoded_tool_response(self) -> None:
        # Tools that serialize their output still need to produce a card.
        events = await _emit(json.dumps(_REGENERATED_RESULT))

        assert len(events) == 1
        assert events[0]["data"]["payload"]["workflowId"] == "wf-1"

    async def test_no_card_for_a_metadata_only_update(self) -> None:
        # Nothing was regenerated, so there is no diff worth linking to.
        events = await _emit(
            {"regenerated": False, "workflow_id": "wf-1", "updated_fields": ["title"]}
        )

        assert events == []

    async def test_no_card_when_the_tool_call_failed(self) -> None:
        events = await _emit(dict(_REGENERATED_RESULT), success=False)

        assert events == []

    async def test_no_card_for_another_tool(self) -> None:
        events = await _emit(dict(_REGENERATED_RESULT), tool_path="/toolsets/tasks/task_manage")

        assert events == []

    async def test_no_card_without_a_ui_client(self) -> None:
        # A headless/scheduled run has nobody to render the card.
        events = await _emit(dict(_REGENERATED_RESULT), has_ui_client=False)

        assert events == []

    async def test_no_card_when_the_workflow_id_is_missing(self) -> None:
        events = await _emit({"regenerated": True, "updated_fields": ["instructions"]})

        assert events == []

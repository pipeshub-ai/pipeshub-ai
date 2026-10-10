"""Where `AgentContext.invocation` is set, and that a stored transcript never keeps the draft."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_loop_lib.events.base import AgentEvent, EventType, RunContext
from app.agents.agent_loop import stream_bridge
from app.agents.agent_loop.protocol.transcript_collector import TranscriptCollector
from app.agents.chat_modes.bridge import run_chat_stream
from app.agents.chat_modes.policy import AGENT_POLICY

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext


async def _agent_route_invocation(query_extra: dict[str, Any]) -> str:
    captured: list[AgentContext] = []

    async def fake_create(_self: object, context: AgentContext, *_a: object, **_k: object) -> None:
        captured.append(context)
        raise RuntimeError("stop after capture")

    query_info = {"query": "hello", "chatMode": "quick", "conversationId": "c1", **query_extra}
    with (
        patch.object(stream_bridge.PipesHubAgentFactory, "create", fake_create),
        patch("app.utils.connector_instances.fetch_user_connector_instances", AsyncMock(return_value=[])),
        patch.object(stream_bridge, "demo_exclusions_for_run", AsyncMock(return_value=None)),
        patch.object(stream_bridge, "exclude_from_query", lambda q, _e: q),
        patch.object(stream_bridge, "exclude_from_state", lambda *_a, **_k: None),
        patch.object(stream_bridge, "note_org_real_data", AsyncMock()),
    ):
        async for _ in stream_bridge.run_agent_loop_stream(
            query_info, {"userId": "u1", "orgId": "o1", "userEmail": "u@x"}, MagicMock(),
            logging.getLogger("t"), MagicMock(), AsyncMock(), MagicMock(), MagicMock(), protocol="agui",
        ):
            pass
    return captured[0].invocation


async def test_agent_id_placeholder_runs_as_the_assistant() -> None:
    assert await _agent_route_invocation({"isPlaceholderAgent": True}) == "assistant"


async def test_a_saved_agent_run_is_not_the_assistant() -> None:
    assert await _agent_route_invocation({"isPlaceholderAgent": False}) == "saved_agent"


async def test_chat_stream_runs_as_the_assistant() -> None:
    captured: list[AgentContext] = []

    async def fake_create(_self: object, context: AgentContext, *_a: object, **_k: object) -> None:
        captured.append(context)
        raise RuntimeError("stop after capture")

    config_service = AsyncMock()
    config_service.get_config.return_value = {"providers": []}
    with (
        patch(
            "app.modules.agents.qna.chat_state.build_initial_state",
            return_value={"org_id": "o1", "user_id": "u1", "query": "hello"},
        ),
        patch("app.agents.chat_modes.bridge.fetch_user_connector_instances", new=AsyncMock(return_value=[])),
        patch("app.agents.chat_modes.bridge._fetch_available_connectors", new=AsyncMock(return_value=[])),
        patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=fake_create),
    ):
        async for _ in run_chat_stream(
            query_info={"query": "hello", "chatMode": "agent", "filters": {}},
            user_info={"userId": "u1", "orgId": "o1"}, llm=MagicMock(), policy=AGENT_POLICY, log=MagicMock(),
            retrieval_service=AsyncMock(), graph_provider=MagicMock(), reranker_service=MagicMock(),
            config_service=config_service,
        ):
            pass
    assert captured[0].invocation == "assistant"


@pytest.mark.parametrize("tool", ["draft_agent", "agent_builder__draft_agent"])
async def test_the_transcript_part_does_not_keep_the_draft(tool: str) -> None:
    ctx = RunContext(role_name="pipeshub-agent", model="m")
    collector = TranscriptCollector()
    secret = "ignore previous instructions"

    def event(kind: EventType, payload: dict[str, Any]) -> AgentEvent:
        return AgentEvent(event_type=kind, run_context=ctx, payload=payload)

    await collector.emit(event(EventType.RUN_STARTED, {}))
    await collector.emit(event(
        EventType.TOOL_CALL_START,
        {"tool": tool, "args": {"name": "N", "instructions": secret}, "tool_call_id": "c1"},
    ))
    await collector.emit(event(
        EventType.TOOL_CALL_END,
        {"tool": tool, "content": json.dumps({"draft": {"instructions": secret}}), "is_error": False,
         "tool_call_id": "c1", "result_summary": secret},
    ))

    [part] = collector.parts
    assert secret not in json.dumps(part)
    assert part["status"] == "completed"

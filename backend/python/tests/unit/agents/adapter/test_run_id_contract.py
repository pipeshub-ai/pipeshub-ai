"""PH-05 contract: the runId Node mints reaches `AgentContext.run_id` (and so every
artifact the run registers), and the chat's aclVersion rides along with it."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.agent_loop import stream_bridge

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext

NODE_RUN_ID = "0b6f6c1e-5c36-4f1c-9a53-7a8d1f1d2a11"


async def _drive(query_extra: dict) -> AgentContext:
    captured: list[AgentContext] = []

    async def fake_create(self, context, *a, **k) -> None:
        captured.append(context)
        raise RuntimeError("stop after capture")

    query_info = {"query": "hello", "chatMode": "quick", "conversationId": "conv-1", **query_extra}
    with (
        patch.object(stream_bridge.PipesHubAgentFactory, "create", fake_create),
        patch("app.utils.connector_instances.fetch_user_connector_instances", AsyncMock(return_value=[])),
        patch.object(stream_bridge, "demo_exclusions_for_run", AsyncMock(return_value=None)),
        patch.object(stream_bridge, "exclude_from_query", lambda q, _e: q),
        patch.object(stream_bridge, "exclude_from_state", lambda *_a, **_k: None),
        patch.object(stream_bridge, "note_org_real_data", AsyncMock()),
    ):
        gen = stream_bridge.run_agent_loop_stream(
            query_info, {"userId": "user-1", "orgId": "org-1", "userEmail": "u@x"}, MagicMock(),
            logging.getLogger("t"), MagicMock(), AsyncMock(), MagicMock(), MagicMock(), protocol="agui",
        )
        async for _ in gen:
            pass
    assert captured, "the agent factory was never reached"
    return captured[0]


@pytest.mark.asyncio
async def test_context_run_id_equals_the_node_minted_run_id() -> None:
    context = await _drive({"runId": NODE_RUN_ID, "aclVersion": 12})
    assert context.run_id == NODE_RUN_ID
    assert context.tool_state["run_id"] == NODE_RUN_ID
    assert context.acl_version == 12


@pytest.mark.asyncio
async def test_artifact_actor_carries_run_id_and_acl_version() -> None:
    from app.services.artifact_registry.models import Actor

    context = await _drive({"runId": NODE_RUN_ID, "aclVersion": 12})
    for actor in (Actor.from_context(context), Actor.from_state(context.tool_state)):
        assert (actor.run_id, actor.acl_version) == (NODE_RUN_ID, 12)


@pytest.mark.asyncio
async def test_a_generated_run_id_is_still_stamped_when_the_client_sent_none() -> None:
    context = await _drive({})
    assert context.run_id
    assert context.tool_state["run_id"] == context.run_id
    assert context.acl_version is None

"""A service-account agent's run marks its own task, so a tool that builds its artifact actor
by hand (the owner and the run id only) still writes the agent's stamp; any other run writes
none, and the mark never outlives the run."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.agent_loop import stream_bridge
from app.services.artifact_registry.models import Actor, written_by_service_agent


async def _stamp_inside_the_run(query_extra: dict) -> bool:
    seen: list[bool] = []

    async def fake_create(self: object, context: object, *_a: object, **_k: object) -> None:
        seen.append(written_by_service_agent(Actor(org_id="org-1", user_id="bob", run_id="r1")))
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
        async for _ in stream_bridge.run_agent_loop_stream(
            query_info, {"userId": "user-1", "orgId": "org-1", "userEmail": "u@x"}, MagicMock(),
            logging.getLogger("t"), MagicMock(), AsyncMock(), MagicMock(), MagicMock(), protocol="agui",
        ):
            pass
    assert seen, "the agent factory was never reached"
    return seen[0]


@pytest.mark.asyncio
async def test_a_service_agent_run_stamps_what_a_hand_built_actor_writes() -> None:
    assert await _stamp_inside_the_run({"is_service_account": True}) is True


@pytest.mark.asyncio
async def test_a_service_agent_run_as_the_person_asking_still_stamps() -> None:
    """The agent route runs a service agent as the caller (`is_service_account` off) and names the
    agent by its own flag; what that run writes is still the agent's."""
    stamped = await _stamp_inside_the_run(
        {"is_service_account": False, "service_agent_shared_databases_only": True},
    )
    assert stamped is True


@pytest.mark.asyncio
async def test_a_service_agent_run_opens_its_label_ledger_as_the_agents(monkeypatch) -> None:
    opened: list[bool] = []
    real = stream_bridge.open_ledger

    def spy(context: object, **kwargs: bool) -> object:
        opened.append(bool(kwargs.get("service_agent")))
        return real(context, **kwargs)

    monkeypatch.setattr(stream_bridge, "open_ledger", spy)
    await _stamp_inside_the_run({"is_service_account": False, "service_agent_shared_databases_only": True})
    assert opened == [True]


@pytest.mark.asyncio
async def test_any_other_run_writes_no_stamp() -> None:
    assert await _stamp_inside_the_run({}) is False


@pytest.mark.asyncio
async def test_the_mark_ends_with_the_run() -> None:
    await _stamp_inside_the_run({"is_service_account": True})
    assert written_by_service_agent(Actor(org_id="org-1", user_id="bob")) is False

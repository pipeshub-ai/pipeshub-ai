"""A harness turn of a service-account agent searches what main's agent route (``chat_stream``) lets it: the org's
team connectors, and a collection only when the agent is bound to a service account (``service_account_knowledge``).
Without this the durable path searched the agent's saved knowledge as its creator, personal connectors included."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.harness.assembly.context import RunContext
from app.harness.assembly.tool_environment import _turn_scope
from tests.unit.harness.assembly.world import agent_doc, claim, pinned_spec

TEAM = {"connectorId": "team-c", "type": "JIRA", "name": "Team Jira"}
PERSONAL = {"connectorId": "personal-c", "type": "JIRA", "name": "My Jira"}
COLLECTION = {"connectorId": "kb-c", "type": "KB", "name": "Handbook"}
OPEN = {"connectorId": "s3-c", "type": "S3", "name": "Bucket"}
INSTANCES = [
    {"_key": "team-c", "scope": "team", "type": "JIRA"},
    {"_key": "personal-c", "scope": "personal", "type": "JIRA"},
    {"_key": "s3-c", "scope": "team", "type": "S3"},
]
LICENSED: object = None  # no key: licensed until licence keys exist
UNLICENSED: object = {"allowed": False}


def _config(licence: object) -> MagicMock:
    config = MagicMock()
    if isinstance(licence, Exception):
        config.get_config = AsyncMock(side_effect=licence)
    else:
        config.get_config = AsyncMock(return_value=licence)
    return config


async def _turn(agent: dict[str, Any], licence: object = LICENSED, *, strict: bool = True) -> tuple[list[str], dict]:
    """The knowledge ``_turn_scope`` hands to main's filter resolution, and the filters it returns."""
    from app.connectors.core.base.data_processor import permission_sync_gate

    permission_sync_gate._licence_answers.clear()
    run = RunContext.from_claim(claim(pinned_spec(tool_ids=[], strict_scope=strict)))
    resolve = AsyncMock(return_value={"apps": [], "kb": ["kb-c"]})
    with patch("app.harness.assembly.scope.bounded_agent_knowledge", AsyncMock(side_effect=lambda k, *_: k)), \
            patch("app.api.routes.agent._resolve_turn_filters", resolve), \
            patch("app.modules.agents.knowledge_scope.fetch_user_connector_instances",
                  AsyncMock(return_value=INSTANCES)):
        filters, _ = await _turn_scope(
            run, agent, {"userId": "creator-user"}, MagicMock(), logging.getLogger("t"), _config(licence)
        )
    return [k["connectorId"] for k in resolve.await_args.kwargs["agent_knowledge"]], filters


async def _searched(agent: dict[str, Any]) -> list[str]:
    return (await _turn(agent))[0]


@pytest.mark.asyncio
async def test_an_unbound_service_account_agent_searches_team_connectors_only() -> None:
    agent = agent_doc(isServiceAccount=True, knowledge=[TEAM, PERSONAL, COLLECTION])
    assert await _searched(agent) == ["team-c"]


@pytest.mark.asyncio
async def test_a_bound_service_account_agent_also_keeps_its_collections() -> None:
    agent = agent_doc(isServiceAccount=True, serviceAccountUserId="sa-user", knowledge=[TEAM, PERSONAL, COLLECTION])
    assert await _searched(agent) == ["team-c", "kb-c"]


@pytest.mark.asyncio
async def test_an_ordinary_agent_keeps_its_knowledge() -> None:
    agent = agent_doc(knowledge=[TEAM, PERSONAL, COLLECTION])
    assert await _searched(agent) == ["team-c", "personal-c", "kb-c"]


@pytest.mark.asyncio
@pytest.mark.parametrize("licence", [UNLICENSED, ConnectionError("store down")], ids=["unlicensed", "unreadable"])
async def test_an_unlicensed_unbound_agent_keeps_open_sources_only_with_a_strict_scope(licence: object) -> None:
    """Without a licence (or when it cannot be read) an unbound agent searches as its creator, so a gated team
    connector is left out, and the scope stays strict even if the pin's was not."""
    agent = agent_doc(isServiceAccount=True, knowledge=[TEAM, OPEN])
    searched, filters = await _turn(agent, licence, strict=False)
    assert searched == ["s3-c"]
    assert filters["strictScope"] is True


@pytest.mark.asyncio
async def test_an_unlicensed_unbound_agent_whose_only_source_is_gated_searches_nothing() -> None:
    searched, filters = await _turn(agent_doc(isServiceAccount=True, knowledge=[TEAM]), UNLICENSED, strict=False)
    assert searched == []
    assert filters["strictScope"] is True


@pytest.mark.asyncio
async def test_an_unlicensed_bound_agent_keeps_its_team_connectors() -> None:
    """A bound agent searches as its own service account, whose grants decide what it reads."""
    agent = agent_doc(isServiceAccount=True, serviceAccountUserId="sa-user", knowledge=[TEAM, OPEN])
    searched, filters = await _turn(agent, UNLICENSED, strict=False)
    assert searched == ["team-c", "s3-c"]
    assert "strictScope" not in filters


async def _resolve_kwargs(agent: dict[str, Any], licence: object = LICENSED) -> dict[str, Any]:
    from app.connectors.core.base.data_processor import permission_sync_gate

    permission_sync_gate._licence_answers.clear()
    run = RunContext.from_claim(claim(pinned_spec(tool_ids=[])))
    resolve = AsyncMock(return_value={"apps": [], "kb": ["kb-c"]})
    with patch("app.harness.assembly.scope.bounded_agent_knowledge", AsyncMock(side_effect=lambda k, *_: k)), \
            patch("app.api.routes.agent._resolve_turn_filters", resolve), \
            patch("app.modules.agents.knowledge_scope.fetch_user_connector_instances",
                  AsyncMock(return_value=INSTANCES)):
        await _turn_scope(run, agent, {"userId": "creator-user"}, MagicMock(), logging.getLogger("t"), _config(licence))
    return resolve.await_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(("fields", "admitted"), [
    ({"isServiceAccount": True}, False),
    ({"isServiceAccount": True, "serviceAccountUserId": "sa-user"}, True),
    ({}, True),
])
async def test_an_unbound_service_account_agent_gets_no_project_collection_back(fields: dict, admitted: bool) -> None:
    """A dropped collection the caller has a role on would be put back (``admit_caller_project_collections``) and read
    as the creator; chat_stream passes the same flag."""
    kwargs = await _resolve_kwargs(agent_doc(knowledge=[TEAM, COLLECTION], **fields))
    assert kwargs["admit_project_collections"] is admitted

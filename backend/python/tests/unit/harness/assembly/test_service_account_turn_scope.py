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
INSTANCES = [{"_key": "team-c", "scope": "team", "type": "JIRA"}, {"_key": "personal-c", "scope": "personal", "type": "JIRA"}]


async def _searched(agent: dict[str, Any]) -> list[str]:
    """The knowledge ``_turn_scope`` hands to main's filter resolution."""
    run = RunContext.from_claim(claim(pinned_spec(tool_ids=[])))
    resolve = AsyncMock(return_value={"apps": [], "kb": ["kb-c"]})
    with patch("app.harness.assembly.scope.bounded_agent_knowledge", AsyncMock(side_effect=lambda k, *_: k)), \
            patch("app.api.routes.agent._resolve_turn_filters", resolve), \
            patch("app.modules.agents.knowledge_scope.fetch_user_connector_instances",
                  AsyncMock(return_value=INSTANCES)):
        await _turn_scope(run, agent, {"userId": "creator-user"}, MagicMock(), logging.getLogger("t"))
    return [k["connectorId"] for k in resolve.await_args.kwargs["agent_knowledge"]]


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
async def test_a_connector_limited_to_its_creator_is_not_searched_by_a_service_account_agent() -> None:
    """Every caller runs the agent: a connector limited to its creator stays out of a harness turn as it does out of
    chat_stream, even though the agent's pin lists it."""
    limited = {"connectorId": "limited-c", "type": "JIRA", "name": "Limited Jira"}
    org_wide = {"connectorId": "org-c", "type": "JIRA", "name": "Open-to-org Jira"}
    instances = [
        *INSTANCES,
        {"_key": "limited-c", "scope": "team", "type": "JIRA", "accessLimit": "CREATOR"},
        {"_key": "org-c", "scope": "team", "type": "JIRA", "accessLimit": "ORG"},
    ]
    with patch(f"{__name__}.INSTANCES", instances):
        searched = await _searched(agent_doc(isServiceAccount=True, knowledge=[TEAM, limited, org_wide]))
    assert searched == ["team-c", "org-c"]

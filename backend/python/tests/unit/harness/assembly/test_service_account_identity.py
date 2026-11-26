"""SF-5: a Slack service-account run acts as legacy's: the agent creator's
knowledge, the Slack user's name and email in the model's context."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.harness.assembly.context import RunContext
from app.harness.assembly.tool_environment import _identity
from tests.unit.harness.assembly.world import ORG, agent_doc, claim, pinned_spec

CREATOR = {"_key": "creator-key", "userId": "creator-user", "orgId": ORG, "email": "creator@corp.example", "fullName": "Cora Creator"}
SLACK_CALLER = {"email": "sam@slack.example", "displayName": "Sam from Slack"}


def _graph() -> MagicMock:
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value=dict(CREATOR))
    # The actor is Node's synthetic Slack identity: no user document.
    graph.get_user_by_user_id = AsyncMock(return_value=None)
    graph.check_agent_permission = AsyncMock(return_value=None)
    return graph


def _run(caller: dict[str, Any] | None) -> RunContext:
    return RunContext.from_claim(claim(pinned_spec(tool_ids=[]), caller=caller))


@pytest.mark.asyncio
async def test_the_slack_caller_names_the_user_and_the_creator_scopes_the_knowledge() -> None:
    run = _run(SLACK_CALLER)
    assert (run.caller_email, run.caller_display_name) == ("sam@slack.example", "Sam from Slack")
    graph = _graph()
    info, service_account = await _identity(
        run, agent_doc(isServiceAccount=True, createdBy="creator-key"), graph, logging.getLogger("t")
    )
    assert service_account is True
    assert info["userId"] == "creator-user"
    assert info["orgId"] == ORG
    assert (info["email"], info["userEmail"]) == ("sam@slack.example", "sam@slack.example")
    assert (info["fullName"], info["displayName"]) == ("Sam from Slack", "Sam from Slack")
    graph.check_agent_permission.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_a_caller_the_run_keeps_the_creator_as_legacy_does() -> None:
    info, _ = await _identity(
        _run(None), agent_doc(isServiceAccount=True, createdBy="creator-key"), _graph(), logging.getLogger("t")
    )
    assert (info["userId"], info["email"], info["fullName"]) == ("creator-user", "creator@corp.example", "Cora Creator")


@pytest.mark.asyncio
async def test_an_ordinary_agent_ignores_the_caller() -> None:
    graph = _graph()
    graph.get_user_by_user_id = AsyncMock(
        return_value={"_key": "ukey-1", "userId": "user-1", "email": "user@corp.example", "fullName": "Uma"}
    )
    graph.check_agent_permission = AsyncMock(return_value={"role": "READER"})
    info, service_account = await _identity(_run(SLACK_CALLER), agent_doc(), graph, logging.getLogger("t"))
    assert service_account is False
    assert (info["userEmail"], info["fullName"]) == ("user@corp.example", "Uma")


@pytest.mark.asyncio
async def test_the_creator_of_another_org_is_refused() -> None:
    from harness_core.driver.run_driver import RunRefused

    graph = _graph()
    graph.get_document = AsyncMock(return_value={**CREATOR, "orgId": "org-2"})
    with pytest.raises(RunRefused):
        await _identity(
            _run(SLACK_CALLER), agent_doc(isServiceAccount=True, createdBy="creator-key"), graph, logging.getLogger("t")
        )

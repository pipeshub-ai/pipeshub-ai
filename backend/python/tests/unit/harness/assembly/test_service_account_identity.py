"""A durable run acts as the person it was admitted for, whatever kind of agent it runs: never as the agent's
creator or bound account, and never for an actor who is not a live person."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.chat import build_chat_environment
from app.harness.assembly.context import RunContext
from app.harness.assembly.tool_environment import _identity, _turn_scope
from app.harness.credentials.tool_credentials import toolset_reference
from harness_core.driver.run_driver import RunRefused
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort
from tests.unit.harness.assembly import world as w
from tests.unit.harness.assembly.world import agent_doc, claim, pinned_spec

ACTOR = {"_key": "ukey-1", "userId": "user-1", "email": "user@corp.example", "fullName": "Uma"}
SLACK_CALLER = {"email": "sam@slack.example", "displayName": "Sam from Slack"}
SERVICE_AGENTS = [
    pytest.param({"isServiceAccount": True, "createdBy": "creator-key"}, id="unbound"),
    pytest.param(
        {"isServiceAccount": True, "createdBy": "creator-key", "serviceAccountUserId": "svc-1"}, id="bound"
    ),
]


class _Built(Exception):
    """Stops a build where its state is complete."""

    def __init__(self, *_: object, **__: object) -> None:
        super().__init__()


def _graph(actor: dict[str, Any] | None = ACTOR) -> MagicMock:
    graph = MagicMock()
    graph.get_document = AsyncMock(side_effect=AssertionError("the creator is never read"))
    graph.get_user_by_user_id = AsyncMock(return_value=actor)
    graph.check_agent_permission = AsyncMock(return_value={"role": "READER"})
    return graph


def _run(caller: dict[str, Any] | None = None) -> RunContext:
    return RunContext.from_claim(claim(pinned_spec(tool_ids=[]), caller=caller))


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", [pytest.param({}, id="ordinary"), *SERVICE_AGENTS])
async def test_every_agent_runs_as_the_person_and_ignores_the_caller_fields(agent: dict[str, Any]) -> None:
    graph = _graph()
    info = await _identity(_run(SLACK_CALLER), agent_doc(**agent), graph, logging.getLogger("t"))
    assert (info["userId"], info["userEmail"], info["fullName"]) == ("user-1", "user@corp.example", "Uma")
    graph.check_agent_permission.assert_awaited_once()
    assert graph.check_agent_permission.await_args.args[1] == "ukey-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", SERVICE_AGENTS)
async def test_a_service_agent_still_needs_the_persons_own_permission(agent: dict[str, Any]) -> None:
    graph = _graph()
    graph.check_agent_permission = AsyncMock(return_value=None)
    with pytest.raises(RunRefused) as refused:
        await _identity(_run(), agent_doc(**agent), graph, logging.getLogger("t"))
    assert refused.value.error_type == "agent_access_revoked"


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", [pytest.param({}, id="ordinary"), *SERVICE_AGENTS])
@pytest.mark.parametrize(
    "actor",
    [
        # Node's synthetic Slack identity had no user document; a run admitted for one before an upgrade stops here.
        pytest.param(None, id="unknown"),
        pytest.param({**ACTOR, "kind": "service"}, id="service-account"),
        pytest.param({**ACTOR, "isActive": False}, id="removed"),
        pytest.param({**ACTOR, "isDisabled": True}, id="disabled"),
    ],
)
async def test_an_actor_who_is_not_a_live_person_is_refused(
    agent: dict[str, Any], actor: dict[str, Any] | None
) -> None:
    with pytest.raises(RunRefused) as refused:
        await _identity(_run(), agent_doc(**agent), _graph(actor), logging.getLogger("t"))
    assert refused.value.error_type == "identity_unavailable"


@pytest.mark.asyncio
async def test_an_actor_who_cannot_be_read_fails_the_attempt_and_is_not_refused() -> None:
    # A refusal settles the run for good; a failed read must leave it to be assembled again.
    graph = _graph()
    graph.get_user_by_user_id = AsyncMock(side_effect=RuntimeError("graph down"))
    with pytest.raises(HTTPException) as failed:
        await _identity(_run(), agent_doc(isServiceAccount=True), graph, logging.getLogger("t"))
    assert failed.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent", "kept"),
    [
        pytest.param({}, {"team-1", "mine-1", "kb-1"}, id="ordinary"),
        pytest.param({"isServiceAccount": True}, {"team-1"}, id="unbound"),
        pytest.param({"isServiceAccount": True, "serviceAccountUserId": "svc-1"}, {"team-1", "kb-1"}, id="bound"),
    ],
)
async def test_a_service_agent_keeps_only_the_sources_shared_with_the_org(
    agent: dict[str, Any], kept: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    looked_up_as: list[str] = []

    async def instances(_graph: object, user_id: str, _org: str, _log: object) -> list[dict[str, Any]]:
        looked_up_as.append(user_id)
        return [
            {"_key": "team-1", "type": "Drive", "scope": "team"},
            {"_key": "mine-1", "type": "Drive", "scope": "personal"},
        ]

    async def unbounded(knowledge: list[dict[str, Any]], *_a: object) -> list[dict[str, Any]]:
        return knowledge

    seen: list[dict[str, Any]] = []

    async def filters(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        seen.extend(kwargs["agent_knowledge"])
        return {"apps": [], "kb": []}

    monkeypatch.setattr("app.modules.agents.knowledge_scope.fetch_user_connector_instances", instances)
    monkeypatch.setattr("app.harness.assembly.scope.bounded_agent_knowledge", unbounded)
    monkeypatch.setattr("app.api.routes.agent._resolve_turn_filters", filters)
    doc = agent_doc(**agent, knowledge=[
        {"connectorId": "team-1", "type": "Drive"},
        {"connectorId": "mine-1", "type": "Drive"},
        {"connectorId": "kb-1", "type": "KB"},
    ])
    await _turn_scope(_run(), doc, dict(ACTOR), _graph(), logging.getLogger("t"))
    assert {k["connectorId"] for k in seen} == kept
    assert set(looked_up_as) <= {"user-1"}


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", [pytest.param({}, id="ordinary"), *SERVICE_AGENTS])
async def test_every_agent_uses_the_persons_credentials(agent: dict[str, Any]) -> None:
    services = w.Services(agent_doc(**agent))
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    with w.deployment(), patch("app.agents.agent_loop.context.AgentContext.from_chat_state", side_effect=_Built) as built:
        with pytest.raises(_Built):
            await PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port)).build(
                claim(pinned_spec(tool_ids=[f"jira@{w.JIRA}/search_issues"]))
            )
    state = built.call_args.args[0]
    assert state["credential_owner"] == w.ACTOR
    assert state["toolset_configs"] == {w.JIRA: toolset_reference(w.JIRA, w.ACTOR)}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor",
    [{**ACTOR, "kind": "service"}, {**ACTOR, "isActive": False}, {**ACTOR, "isDisabled": True}, None],
    ids=["service", "removed", "disabled", "no-record"],
)
async def test_the_built_in_assistant_is_refused_an_actor_who_is_not_a_live_person(actor: object) -> None:
    services = w.Services()
    services.graph.get_user_by_user_id = (
        AsyncMock(side_effect=actor) if isinstance(actor, Exception) else AsyncMock(return_value=actor)
    )
    run = RunContext.from_claim(claim(pinned_spec(tool_ids=[]))).model_copy(update={"chat": MagicMock(), "agent": None})
    with pytest.raises(RunRefused) as refused:
        await build_chat_environment(run, services, MagicMock())
    assert refused.value.error_type == "identity_unavailable"


@pytest.mark.asyncio
async def test_the_built_in_assistant_fails_the_attempt_when_the_actor_cannot_be_read() -> None:
    # A refusal settles the run for good; a failed read must leave it to be assembled again.
    services = w.Services()
    services.graph.get_user_by_user_id = AsyncMock(side_effect=RuntimeError("graph down"))
    run = RunContext.from_claim(claim(pinned_spec(tool_ids=[]))).model_copy(update={"chat": MagicMock(), "agent": None})
    with pytest.raises(HTTPException) as failed:
        await build_chat_environment(run, services, MagicMock())
    assert failed.value.status_code == 503

"""AD-1: the run definition pins each granted skill by the digest of its content."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.edition_config  # noqa: F401  (edition seams load before any router)
from app.agents.agent_loop.skills.bundle_source import GraphSkillSource
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from app.agents.agent_loop.skills.manager_factory import (
    get_builtin_seeder,
    reset_builtin_reconcile_state,
)
from app.agents.run_definition import (
    AgentRunDefinition,
    RunDefinitionSkill,
    pin_skill_content,
)
from harness_core.skills import SkillRef, skill_bundle_digest
from tests.support.agent_versions import versions_beside
from tests.support.bound_org import bind_test_org
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    """``authMiddleware`` binds the caller's org (every caller here is in ``org-1``); these tests call the route
    functions directly."""
    yield from bind_test_org("org-1")



@pytest.fixture(autouse=True)
def _agent_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    """The routes' agent versions (OWD-14) beside this file's mocked graph."""
    versions_beside(monkeypatch, "app.api.routes.agent")


ORG = "org-1"
SKILL_MD = "---\nname: {name}\ndescription: Use when testing {marker}\n---\n\nStep 1. {marker}.\n"


async def _skill(graph: FakeGraphProvider, name: str, marker: str) -> str:
    store = GraphSkillStore(graph, ORG, "user-1")  # type: ignore[arg-type]
    content = SKILL_MD.format(name=name, marker=marker)
    if await store.exists(name):
        return (await store.update_skill(name, content)).version
    return (await store.create_skill(name, content, resources={"scripts/run.py": f"print({marker!r})"})).version


async def _digest(graph: FakeGraphProvider, name: str, version: str | None = None) -> str:
    files = await GraphSkillSource(graph).files(ORG, SkillRef(name, version))  # type: ignore[arg-type]
    assert files is not None
    return skill_bundle_digest(files)


def _definition(*skills: RunDefinitionSkill) -> AgentRunDefinition:
    return AgentRunDefinition(agentKey="agent-1", definitionVersion=1, skills=skills)


async def test_each_skill_is_pinned_by_the_content_of_its_granted_version() -> None:
    graph = FakeGraphProvider()
    first = await _skill(graph, "deploy", "one")
    first_digest = await _digest(graph, "deploy")
    _ = await _skill(graph, "deploy", "two")
    _ = await _skill(graph, "report", "one")
    definition = _definition(
        RunDefinitionSkill(name="deploy"),
        RunDefinitionSkill(name="deploy-old", version=None),
        RunDefinitionSkill(name="report"),
    )
    definition = definition.model_copy(
        update={"skills": (*definition.skills, RunDefinitionSkill(name="deploy", version=first))}
    )

    pinned = await pin_skill_content(definition, GraphSkillSource(graph), ORG)  # type: ignore[arg-type]

    assert [(s.name, s.bundle_digest) for s in pinned.skills] == [
        ("deploy", await _digest(graph, "deploy")),
        ("deploy-old", None),
        ("report", await _digest(graph, "report")),
        ("deploy", first_digest),
    ]
    assert first_digest != await _digest(graph, "deploy")


async def test_a_name_that_cannot_be_a_folder_keeps_no_digest() -> None:
    pinned = await pin_skill_content(
        _definition(RunDefinitionSkill(name="a/b")), GraphSkillSource(FakeGraphProvider()), ORG  # type: ignore[arg-type]
    )
    assert pinned.skills[0].bundle_digest is None


class _AgentGraph(FakeGraphProvider):
    def __init__(self, agent: dict[str, Any]) -> None:
        super().__init__()
        self.get_agent = AsyncMock(return_value=agent)
        self.check_agent_permission = AsyncMock(return_value={"role": "READER"})


async def test_the_route_answers_with_the_digests() -> None:
    from app.api.routes.agent import get_agent_run_definition

    # A fresh graph: this process must seed its builtins again, whatever an earlier test reconciled.
    reset_builtin_reconcile_state()
    graph = _AgentGraph({"_key": "agent-1", "skills": [{"name": "deploy"}, {"name": "missing"}]})
    _ = await _skill(graph, "deploy", "one")
    request = MagicMock()
    request.state.user = {"orgId": ORG, "userId": "u1", "scopes": ["agent:execute"]}
    request.query_params = {}
    services = {"graph_provider": graph, "config_service": AsyncMock(), "logger": MagicMock()}
    with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
         patch("app.api.routes.agent._get_user_context", return_value={"orgId": ORG, "userId": "u1", "isServiceAccount": False}), \
         patch("app.api.routes.agent.is_service_token", return_value=False), \
         patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}), \
         patch("app.api.routes.agent._enrich_agent_models", new_callable=AsyncMock), \
         patch("app.services.featureflag.platform_settings.is_actions_enabled", new_callable=AsyncMock, return_value=True), \
         patch("app.agents.mcp.service.is_mcp_enabled", new_callable=AsyncMock, return_value=False):
        response = await get_agent_run_definition(request, "agent-1")

    skills = json.loads(response.body)["definition"]["skills"]
    assert skills[:2] == [
        {"name": "deploy", "version": None, "bundleDigest": await _digest(graph, "deploy")},
        {"name": "missing", "version": None, "bundleDigest": None},
    ]
    # FIX-SKILLS: then the org's builtin packs the model may load, each by its digest.
    seeder = get_builtin_seeder()
    assert seeder is not None
    assert {s["name"] for s in skills[2:]} == {p.metadata.name for p in seeder.packs if p.metadata.invocation_model}
    assert all(s["bundleDigest"] for s in skills[2:])

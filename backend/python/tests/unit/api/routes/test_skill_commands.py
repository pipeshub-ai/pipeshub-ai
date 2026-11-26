"""SLASH1: a skill marked usable as a /command, through the real skills routes over a real store.

Covers the menu listing (`invocation=user`), expansion as the caller (`POST /{name}/expand`), the reserved engine
words, and that neither the agent's `skill_manage` nor the learning loop can make a skill user-invocable (D5).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from fastapi import HTTPException

from app.agent_loop_lib.modules.providers.skills.base import (
    SkillCandidate,
    SkillMetadata,
    SkillSource,
    is_advertised,
)
from app.agent_loop_lib.modules.providers.skills.loader import parse_skill_md
from app.agent_loop_lib.modules.providers.skills.manager import _candidate_to_skill_md
from app.agent_loop_lib.tools.builtin.data.skills import _render_skill_md
from app.api.routes.skills import (
    ExpandRequest,
    InvocationRequest,
    SkillWriteRequest,
    create_skill,
    disable_skill,
    expand_command,
    get_skill,
    list_skills,
    update_skill,
)
from tests.support.bound_org import bind_test_org
from tests.unit.api.routes.test_skills_custom_lifecycle import (
    _LifecycleGraphProvider,
    _request,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

TRIAGE = SkillWriteRequest(
    name="triage",
    description="Triage an incident",
    body="Look up incident $incident in Jira. Depth: $depth.",
    invocation=InvocationRequest(user=True, model=False),
    arguments=[
        {"name": "incident", "description": "Incident id", "required": True},
        {"name": "depth", "enum": ["quick", "full"], "default": "quick"},
    ],
)


@pytest.fixture
def graph() -> _LifecycleGraphProvider:
    g = _LifecycleGraphProvider()
    g.add_user("user-a", "user-a-key")
    g.add_user("user-b", "user-b-key")
    return g


async def _commands(req) -> list[dict]:
    return json.loads((await list_skills(req, invocation="user")).body)["skills"]


class TestMenu:
    async def test_only_user_invocable_active_skills_are_listed_with_their_owner(self, graph) -> None:
        req = _request("user-a", "org-1", graph)
        await create_skill(req, TRIAGE)
        await create_skill(req, SkillWriteRequest(name="model-only", description="d", body="b"))

        commands = await _commands(req)
        triage = next(c for c in commands if c["name"] == "triage")
        assert "model-only" not in {c["name"] for c in commands}
        assert triage["owner"] == "you"
        assert triage["invocation"] == {"user": True, "model": False}
        assert triage["arguments"][1] == {"name": "depth", "enum": ["quick", "full"], "default": "quick"}

        await disable_skill(req, "triage")
        assert "triage" not in {c["name"] for c in await _commands(req)}

    async def test_another_users_command_is_neither_listed_nor_expanded(self, graph) -> None:
        await create_skill(_request("user-a", "org-1", graph), TRIAGE)
        req_b = _request("user-b", "org-1", graph)
        assert "triage" not in {c["name"] for c in await _commands(req_b)}
        with pytest.raises(HTTPException) as exc:
            await expand_command(req_b, "triage", ExpandRequest(args="INC-1"))
        assert exc.value.status_code == 404
        assert exc.value.detail["code"] == "command_not_found"

    async def test_an_update_without_command_fields_keeps_them(self, graph) -> None:
        req = _request("user-a", "org-1", graph)
        await create_skill(req, TRIAGE)
        await update_skill(req, "triage", SkillWriteRequest(description="Triage", body="Check $incident."))
        skill = json.loads((await get_skill(req, "triage")).body)
        assert skill["invocation"] == {"user": True, "model": False}
        assert [a["name"] for a in skill["arguments"]] == ["incident", "depth"]


class TestExpand:
    async def test_expands_as_typed_and_names_the_version_it_used(self, graph) -> None:
        req = _request("user-a", "org-1", graph)
        await create_skill(req, TRIAGE)
        body = json.loads((await expand_command(req, "triage", ExpandRequest(args="INC-4211 full"))).body)
        assert body["expanded"] == "Look up incident INC-4211 in Jira. Depth: full."
        assert body["args"] == {"incident": "INC-4211", "depth": "full"}
        assert body["owner"] == "you"
        assert body["contentDigest"].startswith("sha256:")
        version = body["version"]

        await update_skill(req, "triage", SkillWriteRequest(description="Triage", body="New text $incident."))
        again = json.loads((await expand_command(req, "triage", ExpandRequest(args="INC-4211"))).body)
        assert again["version"] != version
        assert again["contentDigest"] != body["contentDigest"]

    async def test_a_missing_argument_is_refused_with_its_name(self, graph) -> None:
        req = _request("user-a", "org-1", graph)
        await create_skill(req, TRIAGE)
        with pytest.raises(HTTPException) as exc:
            await expand_command(req, "triage", ExpandRequest(args=""))
        assert exc.value.status_code == 422
        assert exc.value.detail == {
            "code": "command_argument_missing", "message": "Missing: incident.", "names": ["incident"],
        }

    async def test_a_skill_that_is_not_a_command_is_not_found(self, graph) -> None:
        req = _request("user-a", "org-1", graph)
        await create_skill(req, SkillWriteRequest(name="runbook", description="d", body="b"))
        with pytest.raises(HTTPException) as exc:
            await expand_command(req, "runbook", ExpandRequest())
        assert exc.value.detail["code"] == "command_not_found"


class TestAuthoringGuards:
    async def test_an_engine_word_cannot_be_a_command(self, graph) -> None:
        req = _request("user-a", "org-1", graph)
        with pytest.raises(HTTPException) as exc:
            await create_skill(req, TRIAGE.model_copy(update={"name": "compact"}))
        assert exc.value.status_code == 400
        # The same name as a model-only skill is still allowed.
        resp = await create_skill(req, SkillWriteRequest(name="compact", description="d", body="b"))
        assert resp.status_code == 201

    async def test_bad_argument_declarations_are_400(self, graph) -> None:
        req = _request("user-a", "org-1", graph)
        with pytest.raises(HTTPException) as exc:
            await create_skill(req, TRIAGE.model_copy(update={"arguments": [{"name": "Bad Name"}]}))
        assert exc.value.status_code == 400


class TestAgentCannotMakeACommand:
    def test_skill_manage_edit_drops_user_invocation(self) -> None:
        existing = SkillMetadata(name="triage", description="d", invocation_user=True, invocation_model=False)
        content = _render_skill_md("triage", "d", "body", None, None, None, existing=existing)
        assert parse_skill_md(content).metadata.invocation_user is False

    def test_learning_loop_candidates_are_never_user_invocable(self) -> None:
        candidate = SkillCandidate(
            candidate_id="c1", name="learned", description="d", body="b", created_at="2026-10-16T00:00:00Z",
        )
        metadata = parse_skill_md(_candidate_to_skill_md(candidate)).metadata
        assert metadata.source == SkillSource.AGENT_CREATED
        assert metadata.invocation_user is False

    def test_a_command_the_model_may_not_load_is_not_advertised(self) -> None:
        assert not is_advertised(SkillMetadata(name="t", description="d", invocation_user=True, invocation_model=False))
        assert is_advertised(SkillMetadata(name="t", description="d"))


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    """The auth middleware binds the verified token's org; these tests call the route functions directly."""
    yield from bind_test_org("org-1")

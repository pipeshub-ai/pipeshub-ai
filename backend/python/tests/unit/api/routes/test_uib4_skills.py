"""UI-B4: skills for the new interface, through the real router over the in-memory graph fake.

A proposal (learning-loop candidate) is decided by the person whose run learned it or an org admin, approved with
edits as one version, and names the agent that learned it; a save carries a change note into its version's history;
a skill gets the name people see without its id changing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import edition_config  # must load before the route modules
from app.agent_loop_lib.modules.providers.skills.base import SkillCandidate
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from app.api.routes import skills
from tests.support.bound_org import bound_like_auth
from tests.support.config_service import FakeConfigurationService
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

ORG = "org-1"
OWNER = "owner-1"
OTHER = "other-1"


class _UsersGraph(FakeGraphProvider):
    async def get_user_by_user_id(self, user_id: str, *, raise_on_error: bool = False) -> dict[str, Any]:
        return {"_key": f"key-{user_id}", "userId": user_id}


class _Container:
    def __init__(self, graph: FakeGraphProvider) -> None:
        self._graph = graph
        self._config = FakeConfigurationService()

    def config_service(self) -> FakeConfigurationService:
        return self._config

    async def graph_provider(self) -> FakeGraphProvider:
        return self._graph

    async def retrieval_service(self) -> None:
        return None

    def logger(self) -> Any:  # noqa: ANN401
        import logging

        return logging.getLogger("test")


def _client(graph: FakeGraphProvider, user_id: str) -> TestClient:
    app = FastAPI()
    app.container = _Container(graph)  # type: ignore[attr-defined]

    @app.middleware("http")
    async def _session(request: Request, call_next: Any) -> Any:  # noqa: ANN401
        request.state.user = {"userId": user_id, "orgId": ORG, "email": f"{user_id}@example.com"}
        with bound_like_auth(request.state.user):
            return await call_next(request)

    app.include_router(skills.router, prefix="/api/v1/skills")
    return TestClient(app)


def _admin(answer: bool) -> Any:  # noqa: ANN401
    return patch.object(edition_config, "check_user_is_admin", new=AsyncMock(return_value=answer))


async def _queue(graph: FakeGraphProvider, *, learner: str = f"key-{OWNER}", candidate_id: str = "cand-1") -> None:
    """A candidate as a run of ``learner``'s store queues it, learned by agent ``a7`` in run ``run-9``."""
    await GraphSkillStore(graph, ORG, learner).queue_candidate(  # type: ignore[arg-type]
        SkillCandidate(
            candidate_id=candidate_id,
            name="weekly-digest",
            description="Use when writing the weekly digest",
            body="Collect the week's merged changes, then summarise them.",
            created_at="2026-11-09T00:00:00+00:00",
            source_agent_key="a7",
            source_run_id="run-9",
        )
    )


async def _pending(graph: FakeGraphProvider) -> list[str]:
    return [c.candidate_id for c in await GraphSkillStore(graph, ORG, "x").get_pending_candidates()]  # type: ignore[arg-type]


# ---- who decides a proposal -------------------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_the_person_a_proposal_was_learned_for_decides_it_without_being_an_admin(action: str) -> None:
    graph = _UsersGraph()
    await _queue(graph)

    with _admin(False):
        response = _client(graph, OWNER).post(f"/api/v1/skills/candidates/cand-1/{action}")

    assert response.status_code == 200, response.text
    assert await _pending(graph) == []


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_another_member_cannot_decide_someone_elses_proposal(action: str) -> None:
    graph = _UsersGraph()
    await _queue(graph)

    with _admin(False):
        response = _client(graph, OTHER).post(f"/api/v1/skills/candidates/cand-1/{action}")

    assert response.status_code == 403
    assert await _pending(graph) == ["cand-1"]


async def test_an_admin_still_decides_any_proposal() -> None:
    graph = _UsersGraph()
    await _queue(graph)

    with _admin(True):
        response = _client(graph, OTHER).post("/api/v1/skills/candidates/cand-1/approve")

    assert response.status_code == 200, response.text
    assert await _pending(graph) == []


def _skill_doc(graph: FakeGraphProvider, name: str = "weekly-digest") -> dict[str, Any]:
    return graph._collections[skills.CollectionNames.AGENT_SKILLS.value][f"{ORG}_{name}"]


def _owners(graph: FakeGraphProvider, name: str = "weekly-digest") -> list[str]:
    return [
        e["from_id"] for edges in graph._edges.values() for e in edges
        if e.get("role") == "OWNER" and e.get("to_id") == f"{ORG}_{name}"
    ]


async def test_an_admin_approving_someone_elses_proposal_makes_it_the_learners_skill_not_the_admins() -> None:
    graph = _UsersGraph()
    await _queue(graph)

    with _admin(True):
        response = _client(graph, OTHER).post("/api/v1/skills/candidates/cand-1/approve")
    with _admin(False):
        learner_view = _client(graph, OWNER).get("/api/v1/skills/weekly-digest")

    assert response.status_code == 200, response.text
    doc = _skill_doc(graph)
    assert doc["createdBy"] == f"key-{OWNER}"
    assert _owners(graph) == [f"key-{OWNER}"]
    assert doc["approvedBy"] == OTHER
    assert isinstance(doc["approvedAtTimestamp"], int)
    assert learner_view.status_code == 200, learner_view.text
    assert learner_view.json()["approvedBy"] == OTHER


async def test_the_learner_approving_their_own_proposal_owns_it_and_is_its_approver() -> None:
    graph = _UsersGraph()
    await _queue(graph)

    with _admin(False):
        response = _client(graph, OWNER).post("/api/v1/skills/candidates/cand-1/approve")

    assert response.status_code == 200, response.text
    assert _skill_doc(graph)["createdBy"] == f"key-{OWNER}"
    assert _owners(graph) == [f"key-{OWNER}"]
    assert _skill_doc(graph)["approvedBy"] == OWNER


async def test_a_proposal_with_no_recorded_learner_is_the_approving_admins() -> None:
    graph = _UsersGraph()
    await graph.batch_upsert_nodes(
        [{"id": "legacy", "orgId": ORG, "candidateId": "legacy", "name": "old-one", "description": "Use when old",
          "body": "Do the old thing.", "createdAtTimestamp": 1}],
        skills.CollectionNames.AGENT_SKILL_CANDIDATES.value,
    )

    with _admin(True):
        response = _client(graph, OTHER).post("/api/v1/skills/candidates/legacy/approve")

    assert response.status_code == 200, response.text
    assert _skill_doc(graph, "old-one")["createdBy"] == f"key-{OTHER}"
    assert _skill_doc(graph, "old-one")["approvedBy"] == OTHER


async def test_a_proposal_no_one_recorded_as_its_learner_is_an_admins_alone() -> None:
    graph = _UsersGraph()
    await graph.batch_upsert_nodes(
        [{"id": "legacy", "orgId": ORG, "candidateId": "legacy", "name": "old-one", "description": "d", "body": "b",
          "createdAtTimestamp": 1}],
        skills.CollectionNames.AGENT_SKILL_CANDIDATES.value,
    )

    with _admin(False):
        refused = _client(graph, OWNER).post("/api/v1/skills/candidates/legacy/reject")
    with _admin(True):
        decided = _client(graph, OWNER).post("/api/v1/skills/candidates/legacy/reject")

    assert refused.status_code == 403
    assert decided.status_code == 200


async def test_the_pending_list_shows_a_member_only_their_own_proposals_and_who_learned_them() -> None:
    graph = _UsersGraph()
    await _queue(graph)
    await _queue(graph, learner=f"key-{OTHER}", candidate_id="cand-2")

    with _admin(False):
        mine = _client(graph, OWNER).get("/api/v1/skills/candidates/pending").json()["candidates"]
    with _admin(True):
        every = _client(graph, OWNER).get("/api/v1/skills/candidates/pending").json()["candidates"]

    assert [(c["candidate_id"], c["mine"]) for c in mine] == [("cand-1", True)]
    assert mine[0]["source_agent_key"] == "a7"
    assert mine[0]["source_run_id"] == "run-9"
    assert mine[0]["proposed_by"] == f"key-{OWNER}"
    assert sorted((c["candidate_id"], c["mine"]) for c in every) == [("cand-1", True), ("cand-2", False)]


# ---- approve with edits, one version ------------------------------------------------------------------------------


async def test_approving_with_edits_writes_one_version_holding_the_edits() -> None:
    graph = _UsersGraph()
    await _queue(graph)
    client = _client(graph, OWNER)

    with _admin(False):
        response = client.post(
            "/api/v1/skills/candidates/cand-1/approve",
            json={
                "name": "monday-digest",
                "description": "Use when writing Monday's digest",
                "body": "List the merged changes by team.",
                "tags": ["digest"],
                "changeNote": "Learned from a chat, tightened",
            },
        )
        skill = client.get("/api/v1/skills/monday-digest").json()
        versions = client.get("/api/v1/skills/monday-digest/versions").json()["versions"]

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "monday-digest"
    assert skill["description"] == "Use when writing Monday's digest"
    assert "List the merged changes by team." in skill["body"]
    assert skill["tags"] == ["digest"]
    assert skill["changeNote"] == "Learned from a chat, tightened"
    # The approval is the first version: nothing archived, no second write; the history is the current version.
    assert [(v["version"], v["current"]) for v in versions] == [("1.0.0", True)]
    assert skill["version"] == "1.0.0"
    assert await _pending(graph) == []


async def test_approving_without_a_body_keeps_the_proposal_as_it_was() -> None:
    graph = _UsersGraph()
    await _queue(graph)

    with _admin(False):
        response = _client(graph, OWNER).post("/api/v1/skills/candidates/cand-1/approve")

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "weekly-digest"


async def test_an_edit_to_an_invalid_name_is_refused_and_the_proposal_stays() -> None:
    graph = _UsersGraph()
    await _queue(graph)

    with _admin(False):
        response = _client(graph, OWNER).post("/api/v1/skills/candidates/cand-1/approve", json={"name": "Not A Name"})

    assert response.status_code in (400, 409, 422)
    assert await _pending(graph) == ["cand-1"]


async def test_an_unknown_edit_field_is_refused() -> None:
    graph = _UsersGraph()
    await _queue(graph)

    with _admin(False):
        response = _client(graph, OWNER).post("/api/v1/skills/candidates/cand-1/approve", json={"createdBy": "x"})

    assert response.status_code == 422
    assert await _pending(graph) == ["cand-1"]


# ---- change notes ---------------------------------------------------------------------------------------------


def _create(client: TestClient, name: str = "release-notes", note: str | None = "First draft") -> None:
    body: dict[str, Any] = {"name": name, "description": "Use when writing release notes", "body": "Group by area."}
    if note is not None:
        body["changeNote"] = note
    response = client.post("/api/v1/skills/", json=body)
    assert response.status_code == 201, response.text


async def test_each_saves_note_travels_with_its_version_into_history() -> None:
    graph = _UsersGraph()
    client = _client(graph, OWNER)
    _create(client)

    current = client.get("/api/v1/skills/release-notes").json()
    assert current["changeNote"] == "First draft"
    updated = client.put(
        "/api/v1/skills/release-notes",
        json={"description": "Use when writing release notes", "body": "Group by area, newest first.",
              "changeNote": "Order newest first"},
        headers={"If-Match": str(current["updatedAt"])},
    )
    assert updated.status_code == 200, updated.text
    patched = client.patch(
        "/api/v1/skills/release-notes/body",
        json={"old_string": "newest first", "new_string": "newest at the top", "changeNote": "Wording"},
    )
    assert patched.status_code == 200, patched.text

    skill = client.get("/api/v1/skills/release-notes").json()
    versions = client.get("/api/v1/skills/release-notes/versions").json()["versions"]
    assert skill["changeNote"] == "Wording"
    assert [(v["version"], v["summary"]) for v in versions] == [
        ("1.0.2", "Wording"), ("1.0.1", "Order newest first"), ("1.0.0", "First draft"),
    ]


async def test_the_history_lists_the_current_version_first_and_names_each_author() -> None:
    """UIADOPT2-FU-1: the route listed archived versions only and named authors by their stored id, so the new
    interface's history could name no one and had to read the current version from the skill itself."""
    graph = _UsersGraph()
    users = graph._col("users")
    users[f"key-{OWNER}"] = {"_key": f"key-{OWNER}", "userId": OWNER, "orgId": ORG, "fullName": "Olive Owner"}
    client = _client(graph, OWNER)
    _create(client)
    current = client.get("/api/v1/skills/release-notes").json()
    client.put(
        "/api/v1/skills/release-notes",
        json={"description": "Use when writing release notes", "body": "Group by area, newest first.", "changeNote": "Tighter"},
        headers={"If-Match": str(current["updatedAt"])},
    )

    versions = client.get("/api/v1/skills/release-notes/versions").json()["versions"]
    assert [(v["version"], v["current"], v["summary"]) for v in versions] == [
        ("1.0.1", True, "Tighter"), ("1.0.0", False, "First draft"),
    ]
    assert [(v["updatedByName"], v["updatedByFormer"]) for v in versions] == [("Olive Owner", False)] * 2
    assert all(v["createdAt"] for v in versions)

    del users[f"key-{OWNER}"]  # erased: the user reads back with no name
    gone = client.get("/api/v1/skills/release-notes/versions").json()["versions"]
    assert [(v["updatedByName"], v["updatedByFormer"]) for v in gone] == [(None, True)] * 2


async def test_the_history_of_an_unseen_skill_is_empty() -> None:
    client = _client(_UsersGraph(), OWNER)
    assert client.get("/api/v1/skills/no-such-skill/versions").json() == {"versions": []}


async def test_a_save_without_a_note_does_not_inherit_the_last_ones() -> None:
    graph = _UsersGraph()
    client = _client(graph, OWNER)
    _create(client)

    client.put("/api/v1/skills/release-notes", json={"description": "Use when writing release notes", "body": "New."})

    assert client.get("/api/v1/skills/release-notes").json()["changeNote"] is None


def test_a_change_note_is_at_most_a_line_or_two() -> None:
    client = _client(_UsersGraph(), OWNER)
    response = client.post(
        "/api/v1/skills/",
        json={"name": "x-skill", "description": "d", "body": "b", "changeNote": "n" * (skills.MAX_CHANGE_NOTE + 1)},
    )
    assert response.status_code == 422


# ---- rename -------------------------------------------------------------------------------------------------


async def test_renaming_changes_the_name_people_see_and_keeps_the_id() -> None:
    graph = _UsersGraph()
    client = _client(graph, OWNER)
    _create(client)
    before = client.get("/api/v1/skills/release-notes").json()

    response = client.post("/api/v1/skills/release-notes/rename", json={"displayName": "  Release   notes "})
    after = client.get("/api/v1/skills/release-notes").json()
    listed = {s["name"]: s for s in client.get("/api/v1/skills/").json()["skills"]}

    assert response.status_code == 200, response.text
    assert response.json() == {"name": "release-notes", "displayName": "Release notes", "changeNote": "First draft"}
    assert after["displayName"] == "Release notes"
    assert after["version"] == before["version"]  # a name is not content: no version
    assert listed["release-notes"]["displayName"] == "Release notes"


async def test_a_name_another_skill_shows_is_refused() -> None:
    graph = _UsersGraph()
    client = _client(graph, OWNER)
    _create(client)
    _create(_client(graph, OTHER), name="weekly-digest", note=None)
    _client(graph, OTHER).post("/api/v1/skills/weekly-digest/rename", json={"displayName": "Digest"})

    by_display = client.post("/api/v1/skills/release-notes/rename", json={"displayName": "digest"})
    by_id = client.post("/api/v1/skills/release-notes/rename", json={"displayName": "Weekly-Digest"})

    assert by_display.status_code == 409
    assert by_id.status_code == 409
    assert client.get("/api/v1/skills/release-notes").json()["displayName"] is None


async def test_a_blank_name_goes_back_to_the_id() -> None:
    graph = _UsersGraph()
    client = _client(graph, OWNER)
    _create(client)
    client.post("/api/v1/skills/release-notes/rename", json={"displayName": "Notes"})

    response = client.post("/api/v1/skills/release-notes/rename", json={"displayName": " "})

    assert response.status_code == 200
    assert client.get("/api/v1/skills/release-notes").json()["displayName"] is None


async def test_only_the_skills_owner_renames_it() -> None:
    graph = _UsersGraph()
    _create(_client(graph, OWNER))

    response = _client(graph, OTHER).post("/api/v1/skills/release-notes/rename", json={"displayName": "Mine now"})

    assert response.status_code in (403, 404)
    assert _client(graph, OWNER).get("/api/v1/skills/release-notes").json()["displayName"] is None


async def test_a_rename_on_a_stale_if_match_is_a_conflict() -> None:
    graph = _UsersGraph()
    client = _client(graph, OWNER)
    _create(client)

    response = client.post(
        "/api/v1/skills/release-notes/rename", json={"displayName": "Notes"}, headers={"If-Match": "1"},
    )

    assert response.status_code == 409

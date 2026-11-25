"""CPS SK-6/SK-7: reviewing a learning-loop candidate is an org admin's decision (or, since UI-B4, its learner's).

The skills router is mounted as the query service mounts it, over the in-memory graph fake; "admin" is the
edition's own answer (``app.edition_config.check_user_is_admin``, which asks Node for the caller's role).
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
MEMBER = "member-1"


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


def _client(graph: FakeGraphProvider) -> TestClient:
    app = FastAPI()
    app.container = _Container(graph)  # type: ignore[attr-defined]

    @app.middleware("http")
    async def _session(request: Request, call_next: Any) -> Any:  # noqa: ANN401
        request.state.user = {"userId": MEMBER, "orgId": ORG, "email": "member@example.com"}
        with bound_like_auth(request.state.user):
            return await call_next(request)

    app.include_router(skills.router, prefix="/api/v1/skills")
    return TestClient(app)


async def _queue(graph: FakeGraphProvider) -> None:
    await GraphSkillStore(graph, ORG, "someone").queue_candidate(  # type: ignore[arg-type]
        SkillCandidate(
            candidate_id="cand-1",
            name="weekly-digest",
            description="Use when writing the weekly digest",
            body="Collect the week's merged changes, then summarise them.",
            created_at="2026-10-09T00:00:00+00:00",
        )
    )


async def _pending(graph: FakeGraphProvider) -> list[str]:
    return [c.candidate_id for c in await GraphSkillStore(graph, ORG, "someone").get_pending_candidates()]  # type: ignore[arg-type]


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_sk6_a_member_with_skill_write_cannot_review_a_candidate(action: str) -> None:
    graph = _UsersGraph()
    await _queue(graph)

    with patch.object(edition_config, "check_user_is_admin", new=AsyncMock(return_value=False)):
        response = _client(graph).post(f"/api/v1/skills/candidates/cand-1/{action}")

    assert response.status_code == 403
    assert await _pending(graph) == ["cand-1"]


async def test_sk7_an_admin_approves_a_candidate_into_a_skill() -> None:
    graph = _UsersGraph()
    await _queue(graph)

    with patch.object(edition_config, "check_user_is_admin", new=AsyncMock(return_value=True)):
        response = _client(graph).post("/api/v1/skills/candidates/cand-1/approve")

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "weekly-digest"
    assert await _pending(graph) == []


async def test_sk7_an_admin_rejects_a_candidate() -> None:
    graph = _UsersGraph()
    await _queue(graph)

    with patch.object(edition_config, "check_user_is_admin", new=AsyncMock(return_value=True)):
        response = _client(graph).post("/api/v1/skills/candidates/cand-1/reject")

    assert response.status_code == 200
    assert await _pending(graph) == []


def test_sk7_every_candidate_route_that_decides_checks_the_decider_in_the_handler() -> None:
    """UI-B4: the learner (the person the run acted for) or an org admin decides; the check needs the candidate, so
    it is the handler's (``_require_decider``), not a route dependency. The two routes stay the only deciders."""
    import inspect

    deciding = {
        route.path: route.endpoint  # type: ignore[attr-defined]
        for route in skills.router.routes
        if route.path.startswith("/candidates/") and "POST" in route.methods  # type: ignore[attr-defined]
    }

    assert set(deciding) == {"/candidates/{candidate_id}/approve", "/candidates/{candidate_id}/reject"}
    assert all("_require_decider(" in inspect.getsource(endpoint) for endpoint in deciding.values())

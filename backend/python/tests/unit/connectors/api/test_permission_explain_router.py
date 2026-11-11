"""The admin explanation route: a non-admin gets the 404 of a route that is not there; an admin gets the service's answer."""

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from app.connectors.api import permission_explain_router as mod
from app.services.permissions.errors import ViewerScopesUnavailable
from app.services.permissions.explain import ContainerRef, ExplainNotFound, Explanation

URL = "/api/v1/connectors/conn-1/permissions/explain"


@pytest.fixture
def role() -> dict[str, str]:
    return {"role": "admin"}


@pytest.fixture
def graph() -> AsyncMock:
    g = AsyncMock()
    users = {"admin-1": {"id": "k-admin", "orgId": "org-1"}, "dana": {"id": "k-dana", "orgId": "org-1"}, "eve": {"id": "k-eve", "orgId": "org-2"}}
    g.get_user_by_user_id = AsyncMock(side_effect=lambda uid, **_: users.get(uid))
    return g


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    s = AsyncMock()
    s.explain = AsyncMock(return_value=Explanation(
        False, "restricted_container", ContainerRef(None), (ContainerRef("HR", "g1"), ContainerRef(None)), "abcd1234"))
    monkeypatch.setattr(mod, "_service", lambda request, provider: s)
    return s


@pytest.fixture
def client(graph: AsyncMock, role: dict[str, str]) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _session_user(request: Request, call_next: Callable) -> Response:
        request.state.user = {"userId": "admin-1", "orgId": "org-1", **role}
        return await call_next(request)

    app.include_router(mod.permission_explain_router)
    app.dependency_overrides[mod.get_graph_provider] = lambda: graph
    return TestClient(app, raise_server_exceptions=False)


def test_an_admin_gets_the_verdict_the_reason_and_the_path_with_restricted_areas_unnamed(client: TestClient, service: AsyncMock) -> None:
    r = client.get(URL, params={"user_id": "dana", "record_id": "rec-1"})
    assert r.status_code == 200
    assert r.json() == {
        "admitted": False, "code": "restricted_container", "container": {"name": None, "restricted": True},
        "path": [{"name": "HR", "restricted": False}, {"name": None, "restricted": True}], "reference": "abcd1234",
    }
    service.explain.assert_awaited_once_with("org-1", "k-dana", "rec-1", asker_key="k-admin", connector_id="conn-1")


@pytest.mark.parametrize("who", [{"role": "member"}, {"role": ""}, {"role": "owner-of-the-record"}])
def test_a_non_admin_gets_a_404_and_the_service_is_never_asked(client: TestClient, service: AsyncMock, role: dict[str, str], who: dict[str, str]) -> None:
    role.clear()
    role.update(who)
    r = client.get(URL, params={"user_id": "dana", "record_id": "rec-1"})
    assert (r.status_code, r.json()["detail"]) == (404, "Not found")
    service.explain.assert_not_awaited()


def test_a_person_of_another_organisation_or_unknown_is_a_404(client: TestClient, service: AsyncMock) -> None:
    for who in ("eve", "nobody"):
        assert client.get(URL, params={"user_id": who, "record_id": "rec-1"}).status_code == 404
    service.explain.assert_not_awaited()


def test_an_unknown_record_is_a_404(client: TestClient, service: AsyncMock) -> None:
    service.explain.side_effect = ExplainNotFound("rec-1")
    assert client.get(URL, params={"user_id": "dana", "record_id": "rec-1"}).status_code == 404


def test_a_graph_that_cannot_answer_is_a_503_never_a_verdict(client: TestClient, service: AsyncMock) -> None:
    service.explain.side_effect = ViewerScopesUnavailable("graph_error")
    r = client.get(URL, params={"user_id": "dana", "record_id": "rec-1"})
    assert r.status_code == 503
    assert "graph_error" not in r.text


def test_the_person_and_the_record_are_required(client: TestClient, service: AsyncMock) -> None:
    assert client.get(URL, params={"user_id": "dana"}).status_code == 422
    assert client.get(URL, params={"record_id": "rec-1"}).status_code == 422

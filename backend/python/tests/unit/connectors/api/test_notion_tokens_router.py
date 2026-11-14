"""Linking a Notion personal access token: each user manages only their own, the token never comes back, an
admin sees who linked one, and anything but a Notion team connector the caller may read is not found."""

from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from app.connectors.api import notion_tokens_router as mod
from app.connectors.core.registry.connector_registry import ConnectorRegistry
from app.connectors.sources.notion import personal_tokens
from app.core import org_context
from tests.unit.connectors.sources.test_notion_personal_access import _Config

URL = "/api/v1/connectors/conn-1/notion/personal-token"
ADMIN_URL = "/api/v1/connectors/conn-1/notion/personal-tokens"
SECRET = "ntn_pat_do_not_echo"


@pytest.fixture(autouse=True)
def _org() -> Iterator[None]:
    with org_context.for_org("org-1", job="test"):
        yield


@pytest.fixture
def config() -> _Config:
    return _Config()


@pytest.fixture
def instance() -> dict[str, Any]:
    # Created by someone else: the registry's read gate would refuse a member here.
    return {"_key": "conn-1", "type": "Notion", "scope": "team", "orgId": "org-1", "createdBy": "admin"}


def _client(config: _Config, instance: dict[str, Any] | None, user: dict[str, str],
            person: dict[str, Any] | None = None) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _session_user(request: Request, call_next: Callable) -> Response:
        request.state.user = {"orgId": "org-1", **user}
        return await call_next(request)

    registry = ConnectorRegistry.__new__(ConnectorRegistry)
    registry.logger = MagicMock()
    registry._get_connector_instance_from_db = AsyncMock(return_value=instance)
    registry._org_id_from_edge = AsyncMock(return_value=None)
    app.state.connector_registry = registry
    app.state.graph_provider = MagicMock(get_user_by_user_id=AsyncMock(
        return_value=person if person is not None else {"userId": user.get("userId"), "orgId": "org-1", "isActive": True}))
    app.container = MagicMock(config_service=lambda: config)
    app.include_router(mod.notion_tokens_router)
    return TestClient(app, raise_server_exceptions=False)


def test_a_user_links_replaces_reads_and_removes_their_own_token_which_never_comes_back(config: _Config, instance: dict) -> None:
    client = _client(config, instance, {"userId": "u1", "role": "member"})
    assert client.get(URL).json() == {"status": "NOT_LINKED"}
    linked = client.put(URL, json={"token": SECRET})
    assert linked.status_code == 200 and linked.json()["status"] == personal_tokens.LINKED
    assert SECRET not in linked.text and SECRET not in client.get(URL).text
    assert config.values[personal_tokens.token_path("conn-1", "u1")]["token"] == SECRET
    client.put(URL, json={"token": SECRET + "2"})
    assert config.values[personal_tokens.token_path("conn-1", "u1")]["token"] == SECRET + "2"
    assert client.delete(URL).json() == {"status": "NOT_LINKED"}
    assert personal_tokens.token_path("conn-1", "u1") not in config.values


@pytest.mark.parametrize("body", [{"token": ""}, {"token": "ntn pat"}, {}, {"token": "x" * 513}])
def test_a_malformed_token_is_refused(config: _Config, instance: dict, body: dict) -> None:
    r = _client(config, instance, {"userId": "u1"}).put(URL, json=body)
    assert r.status_code in (400, 422) and not config.values


@pytest.mark.parametrize("found", [
    None,
    {"type": "Notion", "scope": "personal", "orgId": "org-1"},
    {"type": "Jira", "scope": "team", "orgId": "org-1"},
    {"type": "Notion", "scope": "team", "orgId": "org-2"},
])
def test_anything_but_a_notion_team_connector_of_the_callers_org_is_not_found(config: _Config, found: dict | None) -> None:
    client = _client(config, found, {"userId": "u1", "role": "admin"})
    for r in (client.get(URL), client.put(URL, json={"token": SECRET}), client.delete(URL), client.get(ADMIN_URL)):
        assert r.status_code == 404
    assert not config.values


def test_an_admin_sees_who_linked_a_token_and_its_state_but_no_token(config: _Config, instance: dict) -> None:
    _client(config, instance, {"userId": "u1"}).put(URL, json={"token": SECRET})
    config.values[personal_tokens.token_path("conn-1", "u2")] = {"status": personal_tokens.FAILED, "reason": "Notion no longer accepts the token"}
    r = _client(config, instance, {"userId": "admin", "role": "admin"}).get(ADMIN_URL)
    assert r.status_code == 200 and SECRET not in r.text
    assert [(u["userId"], u["status"]) for u in r.json()["users"]] == [("u1", personal_tokens.LINKED), ("u2", personal_tokens.FAILED)]


def test_a_member_cannot_list_who_linked_a_token(config: _Config, instance: dict) -> None:
    assert _client(config, instance, {"userId": "u1", "role": "member"}).get(ADMIN_URL).status_code == 404


@pytest.mark.parametrize("person", [
    {"userId": "u1", "orgId": "org-1", "isActive": False},
    {},
])
def test_only_an_active_member_of_the_org_may_link(config: _Config, instance: dict, person: dict) -> None:
    client = _client(config, instance, {"userId": "u1", "role": "member"}, person=person)
    assert client.put(URL, json={"token": SECRET}).status_code == 404 and not config.values


def test_a_remove_the_store_did_not_do_is_reported(config: _Config, instance: dict) -> None:
    client = _client(config, instance, {"userId": "u1"})
    client.put(URL, json={"token": SECRET})
    config.delete_config = AsyncMock(return_value=False)  # the store kept it
    assert client.delete(URL).status_code == 503

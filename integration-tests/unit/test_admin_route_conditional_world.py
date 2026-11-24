"""The conditional-admin test world keeps its books: nothing is made twice,
a failure is remembered, and everything made is undone.

``helper/admin_route_conditional_world.py`` creates real things on a shared
stack, so its bookkeeping is checked here without one: each group's maker is
replaced with a stand-in and nothing is sent anywhere.
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import Any

import pytest

import helper.admin_route_conditional_world as world_module
from helper.admin_route_conditional_world import CREATOR, Reply, World, oauth_state
from helper.connector_service import CONNECTOR_URL_ENV, connector_service_url

pytestmark = pytest.mark.unit


class _Person:
    token = "member-token"
    user_id = "member-id"


class _CountingWorld(World):
    def __init__(self) -> None:
        super().__init__(
            gateway_url="http://gateway.test:3000/",
            admin_token=lambda: "admin-token",
            creator=_Person(),
            member=_Person(),
            settings_client=None,
        )
        self.made: list[str] = []

    def _build_connectors(self) -> dict[str, str]:
        self.made.append("connectors")
        return {"team": "team-id", "personal": "personal-id"}

    def _build_mcp(self) -> dict[str, str]:
        self.made.append("mcp")
        raise RuntimeError("MCP could not be switched on")

    def _build_skills(self) -> dict[str, str]:
        self.made.append("skills")
        pytest.skip("no skills here")

    def _build_oauth(self) -> dict[str, str]:
        return {"oauth_config": "only-one-key"}


def test_a_group_is_made_once_and_only_when_asked_for() -> None:
    world = _CountingWorld()
    assert world.made == []
    assert (world.value("team"), world.value("personal"), world.value("team")) == (
        "team-id", "personal-id", "team-id",
    )
    assert world.made == ["connectors"]


def test_a_group_that_could_not_be_made_is_not_tried_again() -> None:
    world = _CountingWorld()
    for key in ("shared_mcp", "own_mcp"):
        with pytest.raises(RuntimeError, match="MCP could not be switched on"):
            world.value(key)
    assert world.made == ["mcp"]


def test_a_group_that_skipped_skips_every_case_that_needs_it() -> None:
    world = _CountingWorld()
    for key in ("builtin_skill", "custom_skill"):
        with pytest.raises(pytest.skip.Exception, match="no skills here"):
            world.value(key)
    assert world.made == ["skills"]


def test_a_group_must_make_exactly_the_keys_the_table_names() -> None:
    with pytest.raises(AssertionError, match="oauth"):
        _CountingWorld().value("oauth_config")


def test_a_key_nothing_makes_is_refused() -> None:
    with pytest.raises(KeyError):
        _CountingWorld().value("no_such_key")


def test_closing_undoes_the_newest_first_and_reports_what_failed() -> None:
    world = _CountingWorld()
    undone: list[str] = []

    def fails() -> None:
        undone.append("second")
        raise RuntimeError("still in use")

    world._later("the first thing", lambda: undone.append("first"))
    world._later("the second thing", fails)
    world._later("the third thing", lambda: undone.append("third"))

    with pytest.raises(RuntimeError, match="the second thing: still in use"):
        world.close()
    assert undone == ["third", "second", "first"]
    # A second close has nothing left to undo, so nothing is undone twice.
    world.close()
    assert undone == ["third", "second", "first"]


@pytest.mark.parametrize(("found", "deletions"), [(200, 1), (404, 0)])
def test_a_personal_toolset_is_deleted_by_its_owner_only_while_it_is_there(
    monkeypatch: pytest.MonkeyPatch, found: int, deletions: int,
) -> None:
    # The owner's own delete case removes it first, and deleting it again is a 403, not a 404.
    world = _CountingWorld()
    looked: list[tuple[str, str, str]] = []
    deleted: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        world_module, "send",
        lambda method, url, token, **_: looked.append((method, url, token)) or Reply(found, ""),
    )
    monkeypatch.setattr(
        world, "_json", lambda caller, method, path, what, **_: deleted.append((caller, method, path)) or {},
    )

    world._delete_personal_toolset("toolset-1", CREATOR)

    assert looked == [("GET", "http://gateway.test:3000/api/v1/toolsets/instances/toolset-1", "member-token")]
    assert deleted == [(CREATOR, "DELETE", "/api/v1/toolsets/instances/toolset-1")] * deletions


def test_a_personal_toolset_that_cannot_be_looked_up_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _CountingWorld()
    monkeypatch.setattr(world_module, "send", lambda *_, **__: Reply(403, "refused"))
    with pytest.raises(RuntimeError, match="HTTP 403: refused"):
        world._delete_personal_toolset("toolset-1", CREATOR)


def _limited_world(monkeypatch: pytest.MonkeyPatch, stored_model: str | None) -> tuple[World, list[tuple[Any, ...]]]:
    """A world whose HTTP calls and graph processor are stand-ins that log what was asked of them."""
    world = _CountingWorld()
    done: list[tuple[Any, ...]] = []

    async def get_document(key: str, collection: str) -> dict[str, Any]:
        return {"_key": key, "permissionModel": stored_model}

    async def set_access_limit(connector_id: str, limit: Any) -> None:
        done.append(("limit", connector_id, limit))

    async def clear_access_limit(connector_id: str, model: Any) -> None:
        done.append(("lift", connector_id, model))

    processor = SimpleNamespace(
        data_store_provider=SimpleNamespace(graph_provider=SimpleNamespace(get_document=get_document)),
        set_access_limit=set_access_limit, clear_access_limit=clear_access_limit,
    )

    def http(caller: str, method: str, path: str, what: str, **kw: Any) -> dict[str, Any]:
        done.append((method, path, kw.get("json")))
        return {"agent": {"_key": "agent-1"}}

    monkeypatch.setattr(world, "_on_the_graph", lambda work: asyncio.run(work(processor)))
    monkeypatch.setattr(world, "_create_connector", lambda *_: ("connector-1", "it-cond-connector-1"))
    monkeypatch.setattr(world, "_delete_connector", lambda connector_id, _: done.append(("delete", connector_id)))
    monkeypatch.setattr(world, "_json", http)
    return world, done


def test_the_limited_group_lifts_the_limit_first_and_puts_the_model_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config.constants.arangodb import AccessLimit, PermissionModel

    world, done = _limited_world(monkeypatch, PermissionModel.RECORD_LEVEL.value)
    assert world.value("limited_agent") == "agent-1"
    assert world.value("limited_connector_name") == "it-cond-connector-1"
    create, limit = done
    assert create[:2] == ("POST", "/api/v1/agents/create")
    assert create[2]["shareWithOrg"] is True
    assert create[2]["knowledge"] == [{"connectorId": "connector-1", "filters": {}}]
    assert limit == ("limit", "connector-1", AccessLimit.CREATOR)

    world.close()
    assert done[2:] == [
        ("lift", "connector-1", PermissionModel.RECORD_LEVEL),
        ("DELETE", "/api/v1/agents/agent-1", None),
        ("delete", "connector-1"),
    ]


def test_a_connector_with_no_model_to_put_back_is_not_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    world, done = _limited_world(monkeypatch, None)
    with pytest.raises(RuntimeError, match="no permission model to put back"):
        world.value("limited_connector")
    assert not any(step[0] == "limit" for step in done)


def test_the_oauth_state_carries_the_connector_id() -> None:
    decoded = json.loads(base64.urlsafe_b64decode(oauth_state("connector-1")))
    assert decoded["connector_id"] == "connector-1" and decoded["state"]


def test_the_connector_service_is_port_8088_beside_the_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONNECTOR_URL_ENV, raising=False)
    assert connector_service_url("https://stack.test:3000/base") == "https://stack.test:8088"
    monkeypatch.setenv(CONNECTOR_URL_ENV, "http://connectors.test:9000/")
    assert connector_service_url("https://stack.test:3000") == "http://connectors.test:9000"

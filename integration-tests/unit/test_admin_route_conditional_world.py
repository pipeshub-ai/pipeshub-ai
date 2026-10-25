"""The conditional-admin test world keeps its books: nothing is made twice,
a failure is remembered, and everything made is undone.

``helper/admin_route_conditional_world.py`` creates real things on a shared
stack, so its bookkeeping is checked here without one: each group's maker is
replaced with a stand-in and nothing is sent anywhere.
"""

from __future__ import annotations

import base64
import json

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


def test_the_oauth_state_carries_the_connector_id() -> None:
    decoded = json.loads(base64.urlsafe_b64decode(oauth_state("connector-1")))
    assert decoded["connector_id"] == "connector-1" and decoded["state"]


def test_the_connector_service_is_port_8088_beside_the_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONNECTOR_URL_ENV, raising=False)
    assert connector_service_url("https://stack.test:3000/base") == "https://stack.test:8088"
    monkeypatch.setenv(CONNECTOR_URL_ENV, "http://connectors.test:9000/")
    assert connector_service_url("https://stack.test:3000") == "http://connectors.test:9000"

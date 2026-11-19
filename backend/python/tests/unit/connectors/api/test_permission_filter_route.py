"""PATCH /connectors/{id}/permissions/filter: admin only (the 404 of the status route for anyone else); `scopes` is refused when the deployment
cannot search with the scope filter; `legacy` and `inherit` are always written."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.connectors.api import router as r
from app.core import org_context
from app.services.permissions.currency import switched_connectors
from app.services.permissions.mode import ENV_ACL_FILTER_MODE


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_ACL_FILTER_MODE, "shadow")
    monkeypatch.setenv("VECTOR_DB_TYPE", "pgvector")


@pytest.fixture(autouse=True)
def _org() -> object:
    with org_context.for_org("o1", job="test"):
        yield


def _req(role: str = "admin", *, belongs: bool = True) -> MagicMock:
    req = MagicMock()
    req.state.user = {"userId": "u1", "orgId": "o1", "role": role}
    req.app.state.connector_registry.get_connector_instance = AsyncMock(return_value={"id": "c1"} if belongs else None)
    return req


def _graph(app: dict | None, *, updated: bool = True) -> MagicMock:
    g = MagicMock()
    g.get_document = AsyncMock(return_value=app)
    g.update_node = AsyncMock(return_value=updated)
    return g


async def _patch(mode: str, *, role: str = "admin", app: dict | None = None, belongs: bool = True, g: MagicMock | None = None) -> dict:
    g = g or _graph({"status": "IDLE"} if app is None else app)
    return await r.set_permission_filter("c1", r.PermissionFilterRequest(mode=mode), _req(role, belongs=belongs), g)


@pytest.mark.parametrize(("role", "belongs"), [("member", True), ("admin", False)], ids=["non-admin", "other organisation"])
async def test_anyone_but_an_admin_of_the_connector_gets_404_and_nothing_is_written(role: str, belongs: bool) -> None:
    g = _graph({"status": "IDLE"})
    with pytest.raises(HTTPException) as e:
        await _patch("scopes", role=role, belongs=belongs, g=g)
    assert e.value.status_code == 404
    g.update_node.assert_not_called()


@pytest.mark.parametrize(("env", "store", "blocker"), [("legacy", "pgvector", "serving_off"), ("shadow", "qdrant", "store_unsupported")])
async def test_scopes_is_refused_with_the_blocker_when_the_deployment_cannot_serve_it(monkeypatch: pytest.MonkeyPatch, env: str, store: str, blocker: str) -> None:
    monkeypatch.setenv(ENV_ACL_FILTER_MODE, env)
    monkeypatch.setenv("VECTOR_DB_TYPE", store)
    g = _graph({"status": "IDLE"})
    with pytest.raises(HTTPException) as e:
        await _patch("scopes", g=g)
    assert e.value.status_code == 409 and e.value.detail == {"error": "not_switchable", "blockers": [blocker]}
    g.update_node.assert_not_called()


@pytest.mark.parametrize("mode", ["legacy", "inherit"])
async def test_a_rollback_is_written_even_when_nothing_can_be_served(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv(ENV_ACL_FILTER_MODE, "legacy")
    monkeypatch.setenv("VECTOR_DB_TYPE", "qdrant")
    g = _graph({"status": "IDLE", "aclFilterMode": "scopes"})
    out = await _patch(mode, g=g)
    g.update_node.assert_awaited_once_with("c1", "apps", {"aclFilterMode": mode})
    assert out == {"mode": mode, "effective": "legacy", "blockers": ["serving_off", "store_unsupported"]}


async def test_scopes_is_written_and_reads_legacy_until_the_connector_is_served() -> None:
    g = _graph({"status": "IDLE"})
    out = await _patch("scopes", g=g)
    g.update_node.assert_awaited_once_with("c1", "apps", {"aclFilterMode": "scopes"})
    assert out == {"mode": "scopes", "effective": "legacy", "blockers": []}


async def test_a_connector_gone_between_the_read_and_the_write_is_404() -> None:
    with pytest.raises(HTTPException) as e:
        await _patch("legacy", g=_graph({"status": "IDLE"}, updated=False))
    assert e.value.status_code == 404


async def test_an_unknown_mode_is_refused_by_the_request_model() -> None:
    with pytest.raises(ValueError):
        r.PermissionFilterRequest(mode="shadow")


class Apps:
    """The app documents as a store holds them: each write lands whole; ``pause`` holds a write of that mode until released, to order requests."""

    def __init__(self, mode: str | None = None) -> None:
        self.docs: dict[str, dict] = {"c1": {"_key": "c1", "org": "o1", "status": "IDLE", **({"aclFilterMode": mode} if mode else {})}}
        self.gates: dict[str, asyncio.Event] = {}

    def graph(self) -> MagicMock:
        g = MagicMock()
        g.get_document = AsyncMock(side_effect=lambda key, coll, **k: dict(self.docs[key]) if key in self.docs else None)

        async def update(key: str, coll: str, fields: dict, **k: object) -> bool:
            if gate := self.gates.get(fields.get("aclFilterMode")):
                await gate.wait()
            if key not in self.docs:
                return False
            self.docs[key].update(fields)
            return True

        g.update_node = AsyncMock(side_effect=update)
        g.get_org_apps = AsyncMock(side_effect=lambda org, **k: [d for d in self.docs.values() if d.get("org") == org])
        return g


@pytest.mark.parametrize("first", ["scopes", "legacy"])
async def test_two_concurrent_switches_end_with_the_plan_reading_whatever_the_document_says(first: str) -> None:
    """There is no second record to keep in step: whichever write lands last, the switched connectors are read from that document."""
    second = "legacy" if first == "scopes" else "scopes"
    apps = Apps()
    g = apps.graph()
    apps.gates[first] = asyncio.Event()
    a = asyncio.create_task(r.set_permission_filter("c1", r.PermissionFilterRequest(mode=first), _req(), g))
    await asyncio.sleep(0)
    await r.set_permission_filter("c1", r.PermissionFilterRequest(mode=second), _req(), g)      # lands first
    apps.gates[first].set()
    await a                                                                                        # lands last
    assert apps.docs["c1"]["aclFilterMode"] == first
    assert await switched_connectors(g, "o1") == ({"c1"} if first == "scopes" else set())


async def test_a_delete_racing_an_opt_in_leaves_nothing_switched_once_the_document_is_gone() -> None:
    apps = Apps()
    g = apps.graph()
    apps.gates["scopes"] = asyncio.Event()
    opt_in = asyncio.create_task(r.set_permission_filter("c1", r.PermissionFilterRequest(mode="scopes"), _req(), g))
    await asyncio.sleep(0)
    del apps.docs["c1"]                                       # the delete removes the app document
    apps.gates["scopes"].set()
    with pytest.raises(HTTPException) as e:
        await opt_in
    assert e.value.status_code == 404
    assert await switched_connectors(g, "o1") == set()
    apps2 = Apps("scopes")                                   # the other order: switched first, then deleted
    g2 = apps2.graph()
    assert await switched_connectors(g2, "o1") == {"c1"}
    del apps2.docs["c1"]
    assert await switched_connectors(g2, "o1") == set()


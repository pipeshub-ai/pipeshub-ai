"""GET /connectors/{id}/permissions/status: admin only; anyone else, and another organisation's connector, gets the 404 of a missing one."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.connectors.api import router as r
from app.core import org_context


def _req(role: str = "admin", *, sync: dict | None = None, belongs: bool = True, limit: dict | None = None) -> tuple[MagicMock, MagicMock]:
    """A request; ``belongs`` is whether the registry's read gate admits this caller to the connector."""
    req = MagicMock()
    req.state.user = {"userId": "u1", "orgId": "o1", "role": role, **({"knowledgeLimit": limit} if limit is not None else {})}
    req.app.state.connector_registry.get_connector_instance = AsyncMock(return_value={"id": "c1"} if belongs else None)
    config = MagicMock()
    config.get_config = AsyncMock(return_value={"sync": sync} if sync else {})
    req.app.container = MagicMock()
    return req, config


def _graph(app: dict | None) -> MagicMock:
    """A graph provider that returns ``app`` for any read."""
    g = MagicMock()
    g.get_document = AsyncMock(return_value=app)
    g.execute_query = AsyncMock(return_value=None)
    return g


@pytest.fixture(autouse=True)
def _no_store_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The store clock is not asked."""
    monkeypatch.setattr(r, "store_now", AsyncMock(return_value=None))


@pytest.fixture(autouse=True)
def _org() -> object:
    """Bind the request org as the auth entry does."""
    with org_context.for_org("o1", job="test"):      # the route reads the request's org from the context, as the auth entry binds it
        yield


async def _call(role: str = "admin", app: dict | None = None, *, sync: dict | None = None, belongs: bool = True, monkeypatch: pytest.MonkeyPatch | None = None) -> tuple[dict, MagicMock]:
    """Call the route as ``role`` and return the answer with the request."""
    req, config = _req(role, sync=sync, belongs=belongs)
    if monkeypatch:
        monkeypatch.setattr(r, "resolve_config_service", lambda *_: config)
    return await r.get_permission_status("c1", req, _graph(app)), req


async def test_non_admin_gets_404_and_the_document_is_not_even_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non admin gets 404 and the document is not even read."""
    req, _ = _req("member")
    g = _graph({"status": "IDLE"})
    with pytest.raises(HTTPException) as e:
        await r.get_permission_status("c1", req, g)
    assert e.value.status_code == 404
    g.get_document.assert_not_called()


async def test_the_registry_gate_is_asked_as_this_caller_so_another_users_personal_connector_is_a_404() -> None:
    """The gate lets only the creator read a personal connector; the route passes the caller's id and the gate's refusal is the 404."""
    req, _ = _req(belongs=False)
    with pytest.raises(HTTPException) as e:
        await r.get_permission_status("c1", req, _graph({"status": "IDLE", "scope": "personal", "createdBy": "someone-else"}))
    assert e.value.status_code == 404
    req.app.state.connector_registry.get_connector_instance.assert_awaited_once_with(
        connector_id="c1", user_id="u1", org_id="o1", is_admin=True)


def _coverage(monkeypatch: pytest.MonkeyPatch, whole: set[str], part: set[str] | None = None) -> AsyncMock:
    """Coverage."""
    cover = AsyncMock(return_value=(frozenset(whole), frozenset(part or ())))
    monkeypatch.setattr(r, "connection_apps", cover)
    return cover


async def test_a_connected_app_limited_to_part_of_a_connector_gets_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """The counts are the whole connector's and cannot be limited to a folder the grant names."""
    _coverage(monkeypatch, set(), {"c1"})
    req, _ = _req(limit={"apps": [], "recordGroups": ["g1"], "records": []})
    with pytest.raises(HTTPException) as e:
        await r.get_permission_status("c1", req, _graph({"status": "IDLE"}))
    assert e.value.status_code == 404


async def test_a_connected_app_limited_to_other_apps_gets_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """A grant that names another app does not name this one."""
    _coverage(monkeypatch, {"other"})
    req, _ = _req(limit={"apps": ["other"], "recordGroups": [], "records": []})
    with pytest.raises(HTTPException) as e:
        await r.get_permission_status("c1", req, _graph({"status": "IDLE"}))
    assert e.value.status_code == 404


async def test_a_connected_app_granted_the_whole_connector_gets_the_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """A limit that names the connector whole bounds nothing here."""
    cover = _coverage(monkeypatch, {"c1"})
    monkeypatch.setattr(r, "resolve_config_service", lambda *_: _req()[1])
    req, _ = _req(limit={"apps": ["c1"], "recordGroups": [], "records": []})
    out = await r.get_permission_status("c1", req, _graph({"status": "IDLE"}))
    assert out["state"] == "never"
    cover.assert_awaited_once()


async def test_no_limit_asks_nothing_of_the_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    """A session, or an app given no limit, is not checked against a grant."""
    cover = _coverage(monkeypatch, set())
    monkeypatch.setattr(r, "resolve_config_service", lambda *_: _req()[1])
    await r.get_permission_status("c1", _req()[0], _graph({"status": "IDLE"}))
    cover.assert_not_awaited()


@pytest.mark.parametrize(("app", "belongs"), [(None, True), ({"status": "IDLE"}, False)], ids=["missing", "other organisation"])
async def test_a_missing_or_foreign_connector_is_404(app: dict | None, belongs: bool) -> None:
    """A missing or foreign connector is 404."""
    req, _ = _req(belongs=belongs)
    with pytest.raises(HTTPException) as e:
        await r.get_permission_status("c1", req, _graph(app))
    assert e.value.status_code == 404


async def test_admin_gets_the_status_with_the_connector_s_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin gets the status with the connector s schedule."""
    sync = {"selectedStrategy": "SCHEDULED", "scheduledConfig": {"intervalMinutes": 360}}
    out, _ = await _call(app={"status": "SYNCING"}, sync=sync, monkeypatch=monkeypatch)
    assert out["state"] == "running" and out["reachWithinMinutes"] == 360


@pytest.mark.parametrize("sync", [None, {"selectedStrategy": "MANUAL", "scheduledConfig": {"intervalMinutes": 60}},
                                  {"selectedStrategy": "SCHEDULED", "scheduledConfig": {"intervalMinutes": 0}}], ids=["none", "manual", "zero"])
async def test_no_schedule_means_no_reach(sync: dict | None, monkeypatch: pytest.MonkeyPatch) -> None:
    """No schedule means no reach."""
    out, _ = await _call(app={"status": "IDLE"}, sync=sync, monkeypatch=monkeypatch)
    assert out["reachWithinMinutes"] is None


async def test_an_unreadable_schedule_does_not_fail_the_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreadable schedule does not fail the status."""
    req, config = _req()
    config.get_config = AsyncMock(side_effect=RuntimeError("store down"))
    monkeypatch.setattr(r, "resolve_config_service", lambda *_: config)
    out = await r.get_permission_status("c1", req, _graph({"status": "IDLE"}))
    assert out["reachWithinMinutes"] is None and out["state"] == "never"

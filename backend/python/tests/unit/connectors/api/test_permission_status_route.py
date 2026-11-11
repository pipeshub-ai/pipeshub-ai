"""GET /connectors/{id}/permissions/status: admin only; anyone else, and another organisation's connector, gets the 404 of a missing one."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.connectors.api import router as r
from app.core import org_context


def _req(role: str = "admin", *, sync: dict | None = None, belongs: bool = True) -> tuple[MagicMock, MagicMock]:
    req = MagicMock()
    req.state.user = {"userId": "u1", "orgId": "o1", "role": role}
    req.app.state.connector_registry.belongs_to_org = AsyncMock(return_value=belongs)
    config = MagicMock()
    config.get_config = AsyncMock(return_value={"sync": sync} if sync else {})
    req.app.container = MagicMock()
    return req, config


def _graph(app: dict | None) -> MagicMock:
    g = MagicMock()
    g.get_document = AsyncMock(return_value=app)
    g.execute_query = AsyncMock(return_value=None)
    return g


@pytest.fixture(autouse=True)
def _no_store_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(r, "store_now", AsyncMock(return_value=None))


@pytest.fixture(autouse=True)
def _org() -> object:
    with org_context.for_org("o1", job="test"):      # the route reads the request's org from the context, as the auth entry binds it
        yield


async def _call(role: str = "admin", app: dict | None = None, *, sync: dict | None = None, belongs: bool = True, monkeypatch: pytest.MonkeyPatch | None = None) -> tuple[dict, MagicMock]:
    req, config = _req(role, sync=sync, belongs=belongs)
    if monkeypatch:
        monkeypatch.setattr(r, "resolve_config_service", lambda *_: config)
    return await r.get_permission_status("c1", req, _graph(app)), req


async def test_non_admin_gets_404_and_the_document_is_not_even_read(monkeypatch: pytest.MonkeyPatch) -> None:
    req, _ = _req("member")
    g = _graph({"status": "IDLE"})
    with pytest.raises(HTTPException) as e:
        await r.get_permission_status("c1", req, g)
    assert e.value.status_code == 404
    g.get_document.assert_not_called()


@pytest.mark.parametrize(("app", "belongs"), [(None, True), ({"status": "IDLE"}, False)], ids=["missing", "other organisation"])
async def test_a_missing_or_foreign_connector_is_404(app: dict | None, belongs: bool) -> None:
    req, _ = _req(belongs=belongs)
    with pytest.raises(HTTPException) as e:
        await r.get_permission_status("c1", req, _graph(app))
    assert e.value.status_code == 404


async def test_admin_gets_the_status_with_the_connector_s_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    sync = {"selectedStrategy": "SCHEDULED", "scheduledConfig": {"intervalMinutes": 360}}
    out, _ = await _call(app={"status": "SYNCING"}, sync=sync, monkeypatch=monkeypatch)
    assert out["state"] == "running" and out["reachWithinMinutes"] == 360


@pytest.mark.parametrize("sync", [None, {"selectedStrategy": "MANUAL", "scheduledConfig": {"intervalMinutes": 60}},
                                  {"selectedStrategy": "SCHEDULED", "scheduledConfig": {"intervalMinutes": 0}}], ids=["none", "manual", "zero"])
async def test_no_schedule_means_no_reach(sync: dict | None, monkeypatch: pytest.MonkeyPatch) -> None:
    out, _ = await _call(app={"status": "IDLE"}, sync=sync, monkeypatch=monkeypatch)
    assert out["reachWithinMinutes"] is None


async def test_an_unreadable_schedule_does_not_fail_the_status(monkeypatch: pytest.MonkeyPatch) -> None:
    req, config = _req()
    config.get_config = AsyncMock(side_effect=RuntimeError("store down"))
    monkeypatch.setattr(r, "resolve_config_service", lambda *_: config)
    out = await r.get_permission_status("c1", req, _graph({"status": "IDLE"}))
    assert out["reachWithinMinutes"] is None and out["state"] == "never"

"""PATCH /connectors/{id}/permissions/filter: admin only (the 404 of the status route for anyone else); `scopes` is refused when the deployment
cannot search with the scope filter; `legacy` and `inherit` are always written."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.connectors.api import router as r
from app.core import org_context
from app.services.permissions.currency import SCOPES_CONNECTORS_KEY
from app.services.permissions.mode import ENV_ACL_FILTER_MODE


class Config:
    """The config store of the switched list; ``down`` makes every read and write fail."""

    def __init__(self, listed: list[str] | None = None) -> None:
        self.listed, self.down, self.writes = listed, False, 0

    async def get_config(self, key: str, default: object = None, **_: object) -> object:
        if self.down:
            raise RuntimeError("store down")
        return self.listed if key == SCOPES_CONNECTORS_KEY and self.listed is not None else default

    async def set_config(self, key: str, value: object, **_: object) -> bool:
        if self.down:
            raise RuntimeError("store down")
        assert key == SCOPES_CONNECTORS_KEY
        self.listed, self.writes = value, self.writes + 1      # type: ignore[assignment]
        return True


@pytest.fixture(autouse=True)
def cfg(monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_ACL_FILTER_MODE, "shadow")
    monkeypatch.setenv("VECTOR_DB_TYPE", "pgvector")
    c = Config()
    monkeypatch.setattr(r, "resolve_config_service", lambda *_: c)
    return c


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


async def test_scopes_lists_the_connector_before_its_document_and_a_store_that_is_down_stores_nothing(cfg: Config) -> None:
    cfg.listed = ["c0"]
    g = _graph({"status": "IDLE"})
    await _patch("scopes", g=g)
    assert cfg.listed == ["c0", "c1"]
    cfg.down = True
    g2 = _graph({"status": "IDLE"})
    with pytest.raises(HTTPException) as e:
        await _patch("scopes", g=g2)
    assert e.value.status_code == 503
    g2.update_node.assert_not_called()


async def test_a_rollback_takes_the_connector_off_the_list_and_still_succeeds_when_the_list_cannot_be_written(cfg: Config) -> None:
    cfg.listed = ["c1", "c2"]
    await _patch("legacy")
    assert cfg.listed == ["c2"]
    cfg.listed, cfg.down = ["c1"], True
    g = _graph({"status": "IDLE", "aclFilterMode": "scopes"})
    assert (await _patch("inherit", g=g))["mode"] == "inherit"      # the document decides; a list that still names c1 costs a read, never access
    g.update_node.assert_awaited_once_with("c1", "apps", {"aclFilterMode": "inherit"})


async def test_setting_the_mode_it_already_has_does_not_rewrite_the_list(cfg: Config) -> None:
    cfg.listed = ["c1"]
    await _patch("scopes")
    assert cfg.writes == 0


async def test_scopes_again_puts_back_an_entry_the_list_lost(cfg: Config) -> None:
    """A concurrent change can drop an entry; setting `scopes` again repairs it even though the document already says so."""
    cfg.listed = []
    g = _graph({"status": "IDLE", "aclFilterMode": "scopes"})
    await _patch("scopes", g=g)
    assert cfg.listed == ["c1"]


@pytest.mark.parametrize(("listed", "effective"), [(["c1"], "scopes"), ([], "legacy"), (None, "legacy")])
async def test_the_status_says_scopes_under_shadow_only_for_a_listed_connector(cfg: Config, monkeypatch: pytest.MonkeyPatch,
                                                                                listed: list[str] | None, effective: str) -> None:
    monkeypatch.setattr(r, "store_now", AsyncMock(return_value=None))
    monkeypatch.setattr(r.access_status, "app_is_served", lambda app: True)
    cfg.listed = listed
    out = await r.get_permission_status("c1", _req(), _graph({"status": "IDLE", "aclFilterMode": "scopes"}))
    assert out["filter"]["effective"] == effective


async def test_a_list_that_cannot_be_read_says_legacy_in_the_status(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(r, "store_now", AsyncMock(return_value=None))
    monkeypatch.setattr(r.access_status, "app_is_served", lambda app: True)
    cfg.listed, cfg.down = ["c1"], True
    out = await r.get_permission_status("c1", _req(), _graph({"status": "IDLE", "aclFilterMode": "scopes"}))
    assert out["filter"]["effective"] == "legacy"


async def test_deleting_a_connector_takes_it_off_the_list(cfg: Config) -> None:
    from unittest.mock import patch

    from tests.unit.connectors.api.test_router_part2 import (
        _make_instance,
        _make_request,
    )

    cfg.listed = ["c1", "c2"]
    req = _make_request(is_admin=True)
    req.app.state.connector_registry.get_connector_instance_for_deletion = AsyncMock(return_value=_make_instance(scope="team", created_by="u1"))
    graph_provider = AsyncMock()
    graph_provider.check_connector_in_use = AsyncMock(return_value=[])
    req.app.container.messaging_producer.send_message = AsyncMock()
    with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock), \
         patch("app.connectors.api.router._validate_connector_deletion_permissions"):
        result = await r.delete_connector_instance("c1", req, graph_provider=graph_provider)
    assert result.status_code == 202 and cfg.listed == ["c2"]
    cfg.listed, cfg.down = ["c1"], True
    with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock), \
         patch("app.connectors.api.router._validate_connector_deletion_permissions"):
        assert (await r.delete_connector_instance("c1", req, graph_provider=graph_provider)).status_code == 202      # a list that is down never fails a delete

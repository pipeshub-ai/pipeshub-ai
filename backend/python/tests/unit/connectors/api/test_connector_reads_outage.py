"""Connector routes when the graph or config store cannot answer: a 503 with Retry-After, not "this connector is gone".

A failed read used to read as an absent connector, config or app list. GET and DELETE answered 404 (so the demo-data
removal counted the connector as removed and went on), the lock gate let requests through, the config form came back
empty, and the OAuth app list came back []. A real absence keeps its 404, 409, 400 or empty answer.

Fakes follow the providers' contract: without ``raise_on_error`` a failed read answers None, with it the read raises.
The config side runs the real ConfigurationService on FlakyKV.
"""

from __future__ import annotations

import secrets
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.configuration_service import ConfigurationService
from app.connectors.api import connector_resolvers
from app.connectors.api import router as routes
from app.connectors.api import user_access_router as ua
from app.connectors.core.registry.connector_registry import ConnectorRegistry
from app.core.org_context import OrgContext, bind
from app.utils import error_class
from app.utils.encryption.encryption_service import DecryptionError
from app.utils.user_messages import DATABASE_UNREACHABLE
from tests.unit.connectors.api.test_router_update_auth import _make_request
from tests.unit.connectors.test_oauth_config_read_outage import LOG, FlakyKV

pytestmark = pytest.mark.asyncio

CID = "conn1"
CONFIG_PATH = f"/services/connectors/{CID}/config"
INSTANCE = {"_key": CID, "id": CID, "type": "GOOGLE_DRIVE", "scope": "team", "createdBy": "u1", "orgId": "o1",
            "isActive": True, "isLocked": True, "authType": "OAUTH", "name": "Drive"}
LEGACY = {k: v for k, v in INSTANCE.items() if k != "orgId"}
NOTION = {"_key": CID, "id": CID, "type": ua.NOTION_CONNECTOR_TYPE, "scope": "team", "orgId": "o1", "name": "N"}
EMPTY_FORM = {"auth": {}, "sync": {}, "filters": {}}


class FakeGraph:
    """``get_document`` as every provider answers it: None for a failed read unless ``raise_on_error``."""

    def __init__(self, docs: dict[str, dict[str, Any]]) -> None:
        self.docs, self.down = docs, False

    async def get_document(self, key: str, collection: str, raise_on_error: bool = False, **_: Any) -> Any:  # noqa: ANN401
        if self.down:
            if raise_on_error:
                raise ConnectionError("graph down")
            return None
        return self.docs.get(key)

    async def get_edges_to_node(self, *_: object, raise_on_error: bool = False, **__: object) -> list:
        if self.down and raise_on_error:
            raise ConnectionError("graph down")
        return []


class EdgeDownGraph(FakeGraph):
    """Documents answer; the org-app edge read fails with ``exc``."""

    def __init__(self, docs: dict[str, dict[str, Any]], exc: Exception) -> None:
        super().__init__(docs)
        self.exc = exc

    async def get_edges_to_node(self, *_: object, raise_on_error: bool = False, **__: object) -> list:
        if raise_on_error:
            raise self.exc
        return []


def _registry(graph: FakeGraph) -> ConnectorRegistry:
    registry = ConnectorRegistry.__new__(ConnectorRegistry)
    registry.logger = MagicMock()
    registry._graph_provider = graph
    registry._collection_name = "apps"
    registry._connectors = {"GOOGLE_DRIVE": {"appGroup": "Google Workspace", "config": {}}}
    return registry


async def _outcome(awaitable: Any) -> tuple[int, Any, dict]:  # noqa: ANN401
    try:
        return 200, await awaitable, {}
    except HTTPException as exc:
        return exc.status_code, exc.detail, dict(exc.headers or {})


def _assert_503(got: tuple[int, Any, dict]) -> None:
    status, detail, headers = got
    assert (status, detail, headers.get("Retry-After")) == (503, DATABASE_UNREACHABLE, "5"), f"answered {status} {detail!r}"


@pytest.fixture
async def config(monkeypatch: pytest.MonkeyPatch) -> ConfigurationService:
    monkeypatch.setenv("SECRET_KEY", secrets.token_urlsafe(32))
    kv = FlakyKV()
    service = ConfigurationService(LOG, kv)
    await service.set_config(CONFIG_PATH, {"auth": {"authType": "OAUTH", "clientId": "cid"}, "sync": {}, "filters": {},
                                           "credentials": {"access_token": "t"}})
    service.kv = kv
    yield service
    await service.close()


def _request(graph: FakeGraph, config: ConfigurationService | None = None, body: dict | None = None) -> MagicMock:
    container = MagicMock()
    container.config_service = MagicMock(return_value=config)
    request = _make_request(is_admin=True, body=body, container=container, connector_registry=_registry(graph))
    request.app.state.graph_provider = graph
    return request


def _fail_reads(config: ConfigurationService, path: str = CONFIG_PATH) -> None:
    config.kv.fail_reads[path] = set(range(1, 100))


def _down(docs: dict[str, dict[str, Any]]) -> FakeGraph:
    graph = FakeGraph(docs)
    graph.down = True
    return graph


@pytest.fixture(params=["unset", "undecodable"])
async def no_usable_config(request: pytest.FixtureRequest, config: ConfigurationService) -> Any:  # noqa: ANN401
    """A config key that is not set, or one that will not decrypt (a changed SECRET_KEY). Retrying cannot fix either, so
    both read as unset: the empty form is how an admin enters the credentials again."""
    if request.param == "unset":
        await config.delete_config(CONFIG_PATH)
        yield config
    else:
        with patch.object(config.store, "get_key", AsyncMock(side_effect=DecryptionError("bad key"))):
            yield config


@pytest.fixture
def no_beta_check() -> Any:  # noqa: ANN401
    with patch.object(routes, "check_beta_connector_access", AsyncMock()):
        yield


@pytest.fixture
def org_bound() -> Any:  # noqa: ANN401
    with bind(OrgContext("o1", "u1", "user", "t", "-")):
        yield


# ---- the registry read: GET, DELETE and the lock gate

async def test_get_connector_graph_down_answers_503(no_beta_check: None) -> None:
    _assert_503(await _outcome(routes.get_connector_instance(CID, _request(_down({CID: INSTANCE})))))


async def test_get_connector_with_the_data_store_down_at_its_first_build_answers_503(no_beta_check: None) -> None:
    """The registry builds the data store on its first read; a store that is down then is the same outage, not a 404."""
    request = _request(FakeGraph({CID: INSTANCE}))
    registry = request.app.state.connector_registry
    registry._graph_provider = None
    registry.container = MagicMock(data_store=AsyncMock(side_effect=ConnectionError("store down")))
    _assert_503(await _outcome(routes.get_connector_instance(CID, request)))


async def test_get_connector_really_missing_still_answers_404(no_beta_check: None) -> None:
    status, _, _ = await _outcome(routes.get_connector_instance("nope", _request(FakeGraph({CID: INSTANCE}))))
    assert status == 404


async def test_the_503_keeps_the_store_error_as_its_hidden_cause() -> None:
    with pytest.raises(HTTPException) as caught:
        await _registry(_down({CID: INSTANCE})).get_connector_instance(CID, "u1", "o1", is_admin=True)
    assert isinstance(caught.value.__cause__, ConnectionError)
    assert error_class._hides_text(caught.value.__cause__)


async def test_lock_gate_graph_down_does_not_let_the_request_through() -> None:
    _assert_503(await _outcome(routes.require_connector_not_locked(CID, _request(_down({CID: INSTANCE})))))


async def test_lock_gate_healthy_store_still_refuses_a_locked_connector() -> None:
    status, _, _ = await _outcome(routes.require_connector_not_locked(CID, _request(FakeGraph({CID: INSTANCE}))))
    assert status == 409


async def test_delete_gate_graph_down_answers_503() -> None:
    graph = _down({CID: INSTANCE})
    _assert_503(await _outcome(_registry(graph).get_connector_instance_for_deletion(CID, "u1", "o1", is_admin=True)))


async def test_delete_handler_graph_down_answers_503_not_404(no_beta_check: None) -> None:
    """remove-demo-data.ts reads a 404 from DELETE as "already removed" and goes on to delete the sample accounts."""
    graph = _down({CID: INSTANCE})
    _assert_503(await _outcome(routes.delete_connector_instance(CID, _request(graph), graph)))


async def test_legacy_instance_edge_outage_answers_503() -> None:
    graph = EdgeDownGraph({CID: LEGACY}, ConnectionError("edge down"))
    _assert_503(await _outcome(_registry(graph).get_connector_instance(CID, "u1", "o1", is_admin=True)))


async def test_legacy_instance_edge_outage_answers_503_on_the_delete_gate() -> None:
    graph = EdgeDownGraph({CID: LEGACY}, ConnectionError("edge down"))
    _assert_503(await _outcome(_registry(graph).get_connector_instance_for_deletion(CID, "u1", "o1", is_admin=True)))


async def test_legacy_instance_edge_error_that_is_not_an_outage_stays_none() -> None:
    graph = EdgeDownGraph({CID: LEGACY}, ValueError("bad edge row"))
    assert await _outcome(_registry(graph).get_connector_instance(CID, "u1", "o1", is_admin=True)) == (200, None, {})


@pytest.mark.parametrize("doc", [
    {**INSTANCE, "orgId": "o2"},                          # another org's connector
    {**INSTANCE, "type": "NOPE"},                         # a type the registry does not know
    {k: v for k, v in INSTANCE.items() if k != "type"},   # a fault inside the read (KeyError), not the store's
], ids=["other_org", "unknown_type", "no_type_key"])
async def test_a_healthy_read_that_finds_nothing_usable_stays_none(doc: dict[str, Any]) -> None:
    assert await _outcome(_registry(FakeGraph({CID: doc})).get_connector_instance(CID, "u1", "o1", is_admin=True)) \
        == (200, None, {})


# ---- GET /config, GET /filters, POST /toggle: the config read

async def test_get_config_store_down_answers_503_not_an_empty_form(config: ConfigurationService, no_beta_check: None) -> None:
    _fail_reads(config)
    _assert_503(await _outcome(routes.get_connector_instance_config(CID, _request(FakeGraph({CID: INSTANCE}), config))))


async def test_get_config_with_no_usable_config_still_answers_the_empty_form(
    no_usable_config: ConfigurationService, no_beta_check: None
) -> None:
    request = _request(FakeGraph({CID: INSTANCE}), no_usable_config)
    status, value, _ = await _outcome(routes.get_connector_instance_config(CID, request))
    assert status == 200 and value["config"]["config"] == EMPTY_FORM


async def test_get_filters_store_down_answers_503_not_configure_first(
    config: ConfigurationService, no_beta_check: None
) -> None:
    _fail_reads(config)
    request = _request(FakeGraph({CID: INSTANCE}), config)
    request.app.state.connector_registry.get_connector_metadata = AsyncMock(return_value={"config": {}})
    _assert_503(await _outcome(routes.get_connector_instance_filters(CID, request, FakeGraph({}))))


async def test_get_filters_with_no_usable_config_still_asks_to_authenticate_first(
    no_usable_config: ConfigurationService, no_beta_check: None
) -> None:
    request = _request(FakeGraph({CID: INSTANCE}), no_usable_config)
    request.app.state.connector_registry.get_connector_metadata = AsyncMock(return_value={"config": {}})
    status, detail, _ = await _outcome(routes.get_connector_instance_filters(CID, request, FakeGraph({})))
    assert (status, detail) == (400, "OAuth credentials not found. Please authenticate first.")


def _toggle_request(config: ConfigurationService) -> tuple[MagicMock, FakeGraph]:
    graph = FakeGraph({CID: {**INSTANCE, "isActive": False, "isLocked": False}, "o1": {"_key": "o1"}})
    return _request(graph, config, body={"type": "sync", "enabled": True}), graph


async def test_turning_sync_on_with_the_config_store_down_answers_503(
    config: ConfigurationService, no_beta_check: None
) -> None:
    _fail_reads(config)
    request, graph = _toggle_request(config)
    _assert_503(await _outcome(routes.toggle_connector_instance(CID, request, graph)))


async def test_turning_sync_on_with_no_usable_config_still_answers_400(
    no_usable_config: ConfigurationService, no_beta_check: None
) -> None:
    request, graph = _toggle_request(no_usable_config)
    status, detail, _ = await _outcome(routes.toggle_connector_instance(CID, request, graph))
    assert (status, detail) == (400, "Connector cannot be enabled until OAuth authentication is completed")


# ---- the other connector document reads

async def test_stats_graph_down_answers_503() -> None:
    graph = _down({CID: INSTANCE})
    request = _request(graph)
    _assert_503(await _outcome(connector_resolvers.authorize_connector_stats(
        request, graph, request.app.state.connector_registry, CID, "o1")))


async def test_stats_really_missing_still_answers_404() -> None:
    graph = FakeGraph({})
    request = _request(graph)
    status, _, _ = await _outcome(connector_resolvers.authorize_connector_stats(
        request, graph, request.app.state.connector_registry, CID, "o1"))
    assert status == 404


async def test_stats_for_a_legacy_connector_edge_outage_answers_503() -> None:
    graph = EdgeDownGraph({CID: LEGACY}, ConnectionError("edge down"))
    request = _request(graph)
    _assert_503(await _outcome(connector_resolvers.authorize_connector_stats(
        request, graph, request.app.state.connector_registry, CID, "o1")))


async def test_admin_connector_app_graph_down_answers_503(org_bound: None) -> None:
    graph = _down({CID: INSTANCE})
    _assert_503(await _outcome(routes._admin_connector_app(_request(graph), CID, graph)))


async def test_reindex_kb_fallback_graph_down_answers_503() -> None:
    request = _request(_down({}))
    request.app.state.connector_registry = AsyncMock()
    request.app.state.connector_registry.get_connector_instance = AsyncMock(return_value=None)
    _assert_503(await _outcome(routes.reindex_connector(CID, request, kafka_service=MagicMock())))


async def test_connector_init_graph_down_answers_503_not_connector_not_found() -> None:
    container = MagicMock()
    container.connectors_map = {}
    _assert_503(await _outcome(routes._build_and_store_connector(
        container, CID, "DRIVE", MagicMock(), _down({CID: INSTANCE}), "u1", "o1", is_admin=True, logger=LOG)))


# ---- the shared OAuth app list

async def test_oauth_app_list_store_down_is_not_an_empty_list(config: ConfigurationService) -> None:
    path = routes._get_oauth_config_path("GOOGLE_DRIVE")
    await config.set_config(path, [{"_id": "a1", "orgId": "o1", "oauthInstanceName": "Drive app"}])
    _fail_reads(config, path)
    _assert_503(await _outcome(connector_resolvers.resolve_oauth_configs(None, path, "o1", config)))


async def test_oauth_app_list_unset_is_still_empty(config: ConfigurationService) -> None:
    assert await connector_resolvers.resolve_oauth_configs(None, routes._get_oauth_config_path("JIRA"), "o1", config) == []


@pytest.mark.parametrize(("resolve", "unset"), [
    (lambda config, path: connector_resolvers.resolve_oauth_configs(None, path, "o1", config), []),
    (lambda config, path: connector_resolvers.resolve_shared_oauth_config_for_flow({}, "a1", "o1", path, config), None),
], ids=["app_list", "sign_in_flow_app"])
async def test_an_oauth_app_list_that_will_not_decrypt_reads_as_unset(
    config: ConfigurationService, resolve: Any, unset: list | None  # noqa: ANN401
) -> None:
    """A changed SECRET_KEY: retrying cannot fix it, so the list reads as unset (no apps), not as an error."""
    path = routes._get_oauth_config_path("GOOGLE_DRIVE")
    await config.set_config(path, [{"_id": "a1", "orgId": "o1"}])
    with patch.object(config.store, "get_key", AsyncMock(side_effect=DecryptionError("bad key"))):
        assert await _outcome(resolve(config, path)) == (200, unset, {})


async def test_sign_in_flow_app_read_store_down_answers_503(config: ConfigurationService) -> None:
    path = routes._get_oauth_config_path("GOOGLE_DRIVE")
    await config.set_config(path, [{"_id": "a1", "orgId": "o1"}])
    _fail_reads(config, path)
    _assert_503(await _outcome(connector_resolvers.resolve_shared_oauth_config_for_flow({}, "a1", "o1", path, config)))


# ---- per-user (user-access) connectors

async def test_per_user_connector_graph_down_answers_503(org_bound: None) -> None:
    graph = _down({CID: NOTION})
    _assert_503(await _outcome(ua._per_user_connector(_request(graph, MagicMock()), CID, graph)))


async def test_per_user_connector_config_store_down_answers_503(config: ConfigurationService, org_bound: None) -> None:
    graph = FakeGraph({CID: NOTION})
    _fail_reads(config)
    request = _request(graph, config)
    with patch.object(ua, "_config_service", return_value=config):
        _assert_503(await _outcome(ua._per_user_connector(request, CID, graph)))


async def test_per_user_connector_legacy_edge_outage_answers_503(org_bound: None) -> None:
    graph = EdgeDownGraph({CID: {k: v for k, v in NOTION.items() if k != "orgId"}}, ConnectionError("edge down"))
    _assert_503(await _outcome(ua._per_user_connector(_request(graph, MagicMock()), CID, graph)))


async def test_connect_link_graph_down_answers_503_not_link_gone(org_bound: None) -> None:
    graph = _down({CID: NOTION})
    link = MagicMock(org_id="o1", user_id="u1", connector_id=CID)
    with patch.object(ua, "_link_user", AsyncMock(return_value={"email": "a@b"})):
        _assert_503(await _outcome(ua._link_target(_request(graph, MagicMock()), graph, link)))


async def test_connect_link_to_a_connector_that_is_gone_still_answers_link_gone(org_bound: None) -> None:
    graph = FakeGraph({})
    link = MagicMock(org_id="o1", user_id="u1", connector_id=CID)
    with patch.object(ua, "_link_user", AsyncMock(return_value={"email": "a@b"})):
        status, detail, _ = await _outcome(ua._link_target(_request(graph, MagicMock()), graph, link))
    assert (status, detail) == (404, ua._LINK_GONE)


async def test_sign_in_completion_graph_down_answers_503_not_does_not_let_users_connect(org_bound: None) -> None:
    graph = _down({CID: NOTION})
    _assert_503(await _outcome(ua._complete_user_sign_in(
        _request(graph, MagicMock()), graph, {"userId": "u1"}, connector_id=CID, state="s", code="c")))


async def test_sign_in_completion_for_a_connector_that_is_gone_still_says_users_cannot_connect(org_bound: None) -> None:
    graph = FakeGraph({})
    with pytest.raises(ua._SignInFailed) as caught:
        await ua._complete_user_sign_in(
            _request(graph, MagicMock()), graph, {"userId": "u1"}, connector_id=CID, state="s", code="c")
    assert caught.value.user_message == "This connector does not let users connect their own Notion account."

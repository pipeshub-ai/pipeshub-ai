"""Connector config and OAuth app routes when a read of what they are about to save back fails.

Each route reads a whole record (a connector's config, or the OAuth app list every org shares for a connector type), changes
it and saves it whole. A failed read must answer 500 and write nothing; it used to read as "nothing saved", and the save then
replaced the record. The real ConfigurationService runs on FlakyKV; the routes are called directly.
"""
from __future__ import annotations

import secrets
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes import toolsets
from app.config.configuration_service import ConfigurationService
from app.connectors.api import router as routes
from app.connectors.core.base.token_service import oauth_service
from app.utils import error_class
from tests.unit.connectors.api.test_router_update_auth import (
    _base_instance,
    _make_connector_registry,
    _make_container,
    _make_request,
)
from tests.unit.connectors.test_oauth_config_read_outage import (
    LOG,
    SAVED,
    TOOLSET_PATH,
    TOOLSET_SAVED,
    FlakyKV,
    _refusal,
)

CONNECTOR_TYPE = "GOOGLE_DRIVE"
CONFIG_PATH = "/services/connectors/conn1/config"
APP_X = {"_id": "app-x", "orgId": "o1", "oauthInstanceName": "Drive app", "createdBy": "u1",
         "config": {"clientId": "cid-x", "clientSecret": "old-secret"}}
APP_OTHER_ORG = {"_id": "app-y", "orgId": "o2", "oauthInstanceName": "Their app", "createdBy": "u9",
                 "config": {"clientId": "cid-y", "clientSecret": "their-secret"}}
APPS = [APP_X, APP_OTHER_ORG]


@pytest.fixture
async def kv(monkeypatch: pytest.MonkeyPatch) -> FlakyKV:
    monkeypatch.setenv("SECRET_KEY", secrets.token_urlsafe(32))
    return FlakyKV()


@pytest.fixture
async def config(kv: FlakyKV) -> ConfigurationService:
    service = ConfigurationService(LOG, kv)
    yield service
    await service.close()


@pytest.fixture
def apps_path() -> str:
    return routes._get_oauth_config_path(CONNECTOR_TYPE)


def _request(config: ConfigurationService, body: dict[str, Any] | None = None) -> MagicMock:
    request = _make_request(is_admin=True, body=body or {}, container=_make_container(config),
                            connector_registry=_make_connector_registry())
    request.app.state.connector_registry.get_connector_instance = AsyncMock(return_value=_base_instance())
    return request


def _assert_unavailable(exc: Exception | None) -> None:
    assert isinstance(exc, HTTPException) and exc.status_code == 500
    assert isinstance(exc.__cause__, oauth_service.OAuthStoreUnavailableError)
    assert error_class.classify_error(exc) == "unavailable"


# ---- the connector config: B1-B4

async def _update_auth(config: ConfigurationService, body: dict[str, Any]) -> dict[str, Any]:
    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value={"scope": "personal"})
    return await routes.update_connector_instance_auth_config("conn1", _request(config, body), graph)


async def _update_filters_sync(config: ConfigurationService, body: dict[str, Any]) -> dict[str, Any]:
    return await routes.update_connector_instance_filters_sync_config("conn1", _request(config, body), graph_provider=AsyncMock())


async def _update_config(config: ConfigurationService, body: dict[str, Any]) -> dict[str, Any]:
    return await routes.update_connector_instance_config("conn1", _request(config, body))


async def _save_filters(config: ConfigurationService, body: dict[str, Any]) -> dict[str, Any]:
    return await routes.save_connector_instance_filters("conn1", _request(config, body))


SAVES = {
    "auth": (_update_auth, {"auth": {"clientId": "cid"}}),
    "filters-sync": (_update_filters_sync, {"sync": {"selectedStrategy": "MANUAL"}}),
    "config": (_update_config, {"sync": {"selectedStrategy": "MANUAL"}}),
    "filters": (_save_filters, {"filters": {"folders": ["finance"]}}),
}


@pytest.fixture
def _route_patches() -> Any:  # noqa: ANN401
    with patch(f"{routes.__name__}.get_validated_connector_instance", new_callable=AsyncMock,
               return_value=_base_instance()), \
         patch(f"{routes.__name__}._get_and_validate_connector_instance", new_callable=AsyncMock,
               return_value=_base_instance()), \
         patch(f"{routes.__name__}._validate_connector_permissions"), \
         patch(f"{routes.__name__}.check_beta_connector_access", new_callable=AsyncMock):
        yield


@pytest.mark.usefixtures("_route_patches")
@pytest.mark.parametrize("save", list(SAVES))
async def test_a_config_save_whose_read_fails_answers_500_and_writes_nothing(
    kv: FlakyKV, config: ConfigurationService, save: str,
) -> None:
    await kv.create_key(CONFIG_PATH, SAVED)
    kv.fail_reads[CONFIG_PATH] = {1}
    call, body = SAVES[save]
    exc = await _refusal(call(config, body))
    assert kv.saved(CONFIG_PATH) == SAVED
    _assert_unavailable(exc)


@pytest.mark.usefixtures("_route_patches")
@pytest.mark.parametrize("save", ["filters-sync", "filters"])
async def test_a_config_save_with_nothing_saved_still_saves(kv: FlakyKV, config: ConfigurationService, save: str) -> None:
    call, body = SAVES[save]
    await call(config, body)
    assert kv.saved(CONFIG_PATH)


# ---- the connector sign-in start

def _start_patches() -> Any:  # noqa: ANN401
    oauth = oauth_service.OAuthConfig(client_id="cid", client_secret="cs", redirect_uri="https://app.example/cb",
                                      authorize_url="https://idp.example/authorize", token_url="https://idp.example/token")
    return patch(f"{routes.__name__}.check_beta_connector_access", new_callable=AsyncMock), \
        patch(f"{routes.__name__}._build_oauth_flow_config", new_callable=AsyncMock, return_value={}), \
        patch(f"{routes.__name__}.get_oauth_config", return_value=oauth)


async def _start(config: ConfigurationService) -> dict[str, Any]:
    beta, flow, oauth = _start_patches()
    request = _request(config)
    request.app.state.connector_registry.get_connector_instance = AsyncMock(
        return_value=_base_instance(scope="team", authType="OAUTH"))
    with beta, flow, oauth:
        return await routes.get_oauth_authorization_url("conn1", request, None, graph_provider=AsyncMock())


@pytest.mark.parametrize("failing_read", [1, 2], ids=["route-read", "provider-read"])
async def test_a_sign_in_start_whose_read_fails_answers_500_and_writes_nothing(
    kv: FlakyKV, config: ConfigurationService, failing_read: int,
) -> None:
    await kv.create_key(CONFIG_PATH, SAVED)
    kv.fail_reads[CONFIG_PATH] = {failing_read}
    exc = await _refusal(_start(config))
    assert kv.saved(CONFIG_PATH) == SAVED
    _assert_unavailable(exc)


@pytest.mark.parametrize("saved", [None, {"credentials": None}], ids=["absent", "no-auth"])
async def test_a_sign_in_start_with_no_auth_saved_still_asks_to_configure(
    kv: FlakyKV, config: ConfigurationService, saved: dict[str, Any] | None,
) -> None:
    if saved is not None:
        await kv.create_key(CONFIG_PATH, saved)
    with pytest.raises(HTTPException) as exc:
        await _start(config)
    assert exc.value.status_code == 400
    assert kv.saved(CONFIG_PATH) == saved


async def test_a_sign_in_start_keeps_the_saved_settings(kv: FlakyKV, config: ConfigurationService) -> None:
    await kv.create_key(CONFIG_PATH, SAVED)
    result = await _start(config)
    assert result["success"] is True
    assert {k: v for k, v in kv.saved(CONFIG_PATH).items() if k != "oauth"} == {
        k: v for k, v in SAVED.items() if k != "oauth"}


async def test_a_toolset_sign_in_start_whose_read_fails_answers_500_and_writes_nothing(
    kv: FlakyKV, config: ConfigurationService,
) -> None:
    await kv.create_key(TOOLSET_PATH, TOOLSET_SAVED)
    kv.fail_reads[TOOLSET_PATH] = {1}
    oauth = oauth_service.OAuthConfig(client_id="cid", client_secret="cs", redirect_uri="https://app.example/cb",
                                      authorize_url="https://idp.example/authorize", token_url="https://idp.example/token")
    request = MagicMock()
    request.state.user = {"userId": "u1", "orgId": "o1"}
    instance = {"_id": "i1", "orgId": "o1", "authType": "OAUTH", "oauthConfigId": "oc1", "toolsetType": "jira"}
    with patch.object(toolsets, "_find_instance", new=AsyncMock(return_value=instance)), \
         patch.object(toolsets, "_resolve_oauth_config_for_flow", new=AsyncMock(return_value={"config": {}})), \
         patch.object(toolsets, "_get_registry"), \
         patch.object(toolsets, "resolve_oauth_base_url", new=AsyncMock(return_value="https://app.example")), \
         patch.object(toolsets, "_build_oauth_config", new=AsyncMock(return_value={})), \
         patch.object(toolsets, "get_oauth_config", return_value=oauth):
        exc = await _refusal(toolsets.get_instance_oauth_authorization_url("i1", request, None, config_service=config))
    assert kv.saved(TOOLSET_PATH) == TOOLSET_SAVED
    _assert_unavailable(exc)


# ---- the OAuth app list every org shares for a connector type

@pytest.fixture
def _app_patches() -> Any:  # noqa: ANN401
    registry = MagicMock()
    registry.get_metadata.return_value = {}
    with patch("app.connectors.core.registry.oauth_config_registry.get_oauth_config_registry", return_value=registry), \
         patch(f"{routes.__name__}._update_oauth_infrastructure_fields", new_callable=AsyncMock), \
         patch(f"{routes.__name__}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]), \
         patch(f"{routes.__name__}._get_secret_oauth_field_names_from_registry", return_value={"clientSecret"}):
        yield


async def _create_app(config: ConfigurationService) -> dict[str, Any]:
    body = {"oauthInstanceName": "New app", "config": {"clientId": "cid-n", "clientSecret": "new"}}
    return await routes.create_oauth_config(CONNECTOR_TYPE, _request(config, body), config_service=config)


@pytest.mark.usefixtures("_app_patches")
async def test_creating_an_app_whose_list_read_fails_answers_500_and_keeps_every_app(
    kv: FlakyKV, config: ConfigurationService, apps_path: str,
) -> None:
    await kv.create_key(apps_path, APPS)
    kv.fail_reads[apps_path] = {1}
    exc = await _refusal(_create_app(config))
    assert kv.saved(apps_path) == APPS
    assert isinstance(exc, HTTPException) and exc.status_code == 500


@pytest.mark.usefixtures("_app_patches")
async def test_creating_the_first_app_of_a_type_starts_the_list(
    kv: FlakyKV, config: ConfigurationService, apps_path: str,
) -> None:
    await _create_app(config)
    assert [app["oauthInstanceName"] for app in kv.saved(apps_path)] == ["New app"]


@pytest.mark.usefixtures("_app_patches")
@pytest.mark.parametrize("change", ["update", "delete"])
async def test_changing_an_app_whose_list_read_fails_answers_500_not_404(
    kv: FlakyKV, config: ConfigurationService, apps_path: str, change: str,
) -> None:
    await kv.create_key(apps_path, APPS)
    kv.fail_reads[apps_path] = {1}
    request = _request(config, {"config": {"clientSecret": "new-secret"}})
    if change == "update":
        call = routes.update_oauth_config(CONNECTOR_TYPE, "app-x", request, config_service=config)
    else:
        call = routes.delete_oauth_config(CONNECTOR_TYPE, "app-x", request, config_service=config)
    exc = await _refusal(call)
    assert kv.saved(apps_path) == APPS
    assert isinstance(exc, HTTPException) and exc.status_code == 500


async def _save_app(config: ConfigurationService, app_id: str | None) -> str | None:
    return await routes._create_or_update_oauth_config(
        connector_type=CONNECTOR_TYPE, auth_config={"clientId": "cid-x", "clientSecret": "new-secret"},
        instance_name="Drive app", user_id="u1", org_id="o1", is_admin=True, config_service=config,
        base_url="https://app.example", oauth_app_id=app_id, logger=LOG)


@pytest.mark.usefixtures("_app_patches")
async def test_updating_an_app_whose_list_read_fails_saves_nothing(
    kv: FlakyKV, config: ConfigurationService, apps_path: str,
) -> None:
    await kv.create_key(apps_path, APPS)
    kv.fail_reads[apps_path] = {1}
    saved_id = await _save_app(config, "app-x")
    assert kv.saved(apps_path) == APPS
    assert saved_id is None


@pytest.mark.usefixtures("_app_patches")
async def test_updating_an_app_changes_it_in_place_and_keeps_the_others(
    kv: FlakyKV, config: ConfigurationService, apps_path: str,
) -> None:
    await kv.create_key(apps_path, APPS)
    assert await _save_app(config, "app-x") == "app-x"
    saved = kv.saved(apps_path)
    assert [app["_id"] for app in saved] == ["app-x", "app-y"]
    assert saved[0]["config"]["clientSecret"] == "new-secret"
    assert saved[1] == APP_OTHER_ORG


@pytest.mark.usefixtures("_app_patches")
async def test_a_connector_settings_save_whose_app_list_read_fails_does_not_create_a_second_app(
    kv: FlakyKV, config: ConfigurationService, apps_path: str,
) -> None:
    """The same choice in the path a full settings save and connector create take."""
    await kv.create_key(apps_path, APPS)
    kv.fail_reads[apps_path] = {1}
    outcome = await _refusal(routes._handle_oauth_config_creation(
        CONNECTOR_TYPE, {"clientId": "cid-x", "clientSecret": "new-secret"}, "Drive", "u1", "o1", is_admin=True,
        config_service=config, oauth_config_id="app-x", auth_type="OAUTH", base_url="https://app.example", logger=LOG))
    assert kv.saved(apps_path) == APPS
    assert isinstance(outcome, ConnectionError)


@pytest.mark.usefixtures("_route_patches", "_app_patches")
async def test_a_connector_auth_save_whose_app_list_read_fails_does_not_create_a_second_app(
    kv: FlakyKV, config: ConfigurationService, apps_path: str,
) -> None:
    """The first list read picks the app to update. Read as empty, "update app X" became "create app Y" with the new secret,
    and every other connector on X kept the old one."""
    await kv.create_key(CONFIG_PATH, SAVED)
    await kv.create_key(apps_path, APPS)
    kv.fail_reads[apps_path] = {1}
    body = {"auth": {"clientId": "cid-x", "clientSecret": "new-secret", "oauthConfigId": "app-x"}}
    exc = await _refusal(_update_auth(config, body))
    assert kv.saved(apps_path) == APPS
    assert kv.saved(CONFIG_PATH) == SAVED
    assert isinstance(exc, HTTPException) and exc.status_code == 500


# ---- connector create: an outage of the app list read is caught before the instance exists

def _create_request(names: set[str], body: dict[str, Any], on_create: Any = None) -> MagicMock:  # noqa: ANN401
    """An admin create request whose registry refuses a name it already holds, as the real one does.
    ``on_create`` runs while the instance is stored, as another request would."""
    async def create_instance(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        if kwargs["instance_name"] in names:
            raise ValueError("A connector with this name already exists")
        names.add(kwargs["instance_name"])
        if on_create:
            await on_create()
        return {"_key": "conn1", "type": "Drive"}

    request = MagicMock()
    request.state.user = {"userId": "u1", "orgId": "o1", "role": "admin"}
    request.json = AsyncMock(return_value=body)
    request.app.container.logger = MagicMock(return_value=LOG)
    request.app.container.connectors_map = {}
    registry = AsyncMock()
    registry.get_connector_metadata = AsyncMock(return_value={
        "scope": ["personal"], "supportedAuthTypes": ["OAUTH"],
        "config": {"auth": {"schemas": {"OAUTH": {"redirectUri": "connectors/oauth/callback/Drive", "fields": []}}}},
    })
    registry.create_connector_instance_on_configuration = AsyncMock(side_effect=create_instance)
    registry._normalize_connector_name = MagicMock(return_value="drive")
    registry._get_beta_connector_names = MagicMock(return_value=[])
    request.app.state.connector_registry = registry
    return request


async def _create_connector(config: ConfigurationService, names: set[str], on_create: Any = None) -> dict[str, Any]:  # noqa: ANN401
    body = {"connectorType": "Drive", "instanceName": "My Drive", "scope": "personal", "authType": "OAUTH",
            "config": {"auth": {"clientId": "cid-n", "clientSecret": "new"}}}
    graph = AsyncMock()
    graph.get_account_type = AsyncMock(return_value="individual")
    with patch.object(routes, "resolve_config_service", return_value=config), \
         patch.object(routes, "check_beta_connector_access", AsyncMock()), \
         patch.object(routes, "_store_config_action_items", AsyncMock()):
        return await routes.create_connector_instance(_create_request(names, body, on_create), graph)


@pytest.mark.usefixtures("_app_patches")
async def test_a_create_whose_app_list_read_fails_stores_no_instance_and_a_retry_completes(
    kv: FlakyKV, config: ConfigurationService,
) -> None:
    """The first list read raised only after the instance was stored, and the instance's name refused the retry."""
    apps_path = routes._get_oauth_config_path("Drive")
    await kv.create_key(apps_path, APPS)
    kv.fail_reads[apps_path] = {1}
    names: set[str] = set()
    exc = await _refusal(_create_connector(config, names))
    assert names == set()
    assert kv.saved(apps_path) == APPS
    assert kv.saved(CONFIG_PATH) is None
    assert isinstance(exc, HTTPException) and exc.status_code == 500

    result = await _create_connector(config, names)
    assert result["success"] is True and names == {"My Drive"}
    saved_apps = kv.saved(apps_path)
    assert saved_apps[:2] == APPS and saved_apps[2]["oauthInstanceName"] == "My Drive"
    assert kv.saved(CONFIG_PATH)["auth"]["oauthConfigId"] == saved_apps[2]["_id"]


@pytest.mark.usefixtures("_app_patches")
async def test_a_create_keeps_app_changes_saved_while_its_instance_was_stored(
    kv: FlakyKV, config: ConfigurationService,
) -> None:
    """The app save reads the list again just before it writes, so a change saved after the pre-check is not replaced."""
    apps_path = routes._get_oauth_config_path("Drive")
    await kv.create_key(apps_path, APPS)
    rotated = {**APP_X, "config": {**APP_X["config"], "clientSecret": "rotated"}}
    added = {"_id": "app-z", "orgId": "o1", "oauthInstanceName": "Saved meanwhile", "config": {"clientId": "cid-z"}}

    async def another_request_saves_apps() -> None:
        await kv.create_key(apps_path, [rotated, APP_OTHER_ORG, added])

    await _create_connector(config, set(), another_request_saves_apps)
    saved_apps = kv.saved(apps_path)
    assert saved_apps[:3] == [rotated, APP_OTHER_ORG, added]
    assert [app["oauthInstanceName"] for app in saved_apps[3:]] == ["My Drive"]

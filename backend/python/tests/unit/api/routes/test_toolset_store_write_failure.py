"""A config store that fails must not be answered as saved, and must not be overwritten.

`ConfigurationService.set_config` reports a failed write as False (it never raises), and
`get_config` reads a failed read as its default unless asked to raise. The toolset and MCP
routes must check the first, and read with ``raise_on_error=True`` before writing back.
"""

from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.agents.mcp.dcr import DCRClient, DiscoveredOAuthMetadata
from app.api.routes.mcp_servers import (
    _build_oauth_authorization_url,
    handle_oauth_callback,
)
from app.api.routes.toolset_resolvers import DEFAULT_TOOLSET_INSTANCES_PATH
from app.api.routes.toolsets import (
    _create_or_update_toolset_oauth_config,
    authenticate_agent_toolset,
    authenticate_toolset_instance,
    create_toolset_instance,
    delete_toolset_instance,
    delete_toolset_oauth_config,
    handle_toolset_oauth_callback,
    update_agent_toolset_credentials,
    update_toolset_credentials,
    update_toolset_instance,
    update_toolset_oauth_config,
)
from app.config.configuration_service import ConfigurationService
from tests.unit.api.routes import test_mcp_servers as mcp_tests
from tests.unit.api.routes import test_toolsets_full_coverage as toolset_tests
from tests.unit.api.routes.test_mcp_servers import _mock_request
from tests.unit.api.routes.test_toolsets import _make_registry, _make_request
from tests.unit.api.routes.test_toolsets_full_coverage import _make_oauth_registry
from tests.unit.config.test_configuration_service import _build_service

T = "app.api.routes.toolsets"
API_TOKEN_INSTANCE = {"_id": "i1", "orgId": "o1", "instanceName": "Jira", "toolsetType": "jira", "authType": "API_TOKEN"}
OAUTH_INSTANCE = {"_id": "i1", "orgId": "o1", "instanceName": "Jira", "toolsetType": "jira", "authType": "OAUTH", "oauthConfigId": "oc-1"}
OTHER_INSTANCE = {"_id": "i0", "orgId": "o1", "instanceName": "Old", "toolsetType": "slack", "authType": "API_TOKEN"}
OAUTH_APPS_PATH = "/services/oauths/toolsets/jira"
USER_AUTH_PATH = "/services/toolsets/i1/u1"


@pytest.fixture(autouse=True)
def _credential_instance_and_schema() -> Iterator[None]:
    """The credential handlers' own lookups are covered in test_toolset_credential_fields.py."""
    with patch(f"{T}._declared_auth_fields", return_value={"apiToken"}), \
         patch(f"{T}._instance_for_credential_update", new_callable=AsyncMock, return_value=API_TOKEN_INSTANCE):
        yield


def _service(
    *, reads: bool = True, writes: bool | None = True, data: dict[str, Any] | None = None,
) -> tuple[ConfigurationService, AsyncMock, dict[str, Any]]:
    """A real ConfigurationService over a store that fails the way the encrypted stores do:
    a failed read is None unless the caller asks it to raise; ``writes=None`` raises."""
    stored = dict(data or {})

    async def get_key(key: str, *, raise_on_error: bool = False) -> Any:  # noqa: ANN401
        if reads:
            return stored.get(key)
        if raise_on_error:
            raise ConnectionError("store down")
        return None

    async def create_key(key: str, value: Any, overwrite: bool = True, **_kw: Any) -> bool:  # noqa: ANN401
        if writes is None:
            raise ConnectionError("store down")
        if writes:
            stored[key] = value
        return writes

    store = AsyncMock()
    store.get_key = AsyncMock(side_effect=get_key)
    store.create_key = AsyncMock(side_effect=create_key)
    return _build_service(store=store), store, stored


def _failing_writes() -> AsyncMock:
    """A config service whose every write reports failure the way the real one does."""
    cs = AsyncMock()
    cs.set_config = AsyncMock(return_value=False)
    return cs


def _oauth_registry_entry() -> MagicMock:
    oauth = MagicMock(redirect_uri="callback", authorize_url="https://auth.example.com/authorize",
                      token_url="https://auth.example.com/token", additional_params=None,
                      token_access_type=None, scope_parameter_name="scope", token_response_path=None)
    oauth.scopes.get_scopes_for_type.return_value = ["read"]
    return oauth


# --- a failed write, through the real ConfigurationService ----------------------------------

async def _authenticate(cs: Any) -> dict:  # noqa: ANN401
    req = _make_request(body_dict={"auth": {"apiToken": "tok"}})
    req.app.state.toolset_registry = _make_registry("jira")
    with patch(f"{T}._load_toolset_instances", new_callable=AsyncMock, return_value=[API_TOKEN_INSTANCE]):
        return await authenticate_toolset_instance("i1", req, cs)


@pytest.mark.parametrize("writes", [None, False], ids=["store-raises", "store-refuses"])
async def test_authenticate_with_a_failed_write_is_an_error_not_authenticated(writes: bool | None) -> None:
    cs, _store, stored = _service(writes=writes)
    with pytest.raises(HTTPException) as exc:
        await _authenticate(cs)
    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to save credentials."
    assert USER_AUTH_PATH not in stored


async def test_authenticate_with_the_store_up_is_unchanged() -> None:
    cs, _store, stored = _service()
    result = await _authenticate(cs)
    assert result == {"status": "success", "message": "Toolset authenticated successfully.", "isAuthenticated": True}
    assert stored[USER_AUTH_PATH]["isAuthenticated"] is True


# --- every other toolset route that answered "saved" ----------------------------------------

async def test_update_credentials_write_failure_is_500() -> None:
    cs = _failing_writes()
    cs.get_config = AsyncMock(return_value={"isAuthenticated": True, "auth": {}})
    req = _make_request(body_dict={"auth": {"apiToken": "new"}})
    with pytest.raises(HTTPException) as exc:
        await update_toolset_credentials("i1", req, cs)
    assert (exc.value.status_code, exc.value.detail) == (500, "Failed to update credentials.")


async def test_authenticate_agent_write_failure_is_500() -> None:
    cs = _failing_writes()
    req = _make_request(body_dict={"auth": {"apiToken": "tok"}})
    req.app.state.toolset_registry = _make_registry("jira")
    with patch(f"{T}._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}), \
         patch(f"{T}._load_toolset_instances", new_callable=AsyncMock, return_value=[API_TOKEN_INSTANCE]):
        with pytest.raises(HTTPException) as exc:
            await authenticate_agent_toolset("a1", "i1", req, config_service=cs)
    assert (exc.value.status_code, exc.value.detail) == (500, "Failed to save agent credentials.")


async def test_update_agent_credentials_write_failure_is_500() -> None:
    cs = _failing_writes()
    cs.get_config = AsyncMock(return_value={"isAuthenticated": True, "auth": {}})
    req = _make_request(body_dict={"auth": {"apiToken": "new"}})
    with patch(f"{T}._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}):
        with pytest.raises(HTTPException) as exc:
            await update_agent_toolset_credentials("a1", "i1", req, config_service=cs)
    assert (exc.value.status_code, exc.value.detail) == (500, "Failed to update agent credentials.")


async def test_create_instance_write_failure_is_500() -> None:
    cs = _failing_writes()
    req = _make_request(body_dict={"instanceName": "My Jira", "toolsetType": "jira", "authType": "API_TOKEN"})
    req.app.state.toolset_registry = _make_registry("jira")
    with patch(f"{T}._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
         patch(f"{T}._load_instances_for_mutation", new_callable=AsyncMock, return_value=[]), \
         patch(f"{T}._check_instance_name_conflict", return_value=False):
        with pytest.raises(HTTPException) as exc:
            await create_toolset_instance(req, config_service=cs)
    assert (exc.value.status_code, exc.value.detail) == (500, "Failed to save toolset instance. Please try again.")


async def test_update_instance_write_failure_is_500_and_signs_nobody_out() -> None:
    cs = _failing_writes()
    req = _make_request(body_dict={"authConfig": {"clientId": "new-cid", "clientSecret": "new-csec"}})
    req.app.state.toolset_registry = _make_registry("jira", ["OAUTH"])
    with patch(f"{T}._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
         patch(f"{T}._load_instances_for_mutation", new_callable=AsyncMock, return_value=[dict(OAUTH_INSTANCE)]), \
         patch(f"{T}.resolve_oauth_base_url", new_callable=AsyncMock, return_value="http://localhost:3000"), \
         patch(f"{T}._create_or_update_toolset_oauth_config", new_callable=AsyncMock, return_value="oc-1"), \
         patch(f"{T}.resolve_inherited_from_org_id", new_callable=AsyncMock, return_value=None), \
         patch(f"{T}._deauth_all_instance_users", new_callable=AsyncMock, return_value=1) as deauth:
        with pytest.raises(HTTPException) as exc:
            await update_toolset_instance("i1", req, cs)
    assert exc.value.status_code == 500
    deauth.assert_not_awaited()


async def test_delete_instance_write_failure_is_500_and_keeps_its_rules() -> None:
    cs = _failing_writes()
    cs.list_keys_in_directory = AsyncMock(return_value=[])
    req = _make_request()
    req.app.state.toolset_registry = _make_registry("jira")
    graph = AsyncMock()
    graph.check_toolset_instance_in_use = AsyncMock(return_value=[])
    with patch(f"{T}._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
         patch(f"{T}._load_instances_for_mutation", new_callable=AsyncMock, return_value=[dict(API_TOKEN_INSTANCE)]), \
         patch(f"{T}._get_graph_provider", return_value=graph), \
         patch(f"{T}.toolset_store.delete_instance_rules", new_callable=AsyncMock) as delete_rules:
        with pytest.raises(HTTPException) as exc:
            await delete_toolset_instance("i1", req, cs)
    assert exc.value.status_code == 500
    delete_rules.assert_not_awaited()


async def test_delete_oauth_config_write_failure_is_500() -> None:
    cs = _failing_writes()
    cs.get_config = AsyncMock(return_value=[{"_id": "oc-1"}])
    with patch(f"{T}._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
         patch(f"{T}._get_oauth_config_by_id", new_callable=AsyncMock, return_value={"_id": "oc-1"}), \
         patch(f"{T}._load_toolset_instances", new_callable=AsyncMock, return_value=[]):
        with pytest.raises(HTTPException) as exc:
            await delete_toolset_oauth_config("jira", "oc-1", _make_request(), cs)
    assert (exc.value.status_code, exc.value.detail) == (500, "Failed to delete OAuth configuration.")


@pytest.mark.parametrize("existing_id", [None, "oc-1"], ids=["create", "update"])
async def test_oauth_app_write_failure_returns_no_id(existing_id: str | None) -> None:
    cs = _failing_writes()
    cs.get_config = AsyncMock(return_value=[{"_id": "oc-1", "orgId": "o1", "config": {}}])
    with patch(f"{T}._get_oauth_config_from_registry", return_value=_oauth_registry_entry()):
        result = await _create_or_update_toolset_oauth_config(
            toolset_type="jira", auth_config={"type": "OAUTH", "clientId": "cid", "clientSecret": "csec"},
            instance_name="My Jira", user_id="u1", org_id="o1", config_service=cs,
            registry=MagicMock(), base_url="http://localhost:3000", oauth_config_id=existing_id,
        )
    assert result is None


async def test_update_oauth_config_route_write_failure_is_500_and_signs_nobody_out() -> None:
    cs = _failing_writes()
    req = _make_request(body_dict={"authConfig": {"clientId": "cid", "clientSecret": "csec"}})
    req.app.state.toolset_registry = MagicMock()
    with patch(f"{T}._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
         patch(f"{T}._get_oauth_config_by_id", new_callable=AsyncMock, return_value={"_id": "oc-1"}), \
         patch(f"{T}.resolve_oauth_base_url", new_callable=AsyncMock, return_value="http://localhost:3000"), \
         patch(f"{T}._create_or_update_toolset_oauth_config", new_callable=AsyncMock, return_value=None), \
         patch(f"{T}._load_toolset_instances", new_callable=AsyncMock, return_value=[dict(OAUTH_INSTANCE)]), \
         patch(f"{T}._deauth_all_instance_users", new_callable=AsyncMock, return_value=1) as deauth:
        with pytest.raises(HTTPException) as exc:
            await update_toolset_oauth_config("jira", "oc-1", req, cs)
    assert (exc.value.status_code, exc.value.detail) == (500, "Failed to save the OAuth app. Please try again.")
    deauth.assert_not_awaited()


# --- a failed read must not be written back as an empty list --------------------------------

async def _create_api_token_instance(cs: Any) -> dict:  # noqa: ANN401
    req = _make_request(body_dict={"instanceName": "My Jira", "toolsetType": "jira", "authType": "API_TOKEN"})
    req.app.state.toolset_registry = _make_registry("jira")
    with patch(f"{T}._check_user_is_admin", new_callable=AsyncMock, return_value=True):
        return await create_toolset_instance(req, config_service=cs)


async def test_create_instance_with_an_unreadable_list_is_500_and_overwrites_nothing() -> None:
    cs, store, _stored = _service(reads=False)
    with pytest.raises(HTTPException) as exc:
        await _create_api_token_instance(cs)
    assert exc.value.status_code == 500
    store.create_key.assert_not_awaited()


async def test_create_instance_with_a_readable_list_keeps_the_others() -> None:
    cs, _store, stored = _service(data={DEFAULT_TOOLSET_INSTANCES_PATH: [OTHER_INSTANCE]})
    result = await _create_api_token_instance(cs)
    assert result["status"] == "success"
    assert [i["_id"] for i in stored[DEFAULT_TOOLSET_INSTANCES_PATH]] == ["i0", result["instance"]["_id"]]


async def test_update_instance_with_an_unreadable_list_is_500_not_404() -> None:
    cs, store, _stored = _service(reads=False)
    req = _make_request(body_dict={"instanceName": "Renamed"})
    req.app.state.toolset_registry = _make_registry("jira")
    with patch(f"{T}._check_user_is_admin", new_callable=AsyncMock, return_value=True):
        with pytest.raises(HTTPException) as exc:
            await update_toolset_instance("i1", req, cs)
    assert exc.value.status_code == 500
    store.create_key.assert_not_awaited()


async def test_delete_instance_with_an_unreadable_list_is_500_and_keeps_credentials() -> None:
    cs, store, _stored = _service(reads=False)
    req = _make_request()
    req.app.state.toolset_registry = _make_registry("jira")
    with patch(f"{T}._check_user_is_admin", new_callable=AsyncMock, return_value=True):
        with pytest.raises(HTTPException) as exc:
            await delete_toolset_instance("i1", req, cs)
    assert exc.value.status_code == 500
    store.create_key.assert_not_awaited()
    store.delete_key.assert_not_awaited()


async def test_new_oauth_app_with_an_unreadable_list_overwrites_nothing() -> None:
    cs, store, _stored = _service(reads=False)
    with patch(f"{T}._get_oauth_config_from_registry", return_value=_oauth_registry_entry()):
        result = await _create_or_update_toolset_oauth_config(
            toolset_type="jira", auth_config={"type": "OAUTH", "clientId": "cid", "clientSecret": "csec"},
            instance_name="My Jira", user_id="u1", org_id="o1", config_service=cs,
            registry=MagicMock(), base_url="http://localhost:3000",
        )
    assert result is None
    store.create_key.assert_not_awaited()


async def test_new_oauth_app_with_a_readable_list_keeps_the_others() -> None:
    other_org_app = {"_id": "oc-9", "orgId": "o9", "config": {}}
    cs, _store, stored = _service(data={OAUTH_APPS_PATH: [other_org_app]})
    with patch(f"{T}._get_oauth_config_from_registry", return_value=_oauth_registry_entry()):
        new_id = await _create_or_update_toolset_oauth_config(
            toolset_type="jira", auth_config={"type": "OAUTH", "clientId": "cid", "clientSecret": "csec"},
            instance_name="My Jira", user_id="u1", org_id="o1", config_service=cs,
            registry=MagicMock(), base_url="http://localhost:3000",
        )
    assert [c["_id"] for c in stored[OAUTH_APPS_PATH]] == ["oc-9", new_id]


@pytest.mark.parametrize("unreadable", ["in-use check", "app list"])
async def test_delete_oauth_config_with_an_unreadable_read_is_500_and_overwrites_nothing(unreadable: str) -> None:
    cs, store, _stored = _service(reads=False)
    patches = [
        patch(f"{T}._check_user_is_admin", new_callable=AsyncMock, return_value=True),
        # The lookup itself saw the app; the store fails on the reads that guard and feed the write.
        patch(f"{T}._get_oauth_config_by_id", new_callable=AsyncMock, return_value={"_id": "oc-1"}),
    ]
    if unreadable == "app list":
        patches.append(patch(f"{T}._load_toolset_instances", new_callable=AsyncMock, return_value=[]))
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with pytest.raises(HTTPException) as exc:
            await delete_toolset_oauth_config("jira", "oc-1", _make_request(), cs)
    assert exc.value.status_code == 500
    store.create_key.assert_not_awaited()


async def test_update_oauth_config_with_an_unreadable_instance_list_is_500_and_keeps_the_app() -> None:
    cs, store, _stored = _service(reads=False)
    save_app = AsyncMock(return_value="oc-1")
    with patch(f"{T}._check_user_is_admin", new_callable=AsyncMock, return_value=True), \
         patch(f"{T}._get_oauth_config_by_id", new_callable=AsyncMock, return_value={"_id": "oc-1"}), \
         patch(f"{T}.resolve_oauth_base_url", new_callable=AsyncMock, return_value="http://localhost:3000"), \
         patch(f"{T}._create_or_update_toolset_oauth_config", new=save_app):
        with pytest.raises(HTTPException) as exc:
            await update_toolset_oauth_config("jira", "oc-1", _make_request(body_dict={"authConfig": {}}), cs)
    assert exc.value.status_code == 500
    save_app.assert_not_awaited()
    store.create_key.assert_not_awaited()


async def test_delete_oauth_config_with_readable_lists_removes_only_that_app() -> None:
    apps = [{"_id": "oc-1", "orgId": "o1"}, {"_id": "oc-9", "orgId": "o9"}]
    cs, _store, stored = _service(data={OAUTH_APPS_PATH: apps, DEFAULT_TOOLSET_INSTANCES_PATH: []})
    with patch(f"{T}._check_user_is_admin", new_callable=AsyncMock, return_value=True):
        result = await delete_toolset_oauth_config("jira", "oc-1", _make_request(), cs)
    assert result["status"] == "success"
    assert stored[OAUTH_APPS_PATH] == [{"_id": "oc-9", "orgId": "o9"}]


@pytest.mark.parametrize("agent", [False, True], ids=["user", "agent"])
async def test_credential_update_with_an_unreadable_record_is_500_not_missing(agent: bool) -> None:
    cs, store, _stored = _service(reads=False)
    req = _make_request(body_dict={"auth": {"apiToken": "new"}})
    with patch(f"{T}._require_agent_edit_access", new_callable=AsyncMock, return_value={"_key": "a1"}):
        with pytest.raises(HTTPException) as exc:
            if agent:
                await update_agent_toolset_credentials("a1", "i1", req, config_service=cs)
            else:
                await update_toolset_credentials("i1", req, cs)
    assert exc.value.status_code == 500
    store.create_key.assert_not_awaited()


# --- OAuth callbacks: the redirect must not say "connected" ---------------------------------

async def _toolset_callback(cs: Any) -> tuple[dict, MagicMock]:  # noqa: ANN401
    req = _make_request()
    req.app.state.toolset_registry = _make_oauth_registry("jira")
    provider = AsyncMock()
    provider.handle_callback = AsyncMock(return_value=MagicMock(access_token="token123"))
    refresh = MagicMock(schedule_token_refresh=AsyncMock())
    state = toolset_tests.TestHandleToolsetOAuthCallbackFlows()._encode_state("orig-state", "i1", "u1")
    startup = MagicMock(startup_service=MagicMock(get_toolset_token_refresh_service=MagicMock(return_value=refresh)))
    with patch(f"{T}._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
         patch(f"{T}._load_toolset_instances", new_callable=AsyncMock, return_value=[OAUTH_INSTANCE]), \
         patch(f"{T}._get_oauth_config_by_id", new_callable=AsyncMock, return_value={"_id": "oc-1", "config": {}}), \
         patch(f"{T}._build_oauth_config", new_callable=AsyncMock, return_value={"clientId": "cid", "clientSecret": "cs"}), \
         patch(f"{T}._validate_toolset_oauth_setup", new_callable=AsyncMock, return_value=None), \
         patch(f"{T}.OAuthProvider", return_value=provider), \
         patch.dict("sys.modules", {"app.connectors.core.base.token_service.startup_service": startup}):
        result = await handle_toolset_oauth_callback(
            req, code="auth-code", state=state, error=None, base_url=None, config_service=cs,
        )
    return result, refresh


def _callback_service(set_config: AsyncMock) -> AsyncMock:
    cs = AsyncMock()
    cs.get_config = AsyncMock(return_value={})
    cs.set_config = set_config
    return cs


async def test_toolset_oauth_callback_write_failure_is_not_success() -> None:
    cs = _callback_service(AsyncMock(return_value=False))
    result, refresh = await _toolset_callback(cs)
    assert result["success"] is False
    assert "oauth_error=server_error" in result["redirect_url"]
    assert "oauth_success" not in result["redirect_url"]
    assert cs.set_config.await_args.args[0] == USER_AUTH_PATH
    refresh.schedule_token_refresh.assert_not_awaited()


async def test_toolset_oauth_callback_unreadable_record_is_not_success_and_writes_nothing() -> None:
    cs, store, _stored = _service(reads=False)
    result, refresh = await _toolset_callback(cs)
    assert result["success"] is False
    assert "oauth_error=server_error" in result["redirect_url"]
    store.create_key.assert_not_awaited()
    refresh.schedule_token_refresh.assert_not_awaited()


async def test_toolset_oauth_callback_success_is_unchanged() -> None:
    cs = _callback_service(AsyncMock(return_value=True))
    result, refresh = await _toolset_callback(cs)
    assert result["success"] is True
    assert "oauth_success=true" in result["redirect_url"]
    assert cs.set_config.await_args.args[1]["isAuthenticated"] is True
    refresh.schedule_token_refresh.assert_awaited_once()


async def _mcp_callback(set_config: AsyncMock) -> tuple[dict, MagicMock, AsyncMock]:
    state_record = mcp_tests.TestHandleOauthCallback()._state_record()
    cs = MagicMock()
    cs.get_config = AsyncMock(side_effect=lambda path, default=None, **_kw: (
        state_record if "oauth-states" in path else
        {"clientId": "cid-1", "clientSecret": "s"} if "dcr-clients" in path else default
    ))
    cs.delete_config = AsyncMock()
    cs.create_config_if_absent = AsyncMock(return_value=True)
    cs.set_config = set_config
    request = _mock_request(user={"userId": "user-1", "orgId": "org-1"}, app_state={"config_service": cs})
    tokens = MagicMock()
    tokens.model_copy.return_value = tokens
    tokens.model_dump.return_value = {"accessToken": "at", "refreshToken": "rt"}
    refresh = MagicMock(schedule_token_refresh=AsyncMock())
    instance = {"_id": "inst-1", "orgId": "org-1", "authMode": "oauth", "url": "https://mcp.example.com/v1/sse"}
    with patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=instance)), \
         patch("app.api.routes.mcp_servers.step_up.clear_step_up_scopes", new=AsyncMock()) as clear_scopes, \
         patch("app.api.routes.mcp_servers.oauth_client_module.exchange_code_for_token", new=AsyncMock(return_value=tokens)), \
         patch("app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
               return_value=refresh):
        result = await handle_oauth_callback(request, code="code-1", state="state-1", error=None)
    return result, refresh, clear_scopes


async def test_mcp_oauth_callback_write_failure_is_not_success() -> None:
    result, refresh, clear_scopes = await _mcp_callback(AsyncMock(return_value=False))
    assert result["success"] is False
    assert result["error"] == "credentials_not_saved"
    assert result["errorMessage"]
    clear_scopes.assert_not_awaited()
    refresh.schedule_token_refresh.assert_not_awaited()


async def test_mcp_oauth_callback_success_is_unchanged() -> None:
    result, refresh, clear_scopes = await _mcp_callback(AsyncMock(return_value=True))
    assert result == {"success": True, "instanceId": "inst-1"}
    clear_scopes.assert_awaited_once()
    refresh.schedule_token_refresh.assert_awaited_once()


# --- MCP sign-in: the shared client registration ---------------------------------------------

DISCOVERED = DiscoveredOAuthMetadata(
    authorization_endpoint="https://auth.example.com/authorize",
    token_endpoint="https://auth.example.com/token",
    registration_endpoint="https://auth.example.com/register",
)
NEW_CLIENT = DCRClient(
    client_id="new-cid", client_secret="new-secret", authorization_url="https://auth.example.com/authorize",
    token_url="https://auth.example.com/token", registered_at=2,
    redirect_uri="https://app.example.com/mcp-servers/oauth/callback/",
)
# Registered for another address, so it no longer fits and a sign-in replaces it.
STALE_CLIENT = {"clientId": "old-cid", "clientSecret": "s", "registeredAt": 1, "redirectUri": "https://elsewhere.example/cb"}


async def _sign_in(cs: MagicMock) -> AsyncMock:
    helpers = mcp_tests.TestBuildOauthAuthorizationUrlDcrReuse()
    patches = helpers._patched(discovered=DISCOVERED, registered=NEW_CLIENT)
    with patches[0], patches[1], patches[2] as register, patches[3], patches[4], patches[5], patches[6]:
        await _build_oauth_authorization_url(
            cs, helpers._oauth_instance(), "inst-1", "user-1", "org-1", None,
            initiated_by="user-1", owner_type="user",
        )
    return register


def _states_stored(cs: MagicMock) -> list[str]:
    return [c.args[0] for c in cs.create_config_if_absent.await_args_list if "oauth-states" in c.args[0]]


@pytest.mark.parametrize("path", ["replacement", "race-loser"])
async def test_mcp_sign_in_unsaved_client_registration_is_500(path: str) -> None:
    helpers = mcp_tests.TestBuildOauthAuthorizationUrlDcrReuse()
    if path == "replacement":
        cs = helpers._config_service(shared_dcr=STALE_CLIENT)
    else:
        # Someone else's registration landed first, but there is no usable client in it.
        cs = helpers._config_service()
        cs.create_config_if_absent = AsyncMock(side_effect=lambda key, *_a, **_kw: "oauth-states" in key)
    cs.set_config = AsyncMock(return_value=False)
    with pytest.raises(HTTPException) as exc:
        await _sign_in(cs)
    assert (exc.value.status_code, exc.value.detail) == (500, "Failed to save the OAuth client registration. Please try again.")
    cs.set_config.assert_awaited_once()
    assert _states_stored(cs) == []


async def test_mcp_sign_in_saved_replacement_registration_is_unchanged() -> None:
    cs = mcp_tests.TestBuildOauthAuthorizationUrlDcrReuse()._config_service(shared_dcr=STALE_CLIENT)
    cs.set_config = AsyncMock(return_value=True)
    register = await _sign_in(cs)
    register.assert_awaited_once()
    assert cs.set_config.await_args.args[0].endswith("/dcr-clients/inst-1")
    assert len(_states_stored(cs)) == 1


async def test_mcp_sign_in_unreadable_winner_is_not_overwritten() -> None:
    helpers = mcp_tests.TestBuildOauthAuthorizationUrlDcrReuse()
    cs = helpers._config_service()
    cs.create_config_if_absent = AsyncMock(return_value=False)
    read_shared = cs.get_config.side_effect

    async def get_config(path: str, default: Any = None, **kwargs: Any) -> Any:  # noqa: ANN401
        if kwargs.get("raise_on_error") and path.endswith("/dcr-clients/inst-1"):
            raise ConnectionError("store down")
        return await read_shared(path, default, **kwargs)

    cs.get_config = AsyncMock(side_effect=get_config)
    with pytest.raises(ConnectionError):
        await _sign_in(cs)
    cs.set_config.assert_not_awaited()

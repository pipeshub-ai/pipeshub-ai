"""Cross-org isolation for `app.api.routes.mcp_servers`.

Instance keys carry no org, so these tests go through the real resolver/service
lookups against one store holding two orgs' instances, and check that a caller in
`org-1` can never see or act on `org-2`'s instance.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.agents.constants.mcp_server_constants import (
    get_mcp_oauth_state_claim_path,
    get_mcp_oauth_state_path,
    get_mcp_unhashed_oauth_state_claim_path,
    get_mcp_unhashed_oauth_state_path,
)
from app.agents.mcp.models import (
    MCPAuthMode,
    MCPServerInstanceConfig,
    MCPTransport,
    OAuthTokens,
)
from app.api.routes import mcp_servers
from app.api.routes.mcp_servers import (
    AuthenticateRequest,
    authenticate_instance,
    delete_instance,
    get_agent_mcp_servers,
    get_instance,
    get_my_mcp_servers,
    get_oauth_config,
    handle_oauth_callback,
    list_instances,
    refresh_oauth_token,
    update_instance,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService, route_request

if TYPE_CHECKING:
    from collections.abc import Iterator


def _instance(instance_id: str, org_id: str) -> dict[str, Any]:
    return {
        "_id": instance_id, "orgId": org_id, "createdBy": "admin-1", "name": instance_id,
        "typeId": None, "transport": MCPTransport.STREAMABLE_HTTP.value,
        "authMode": MCPAuthMode.API_TOKEN.value, "url": "https://mcp.example.com",
        "isCustom": True, "createdAt": 1, "updatedAt": 1,
    }


def _store() -> FakeConfigService:
    return FakeConfigService({
        "/services/mcp/instances/mine": _instance("mine", "org-1"),
        "/services/mcp/instances/theirs": _instance("theirs", "org-2"),
    })


def _request(config_service: FakeConfigService) -> MagicMock:
    return route_request(config_service)


@pytest.fixture(autouse=True)
def _caller_is_admin() -> Iterator[None]:
    with patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=True)):
        yield


async def _assert_not_found(coro: Any) -> None:  # noqa: ANN401
    with pytest.raises(HTTPException) as exc:
        await coro
    assert exc.value.status_code == 404


class TestInstanceRoutes:
    async def test_get_another_orgs_instance_is_not_found(self) -> None:
        await _assert_not_found(get_instance(_request(_store()), "theirs"))

    async def test_update_another_orgs_instance_is_not_found_and_writes_nothing(self) -> None:
        store = _store()
        payload = MCPServerInstanceConfig(
            name="hijacked", transport=MCPTransport.STREAMABLE_HTTP, auth_mode=MCPAuthMode.NONE,
            url="https://evil.example.com",
        )

        await _assert_not_found(update_instance(_request(store), "theirs", payload))

        assert store.writes == []
        assert store.data["/services/mcp/instances/theirs"]["name"] == "theirs"

    async def test_delete_another_orgs_instance_is_not_found_and_deletes_nothing(self) -> None:
        store = _store()

        await _assert_not_found(delete_instance(_request(store), "theirs"))

        assert store.deletes == []

    async def test_authenticate_against_another_orgs_instance_writes_no_credential(self) -> None:
        store = _store()

        await _assert_not_found(authenticate_instance(_request(store), "theirs", AuthenticateRequest(apiToken="t")))

        assert store.writes == []

    async def test_admin_list_contains_only_own_org(self) -> None:
        result = await list_instances(_request(_store()))

        assert [i["_id"] for i in result["instances"]] == ["mine"]

    async def test_my_mcp_servers_contains_only_own_org(self) -> None:
        result = await get_my_mcp_servers(_request(_store()), include_tools=False)

        assert [i["_id"] for i in result["instances"]] == ["mine"]

    async def test_agent_mcp_servers_contains_only_own_org(self) -> None:
        with patch("app.api.routes.toolsets._resolve_agent_with_permission", new=AsyncMock()):
            result = await get_agent_mcp_servers(_request(_store()), "agent-1", include_tools=False)

        assert [i["_id"] for i in result["instances"]] == ["mine"]


class TestOAuthRoutes:
    async def test_oauth_config_of_another_orgs_instance_is_not_found(self) -> None:
        store = _store()
        store.data["/services/mcp/oauth-clients/theirs"] = {"clientId": "id", "clientSecret": "secret"}

        await _assert_not_found(get_oauth_config(_request(store), "theirs"))

    async def test_refreshing_a_token_on_another_orgs_instance_is_not_found(self) -> None:
        with patch.object(mcp_servers.mcp_token_refresh, "refresh_credential_record", new=AsyncMock()) as refresh:
            await _assert_not_found(refresh_oauth_token(_request(_store()), "theirs"))

        refresh.assert_not_awaited()


class TestOAuthCallback:
    def _state(self, store: FakeConfigService, *, instance_id: str, org_id: str, path: "str | None" = None) -> str:
        store.data[path or get_mcp_oauth_state_path("s1")] = {
            "instanceId": instance_id, "userId": "admin-1", "orgId": org_id, "initiatedBy": "admin-1",
            "clientId": "client", "isDcr": False, "tokenUrl": "https://auth.example.com/token",
            "redirectUri": "https://app/cb", "codeVerifier": "v",
            "expiresAt": get_epoch_timestamp_in_ms() + 60_000,
        }
        return "s1"

    async def test_state_from_another_org_is_rejected_before_the_exchange(self) -> None:
        store = _store()
        state = self._state(store, instance_id="theirs", org_id="org-2")

        with patch.object(mcp_servers.oauth_client_module, "exchange_code_for_token", new=AsyncMock()) as exchange:
            result = await handle_oauth_callback(_request(store), code="c", state=state, error=None)

        assert result["success"] is False
        assert result["error"] == "caller_mismatch"
        exchange.assert_not_awaited()
        assert not any(key.startswith("/services/mcp/credentials/") for key in store.writes)

    async def test_instance_deleted_mid_flow_is_rejected_before_the_exchange(self) -> None:
        store = _store()
        state = self._state(store, instance_id="gone", org_id="org-1")

        with patch.object(mcp_servers.oauth_client_module, "exchange_code_for_token", new=AsyncMock()) as exchange:
            result = await handle_oauth_callback(_request(store), code="c", state=state, error=None)

        assert result["error"] == "instance_not_found"
        exchange.assert_not_awaited()

    async def test_a_second_callback_for_the_same_state_is_refused(self) -> None:
        """Two browser tabs (or a replay) racing on one state: both read the state before
        either deletes it, but only the first claim may exchange the code."""
        store = _store()
        state = self._state(store, instance_id="mine", org_id="org-1")
        state_record = dict(store.data[get_mcp_oauth_state_path(state)])
        tokens = OAuthTokens(access_token="a", refresh_token="r", expires_in=3600)
        exchange = AsyncMock(return_value=tokens)

        with (
            patch.object(mcp_servers, "_resolve_oauth_client_secret", new=AsyncMock(return_value="secret")),
            patch.object(mcp_servers.oauth_client_module, "exchange_code_for_token", new=exchange),
            patch(
                "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
                return_value=None,
            ),
        ):
            first = await handle_oauth_callback(_request(store), code="c", state=state, error=None)
            store.data[get_mcp_oauth_state_path(state)] = state_record  # the racer read it too
            second = await handle_oauth_callback(_request(store), code="c", state=state, error=None)

        assert first["success"] is True
        assert second["error"] == "invalid_state"
        exchange.assert_awaited_once()

    async def test_a_sign_in_started_before_states_were_hashed_completes(self) -> None:
        # A rolling deploy: an older server stored the state under its plain value.
        store = _store()
        unhashed = get_mcp_unhashed_oauth_state_path("s1")
        state = self._state(store, instance_id="mine", org_id="org-1", path=unhashed)
        tokens = OAuthTokens(access_token="a", refresh_token="r", expires_in=3600)

        with (
            patch.object(mcp_servers, "_resolve_oauth_client_secret", new=AsyncMock(return_value="secret")),
            patch.object(mcp_servers.oauth_client_module, "exchange_code_for_token", new=AsyncMock(return_value=tokens)),
            patch(
                "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
                return_value=None,
            ),
        ):
            result = await handle_oauth_callback(_request(store), code="c", state=state, error=None)

        assert result["success"] is True
        assert unhashed not in store.data
        # Claimed where the older server claims it, so the two can't both use this state.
        assert get_mcp_unhashed_oauth_state_claim_path(state) in store.data
        assert get_mcp_oauth_state_claim_path(state) not in store.data

    async def test_a_new_sign_in_is_claimed_by_hash(self) -> None:
        store = _store()
        state = self._state(store, instance_id="mine", org_id="org-1")
        tokens = OAuthTokens(access_token="a", refresh_token="r", expires_in=3600)

        with (
            patch.object(mcp_servers, "_resolve_oauth_client_secret", new=AsyncMock(return_value="secret")),
            patch.object(mcp_servers.oauth_client_module, "exchange_code_for_token", new=AsyncMock(return_value=tokens)),
            patch(
                "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
                return_value=None,
            ),
        ):
            assert (await handle_oauth_callback(_request(store), code="c", state=state, error=None))["success"] is True

        assert get_mcp_oauth_state_claim_path(state) in store.data
        assert not any(key.endswith(f"/{state}") for key in store.writes)

    async def test_success_stores_the_credential_under_the_validated_org(self) -> None:
        store = _store()
        state = self._state(store, instance_id="mine", org_id="org-1")
        tokens = OAuthTokens(access_token="a", refresh_token="r", expires_in=3600)

        with (
            patch.object(mcp_servers, "_resolve_oauth_client_secret", new=AsyncMock(return_value="secret")),
            patch.object(mcp_servers.oauth_client_module, "exchange_code_for_token", new=AsyncMock(return_value=tokens)),
            patch(
                "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
                return_value=None,
            ),
        ):
            result = await handle_oauth_callback(_request(store), code="c", state=state, error=None)

        assert result == {"success": True, "instanceId": "mine"}
        assert store.data["/services/mcp/credentials/mine/admin-1"]["orgId"] == "org-1"

"""Shared admin credentials, delete-while-attached and credential reset on edit."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.agents.mcp import service as mcp_service
from app.agents.mcp.models import (
    MCPAuthMode,
    MCPServerInstanceConfig,
    MCPServerTemplate,
    MCPTransport,
)
from app.api.routes import mcp_servers
from app.api.routes.mcp_servers import (
    AuthenticateRequest,
    authenticate_agent_instance,
    authenticate_instance,
    auto_authenticate_instance,
    delete_instance,
    reauthenticate_instance,
    remove_credentials,
    update_instance,
)
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService, route_request

if TYPE_CHECKING:
    from collections.abc import Iterator

INSTANCE_PATH = "/services/mcp/instances/inst-1"
SHARED_PATH = "/services/mcp/credentials/inst-1/_shared"
CREATOR_PATH = "/services/mcp/credentials/inst-1/admin-a"


def _instance(**fields: Any) -> dict[str, Any]:  # noqa: ANN401
    record: dict[str, Any] = {
        "_id": "inst-1", "orgId": "org-1", "createdBy": "admin-a", "name": "Search", "typeId": None,
        "transport": "streamable_http", "authMode": "api_token", "useAdminAuth": True,
        "url": "https://mcp.example.com/mcp", "isCustom": True, "createdAt": 1, "updatedAt": 1,
    }
    record.update(fields)
    return record


@pytest.fixture
def admin() -> "Iterator[AsyncMock]":
    check = AsyncMock(return_value=True)
    with patch.object(mcp_servers, "_check_user_is_admin", new=check):
        yield check


@pytest.fixture(autouse=True)
def _no_refresh_service() -> "Iterator[None]":
    with patch(
        "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
        return_value=None,
    ):
        yield


class TestCredentialOwnerId:
    @pytest.mark.parametrize("auth_mode", ["api_token", "headers"])
    def test_a_shared_credential_has_its_own_owner_never_the_creator(self, auth_mode: str) -> None:
        assert mcp_service.credential_owner_id(_instance(authMode=auth_mode), "admin-b") == "_shared"
        assert mcp_service.credential_owner_id(_instance(authMode=auth_mode), "admin-a") == "_shared"

    def test_oauth_is_always_per_caller_even_with_use_admin_auth(self) -> None:
        assert mcp_service.credential_owner_id(_instance(authMode="oauth"), "user-1") == "user-1"

    def test_without_use_admin_auth_the_caller_owns_it(self) -> None:
        assert mcp_service.credential_owner_id(_instance(useAdminAuth=False), "user-1") == "user-1"


class TestSharedAdminCredential:
    @pytest.mark.usefixtures("admin")
    async def test_a_second_admin_saves_the_shared_credential_where_users_read_it(self) -> None:
        store = FakeConfigService({INSTANCE_PATH: _instance()})

        await authenticate_instance(route_request(store, user_id="admin-b"), "inst-1", AuthenticateRequest(apiToken="t"))

        assert "/services/mcp/credentials/inst-1/admin-b" not in store.data
        assert CREATOR_PATH not in store.data
        saved = store.data[SHARED_PATH]
        assert saved["credentials"] == {"apiToken": "t"}
        assert saved["userId"] == "_shared"
        # ...and an ordinary user's read resolves to it.
        seen = await mcp_service.resolve_effective_user_auth(_instance(), "user-1", store)
        assert seen["credentials"] == {"apiToken": "t"}

    async def test_a_non_admin_cannot_reauthenticate_a_shared_credential(self, admin: AsyncMock) -> None:
        admin.return_value = False
        store = FakeConfigService({INSTANCE_PATH: _instance(), "/services/mcp/credentials/inst-1/admin-a": {"x": 1}})

        with pytest.raises(HTTPException) as exc:
            await reauthenticate_instance(route_request(store, user_id="user-1"), "inst-1")

        assert exc.value.status_code == 403
        assert "/services/mcp/credentials/inst-1/admin-a" in store.data

    @pytest.mark.usefixtures("admin")
    async def test_a_second_admin_removes_the_shared_credential(self) -> None:
        store = FakeConfigService({INSTANCE_PATH: _instance(), SHARED_PATH: {"x": 1}})

        await remove_credentials(route_request(store, user_id="admin-b"), "inst-1")

        assert SHARED_PATH not in store.data

    @pytest.mark.usefixtures("admin")
    async def test_removing_a_legacy_shared_credential_leaves_nothing_to_find_again(self) -> None:
        # Saved before the shared slot existed: the credential sits at the creator's path.
        store = FakeConfigService({INSTANCE_PATH: _instance(), CREATOR_PATH: {"isAuthenticated": True}})

        await remove_credentials(route_request(store, user_id="admin-b"), "inst-1")

        assert CREATOR_PATH not in store.data
        assert SHARED_PATH not in store.data
        assert await mcp_service.resolve_effective_user_auth(_instance(), "user-1", store) is None

    @pytest.mark.usefixtures("admin")
    async def test_an_agent_cannot_store_its_own_copy_of_a_shared_credential(self) -> None:
        store = FakeConfigService({INSTANCE_PATH: _instance()})

        with patch.object(mcp_servers, "_require_mcp_agent_edit_access", new=AsyncMock()), \
             pytest.raises(HTTPException) as exc:
            await authenticate_agent_instance(route_request(store), "agent-1", "inst-1", AuthenticateRequest(apiToken="t"))

        assert exc.value.status_code == 400
        assert store.writes == []

    async def test_a_user_can_disconnect_their_own_oauth_tokens_even_with_use_admin_auth_set(
        self, admin: AsyncMock,
    ) -> None:
        admin.return_value = False
        store = FakeConfigService({
            INSTANCE_PATH: _instance(authMode="oauth"),
            "/services/mcp/credentials/inst-1/user-1": {"oauthTokens": {}},
        })

        await remove_credentials(route_request(store, user_id="user-1"), "inst-1")

        assert "/services/mcp/credentials/inst-1/user-1" not in store.data


def _with_graph(request: MagicMock, in_use: Any) -> MagicMock:  # noqa: ANN401
    check = AsyncMock(side_effect=in_use) if isinstance(in_use, Exception) else AsyncMock(return_value=in_use)
    request.app.state.graph_provider.check_mcp_instance_in_use = check
    return request


@pytest.mark.usefixtures("admin")
class TestDeleteWhileAttached:
    async def test_delete_is_blocked_while_agents_use_the_instance(self) -> None:
        store = FakeConfigService({INSTANCE_PATH: _instance(), "/services/mcp/credentials/inst-1/admin-a": {}})

        with pytest.raises(HTTPException) as exc:
            await delete_instance(_with_graph(route_request(store), ["Sales bot", "Triage"]), "inst-1")

        assert exc.value.status_code == 409
        assert "'Sales bot'" in exc.value.detail
        assert store.deletes == []

    async def test_delete_proceeds_when_no_agent_uses_it(self) -> None:
        store = FakeConfigService({INSTANCE_PATH: _instance(), "/services/mcp/credentials/inst-1/admin-a": {}})

        await delete_instance(_with_graph(route_request(store), []), "inst-1")

        assert INSTANCE_PATH not in store.data
        assert "/services/mcp/credentials/inst-1/admin-a" not in store.data

    async def test_a_record_that_could_not_be_deleted_is_reported_not_hidden(self) -> None:
        store = FakeConfigService({INSTANCE_PATH: _instance(), "/services/mcp/credentials/inst-1/admin-a": {}})
        store.delete_config = AsyncMock(return_value=False)  # type: ignore[method-assign]

        with pytest.raises(HTTPException) as exc:
            await delete_instance(_with_graph(route_request(store), []), "inst-1")

        assert exc.value.status_code == 500
        # Nothing hanging off the server is removed while the server itself remains.
        store.delete_config.assert_awaited_once_with(INSTANCE_PATH)

    async def test_delete_fails_closed_when_the_check_fails(self) -> None:
        store = FakeConfigService({INSTANCE_PATH: _instance()})

        with pytest.raises(HTTPException) as exc:
            await delete_instance(_with_graph(route_request(store), RuntimeError("graph down")), "inst-1")

        assert exc.value.status_code == 500
        assert store.deletes == []


def _update_payload(**fields: Any) -> MCPServerInstanceConfig:  # noqa: ANN401
    values: dict[str, Any] = {
        "name": "Search", "transport": MCPTransport.STREAMABLE_HTTP, "auth_mode": MCPAuthMode.API_TOKEN,
        "use_admin_auth": True, "url": "https://mcp.example.com/mcp",
    }
    values.update(fields)
    return MCPServerInstanceConfig(**values)


@pytest.mark.usefixtures("admin")
class TestCredentialResetOnEdit:
    def _store(self, **instance_fields: Any) -> FakeConfigService:  # noqa: ANN401
        return FakeConfigService({
            INSTANCE_PATH: _instance(**instance_fields),
            "/services/mcp/credentials/inst-1/admin-a": {"credentials": {"apiToken": "t"}},
            "/services/mcp/credentials/inst-1/user-1/dcr-client": {"clientId": "c"},
            "/services/mcp/dcr-clients/inst-1": {"clientId": "shared"},
            "/services/mcp/oauth-states/s1": {"instanceId": "inst-1"},
            "/services/mcp/oauth-states/s2": {"instanceId": "other"},
            "/services/mcp/oauth-clients/inst-1": {"clientId": "static"},
        })

    @staticmethod
    async def _shared_credential(store: FakeConfigService) -> Any:  # noqa: ANN401
        # The legacy record keeps its shared credential at the creator's path until an edit
        # moves it into the shared slot.
        return await mcp_service.resolve_effective_user_auth(store.data[INSTANCE_PATH], "user-1", store)

    async def test_a_new_url_removes_every_stored_credential(self) -> None:
        store = self._store()

        result = await update_instance(route_request(store), "inst-1", _update_payload(url="https://moved.example.com/mcp"))

        assert result["credentialsReset"] is True
        remaining = set(store.data)
        assert "/services/mcp/credentials/inst-1/admin-a" not in remaining
        assert "/services/mcp/credentials/inst-1/user-1/dcr-client" not in remaining
        assert "/services/mcp/dcr-clients/inst-1" not in remaining
        assert "/services/mcp/oauth-states/s1" not in remaining
        assert "/services/mcp/oauth-states/s2" in remaining
        assert "/services/mcp/oauth-clients/inst-1" in remaining

    async def test_a_rename_keeps_credentials(self) -> None:
        store = self._store()

        result = await update_instance(route_request(store), "inst-1", _update_payload(name="Renamed"))

        assert result["credentialsReset"] is False
        assert await self._shared_credential(store) == {"credentials": {"apiToken": "t"}}

    async def test_a_new_token_url_removes_credentials(self) -> None:
        store = self._store(authMode="oauth", useAdminAuth=False, tokenUrl="https://auth.example.com/token")
        payload = _update_payload(auth_mode=MCPAuthMode.OAUTH, use_admin_auth=False, token_url="https://other.example.com/token")

        result = await update_instance(route_request(store), "inst-1", payload)

        assert result["credentialsReset"] is True

    def _catalog_request(self, store: FakeConfigService) -> Any:  # noqa: ANN401
        template = MCPServerTemplate(
            type_id="search", display_name="Search", description="d", transport=MCPTransport.STREAMABLE_HTTP,
            default_auth_mode=MCPAuthMode.API_TOKEN, supported_auth_modes=[MCPAuthMode.API_TOKEN, MCPAuthMode.OAUTH],
            default_url="https://mcp.example.com/v2/mcp", token_url="https://auth.example.com/v2/token",
        )
        registry = MagicMock()
        registry.get_template.side_effect = lambda type_id: template if type_id == "search" else None
        return route_request(store, registry=registry)

    async def test_a_catalog_server_whose_template_moved_keeps_credentials_on_a_rename(self) -> None:
        # Saved under an older release: the template's URL and token URL have changed since.
        store = self._store(typeId="search", isCustom=False, url="https://mcp.example.com/mcp", tokenUrl="https://auth.example.com/token")

        result = await update_instance(self._catalog_request(store), "inst-1", _update_payload(type_id="search", name="Renamed"))

        assert result["credentialsReset"] is False
        assert result["url"] == "https://mcp.example.com/v2/mcp"
        assert await self._shared_credential(store) == {"credentials": {"apiToken": "t"}}

    async def test_a_catalog_server_whose_auth_mode_changes_removes_credentials(self) -> None:
        store = self._store(typeId="search", isCustom=False, url="https://mcp.example.com/v2/mcp")
        payload = _update_payload(type_id="search", auth_mode=MCPAuthMode.OAUTH, use_admin_auth=False)

        result = await update_instance(self._catalog_request(store), "inst-1", payload)

        assert result["credentialsReset"] is True
        assert "/services/mcp/credentials/inst-1/admin-a" not in store.data

    async def test_credentials_are_purged_before_the_new_target_is_saved(self) -> None:
        store = self._store()
        order: list[str] = []
        original_set, original_delete = store.set_config, store.delete_config

        async def _set(key: str, value: Any) -> bool:  # noqa: ANN401
            order.append(f"set {key}")
            return await original_set(key, value)

        async def _delete(key: str) -> bool:
            order.append(f"delete {key}")
            return await original_delete(key)

        store.set_config, store.delete_config = _set, _delete
        await update_instance(route_request(store), "inst-1", _update_payload(url="https://moved.example.com/mcp"))

        assert order.index("delete /services/mcp/credentials/inst-1/admin-a") < order.index(f"set {INSTANCE_PATH}")


async def test_my_mcp_servers_refreshes_an_expired_oauth_token_and_lists_tools(admin: AsyncMock) -> None:
    import httpx

    from app.agents.mcp.client import ToolListing
    from app.agents.mcp.errors import MCPConnectionError
    from app.agents.mcp.models import MCPToolInfo, OAuthTokens
    from app.api.routes.mcp_servers import get_my_mcp_servers

    admin.return_value = False
    store = FakeConfigService({
        INSTANCE_PATH: _instance(authMode="oauth", useAdminAuth=False),
        "/services/mcp/credentials/inst-1/user-1": {"isAuthenticated": True, "oauthTokens": {"accessToken": "old"}},
    })

    async def _discover(config: Any, credentials: dict, timeout_seconds: float | None = None) -> ToolListing:  # noqa: ANN401
        if credentials.get("accessToken") == "old":
            request = httpx.Request("POST", "https://mcp.example.com/mcp")
            error = MCPConnectionError("HTTP 401")
            error.__cause__ = httpx.HTTPStatusError("x", request=request, response=httpx.Response(401, request=request))
            raise error
        return ToolListing(tools=[MCPToolInfo(name="search", namespaced_name="mcp_search_search")])

    refresh = AsyncMock(return_value=OAuthTokens(access_token="new"))
    with patch("app.agents.mcp.discovery.discover_tool_listing", _discover), \
         patch("app.agents.mcp.discovery.refresh_credential_record", refresh):
        result = await get_my_mcp_servers(route_request(store, user_id="user-1"), include_tools=True)

    entry = result["instances"][0]
    assert entry["toolsError"] is None
    assert [t["name"] for t in entry["tools"]] == ["search"]
    refresh.assert_awaited_once_with("inst-1", "user-1", store, stale_access_token="old")


def _marked(**fields: Any) -> dict[str, Any]:  # noqa: ANN401
    """A record saved since the shared credential got its own slot."""
    return _instance(sharedCredentialSlot=True, **fields)


def _payload(**fields: Any) -> MCPServerInstanceConfig:  # noqa: ANN401
    values: dict[str, Any] = {
        "name": "Search", "transport": MCPTransport.STREAMABLE_HTTP, "auth_mode": MCPAuthMode.API_TOKEN,
        "use_admin_auth": True, "url": "https://mcp.example.com/mcp",
    }
    values.update(fields)
    return MCPServerInstanceConfig(**values)


@pytest.mark.usefixtures("admin")
class TestSharedCredentialSlot:
    async def test_turning_sharing_on_never_shares_the_creators_personal_token(self) -> None:
        # The creator connected personally while sharing was off.
        store = FakeConfigService({
            INSTANCE_PATH: _marked(useAdminAuth=False),
            CREATOR_PATH: {"isAuthenticated": True, "credentials": {"apiToken": "creators-own"}},
        })

        result = await update_instance(route_request(store, user_id="admin-b"), "inst-1", _payload(use_admin_auth=True))

        assert result["credentialsReset"] is False
        updated = store.data[INSTANCE_PATH]
        assert await mcp_service.resolve_effective_user_auth(updated, "user-1", store) is None
        assert await mcp_service.resolve_effective_user_auth(updated, "admin-a", store) is None
        # The creator's own credential is theirs and stays where it was.
        assert store.data[CREATOR_PATH]["credentials"] == {"apiToken": "creators-own"}

    async def test_turning_a_legacy_record_sharing_on_never_adopts_the_creators_token(self) -> None:
        store = FakeConfigService({
            INSTANCE_PATH: _instance(useAdminAuth=False),
            CREATOR_PATH: {"isAuthenticated": True, "credentials": {"apiToken": "creators-own"}},
        })

        await update_instance(route_request(store, user_id="admin-b"), "inst-1", _payload(use_admin_auth=True))

        updated = store.data[INSTANCE_PATH]
        assert updated["sharedCredentialSlot"] is True
        assert await mcp_service.resolve_effective_user_auth(updated, "user-1", store) is None
        assert CREATOR_PATH in store.data

    async def test_everyone_reads_the_credential_an_admin_saves_for_sharing(self) -> None:
        store = FakeConfigService({INSTANCE_PATH: _marked()})

        await authenticate_instance(route_request(store, user_id="admin-b"), "inst-1", AuthenticateRequest(apiToken="shared"))

        for caller in ("user-1", "admin-a", "agent-key-1"):
            seen = await mcp_service.resolve_effective_user_auth(store.data[INSTANCE_PATH], caller, store)
            assert seen["credentials"] == {"apiToken": "shared"}

    async def test_turning_sharing_off_removes_the_shared_credential_and_says_so(self) -> None:
        store = FakeConfigService({
            INSTANCE_PATH: _marked(),
            SHARED_PATH: {"isAuthenticated": True, "credentials": {"apiToken": "shared"}},
            "/services/mcp/credentials/inst-1/user-1": {"isAuthenticated": True, "credentials": {"apiToken": "mine"}},
        })

        result = await update_instance(route_request(store, user_id="admin-b"), "inst-1", _payload(use_admin_auth=False))

        assert result["credentialsReset"] is True
        assert SHARED_PATH not in store.data
        updated = store.data[INSTANCE_PATH]
        assert (await mcp_service.resolve_effective_user_auth(updated, "user-1", store))["credentials"] == {"apiToken": "mine"}
        assert await mcp_service.resolve_effective_user_auth(updated, "user-2", store) is None

    async def test_turning_sharing_back_on_starts_empty(self) -> None:
        store = FakeConfigService({INSTANCE_PATH: _marked(useAdminAuth=False), SHARED_PATH: {"isAuthenticated": True}})

        result = await update_instance(route_request(store, user_id="admin-b"), "inst-1", _payload(use_admin_auth=True))

        assert result["credentialsReset"] is False
        assert SHARED_PATH not in store.data

    async def test_a_legacy_shared_credential_moves_on_first_read(self) -> None:
        legacy = {"isAuthenticated": True, "credentials": {"apiToken": "shared"}}
        store = FakeConfigService({INSTANCE_PATH: _instance(), CREATOR_PATH: legacy})

        first = await mcp_service.resolve_effective_user_auth(_instance(), "user-1", store)

        assert first == legacy
        assert store.data[SHARED_PATH] == legacy
        assert CREATOR_PATH not in store.data
        assert await mcp_service.resolve_effective_user_auth(_instance(), "user-2", store) == legacy

    async def test_editing_a_legacy_shared_instance_keeps_its_shared_credential(self) -> None:
        legacy = {"isAuthenticated": True, "credentials": {"apiToken": "shared"}}
        store = FakeConfigService({INSTANCE_PATH: _instance(), CREATOR_PATH: legacy})

        result = await update_instance(route_request(store, user_id="admin-b"), "inst-1", _payload(name="Renamed"))

        assert result["credentialsReset"] is False
        updated = store.data[INSTANCE_PATH]
        assert updated["sharedCredentialSlot"] is True
        assert await mcp_service.resolve_effective_user_auth(updated, "user-1", store) == legacy
        assert CREATOR_PATH not in store.data

    async def test_a_marked_record_never_looks_at_the_creators_path(self) -> None:
        store = FakeConfigService({INSTANCE_PATH: _marked(), CREATOR_PATH: {"isAuthenticated": True}})

        assert await mcp_service.resolve_effective_user_auth(_marked(), "user-1", store) is None
        assert CREATOR_PATH in store.data

    async def test_auto_authenticate_finds_a_legacy_shared_credential(self) -> None:
        store = FakeConfigService({INSTANCE_PATH: _instance(), CREATOR_PATH: {"isAuthenticated": True}})

        result = await auto_authenticate_instance(route_request(store, user_id="user-1"), "inst-1")

        assert result == {"success": True, "isAuthenticated": True}
        assert SHARED_PATH in store.data

    async def test_new_records_are_marked(self) -> None:
        store = FakeConfigService({INSTANCE_PATH: _marked()})

        await update_instance(route_request(store, user_id="admin-b"), "inst-1", _payload(name="Renamed"))

        assert store.data[INSTANCE_PATH]["sharedCredentialSlot"] is True

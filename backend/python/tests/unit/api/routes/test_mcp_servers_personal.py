"""User-created (personal) MCP server instances.

Any user may create an instance; theirs is stored under their own key prefix and only
they can see or use it. Administrators own the org-wide instances, and may list and
delete a user's personal instance but never use it. These tests go through the real
resolver/service lookups against one in-memory store.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.agents.mcp import service as mcp_service
from app.agents.mcp.models import (
    MCPAuthMode,
    MCPServerInstanceConfig,
    MCPTransport,
    OAuthTokens,
)
from app.api.routes import mcp_servers
from app.api.routes.mcp_servers import (
    AuthenticateRequest,
    OAuthClientConfigRequest,
    authenticate_instance,
    create_instance,
    delete_instance,
    get_agent_mcp_servers,
    get_instance,
    get_my_mcp_servers,
    get_oauth_authorization_url,
    get_oauth_config,
    handle_oauth_callback,
    list_instances,
    update_instance,
    update_oauth_config,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService, route_request

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG_PATH = "/services/mcp/instances/{}"
USER_PATH = "/services/mcp/user-instances/org-1/{}/{}"


def _record(instance_id: str, *, owner: str = "admin-1", org_id: str = "org-1", scope: str | None = None) -> dict[str, Any]:
    record = {
        "_id": instance_id, "orgId": org_id, "createdBy": owner, "name": instance_id,
        "typeId": None, "transport": MCPTransport.STREAMABLE_HTTP.value,
        "authMode": MCPAuthMode.API_TOKEN.value, "url": "https://mcp.example.com",
        "isCustom": True, "createdAt": 1, "updatedAt": 1,
    }
    if scope:
        record["scope"] = scope
    return record


def _store() -> FakeConfigService:
    return FakeConfigService({
        ORG_PATH.format("org-inst"): _record("org-inst"),
        USER_PATH.format("u-alice", "alice-own"): _record("alice-own", owner="u-alice", scope="personal"),
        USER_PATH.format("u-bob", "bob-own"): _record("bob-own", owner="u-bob", scope="personal"),
        "/services/mcp/user-instances/org-2/u-eve/other-org": _record("other-org", owner="u-eve", org_id="org-2", scope="personal"),
    })


class _RecordingStore(FakeConfigService):
    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__(data)
        self.listed: list[str] = []
        self.read: list[str] = []

    async def get_config(self, key: str, default: object = None, use_cache: bool = False) -> object:
        self.read.append(key)
        return await super().get_config(key, default, use_cache)

    async def list_keys_in_directory(self, prefix: str) -> list[str]:
        self.listed.append(prefix)
        return await super().list_keys_in_directory(prefix)


def _payload(**overrides: Any) -> MCPServerInstanceConfig:  # noqa: ANN401
    fields: dict[str, Any] = {
        "name": "mine", "transport": MCPTransport.STREAMABLE_HTTP, "auth_mode": MCPAuthMode.API_TOKEN,
        "url": "https://mcp.example.com/mcp",
    }
    fields.update(overrides)
    return MCPServerInstanceConfig(**fields)


def _as(store: FakeConfigService, user_id: str) -> MagicMock:
    return route_request(store, user_id=user_id)


@pytest.fixture(autouse=True)
def _only_admin_1_is_admin() -> Iterator[None]:
    async def _is_admin(user_id: str, *_args: object, **_kwargs: object) -> bool:
        return user_id == "admin-1"

    with patch.object(mcp_servers, "_check_user_is_admin", new=_is_admin):
        yield


@pytest.fixture
def _no_refresh_service() -> Iterator[None]:
    with patch(
        "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
        return_value=None,
    ):
        yield


async def _status(coro: Any) -> int:  # noqa: ANN401
    with pytest.raises(HTTPException) as exc:
        await coro
    return exc.value.status_code


class TestServiceLookups:
    async def test_a_user_sees_the_org_instances_and_only_their_own(self) -> None:
        visible = await mcp_service.load_visible_instances(_store(), "org-1", "u-alice")
        assert sorted(i["_id"] for i in visible) == ["alice-own", "org-inst"]

    async def test_without_a_user_only_org_instances_are_visible(self) -> None:
        visible = await mcp_service.load_visible_instances(_store(), "org-1", None)
        assert [i["_id"] for i in visible] == ["org-inst"]

    async def test_get_resolves_a_personal_instance_for_its_owner_only(self) -> None:
        store = _store()
        assert await mcp_service.get_instance("bob-own", store, "org-1", "u-bob") is not None
        assert await mcp_service.get_instance("bob-own", store, "org-1", "u-alice") is None
        assert await mcp_service.get_instance("bob-own", store, "org-1") is None

    async def test_a_record_under_a_user_path_must_name_that_user(self) -> None:
        store = _store()
        store.data[USER_PATH.format("u-alice", "planted")] = _record("planted", owner="u-bob", scope="personal")
        assert await mcp_service.get_instance("planted", store, "org-1", "u-alice") is None
        assert "planted" not in [i["_id"] for i in await mcp_service.load_user_instances(store, "org-1", "u-alice")]

    async def test_a_personal_record_at_the_org_path_is_not_an_org_instance(self) -> None:
        store = _store()
        store.data[ORG_PATH.format("misplaced")] = _record("misplaced", owner="u-bob", scope="personal")
        assert await mcp_service.get_instance("misplaced", store, "org-1", "u-alice") is None
        assert "misplaced" not in [i["_id"] for i in await mcp_service.load_org_instances(store, "org-1")]

    def test_a_record_without_scope_is_an_org_instance(self) -> None:
        assert mcp_service.instance_scope(_record("legacy")) == mcp_service.SCOPE_ORG
        assert mcp_service.instance_record_path(_record("legacy")) == ORG_PATH.format("legacy")

    async def test_admin_listing_of_personal_instances_is_scoped_to_the_org(self) -> None:
        personal = await mcp_service.load_personal_instances_for_admin(_store(), "org-1")
        assert sorted(i["_id"] for i in personal) == ["alice-own", "bob-own"]

    async def test_admin_review_never_lists_or_reads_another_orgs_records(self) -> None:
        store = _RecordingStore(_store().data)

        await mcp_service.load_personal_instances_for_admin(store, "org-1")
        found = await mcp_service.find_personal_instance_for_admin("other-org", store, "org-1")

        assert found is None
        assert store.listed == ["/services/mcp/user-instances/org-1/"] * 2
        assert not [key for key in store.read if "/org-2/" in key]


class TestCreate:
    async def test_a_user_creates_a_personal_instance_even_when_asking_for_org(self) -> None:
        store = FakeConfigService()
        record = await create_instance(_as(store, "u-alice"), _payload(scope="org"))

        assert record["scope"] == "personal"
        assert store.data[USER_PATH.format("u-alice", record["_id"])] == record
        assert ORG_PATH.format(record["_id"]) not in store.data

    async def test_an_admin_creates_org_instances_by_default_and_personal_on_request(self) -> None:
        store = FakeConfigService()
        org = await create_instance(_as(store, "admin-1"), _payload())
        own = await create_instance(_as(store, "admin-1"), _payload(scope="personal"))

        assert org["scope"] == "org" and ORG_PATH.format(org["_id"]) in store.data
        assert own["scope"] == "personal" and USER_PATH.format("admin-1", own["_id"]) in store.data

    async def test_an_unknown_scope_is_rejected(self) -> None:
        assert await _status(create_instance(_as(FakeConfigService(), "u-alice"), _payload(scope="global"))) == 400

    async def test_a_personal_instance_cannot_run_a_local_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", "true")
        payload = _payload(transport=MCPTransport.STDIO, url=None, command="npx", args=["some-server"])
        assert await _status(create_instance(_as(FakeConfigService(), "u-alice"), payload)) == 400

    async def test_a_personal_instance_cannot_share_an_admin_credential(self) -> None:
        payload = _payload(use_admin_auth=True)
        assert await _status(create_instance(_as(FakeConfigService(), "u-alice"), payload)) == 400

    async def test_the_per_user_cap_is_enforced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_MAX_PERSONAL_INSTANCES", "1")
        store = _store()
        assert await _status(create_instance(_as(store, "u-alice"), _payload())) == 409
        # The cap counts only the caller's own instances.
        await create_instance(_as(store, "u-carol"), _payload())

    @pytest.mark.parametrize(("value", "expected"), [("lots", 25), ("-3", 0), ("", 25), ("7", 7)])
    def test_the_cap_setting_is_read_safely(self, monkeypatch: pytest.MonkeyPatch, value: str, expected: int) -> None:
        monkeypatch.setenv("MCP_MAX_PERSONAL_INSTANCES", value)
        assert mcp_servers._max_personal_instances() == expected

    async def test_a_personal_instance_url_must_be_public(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_ALLOW_PRIVATE_NETWORK_URLS", raising=False)
        store = FakeConfigService()
        private = _payload(url="http://10.0.0.5/mcp")

        assert await _status(create_instance(_as(store, "u-alice"), private)) == 400
        assert await _status(create_instance(_as(store, "u-alice"), _payload(url="http://100.64.0.9/mcp"))) == 400
        # An administrator's org instance may sit on the private network.
        assert (await create_instance(_as(store, "admin-1"), private))["scope"] == "org"


class TestOwnerOnlyAccess:
    @pytest.mark.parametrize("caller", ["u-bob", "admin-1"])
    async def test_someone_elses_personal_instance_cannot_be_viewed_or_used(self, caller: str) -> None:
        store = _store()
        request = _as(store, caller)

        assert await _status(get_instance(request, "alice-own")) == 404
        assert await _status(update_instance(request, "alice-own", _payload())) == 404
        assert await _status(authenticate_instance(request, "alice-own", AuthenticateRequest(apiToken="t"))) == 404
        assert await _status(get_oauth_config(request, "alice-own")) == 404
        assert await _status(get_oauth_authorization_url(request, "alice-own", base_url=None)) == 404
        assert not any(key.startswith("/services/mcp/credentials/") for key in store.writes)

    async def test_another_user_cannot_delete_it(self) -> None:
        store = _store()
        assert await _status(delete_instance(_as(store, "u-bob"), "alice-own")) == 404
        assert store.deletes == []

    async def test_the_owner_updates_it_and_the_scope_stays_personal(self) -> None:
        store = _store()
        updated = await update_instance(_as(store, "u-alice"), "alice-own", _payload(name="renamed", scope="org"))

        assert updated["scope"] == "personal"
        assert store.data[USER_PATH.format("u-alice", "alice-own")]["name"] == "renamed"
        assert ORG_PATH.format("alice-own") not in store.data

    async def test_the_owner_cannot_move_it_onto_the_private_network(self) -> None:
        store = _store()
        assert await _status(update_instance(_as(store, "u-alice"), "alice-own", _payload(url="http://10.0.0.5/mcp"))) == 400
        assert store.data[USER_PATH.format("u-alice", "alice-own")]["url"] == "https://mcp.example.com"

    async def test_the_owner_authenticates_against_it(self) -> None:
        store = _store()
        await authenticate_instance(_as(store, "u-alice"), "alice-own", AuthenticateRequest(apiToken="t"))
        assert "/services/mcp/credentials/alice-own/u-alice" in store.data

    @pytest.mark.usefixtures("_no_refresh_service")
    async def test_the_owner_deletes_it(self) -> None:
        store = _store()
        with patch.object(mcp_servers, "_assert_instance_not_in_use", new=AsyncMock()):
            await delete_instance(_as(store, "u-alice"), "alice-own")
        assert USER_PATH.format("u-alice", "alice-own") not in store.data

    async def test_the_owner_manages_its_oauth_app(self) -> None:
        store = _store()
        request = _as(store, "u-alice")
        await update_oauth_config(request, "alice-own", OAuthClientConfigRequest(clientId="cid", clientSecret="secret-value"))
        assert (await get_oauth_config(request, "alice-own"))["configured"] is True

    async def test_a_user_cannot_manage_an_org_instance(self) -> None:
        store = _store()
        request = _as(store, "u-alice")
        assert await _status(update_instance(request, "org-inst", _payload())) == 403
        assert await _status(delete_instance(request, "org-inst")) == 403
        assert await _status(update_oauth_config(request, "org-inst", OAuthClientConfigRequest(clientId="c", clientSecret="s"))) == 403


class TestAdministratorReview:
    async def test_listing_includes_personal_instances_only_on_request(self) -> None:
        store = _store()
        plain = await list_instances(_as(store, "admin-1"), include_personal=False)
        everything = await list_instances(_as(store, "admin-1"), include_personal=True)

        assert [i["_id"] for i in plain["instances"]] == ["org-inst"]
        by_id = {i["_id"]: i for i in everything["instances"]}
        assert sorted(by_id) == ["alice-own", "bob-own", "org-inst"]
        assert by_id["alice-own"]["scope"] == "personal" and by_id["alice-own"]["createdBy"] == "u-alice"
        assert by_id["org-inst"]["scope"] == "org"

    async def test_an_admin_never_sees_how_to_reach_a_personal_instance(self) -> None:
        store = _store()
        secret = USER_PATH.format("u-alice", "alice-own")
        store.data[secret] = {
            **store.data[secret], "url": "https://mcp.zapier.com/api/mcp/s/SECRET/mcp", "headerName": "X-Key",
            "command": None, "args": ["--token", "SECRET"], "tokenUrl": "https://auth/token?k=SECRET",
            "authorizationUrl": "https://auth/authorize", "scopes": ["all"], "description": "mine",
        }

        listed = await list_instances(_as(store, "admin-1"), include_personal=True)

        by_id = {i["_id"]: i for i in listed["instances"]}
        assert "SECRET" not in repr(by_id["alice-own"])
        assert set(by_id["alice-own"]) == {
            "_id", "orgId", "createdBy", "name", "typeId", "transport", "authMode", "isCustom",
            "createdAt", "updatedAt", "scope",
        }
        assert by_id["org-inst"]["url"] == "https://mcp.example.com"

    @pytest.mark.usefixtures("_no_refresh_service")
    async def test_an_admin_deletes_a_users_personal_instance(self) -> None:
        store = _store()
        with patch.object(mcp_servers, "_assert_instance_not_in_use", new=AsyncMock()):
            await delete_instance(_as(store, "admin-1"), "alice-own")
        assert USER_PATH.format("u-alice", "alice-own") not in store.data

    async def test_an_admin_cannot_delete_another_orgs_personal_instance(self) -> None:
        store = _store()
        assert await _status(delete_instance(_as(store, "admin-1"), "other-org")) == 404
        assert store.deletes == []


class TestConsumption:
    async def test_my_mcp_servers_lists_the_org_instances_and_my_own(self) -> None:
        result = await get_my_mcp_servers(_as(_store(), "u-alice"), include_tools=False)
        by_id = {i["_id"]: i["scope"] for i in result["instances"]}
        assert by_id == {"org-inst": "org", "alice-own": "personal"}

    async def test_service_account_listing_has_only_org_instances(self) -> None:
        with patch("app.api.routes.toolsets._resolve_agent_with_permission", new=AsyncMock()):
            result = await get_agent_mcp_servers(_as(_store(), "u-alice"), "agent-1", include_tools=False)
        assert [i["_id"] for i in result["instances"]] == ["org-inst"]

    @pytest.mark.usefixtures("_no_refresh_service")
    async def test_oauth_callback_resolves_the_initiators_personal_instance(self) -> None:
        store = _store()
        store.data["/services/mcp/oauth-states/s1"] = {
            "instanceId": "alice-own", "userId": "u-alice", "orgId": "org-1", "initiatedBy": "u-alice",
            "clientId": "client", "isDcr": False, "tokenUrl": "https://auth.example.com/token",
            "redirectUri": "https://app/cb", "codeVerifier": "v",
            "expiresAt": get_epoch_timestamp_in_ms() + 60_000,
        }
        exchange = AsyncMock(return_value=OAuthTokens(access_token="a", refresh_token="r", expires_in=3600))
        with (
            patch.object(mcp_servers, "_resolve_oauth_client_secret", new=AsyncMock(return_value="secret")),
            patch.object(mcp_servers.oauth_client_module, "exchange_code_for_token", new=exchange),
        ):
            result = await handle_oauth_callback(_as(store, "u-alice"), code="c", state="s1", error=None)

        assert result == {"success": True, "instanceId": "alice-own"}
        assert exchange.await_args.kwargs["allow_private"] is False
        assert "/services/mcp/credentials/alice-own/u-alice" in store.data

    async def test_the_assistant_attaches_my_authenticated_personal_instance_only(self) -> None:
        from app.api.routes.agent import get_assistant_agent

        store = _store()
        for instance_id, user_id in (("alice-own", "u-alice"), ("bob-own", "u-bob"), ("org-inst", "u-alice")):
            store.data[f"/services/mcp/credentials/{instance_id}/{user_id}"] = {"isAuthenticated": True, "apiToken": "t"}
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        graph.list_user_knowledge_bases = AsyncMock(return_value=([], 0, None))
        graph.get_user_apps = AsyncMock(return_value=[])

        agent, _ = await get_assistant_agent(
            "u-alice", "org-1", store, graph, None, MagicMock(), actions_enabled=False, mcp_enabled=True,
        )

        assert sorted(m["instanceId"] for m in agent["mcpServers"]) == ["alice-own", "org-inst"]


_CONNECTION_DETAILS = {"url", "command", "args", "authorizationUrl", "tokenUrl", "scopes"}


def _store_with_connection_details() -> FakeConfigService:
    store = _store()
    store.data[ORG_PATH.format("org-inst")].update(
        url="https://mcp.example.com/mcp?key=secret", args=["--token", "secret"],
        authorizationUrl="https://auth.example.com/authorize", tokenUrl="https://auth.example.com/token",
        scopes=["read"], requiredEnv=["API_KEY"], headerName="X-Api-Key",
    )
    return store


class TestWhatMembersSeeOfOrgServers:
    async def test_a_member_gets_no_connection_details_for_an_org_server(self) -> None:
        result = await get_my_mcp_servers(_as(_store_with_connection_details(), "u-alice"), include_tools=False)

        org = next(i for i in result["instances"] if i["_id"] == "org-inst")
        assert not _CONNECTION_DETAILS & org.keys()
        # Still enough to connect: what to sign in with, and which values to enter.
        assert org["authMode"] == MCPAuthMode.API_TOKEN.value
        assert org["requiredEnv"] == ["API_KEY"]
        assert org["headerName"] == "X-Api-Key"
        assert org["isAuthenticated"] is False

    async def test_an_administrator_gets_the_full_record(self) -> None:
        result = await get_my_mcp_servers(_as(_store_with_connection_details(), "admin-1"), include_tools=False)

        org = next(i for i in result["instances"] if i["_id"] == "org-inst")
        assert org["url"] == "https://mcp.example.com/mcp?key=secret"
        assert org["args"] == ["--token", "secret"]

    async def test_the_owner_sees_all_of_their_own_server(self) -> None:
        result = await get_my_mcp_servers(_as(_store_with_connection_details(), "u-alice"), include_tools=False)

        own = next(i for i in result["instances"] if i["_id"] == "alice-own")
        assert own["url"] == "https://mcp.example.com"

    async def test_the_agent_listing_hides_them_from_members_too(self) -> None:
        with patch("app.api.routes.toolsets._resolve_agent_with_permission", new=AsyncMock(return_value={"can_edit": True})):
            result = await get_agent_mcp_servers(_as(_store_with_connection_details(), "u-alice"), "agent-1", include_tools=False)

        assert not _CONNECTION_DETAILS & result["instances"][0].keys()


class TestAgentToolsNeedEditAccess:
    def _store(self) -> FakeConfigService:
        store = _store()
        store.data[ORG_PATH.format("org-inst")]["authMode"] = MCPAuthMode.NONE.value
        return store

    async def _list(self, *, can_edit: bool, discover: AsyncMock) -> dict[str, Any]:
        with (
            patch("app.api.routes.toolsets._resolve_agent_with_permission", new=AsyncMock(return_value={"can_edit": can_edit})),
            patch.object(mcp_servers, "discover_tools_for_owner", new=discover),
        ):
            return await get_agent_mcp_servers(_as(self._store(), "u-alice"), "agent-1", include_tools=True)

    async def test_a_viewer_gets_the_status_without_using_the_agents_credentials(self) -> None:
        discover = AsyncMock(return_value=([], {}))

        result = await self._list(can_edit=False, discover=discover)

        discover.assert_not_awaited()
        assert result["instances"][0]["isAuthenticated"] is True
        assert result["instances"][0]["tools"] == []

    async def test_an_editor_gets_the_tools(self) -> None:
        discover = AsyncMock(return_value=([], {}))

        await self._list(can_edit=True, discover=discover)

        discover.assert_awaited_once()
        assert discover.await_args.args[2] == "agent-1"


# The real admin check, kept before the module's fixture swaps in a fake for every test.
_REAL_CHECK_USER_IS_ADMIN = mcp_servers._check_user_is_admin


class TestAnUnconfirmedRoleCreatesNothing:
    """DATA-3: when Node can't say who the caller is, creating an org-capable server fails
    rather than quietly making it personal (its scope can't change afterwards)."""

    @pytest.fixture(autouse=True)
    def _real_role_check(self) -> Iterator[None]:
        with patch.object(mcp_servers, "_check_user_is_admin", new=_REAL_CHECK_USER_IS_ADMIN):
            yield

    @staticmethod
    def _role(status: str, role: str = "member") -> Any:  # noqa: ANN401
        from app.api.middlewares.caller_role import CallerRole, CallerRoleStatus

        return patch(
            "app.api.routes.mcp_servers.fetch_caller_role",
            new=AsyncMock(return_value=CallerRole(CallerRoleStatus(status), role)),
        )

    @pytest.mark.parametrize("status", ["unknown", "rejected"])
    async def test_an_unconfirmed_role_is_an_error_not_a_personal_server(self, status: str) -> None:
        store = FakeConfigService()
        with self._role(status):
            assert await _status(create_instance(_as(store, "admin-1"), _payload())) == 503

        assert not store.data

    async def test_asking_for_a_personal_server_needs_no_role(self) -> None:
        store = FakeConfigService()
        with self._role("unknown") as lookup:
            record = await create_instance(_as(store, "u-alice"), _payload(scope="personal"))

        assert record["scope"] == "personal"
        lookup.assert_not_awaited()

    @pytest.mark.parametrize(("role", "scope"), [("admin", "org"), ("member", "personal")])
    async def test_a_confirmed_role_decides(self, role: str, scope: str) -> None:
        store = FakeConfigService()
        with self._role("valid", role):
            record = await create_instance(_as(store, "u-alice"), _payload())

        assert record["scope"] == scope

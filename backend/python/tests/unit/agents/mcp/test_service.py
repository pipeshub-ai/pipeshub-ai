"""Unit tests for app.agents.mcp.service."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.mcp.models import MCPAuthMode
from app.agents.mcp.service import (
    credentials_to_discovery_dict,
    get_authenticated_mcp_servers,
    get_instance,
    instance_config_from_dict,
    is_effective_auth_authenticated,
    is_mcp_enabled,
    load_org_instances,
    match_enabled_tools_for_mcp_server,
    resolve_effective_user_auth,
)


def _instance(**overrides) -> dict:
    base = {
        "_id": "inst-1",
        "orgId": "org-1",
        "createdBy": "user-1",
        "name": "Brave Search",
        "typeId": "brave_search",
        "transport": "stdio",
        "authMode": MCPAuthMode.API_TOKEN.value,
        "useAdminAuth": False,
        "isCustom": False,
        "createdAt": 1,
        "updatedAt": 2,
    }
    base.update(overrides)
    return base


class TestLoadOrgInstances:
    @pytest.mark.asyncio
    async def test_returns_parsed_dicts(self) -> None:
        cfg = MagicMock()
        cfg.list_keys_in_directory = AsyncMock(return_value=["/services/mcp/instances/org-1/inst-1"])
        cfg.get_config = AsyncMock(return_value=_instance())
        result = await load_org_instances("org-1", cfg)
        assert len(result) == 1
        assert result[0]["_id"] == "inst-1"

    @pytest.mark.asyncio
    async def test_skips_non_dict_entries(self) -> None:
        cfg = MagicMock()
        cfg.list_keys_in_directory = AsyncMock(return_value=["/a", "/b"])
        cfg.get_config = AsyncMock(side_effect=[None, _instance()])
        result = await load_org_instances("org-1", cfg)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_list_failure_returns_empty(self) -> None:
        cfg = MagicMock()
        cfg.list_keys_in_directory = AsyncMock(side_effect=RuntimeError("etcd down"))
        result = await load_org_instances("org-1", cfg)
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_keys_that_fail_to_load(self) -> None:
        cfg = MagicMock()
        cfg.list_keys_in_directory = AsyncMock(return_value=["/a", "/b"])
        cfg.get_config = AsyncMock(side_effect=[RuntimeError("corrupt key"), _instance()])
        result = await load_org_instances("org-1", cfg)
        assert len(result) == 1
        assert result[0]["_id"] == "inst-1"


class TestGetInstance:
    @pytest.mark.asyncio
    async def test_returns_dict(self) -> None:
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value=_instance())
        result = await get_instance("org-1", "inst-1", cfg)
        assert result["_id"] == "inst-1"

    @pytest.mark.asyncio
    async def test_missing_returns_none(self) -> None:
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value=None)
        assert await get_instance("org-1", "inst-1", cfg) is None


class TestResolveEffectiveUserAuth:
    @pytest.mark.asyncio
    async def test_none_auth_mode_short_circuits(self) -> None:
        instance = _instance(authMode=MCPAuthMode.NONE.value)
        cfg = MagicMock()
        result = await resolve_effective_user_auth(instance, "user-2", cfg)
        assert result == {}
        cfg.get_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_admin_auth_resolves_creator_record(self) -> None:
        instance = _instance(useAdminAuth=True, createdBy="admin-1")
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value={"isAuthenticated": True})
        await resolve_effective_user_auth(instance, "user-2", cfg)
        called_path = cfg.get_config.await_args.args[0]
        assert called_path.endswith("/inst-1/admin-1")

    @pytest.mark.asyncio
    async def test_oauth_ignores_admin_auth_flag(self) -> None:
        instance = _instance(authMode=MCPAuthMode.OAUTH.value, useAdminAuth=True, createdBy="admin-1")
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value={"isAuthenticated": True})
        await resolve_effective_user_auth(instance, "user-2", cfg)
        called_path = cfg.get_config.await_args.args[0]
        assert called_path.endswith("/inst-1/user-2")

    @pytest.mark.asyncio
    async def test_missing_record_returns_none(self) -> None:
        instance = _instance()
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value=None)
        assert await resolve_effective_user_auth(instance, "user-2", cfg) is None


class TestIsEffectiveAuthAuthenticated:
    def test_empty_dict_is_authenticated(self) -> None:
        assert is_effective_auth_authenticated({}) is True

    def test_none_is_not_authenticated(self) -> None:
        assert is_effective_auth_authenticated(None) is False

    def test_missing_flag_is_not_authenticated(self) -> None:
        assert is_effective_auth_authenticated({"credentials": {}}) is False

    def test_true_flag_is_authenticated(self) -> None:
        assert is_effective_auth_authenticated({"isAuthenticated": True}) is True


class TestInstanceConfigFromDict:
    def test_builds_typed_config(self) -> None:
        cfg = instance_config_from_dict(_instance())
        assert cfg.id == "inst-1"
        assert cfg.type_id == "brave_search"
        assert cfg.is_custom is False


class TestCredentialsToDiscoveryDict:
    def test_oauth_extracts_access_token(self) -> None:
        result = credentials_to_discovery_dict(MCPAuthMode.OAUTH.value, {"oauthTokens": {"accessToken": "tok"}})
        assert result == {"accessToken": "tok"}

    def test_non_oauth_returns_credentials(self) -> None:
        result = credentials_to_discovery_dict(MCPAuthMode.API_TOKEN.value, {"credentials": {"apiToken": "x"}})
        assert result == {"apiToken": "x"}


class TestGetAuthenticatedMcpServers:
    @pytest.mark.asyncio
    async def test_filters_to_authenticated_only(self) -> None:
        authenticated = _instance(_id="inst-1", typeId="brave_search")
        unauthenticated = _instance(_id="inst-2", typeId="exa", name="Exa")
        cfg = MagicMock()
        cfg.list_keys_in_directory = AsyncMock(
            return_value=["/services/mcp/instances/org-1/inst-1", "/services/mcp/instances/org-1/inst-2"]
        )

        async def _get_config_side_effect(path, default=None, use_cache=False):
            if path.endswith("/inst-1"):
                return authenticated
            if path.endswith("/inst-2"):
                return unauthenticated
            if "/credentials/inst-1/" in path:
                return {"isAuthenticated": True}
            if "/credentials/inst-2/" in path:
                return {"isAuthenticated": False}
            return None

        cfg.get_config = AsyncMock(side_effect=_get_config_side_effect)
        result = await get_authenticated_mcp_servers("user-1", "org-1", cfg)
        assert [r["instanceId"] for r in result] == ["inst-1"]

    @pytest.mark.asyncio
    async def test_dedupes_by_type_key_keeping_first(self) -> None:
        first = _instance(_id="inst-1", typeId="brave_search", name="Brave A")
        second = _instance(_id="inst-2", typeId="brave_search", name="Brave B")
        cfg = MagicMock()
        cfg.list_keys_in_directory = AsyncMock(
            return_value=["/services/mcp/instances/org-1/inst-1", "/services/mcp/instances/org-1/inst-2"]
        )

        async def _get_config_side_effect(path, default=None, use_cache=False):
            if path.endswith("/inst-1"):
                return first
            if path.endswith("/inst-2"):
                return second
            if "/credentials/" in path:
                return {"isAuthenticated": True}
            return None

        cfg.get_config = AsyncMock(side_effect=_get_config_side_effect)
        result = await get_authenticated_mcp_servers("user-1", "org-1", cfg)
        assert len(result) == 1
        assert result[0]["instanceId"] == "inst-1"

    @pytest.mark.asyncio
    async def test_no_instances_returns_empty(self) -> None:
        cfg = MagicMock()
        cfg.list_keys_in_directory = AsyncMock(return_value=[])
        result = await get_authenticated_mcp_servers("user-1", "org-1", cfg)
        assert result == []

    @pytest.mark.asyncio
    async def test_load_failure_returns_empty(self) -> None:
        cfg = MagicMock()
        cfg.list_keys_in_directory = AsyncMock(side_effect=RuntimeError("boom"))
        result = await get_authenticated_mcp_servers("user-1", "org-1", cfg)
        assert result == []

    @pytest.mark.asyncio
    async def test_load_org_instances_raising_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Defensive path: if load_org_instances ever propagates, callers still get []."""
        async def _raise(*_args, **_kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr("app.agents.mcp.service.load_org_instances", _raise)
        result = await get_authenticated_mcp_servers("user-1", "org-1", MagicMock())
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_instances_whose_auth_resolution_fails(self) -> None:
        good = _instance(_id="inst-1", typeId="brave_search")
        bad = _instance(_id="inst-2", typeId="exa", name="Exa")
        cfg = MagicMock()
        cfg.list_keys_in_directory = AsyncMock(
            return_value=["/services/mcp/instances/org-1/inst-1", "/services/mcp/instances/org-1/inst-2"]
        )

        async def _get_config_side_effect(path, default=None, use_cache=False):
            if path.endswith("/inst-1"):
                return good
            if path.endswith("/inst-2"):
                return bad
            if "/credentials/inst-1/" in path:
                return {"isAuthenticated": True}
            if "/credentials/inst-2/" in path:
                raise RuntimeError("etcd read failed")
            return None

        cfg.get_config = AsyncMock(side_effect=_get_config_side_effect)
        result = await get_authenticated_mcp_servers("user-1", "org-1", cfg)
        assert [r["instanceId"] for r in result] == ["inst-1"]


class TestMatchEnabledToolsForMcpServer:
    def test_matches_tools_by_type_prefix(self) -> None:
        server = {"instanceId": "inst-1", "name": "Jira", "typeId": "jira"}
        enabled = {
            "mcp_jira_atlassianUserInfo",
            "mcp_jira_getIssue",
            "mcp_exa_search",
            "slack.send_message",
        }
        matched = match_enabled_tools_for_mcp_server(server, enabled)
        assert {(m["name"], m["fullName"]) for m in matched} == {
            ("atlassianUserInfo", "mcp_jira_atlassianUserInfo"),
            ("getIssue", "mcp_jira_getIssue"),
        }

    def test_returns_empty_when_no_tools_for_server(self) -> None:
        server = {"instanceId": "inst-2", "name": "Exa", "typeId": "exa"}
        enabled = {"mcp_jira_getIssue", "mcp_notion_search"}
        assert match_enabled_tools_for_mcp_server(server, enabled) == []

    def test_normalizes_hyphenated_type_id(self) -> None:
        server = {"instanceId": "inst-3", "name": "Brave", "typeId": "brave-search"}
        enabled = {"mcp_brave_search_web_search"}
        matched = match_enabled_tools_for_mcp_server(server, enabled)
        assert matched == [
            {"name": "web_search", "fullName": "mcp_brave_search_web_search"},
        ]

    def test_returns_empty_when_type_key_blank(self) -> None:
        server = {"instanceId": None, "name": None, "typeId": None, "_id": None}
        assert match_enabled_tools_for_mcp_server(server, {"mcp_jira_getIssue"}) == []

    def test_skips_prefix_only_full_names(self) -> None:
        server = {"instanceId": "inst-1", "name": "Jira", "typeId": "jira"}
        enabled = {"mcp_jira_", "mcp_jira_getIssue"}
        matched = match_enabled_tools_for_mcp_server(server, enabled)
        assert matched == [{"name": "getIssue", "fullName": "mcp_jira_getIssue"}]


class TestIsMcpEnabled:
    """No env override exists anymore — resolution is `config_service` (via
    the shared `read_platform_feature_flag` helper) -> `FeatureFlagService`
    fallback -> default False."""

    @pytest.mark.asyncio
    async def test_reads_platform_settings_flag(self) -> None:
        cfg = MagicMock()
        cfg.get_config = AsyncMock(
            return_value={"featureFlags": {"enable_mcp": True}}
        )
        assert await is_mcp_enabled(cfg) is True

    @pytest.mark.asyncio
    async def test_platform_settings_flag_absent_defaults_false(self) -> None:
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value={"featureFlags": {}})
        assert await is_mcp_enabled(cfg) is False

    @pytest.mark.asyncio
    async def test_platform_settings_read_failure_disables(self) -> None:
        cfg = MagicMock()
        cfg.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        assert await is_mcp_enabled(cfg) is False

    @pytest.mark.asyncio
    async def test_falls_back_to_feature_flag_service(self) -> None:
        mock_ffs = MagicMock()
        mock_ffs.is_feature_enabled.return_value = True
        with patch(
            "app.services.featureflag.featureflag.FeatureFlagService.get_service",
            return_value=mock_ffs,
        ):
            assert await is_mcp_enabled(None) is True

    @pytest.mark.asyncio
    async def test_feature_flag_service_unavailable_defaults_false(self) -> None:
        with patch(
            "app.services.featureflag.featureflag.FeatureFlagService.get_service",
            side_effect=RuntimeError("unwired"),
        ):
            assert await is_mcp_enabled(None) is False

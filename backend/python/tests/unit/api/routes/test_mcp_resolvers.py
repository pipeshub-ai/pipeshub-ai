"""Tests for app.api.routes.mcp_resolvers — OSS edition MCP resolver helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# load_mcp_instances
# ---------------------------------------------------------------------------


class TestLoadMcpInstances:
    """Delegates to mcp_service.load_org_instances."""

    async def test_delegates_to_service(self) -> None:
        from app.api.routes.mcp_resolvers import load_mcp_instances

        expected = [{"_id": "i1", "name": "Server A"}]
        config_service = AsyncMock()
        with patch(
            "app.api.routes.mcp_resolvers.mcp_service.load_org_instances",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_load:
            result = await load_mcp_instances(config_service)
        assert result == expected
        mock_load.assert_awaited_once()

    async def test_returns_empty_list_when_service_returns_empty(self) -> None:
        from app.api.routes.mcp_resolvers import load_mcp_instances

        config_service = AsyncMock()
        with patch(
            "app.api.routes.mcp_resolvers.mcp_service.load_org_instances",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await load_mcp_instances(config_service)
        assert result == []


# ---------------------------------------------------------------------------
# get_mcp_instance
# ---------------------------------------------------------------------------


class TestGetMcpInstance:
    """Delegates to mcp_service.get_instance."""

    async def test_delegates_to_service(self) -> None:
        from app.api.routes.mcp_resolvers import get_mcp_instance

        expected = {"_id": "inst-1", "name": "My MCP"}
        config_service = AsyncMock()
        with patch(
            "app.api.routes.mcp_resolvers.mcp_service.get_instance",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_get:
            result = await get_mcp_instance("inst-1", config_service)
        assert result == expected
        mock_get.assert_awaited_once()

    async def test_returns_none_when_not_found(self) -> None:
        from app.api.routes.mcp_resolvers import get_mcp_instance

        config_service = AsyncMock()
        with patch(
            "app.api.routes.mcp_resolvers.mcp_service.get_instance",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await get_mcp_instance("missing", config_service)
        assert result is None


# ---------------------------------------------------------------------------
# mask_mcp_instance_for_response
# ---------------------------------------------------------------------------


class TestMaskMcpInstanceForResponse:
    """OSS edition returns a dict copy."""

    def test_returns_copy(self) -> None:
        from app.api.routes.mcp_resolvers import mask_mcp_instance_for_response

        instance = {"_id": "i1", "name": "Server", "secret": "s3cr3t"}
        result = mask_mcp_instance_for_response(instance)
        assert result == instance
        assert result is not instance

    def test_handles_empty_dict(self) -> None:
        from app.api.routes.mcp_resolvers import mask_mcp_instance_for_response

        result = mask_mcp_instance_for_response({})
        assert result == {}


# ---------------------------------------------------------------------------
# forbid_inherited_mcp_mutation
# ---------------------------------------------------------------------------


class TestForbidInheritedMcpMutation:
    """OSS no-op — should not raise."""

    def test_no_op(self) -> None:
        from app.api.routes.mcp_resolvers import forbid_inherited_mcp_mutation

        instance = {"_id": "i1", "inherited": True}
        forbid_inherited_mcp_mutation(instance)


# ---------------------------------------------------------------------------
# resolve_mcp_instances_with_inheritance
# ---------------------------------------------------------------------------


class TestResolveMcpInstancesWithInheritance:
    """OSS edition delegates directly to load_mcp_instances."""

    async def test_delegates_to_load(self) -> None:
        from app.api.routes.mcp_resolvers import resolve_mcp_instances_with_inheritance

        expected = [{"_id": "i1"}]
        config_service = AsyncMock()
        with patch(
            "app.api.routes.mcp_resolvers.load_mcp_instances",
            new_callable=AsyncMock,
            return_value=expected,
        ):
            result = await resolve_mcp_instances_with_inheritance(config_service)
        assert result == expected


# ---------------------------------------------------------------------------
# resolve_instance_owner_config_service
# ---------------------------------------------------------------------------


class TestResolveInstanceOwnerConfigService:
    """OSS edition returns the same config_service passed in."""

    async def test_returns_same_config_service(self) -> None:
        from app.api.routes.mcp_resolvers import resolve_instance_owner_config_service

        config_service = AsyncMock()
        result = await resolve_instance_owner_config_service("inst-1", config_service)
        assert result is config_service


# ---------------------------------------------------------------------------
# build_mcp_fallback_config_services
# ---------------------------------------------------------------------------


class TestBuildMcpFallbackConfigServices:
    """OSS edition returns None (no parent tenant fallback)."""

    async def test_returns_none(self) -> None:
        from app.api.routes.mcp_resolvers import build_mcp_fallback_config_services

        instance = {"_id": "i1"}
        config_service = AsyncMock()
        result = await build_mcp_fallback_config_services(instance, config_service)
        assert result is None


# ---------------------------------------------------------------------------
# build_schedule_refresh_kwargs
# ---------------------------------------------------------------------------


class TestBuildScheduleRefreshKwargs:
    """OSS edition returns empty dict."""

    def test_returns_empty_dict(self) -> None:
        from app.api.routes.mcp_resolvers import build_schedule_refresh_kwargs

        result = build_schedule_refresh_kwargs("org-1")
        assert result == {}
        assert isinstance(result, dict)

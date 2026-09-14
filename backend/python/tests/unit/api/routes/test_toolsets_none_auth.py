"""Regression tests for AuthType.NONE toolset readiness (issue #3065)."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_none_auth_instance_is_authenticated_without_user_auth() -> None:
    """A NONE-auth instance with no per-user credential record is still ready."""
    from app.api.routes.toolsets import get_authenticated_toolsets

    config_service = AsyncMock()
    instances = [
        {"_id": "none1", "orgId": "o1", "toolsetType": "calculator", "authType": "NONE"},
        {"_id": "oauth1", "orgId": "o1", "toolsetType": "jira", "authType": "OAUTH"},
    ]

    async def mock_get_config(path, default=None):
        if "toolset-instances" in path:
            return instances
        return None

    config_service.get_config = mock_get_config
    registry = MagicMock()
    registry.get_toolset_metadata.return_value = {
        "display_name": "Calculator",
        "description": "",
        "icon_path": "",
        "category": "app",
        "tools": [{"name": "add", "description": "Add numbers"}],
    }

    result, auth_by_instance = await get_authenticated_toolsets(
        "u1", "o1", config_service, registry
    )
    assert len(result) == 1
    assert result[0]["instanceId"] == "none1"
    assert result[0]["authType"] == "NONE"
    assert result[0]["isAuthenticated"] is True
    assert result[0]["tools"][0]["name"] == "add"
    assert "none1" in auth_by_instance


def test_is_toolset_authenticated_treats_enum_none_as_ready() -> None:
    from app.api.routes.toolsets import _is_toolset_authenticated

    class AuthType:
        def __init__(self, value: str) -> None:
            self.value = value

    inst = {"authType": AuthType("NONE")}
    assert _is_toolset_authenticated(inst, None) is True
    assert _is_toolset_authenticated({"authType": "OAUTH"}, None) is False
    assert _is_toolset_authenticated(
        {"authType": "OAUTH"}, {"isAuthenticated": True}
    ) is True

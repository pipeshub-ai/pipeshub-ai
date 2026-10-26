"""Shared fakes for MCP route tests that go through the real resolver/service lookups."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


class FakeConfigService:
    """In-memory `ConfigurationService` stand-in that records writes and deletes."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = dict(data or {})
        self.writes: list[str] = []
        self.deletes: list[str] = []
        self.ttls: dict[str, int | None] = {}

    async def get_config(
        self, key: str, default: Any = None, use_cache: bool = False, *, raise_on_error: bool = False,  # noqa: ANN401
        keep_in_cache: bool = True,
    ) -> Any:  # noqa: ANN401
        return self.data.get(key, default)

    async def set_config(self, key: str, value: Any, *, ttl_seconds: int | None = None, keep_in_cache: bool = True) -> bool:  # noqa: ANN401
        self.data[key] = value
        self.writes.append(key)
        self.ttls[key] = ttl_seconds
        return True

    async def create_config_if_absent(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> bool:  # noqa: ANN401
        if key in self.data:
            return False
        self.data[key] = value
        self.writes.append(key)
        self.ttls[key] = ttl_seconds
        return True

    async def delete_config(self, key: str) -> bool:
        self.data.pop(key, None)
        self.deletes.append(key)
        return True

    async def list_keys_in_directory(self, prefix: str) -> list[str]:
        return [key for key in self.data if key.startswith(prefix)]


def route_request(
    config_service: FakeConfigService,
    *,
    user_id: str = "admin-1",
    org_id: str = "org-1",
    registry: Any = None,  # noqa: ANN401
) -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": user_id, "orgId": org_id}
    request.headers = {}
    request.app.state.config_service = config_service
    request.app.state.mcp_registry = registry or MagicMock()
    return request

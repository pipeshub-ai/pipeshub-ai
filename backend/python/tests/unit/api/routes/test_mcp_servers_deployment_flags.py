"""H3 BE-3: the MCP list the admin's page reads says whether the hub and STDIO runners are on (P2.8, P5.10)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.mcp_servers import get_my_mcp_servers
from app.utils.env_flags import MCP_HUB, MCP_STDIO_RUNNERS
from tests.support.bound_org import bind_test_org

if TYPE_CHECKING:
    from collections.abc import Iterator


def _request() -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": "u1", "orgId": "o1"}
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value=None)
    request.app.state.config_service = config_service
    return request


async def _answer(instances: list[dict[str, object]]) -> dict[str, object]:
    with patch(
        "app.api.routes.mcp_servers._visible_instances",
        new=AsyncMock(return_value=instances),
    ), patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)):
        return await get_my_mcp_servers(_request(), include_tools=False)


def _set(monkeypatch: pytest.MonkeyPatch, name: str, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hub", "runners", "expected"),
    [
        ("true", "false", (True, False)),
        ("0", "on", (False, True)),
        (None, None, (False, False)),
        ("yes", "1", (True, True)),
    ],
)
async def test_be3_the_list_carries_both_flags(
    monkeypatch: pytest.MonkeyPatch, hub: str | None, runners: str | None, expected: tuple[bool, bool]
) -> None:
    _set(monkeypatch, MCP_HUB, hub)
    _set(monkeypatch, MCP_STDIO_RUNNERS, runners)
    answer = await _answer([])
    assert (answer["mcpHub"], answer["mcpStdioRunners"]) == expected
    assert answer["instances"] == []


@pytest.mark.asyncio
async def test_be3_the_flags_sit_beside_the_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, MCP_HUB, "true")
    _set(monkeypatch, MCP_STDIO_RUNNERS, None)
    instance = {"_id": "i1", "orgId": "o1", "name": "Jira", "transport": "stdio", "authMode": "none"}
    answer = await _answer([instance])
    assert answer["mcpHub"] is True
    assert answer["mcpStdioRunners"] is False
    instances = answer["instances"]
    assert isinstance(instances, list)
    assert [i["_id"] for i in instances] == ["i1"]


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    """The auth middleware binds the verified token's org; these tests call the route functions directly."""
    yield from bind_test_org("o1")

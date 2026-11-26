"""U4 BE-1: the toolset lists tell the agent builder whether two instances of one type stay apart (P2.2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# First: the routes module imported alone meets the `app.edition_config` cycle (P2.2 FB-8).
import app.edition_config  # noqa: F401
from app.api.routes.toolsets import _build_toolsets_list_response
from app.utils.env_flags import INSTANCE_TOOL_IDS


def _request() -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": "u1", "orgId": "o1"}
    registry = MagicMock()
    registry.get_toolset_metadata.return_value = {
        "name": "jira",
        "display_name": "Jira",
        "description": "jira tools",
        "category": "app",
        "group": "",
        "supported_auth_types": ["API_TOKEN"],
        "tools": [],
        "isInternal": False,
    }
    registry.list_toolsets.return_value = ["jira"]
    request.app.state.toolset_registry = registry
    return request


_TWO_JIRAS = [
    {"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "API_TOKEN", "instanceName": "Acme Jira"},
    {"_id": "i2", "orgId": "o1", "toolsetType": "jira", "authType": "API_TOKEN", "instanceName": "Ops Jira"},
]


async def _list(instances: list[dict[str, str]]) -> dict[str, object]:
    with patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
        return await _build_toolsets_list_response(
            request=_request(),
            org_id="o1",
            config_service=AsyncMock(),
            search=None,
            page=1,
            limit=20,
            include_registry=False,
            fetch_auth_for_instance=AsyncMock(return_value={"isAuthenticated": True}),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "expected"), [("true", True), ("false", False), (None, False)])
async def test_be1_lists_carry_the_flag(monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool) -> None:
    if value is None:
        monkeypatch.delenv(INSTANCE_TOOL_IDS, raising=False)
    else:
        monkeypatch.setenv(INSTANCE_TOOL_IDS, value)

    page = await _list(_TWO_JIRAS)
    empty = await _list([])

    assert page["instanceToolIds"] is expected
    assert len(page["toolsets"]) == 2
    assert empty["instanceToolIds"] is expected
    assert empty["toolsets"] == []

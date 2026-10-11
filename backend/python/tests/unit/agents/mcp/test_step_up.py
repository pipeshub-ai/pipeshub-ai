"""More scopes after a 403 (`app.agents.mcp.step_up`): remembered for the next sign-in."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.constants.mcp_server_constants import get_mcp_step_up_scopes_path
from app.agents.mcp import step_up
from app.agents.mcp.client import ToolListing
from app.agents.mcp.discovery import discover_listing_for_owner
from app.agents.mcp.errors import (
    MCPConnectionError,
    MCPHttpStatusError,
    MCPInsufficientScopeError,
    insufficient_scope,
)
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService

OAUTH = {
    "_id": "inst-1", "name": "Drive", "orgId": "org-1", "createdBy": "admin-1", "authMode": "oauth",
    "transport": "streamable_http", "url": "https://mcp.example.com/mcp",
}
SAVED = get_mcp_step_up_scopes_path("inst-1", "user-1")


def _refused(scope: str = "files.write", error: str = "insufficient_scope") -> MCPHttpStatusError:
    return MCPHttpStatusError(403, challenge={"error": error, "scope": scope})


class TestTheRefusalIsFound:
    def test_through_the_cause_chain(self) -> None:
        refused = _refused()
        wrapper = MCPConnectionError("the call failed")
        wrapper.__cause__ = refused
        assert insufficient_scope(wrapper) is refused

    def test_whatever_the_case_of_the_error(self) -> None:
        assert insufficient_scope(_refused(error="Insufficient_Scope")) is not None

    @pytest.mark.parametrize("exc", [
        _refused(error="invalid_token"),
        MCPHttpStatusError(403),
        MCPHttpStatusError(401, challenge={"error": "insufficient_scope", "scope": "x"}),
        RuntimeError("no"),
    ])
    def test_nothing_else_counts(self, exc: BaseException) -> None:
        assert insufficient_scope(exc) is None


class TestTheScopesAreRemembered:
    async def test_saved_for_the_owner_and_returned(self) -> None:
        config = FakeConfigService()
        assert await step_up.remember_needed_scopes(config, OAUTH, "user-1", _refused("files.write files.read")) == [
            "files.write", "files.read",
        ]
        assert config.data[SAVED]["scopes"] == ["files.write", "files.read"]
        assert isinstance(config.data[SAVED]["updatedAt"], int)

    async def test_added_to_those_saved_before_each_once_and_capped(self) -> None:
        config = FakeConfigService({SAVED: {"scopes": [f"s{i}" for i in range(49)]}})
        await step_up.remember_needed_scopes(config, OAUTH, "user-1", _refused("s3 extra-1 extra-2"))
        assert config.data[SAVED]["scopes"] == [*(f"s{i}" for i in range(49)), "extra-1"]

    async def test_nothing_new_writes_nothing(self) -> None:
        config = FakeConfigService({SAVED: {"scopes": ["files.write"]}})
        await step_up.remember_needed_scopes(config, OAUTH, "user-1", _refused("files.write"))
        assert config.writes == []

    @pytest.mark.parametrize("instance,exc", [
        ({**OAUTH, "authMode": "api_token"}, _refused()),
        (OAUTH, _refused(scope="")),
        (OAUTH, _refused(error="invalid_token")),
    ], ids=["not-oauth", "no-scopes", "another-error"])
    async def test_only_what_signing_in_again_can_fix(self, instance: dict[str, Any], exc: BaseException) -> None:
        config = FakeConfigService()
        assert await step_up.remember_needed_scopes(config, instance, "user-1", exc) == []
        assert config.writes == []

    async def test_a_failed_save_is_logged_and_the_scopes_still_returned(self, caplog: pytest.LogCaptureFixture) -> None:
        config = MagicMock()
        config.get_config = AsyncMock(return_value=None)
        config.set_config = AsyncMock(side_effect=RuntimeError("etcd is down"))
        assert await step_up.remember_needed_scopes(config, OAUTH, "user-1", _refused()) == ["files.write"]
        assert "Couldn't save the scopes" in caplog.text

    @pytest.mark.parametrize("stored,read", [
        ({"scopes": ["a", 7, "", "b"]}, ["a", "b"]),
        ({"scopes": "a b"}, []),
        ("junk", []),
        (None, []),
    ])
    async def test_reading_them_back_ignores_junk(self, stored: object, read: list[str]) -> None:
        config = FakeConfigService({SAVED: stored} if stored is not None else {})
        assert await step_up.step_up_scopes(config, "inst-1", "user-1") == read

    async def test_cleared_after_a_sign_in(self) -> None:
        config = FakeConfigService({SAVED: {"scopes": ["a"]}})
        await step_up.clear_step_up_scopes(config, "inst-1", "user-1")
        assert SAVED not in config.data


class TestTheWorkspaceListingToo:
    async def test_a_listing_refused_for_scope_is_remembered_and_explained(self) -> None:
        config = FakeConfigService()
        with patch("app.agents.mcp.discovery.discover_tool_listing", new=AsyncMock(side_effect=_refused("tools.read"))):
            with pytest.raises(MCPInsufficientScopeError, match="Reconnect it to grant it") as caught:
                await discover_listing_for_owner(OAUTH, {"oauthTokens": {"accessToken": "t"}}, "user-1", config)
        assert caught.value.scopes == ["tools.read"]
        assert config.data[SAVED]["scopes"] == ["tools.read"]

    async def test_any_other_failure_is_raised_as_it_was(self) -> None:
        failure = MCPConnectionError("refused")
        with patch("app.agents.mcp.discovery.discover_tool_listing", new=AsyncMock(side_effect=failure)):
            with pytest.raises(MCPConnectionError) as caught:
                await discover_listing_for_owner(OAUTH, {}, "user-1", FakeConfigService())
        assert caught.value is failure

    async def test_a_listing_that_works_is_returned(self) -> None:
        listing = ToolListing(tools=[])
        with patch("app.agents.mcp.discovery.discover_tool_listing", new=AsyncMock(return_value=listing)):
            assert (await discover_listing_for_owner(OAUTH, {}, "user-1", FakeConfigService()))[0] is listing

"""`MCPSessionManager` (`app/agents/agent_loop/mcp_session.py`) — per-request,
per-instance session reuse, resending only what can't have run, and the on-demand
OAuth-refresh-then-retry-once flow for a request the server rejected with HTTP 401."""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import httpx
import pytest
from mcp.shared.exceptions import MCPError

from app.agents.agent_loop import mcp_session as mcp_session_module
from app.agents.agent_loop.mcp_access import ResolvedMCPServer
from app.agents.agent_loop.mcp_session import MCPSessionManager, is_http_unauthorized
from app.agents.constants.mcp_server_constants import get_mcp_step_up_scopes_path
from app.agents.mcp.client import ToolListing
from app.agents.mcp.errors import (
    MCPCallInterruptedError,
    MCPConnectionLostError,
    MCPHttpStatusError,
    MCPInsufficientScopeError,
    MCPRequestLostError,
    MCPRequestNotSentError,
    MCPSessionExpiredError,
    MCPToolChangedError,
    MCPToolNotOfferedError,
    request_may_have_run,
    request_never_reached_server,
)
from app.agents.mcp.models import OAuthTokens
from app.agents.mcp.token_refresh import MCPTokenRefreshError
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService


def _instance(auth_mode: str = "none", transport: str = "streamable_http") -> dict[str, Any]:
    return {
        "_id": "inst-1", "orgId": "org-1", "createdBy": "user-1",
        "name": "JiraMCP", "typeId": "jira_mcp", "transport": transport, "url": "https://mcp.example.com/mcp",
        "authMode": auth_mode, "createdAt": 0, "updatedAt": 0,
    }


def _server(auth_mode: str = "none", **overrides: Any) -> ResolvedMCPServer:  # noqa: ANN401
    defaults: dict[str, Any] = {
        "instance_id": "inst-1", "name": "JiraMCP", "display_name": "Jira MCP",
        "instance": _instance(auth_mode), "auth": {}, "owner_id": "user-1", "attached_tools": None,
    }
    defaults.update(overrides)
    return ResolvedMCPServer(**defaults)


def _oauth_server(token: str = "old-token") -> ResolvedMCPServer:
    return _server("oauth", auth={"oauthTokens": {"accessToken": token}})


class _FakeClientManager:
    """Records `open()`/`call_tool_in_session()`/`aclose()` calls; `responses` is consumed in
    order (an `Exception` instance is raised instead of returned). Like the real client, an
    error saying the session's connection is gone ends the session."""

    def __init__(self, config: object, env: dict | None = None, headers: dict | None = None) -> None:
        self.config = config
        self.env = env
        self.headers = dict(headers or {})
        self.header_updates: list[dict[str, str]] = []
        self.opened = False
        self.closed = False
        self.calls: list[tuple[str, dict]] = []
        self.responses: list[Any] = ["ok"]
        self.list_responses: list[Any] = [[{"name": "search", "inputSchema": {}}]]
        self.listings = 0
        self.ended = False
        self.tools_changed = False
        self.instructions: str | None = None
        self.progress_handlers: list[Any] = []

    @property
    def is_open(self) -> bool:
        return self.opened and not self.closed and not self.ended

    @property
    def server_instructions(self) -> str | None:
        return None

    async def open(self) -> "_FakeClientManager":
        self.opened = True
        return self

    def update_headers(self, headers: dict[str, str]) -> None:
        self.header_updates.append(dict(headers))
        self.headers.update(headers)

    def _answer(self, response: Any) -> Any:  # noqa: ANN401
        if isinstance(response, Exception):
            if isinstance(response, MCPConnectionLostError):
                self.ended = True
            raise response
        return response

    async def call_tool_in_session(self, tool_name: str, arguments: dict, *, on_progress: Any = None) -> Any:  # noqa: ANN401
        self.calls.append((tool_name, arguments))
        self.progress_handlers.append(on_progress)
        return self._answer(self.responses.pop(0) if self.responses else "ok")

    async def list_tools_in_session(self) -> list[Any]:
        return (await self.fetch_tool_listing_in_session()).tools

    async def fetch_tool_listing_in_session(self) -> ToolListing:
        self.listings += 1
        tools = self._answer(self.list_responses.pop(0) if self.list_responses else [{"name": "search", "inputSchema": {}}])
        return ToolListing(tools=tools, instructions=self.instructions)

    async def aclose(self) -> None:
        self.closed = True


class _FakeClientManagerFactory:
    """Constructs `_FakeClientManager`s and keeps every instance built, so tests can assert on
    the specific manager a given `open()` built (e.g. the second one, after a reconnect)."""

    def __init__(self) -> None:
        self.built: list[_FakeClientManager] = []
        # Responses for managers not built yet, one list per manager in build order.
        self.queued_responses: list[list[Any]] = []

    def __call__(self, config: object, env: dict | None = None, headers: dict | None = None) -> _FakeClientManager:
        manager = _FakeClientManager(config, env=env, headers=headers)
        if self.queued_responses:
            manager.responses = self.queued_responses.pop(0)
        self.built.append(manager)
        return manager


@pytest.fixture
def client_manager_factory(monkeypatch: pytest.MonkeyPatch) -> _FakeClientManagerFactory:
    factory = _FakeClientManagerFactory()
    monkeypatch.setattr(mcp_session_module, "MCPClientManager", factory)
    return factory


class _TokenStore:
    """`refresh_credential_record` as the session manager sees it: the endpoint is called only
    when the caller's stale token is still the stored one."""

    def __init__(self, stored: str = "old-token") -> None:
        self.stored = stored
        self.endpoint_calls = 0
        self.stale_tokens: list[str | None] = []
        self.gate: asyncio.Event | None = None
        self.endpoint_called = asyncio.Event()

    async def __call__(self, instance_id: str, owner_id: str, _config_service: Any, **kwargs: Any) -> OAuthTokens:  # noqa: ANN401
        assert (instance_id, owner_id) == ("inst-1", "user-1")
        stale = kwargs.get("stale_access_token")
        self.stale_tokens.append(stale)
        if stale is None or stale == self.stored:
            self.endpoint_calls += 1
            self.endpoint_called.set()
            if self.gate is not None:
                await self.gate.wait()
            self.stored = f"fresh-token-{self.endpoint_calls}"
        return OAuthTokens(access_token=self.stored)


@pytest.fixture
def tokens(monkeypatch: pytest.MonkeyPatch) -> _TokenStore:
    store = _TokenStore()
    monkeypatch.setattr(mcp_session_module, "refresh_credential_record", store)
    return store


class TestCall:
    async def test_opens_session_once_and_reuses_across_calls(
        self, client_manager_factory: _FakeClientManagerFactory,
    ) -> None:
        manager = MCPSessionManager(make_context())
        server = _server()

        assert await manager.call(server, "search", {"q": "a"}) == "ok"
        assert await manager.call(server, "search", {"q": "b"}) == "ok"

        assert len(client_manager_factory.built) == 1
        assert client_manager_factory.built[0].calls == [("search", {"q": "a"}), ("search", {"q": "b"})]

    async def test_second_session_manager_sharing_tool_state_reuses_cache(
        self, client_manager_factory: _FakeClientManagerFactory,
    ) -> None:
        """Mirrors `ToolInstanceCreator`'s per-request `_client_cache` contract: any
        `MCPSessionManager` built against the same `context.tool_state` dict shares the same
        session cache (this is what lets `stream_bridge.py`'s teardown find the real sessions)."""
        context = make_context()
        server = _server()
        await MCPSessionManager(context).call(server, "search", {})
        await MCPSessionManager(context).call(server, "search", {})

        assert len(client_manager_factory.built) == 1

    async def test_a_401_without_oauth_is_not_retried(self, client_manager_factory: _FakeClientManagerFactory) -> None:
        manager = MCPSessionManager(make_context())
        server = _server(auth_mode="api_token")
        (await manager._get_or_open(server)).responses = [MCPHttpStatusError(401)]

        with pytest.raises(MCPHttpStatusError):
            await manager.call(server, "search", {})

        assert len(client_manager_factory.built) == 1

    async def test_another_error_propagates_without_a_refresh(
        self, client_manager_factory: _FakeClientManagerFactory, tokens: _TokenStore,
    ) -> None:
        session_manager = MCPSessionManager(make_context())
        server = _oauth_server()
        (await session_manager._get_or_open(server)).responses = [RuntimeError("some unrelated error")]

        with pytest.raises(RuntimeError, match="unrelated"):
            await session_manager.call(server, "search", {})
        assert tokens.stale_tokens == []


class TestA401OnACallIsRefreshed:
    async def test_the_fresh_token_goes_into_the_open_session_and_the_call_is_sent_again(
        self, client_manager_factory: _FakeClientManagerFactory, tokens: _TokenStore,
    ) -> None:
        session_manager = MCPSessionManager(make_context())
        server = _oauth_server()
        first = await session_manager._get_or_open(server)
        first.responses = [MCPHttpStatusError(401), "ok"]

        assert await session_manager.call(server, "search", {"q": "x"}) == "ok"

        # The session survived the 401, so it carries the new token instead of being replaced.
        assert len(client_manager_factory.built) == 1
        assert first.calls == [("search", {"q": "x"}), ("search", {"q": "x"})]
        assert first.header_updates == [{"Authorization": "Bearer fresh-token-1"}]
        assert server.auth["oauthTokens"]["accessToken"] == "fresh-token-1"
        # The rejected token goes along so a refresh another process already did isn't repeated.
        assert tokens.stale_tokens == ["old-token"]

    async def test_a_session_the_401_ended_is_replaced_with_one_sending_the_fresh_token(
        self, client_manager_factory: _FakeClientManagerFactory, tokens: _TokenStore,
    ) -> None:
        """An SSE server's 401 ends its session."""
        session_manager = MCPSessionManager(make_context())
        server = _oauth_server()
        first = await session_manager._get_or_open(server)
        first.responses = [MCPHttpStatusError(401)]
        real_call = first.call_tool_in_session

        async def _401_ends_the_session(tool_name: str, arguments: dict, **kwargs: Any) -> Any:  # noqa: ANN401
            first.ended = True
            return await real_call(tool_name, arguments, **kwargs)

        first.call_tool_in_session = _401_ends_the_session  # type: ignore[method-assign]

        assert await session_manager.call(server, "search", {"q": "x"}) == "ok"

        first_manager, second = client_manager_factory.built
        assert first_manager.closed is True
        assert second.headers["Authorization"] == "Bearer fresh-token-1"
        assert second.calls == [("search", {"q": "x"})]

    async def test_concurrent_401s_refresh_once(
        self, client_manager_factory: _FakeClientManagerFactory, tokens: _TokenStore,
    ) -> None:
        tokens.gate = asyncio.Event()
        session_manager = MCPSessionManager(make_context())
        server = _oauth_server()
        first = await session_manager._get_or_open(server)
        first.responses = [MCPHttpStatusError(401), MCPHttpStatusError(401), "a", "b"]

        calls = [asyncio.create_task(session_manager.call(server, "search", {"n": n})) for n in (1, 2)]
        await tokens.endpoint_called.wait()
        # Lets the other call reach the lock before the refresh finishes.
        await asyncio.sleep(0)
        tokens.gate.set()
        results = await asyncio.gather(*calls)

        assert sorted(results) == ["a", "b"]
        assert tokens.endpoint_calls == 1
        assert len(client_manager_factory.built) == 1

    async def test_a_refresh_failure_propagates(
        self, client_manager_factory: _FakeClientManagerFactory, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fails(*_args: Any, **_kwargs: Any) -> OAuthTokens:  # noqa: ANN401
            raise MCPTokenRefreshError("no refresh token available")

        monkeypatch.setattr(mcp_session_module, "refresh_credential_record", _fails)
        session_manager = MCPSessionManager(make_context())
        server = _oauth_server()
        (await session_manager._get_or_open(server)).responses = [MCPHttpStatusError(401)]

        with pytest.raises(MCPTokenRefreshError):
            await session_manager.call(server, "search", {})

    async def test_a_retry_that_times_out_after_a_refresh_is_not_a_401(
        self, client_manager_factory: _FakeClientManagerFactory, tokens: _TokenStore,
    ) -> None:
        session_manager = MCPSessionManager(make_context())
        server = _oauth_server()
        (await session_manager._get_or_open(server)).responses = [MCPHttpStatusError(401), TimeoutError()]

        with pytest.raises(TimeoutError) as caught:
            await session_manager.call(server, "search", {})

        assert is_http_unauthorized(caught.value) is False

    async def test_a_401_while_connecting_refreshes_and_runs_the_call_once(
        self, client_manager_factory: _FakeClientManagerFactory, tokens: _TokenStore, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session_manager = MCPSessionManager(make_context())
        server = _oauth_server()
        opens = 0
        real_open = _FakeClientManager.open

        async def _open(self: _FakeClientManager) -> _FakeClientManager:
            nonlocal opens
            opens += 1
            if opens == 1:
                raise MCPHttpStatusError(401, "The MCP server answered HTTP 401")
            return await real_open(self)

        monkeypatch.setattr(_FakeClientManager, "open", _open)
        assert await session_manager.call(server, "search", {"q": "x"}) == "ok"

        assert [m.calls for m in client_manager_factory.built] == [[], [("search", {"q": "x"})]]
        assert client_manager_factory.built[1].headers["Authorization"] == "Bearer fresh-token-1"

    async def test_a_text_that_mentions_expiry_is_not_a_401(
        self, client_manager_factory: _FakeClientManagerFactory, tokens: _TokenStore,
    ) -> None:
        session_manager = MCPSessionManager(make_context())
        server = _oauth_server()
        (await session_manager._get_or_open(server)).responses = [RuntimeError("upstream said: token expired, unauthorized")]

        with pytest.raises(RuntimeError, match="token expired"):
            await session_manager.call(server, "search", {"q": "x"})

        assert tokens.stale_tokens == []


def _refused_for_scope(*scopes: str, error: str = "insufficient_scope") -> MCPHttpStatusError:
    return MCPHttpStatusError(403, "HTTP 403", challenge={"error": error, "scope": " ".join(scopes)})


class TestMoreScopesAfterA403:
    """A 403 `insufficient_scope` is remembered for the next sign-in, and the model told what fixes it."""

    SAVED = get_mcp_step_up_scopes_path("inst-1", "user-1")

    async def test_a_call_refused_for_scope_is_remembered_and_explained(
        self, client_manager_factory: _FakeClientManagerFactory,
    ) -> None:
        config = FakeConfigService()
        client_manager_factory.queued_responses = [[_refused_for_scope("files.write", "files.read")]]

        with pytest.raises(MCPInsufficientScopeError) as caught:
            await MCPSessionManager(make_context(config_service=config)).call(_oauth_server(), "upload", {})

        assert caught.value.scopes == ["files.write", "files.read"]
        assert "needs more permission for this (scopes: files.write, files.read)" in str(caught.value)
        assert "reconnect it in Workspace → MCP Servers" in str(caught.value)
        assert config.data[self.SAVED]["scopes"] == ["files.write", "files.read"]

    async def test_an_agents_sign_in_is_fixed_in_the_agent_builder(
        self, client_manager_factory: _FakeClientManagerFactory,
    ) -> None:
        config = FakeConfigService()
        client_manager_factory.queued_responses = [[_refused_for_scope("admin")]]
        server = _server("oauth", auth={"oauthTokens": {"accessToken": "t"}}, owner_id="agent-7")

        with pytest.raises(MCPInsufficientScopeError, match="agent builder"):
            await MCPSessionManager(make_context(config_service=config)).call(server, "upload", {})
        assert config.data[get_mcp_step_up_scopes_path("inst-1", "agent-7")]["scopes"] == ["admin"]

    async def test_scopes_asked_for_earlier_are_kept(self, client_manager_factory: _FakeClientManagerFactory) -> None:
        config = FakeConfigService({self.SAVED: {"scopes": ["files.read"]}})
        client_manager_factory.queued_responses = [[_refused_for_scope("files.write", "files.read")]]

        with pytest.raises(MCPInsufficientScopeError):
            await MCPSessionManager(make_context(config_service=config)).call(_oauth_server(), "upload", {})
        assert config.data[self.SAVED]["scopes"] == ["files.read", "files.write"]

    async def test_a_403_on_connect_counts_too(
        self, client_manager_factory: _FakeClientManagerFactory, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _refused(_self: object) -> None:
            raise _refused_for_scope("read")

        monkeypatch.setattr(_FakeClientManager, "open", _refused)
        config = FakeConfigService()

        with pytest.raises(MCPInsufficientScopeError):
            await MCPSessionManager(make_context(config_service=config)).call(_oauth_server(), "search", {})
        assert config.data[self.SAVED]["scopes"] == ["read"]

    async def test_a_403_after_a_token_refresh_counts_too(
        self, client_manager_factory: _FakeClientManagerFactory, tokens: _TokenStore,
    ) -> None:
        config = FakeConfigService()
        client_manager_factory.queued_responses = [[MCPHttpStatusError(401), _refused_for_scope("admin")]]

        with pytest.raises(MCPInsufficientScopeError):
            await MCPSessionManager(make_context(config_service=config)).call(_oauth_server(), "search", {})
        assert tokens.stale_tokens == ["old-token"]
        assert config.data[self.SAVED]["scopes"] == ["admin"]

    async def test_a_listing_refused_for_scope_counts_too(self, client_manager_factory: _FakeClientManagerFactory) -> None:
        config = FakeConfigService()
        session_manager = MCPSessionManager(make_context(config_service=config))
        manager = await session_manager._get_or_open(_oauth_server())
        manager.list_responses = [_refused_for_scope("tools.read")]

        with pytest.raises(MCPInsufficientScopeError):
            await session_manager.discover_listing(_oauth_server())
        assert config.data[self.SAVED]["scopes"] == ["tools.read"]

    @pytest.mark.parametrize("refusal,server", [
        (_refused_for_scope("admin", error="invalid_token"), "oauth"),
        (MCPHttpStatusError(403), "oauth"),
        (_refused_for_scope("admin"), "api_token"),
    ], ids=["another-error", "no-challenge", "not-oauth"])
    async def test_any_other_403_changes_nothing(
        self, client_manager_factory: _FakeClientManagerFactory, refusal: MCPHttpStatusError, server: str,
    ) -> None:
        config = FakeConfigService()
        client_manager_factory.queued_responses = [[refusal]]

        with pytest.raises(MCPHttpStatusError) as caught:
            await MCPSessionManager(make_context(config_service=config)).call(_server(server), "search", {})
        assert not isinstance(caught.value, MCPInsufficientScopeError)
        assert config.writes == []


class TestWhatCantHaveRunIsSentAgain:
    async def test_a_call_refused_for_an_expired_session_is_sent_again_on_a_new_one(
        self, client_manager_factory: _FakeClientManagerFactory,
    ) -> None:
        client_manager_factory.queued_responses = [[MCPSessionExpiredError("gone")], ["done"]]
        session_manager = MCPSessionManager(make_context())

        assert await session_manager.call(_server(), "search", {"q": "a"}) == "done"

        first, second = client_manager_factory.built
        assert first.closed is True
        assert first.calls == second.calls == [("search", {"q": "a"})]

    async def test_the_call_sent_again_reports_its_progress_to_the_same_place(
        self, client_manager_factory: _FakeClientManagerFactory,
    ) -> None:
        client_manager_factory.queued_responses = [[MCPSessionExpiredError("gone")], ["done"]]

        async def _shown(_progress: float, _total: float | None, _message: str | None) -> None:
            return None

        assert await MCPSessionManager(make_context()).call(_server(), "search", {}, on_progress=_shown) == "done"

        first, second = client_manager_factory.built
        assert first.progress_handlers == second.progress_handlers == [_shown]

    async def test_a_call_that_never_left_is_sent_again_on_the_same_session(
        self, client_manager_factory: _FakeClientManagerFactory,
    ) -> None:
        """A failed connection attempt doesn't end an HTTP session."""
        client_manager_factory.queued_responses = [[MCPRequestNotSentError("refused"), "done"]]

        assert await MCPSessionManager(make_context()).call(_server(), "search", {}) == "done"
        (only,) = client_manager_factory.built
        assert len(only.calls) == 2

    async def test_a_call_on_a_session_that_had_already_closed_goes_to_a_new_one(
        self, client_manager_factory: _FakeClientManagerFactory,
    ) -> None:
        client_manager_factory.queued_responses = [[MCPRequestNotSentError("closed")], ["done"]]
        session_manager = MCPSessionManager(make_context())
        first = await session_manager._get_or_open(_server())
        real_call = first.call_tool_in_session

        async def _closed_under_it(tool_name: str, arguments: dict, **kwargs: Any) -> Any:  # noqa: ANN401
            first.ended = True
            return await real_call(tool_name, arguments, **kwargs)

        first.call_tool_in_session = _closed_under_it  # type: ignore[method-assign]

        assert await session_manager.call(_server(), "search", {}) == "done"
        assert first.closed is True
        assert len(client_manager_factory.built) == 2

    async def test_a_second_refusal_is_not_retried_again(self, client_manager_factory: _FakeClientManagerFactory) -> None:
        client_manager_factory.queued_responses = [[MCPSessionExpiredError("gone")], [MCPSessionExpiredError("gone again")]]

        with pytest.raises(MCPSessionExpiredError, match="gone again"):
            await MCPSessionManager(make_context()).call(_server(), "search", {})
        assert len(client_manager_factory.built) == 2

    async def test_a_servers_own_error_keeps_the_session(self, client_manager_factory: _FakeClientManagerFactory) -> None:
        client_manager_factory.queued_responses = [[MCPError(-32000, "Jira is down"), "ok"]]
        session_manager = MCPSessionManager(make_context())

        with pytest.raises(MCPError, match="Jira is down"):
            await session_manager.call(_server(), "search", {})
        assert await session_manager.call(_server(), "search", {}) == "ok"
        assert len(client_manager_factory.built) == 1


class TestWhatMayHaveRunIsReported:
    async def test_a_call_in_flight_when_the_connection_closed_is_reported_and_the_next_call_reconnects(
        self, client_manager_factory: _FakeClientManagerFactory,
    ) -> None:
        client_manager_factory.queued_responses = [[MCPConnectionLostError("closed")], ["later"]]
        session_manager = MCPSessionManager(make_context())

        with pytest.raises(MCPCallInterruptedError, match="may or may not have completed"):
            await session_manager.call(_server(), "create_issue", {"title": "x"})
        # The next call starts a new session (a new local process).
        assert await session_manager.call(_server(), "search", {}) == "later"

        first, second = client_manager_factory.built
        assert first.closed is True
        assert first.calls == [("create_issue", {"title": "x"})]
        assert second.calls == [("search", {})]

    async def test_a_lost_request_is_reported_and_the_session_kept(
        self, client_manager_factory: _FakeClientManagerFactory,
    ) -> None:
        client_manager_factory.queued_responses = [[MCPRequestLostError("reset"), "next"]]
        session_manager = MCPSessionManager(make_context())

        with pytest.raises(MCPCallInterruptedError, match="Jira MCP"):
            await session_manager.call(_server(), "create_issue", {"title": "x"})
        assert await session_manager.call(_server(), "search", {}) == "next"

        (only,) = client_manager_factory.built
        assert only.closed is False
        assert only.calls == [("create_issue", {"title": "x"}), ("search", {})]

    async def test_a_session_that_survived_a_timeout_is_kept(self, client_manager_factory: _FakeClientManagerFactory) -> None:
        client_manager_factory.queued_responses = [[TimeoutError(), "ok"]]
        session_manager = MCPSessionManager(make_context())

        with pytest.raises(TimeoutError):
            await session_manager.call(_server(), "search", {})
        await session_manager.call(_server(), "search", {})

        assert len(client_manager_factory.built) == 1


class TestDiscovery:
    async def test_a_dead_session_reconnects_and_lists_again(self, client_manager_factory: _FakeClientManagerFactory) -> None:
        session_manager = MCPSessionManager(make_context())
        first = await session_manager._get_or_open(_server())
        first.list_responses = [MCPConnectionLostError("closed")]

        tools = await session_manager.discover(_server(), "jira")

        assert [t.name for t in tools] == ["search"]
        assert first.closed is True
        assert client_manager_factory.built[1].listings == 1

    async def test_a_lost_listing_is_listed_again_on_the_same_session(
        self, client_manager_factory: _FakeClientManagerFactory,
    ) -> None:
        session_manager = MCPSessionManager(make_context())
        first = await session_manager._get_or_open(_server())
        first.list_responses = [MCPRequestLostError("reset")]

        assert [t.name for t in await session_manager.discover(_server(), "jira")] == ["search"]
        assert first.listings == 2
        assert len(client_manager_factory.built) == 1

    async def test_a_401_refreshes_and_lists_again_with_the_fresh_token(
        self, client_manager_factory: _FakeClientManagerFactory, tokens: _TokenStore,
    ) -> None:
        session_manager = MCPSessionManager(make_context())
        server = _oauth_server()
        first = await session_manager._get_or_open(server)
        first.list_responses = [MCPHttpStatusError(401)]

        assert [t.name for t in await session_manager.discover(server, "jira")] == ["search"]
        assert first.header_updates == [{"Authorization": "Bearer fresh-token-1"}]
        assert first.listings == 2

    async def test_another_failure_is_raised(self, client_manager_factory: _FakeClientManagerFactory) -> None:
        session_manager = MCPSessionManager(make_context())
        (await session_manager._get_or_open(_server())).list_responses = [MCPError(-32603, "boom")]

        with pytest.raises(MCPError, match="boom"):
            await session_manager.discover(_server(), "jira")


class TestAcloseAll:
    async def test_closes_and_clears_every_cached_manager(self, client_manager_factory: _FakeClientManagerFactory) -> None:
        context = make_context()
        server_a = _server(instance_id="inst-1", instance=_instance())
        server_b = _server(instance_id="inst-2", instance={**_instance(), "_id": "inst-2"})
        session_manager = MCPSessionManager(context)
        await session_manager.call(server_a, "search", {})
        await session_manager.call(server_b, "search", {})

        await session_manager.aclose_all()

        assert all(m.closed for m in client_manager_factory.built)
        assert session_manager._managers == {}

    async def test_is_a_no_op_when_nothing_was_ever_opened(self) -> None:
        await MCPSessionManager(make_context()).aclose_all()  # must not raise

    async def test_closes_every_session_at_once(
        self, client_manager_factory: _FakeClientManagerFactory, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A hung server can take its whole close timeout; the others don't wait behind it."""
        closing: list[str] = []
        both_closing, release = asyncio.Event(), asyncio.Event()

        async def _slow_close(self: _FakeClientManager) -> None:
            closing.append(self.config.id)
            if len(closing) == 2:
                both_closing.set()
            await release.wait()

        monkeypatch.setattr(_FakeClientManager, "aclose", _slow_close)
        session_manager = MCPSessionManager(make_context())
        await session_manager.call(_server(instance_id="inst-1", instance=_instance()), "search", {})
        await session_manager.call(_server(instance_id="inst-2", instance={**_instance(), "_id": "inst-2"}), "search", {})

        task = asyncio.create_task(session_manager.aclose_all())
        await asyncio.wait_for(both_closing.wait(), 5)
        release.set()
        await task

        assert sorted(closing) == ["inst-1", "inst-2"]

    async def test_a_failing_close_doesnt_stop_the_others(
        self, client_manager_factory: _FakeClientManagerFactory, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session_manager = MCPSessionManager(make_context())
        await session_manager.call(_server(instance_id="inst-1", instance=_instance()), "search", {})
        await session_manager.call(_server(instance_id="inst-2", instance={**_instance(), "_id": "inst-2"}), "search", {})
        first, second = client_manager_factory.built

        async def _fails() -> None:
            raise RuntimeError("already dead")

        first.aclose = _fails  # type: ignore[method-assign]
        await session_manager.aclose_all()

        assert second.closed is True
        assert session_manager._managers == {}


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://mcp.example.com/mcp")
    return httpx.HTTPStatusError("rejected", request=request, response=httpx.Response(status, request=request))


class TestIsHttpUnauthorized:
    @pytest.mark.parametrize("error,unauthorized", [
        (MCPHttpStatusError(401), True),
        (MCPHttpStatusError(403), False),
        (_http_error(401), True),
        (_http_error(403), False),
    ])
    def test_a_status_error(self, error: Exception, unauthorized: bool) -> None:
        assert is_http_unauthorized(error) is unauthorized

    def test_401_as_the_cause(self) -> None:
        outer = RuntimeError("wrapped")
        outer.__cause__ = MCPHttpStatusError(401)
        assert is_http_unauthorized(outer) is True

    def test_401_inside_an_exception_group(self) -> None:
        assert is_http_unauthorized(ExceptionGroup("tasks", [ValueError("x"), MCPHttpStatusError(401)])) is True

    def test_a_401_that_was_only_being_handled_is_not_the_failure(self) -> None:
        try:
            try:
                raise MCPHttpStatusError(401)
            except MCPHttpStatusError:
                raise TimeoutError  # noqa: B904 — the implicit context is the point
        except TimeoutError as outer:
            assert is_http_unauthorized(outer) is False

    def test_text_alone_is_not_enough(self) -> None:
        assert is_http_unauthorized(RuntimeError("401 Unauthorized: token expired")) is False


class TestFailureKinds:
    @pytest.mark.parametrize("error", [MCPRequestNotSentError("x"), MCPSessionExpiredError("x")])
    def test_what_never_reached_the_server(self, error: Exception) -> None:
        assert request_never_reached_server(error)
        assert not request_may_have_run(error)

    @pytest.mark.parametrize("error", [MCPRequestLostError("x"), MCPConnectionLostError("x")])
    def test_what_may_have_run(self, error: Exception) -> None:
        assert request_may_have_run(error)
        assert not request_never_reached_server(error)

    @pytest.mark.parametrize("error", [MCPError(-32000, "Connection closed"), MCPHttpStatusError(500), TimeoutError()])
    def test_other_errors_are_neither(self, error: Exception) -> None:
        assert not request_never_reached_server(error)
        assert not request_may_have_run(error)

    def test_groups_and_causes_are_looked_through(self) -> None:
        wrapped = RuntimeError("x")
        wrapped.__cause__ = MCPSessionExpiredError("gone")
        assert request_never_reached_server(ExceptionGroup("tg", [wrapped]))


class _Store:
    """`ConfigurationService` as the tool cache uses it."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.writes: list[str] = []

    async def get_config(self, key: str, default: Any = None, use_cache: bool = False, *, keep_in_cache: bool = True) -> Any:  # noqa: ANN401
        return copy.deepcopy(self.values.get(key, default))

    async def set_config(self, key: str, value: Any, *, ttl_seconds: int | None = None, keep_in_cache: bool = True) -> bool:  # noqa: ANN401
        self.values[key] = copy.deepcopy(value)
        self.writes.append(key)
        return True

    async def delete_config(self, key: str) -> bool:
        return self.values.pop(key, None) is not None

    def pointers(self) -> list[str]:
        return sorted(k for k in self.values if "/pointers/" in k)


_TOOLS = [{"name": "search", "inputSchema": {}}, {"name": "create_issue", "inputSchema": {}}]


async def _turn(store: _Store, server: ResolvedMCPServer | None = None) -> tuple[MCPSessionManager, list[str], str | None]:
    """A turn's tool load: the tools the model gets and the server's instructions."""
    session_manager = MCPSessionManager(make_context(config_service=store))
    tools, instructions = await session_manager.tools(server or _server(), "jira")
    return session_manager, [t.namespaced_name for t in tools], instructions


@pytest.fixture
def listing_tools(client_manager_factory: _FakeClientManagerFactory, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """What the fake server lists, for every manager built from now on."""
    tools: list[Any] = list(_TOOLS)
    real_init = _FakeClientManager.__init__

    def _init(self: _FakeClientManager, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        real_init(self, *args, **kwargs)
        self.instructions = "Prefer search."
        self.list_responses = []

    async def _listing(self: _FakeClientManager) -> ToolListing:
        self.listings += 1
        if self.list_responses:
            return ToolListing(tools=self._answer(self.list_responses.pop(0)), instructions=self.instructions)
        return ToolListing(tools=list(tools), instructions=self.instructions)

    monkeypatch.setattr(_FakeClientManager, "__init__", _init)
    monkeypatch.setattr(_FakeClientManager, "fetch_tool_listing_in_session", _listing)
    return tools


class TestATurnTakesItsToolsFromTheCache:
    async def test_a_miss_lists_live_and_the_next_turn_opens_no_session(
        self, client_manager_factory: _FakeClientManagerFactory, listing_tools: list[Any],
    ) -> None:
        store = _Store()
        _, first, instructions = await _turn(store)
        assert len(client_manager_factory.built) == 1

        _, second, cached_instructions = await _turn(store)

        assert first == second == ["mcp_jira_search", "mcp_jira_create_issue"]
        assert instructions == cached_instructions == "Prefer search."
        # The second turn read the cache: no new session.
        assert len(client_manager_factory.built) == 1

    async def test_a_shared_admin_credential_is_kept_once_for_everyone(
        self, client_manager_factory: _FakeClientManagerFactory, listing_tools: list[Any],
    ) -> None:
        store = _Store()
        shared = {**_instance("api_token"), "useAdminAuth": True}
        await _turn(store, _server("api_token", instance=shared, owner_id="u1"))
        await _turn(store, _server("api_token", instance=shared, owner_id="u2"))

        assert store.pointers() == ["/services/mcp/tool-catalogs/pointers/inst-1/_shared"]
        assert len(client_manager_factory.built) == 1

    async def test_the_first_call_lists_once_then_calls(
        self, client_manager_factory: _FakeClientManagerFactory, listing_tools: list[Any],
    ) -> None:
        store = _Store()
        await _turn(store)
        session_manager, _, _ = await _turn(store)

        results = await asyncio.wait_for(asyncio.gather(*(
            session_manager.call(_server(), "search", {"n": n}) for n in range(3)
        )), 5)

        assert results == ["ok", "ok", "ok"]
        second = client_manager_factory.built[1]
        assert second.listings == 1 and len(second.calls) == 3

    async def test_a_tool_the_server_dropped_is_refused_and_the_cache_updated(
        self, client_manager_factory: _FakeClientManagerFactory, listing_tools: list[Any],
    ) -> None:
        store = _Store()
        await _turn(store)
        listing_tools[:] = [{"name": "search", "inputSchema": {}}]
        session_manager, _, _ = await _turn(store)

        with pytest.raises(MCPToolNotOfferedError, match="no longer offers the tool create_issue"):
            await session_manager.call(_server(), "create_issue", {})
        assert client_manager_factory.built[1].calls == []

        _, third, _ = await _turn(store)
        assert third == ["mcp_jira_search"]

    @pytest.mark.parametrize("changed", [
        {"name": "create_issue", "inputSchema": {}, "annotations": {"readOnlyHint": False}},
        {"name": "create_issue", "inputSchema": {"type": "object", "required": ["title"]}, "annotations": {"readOnlyHint": True}},
    ], ids=["hints", "arguments"])
    async def test_a_tool_that_changed_since_the_cache_is_refused_and_the_next_turn_has_it_new(
        self, client_manager_factory: _FakeClientManagerFactory, listing_tools: list[Any], changed: dict[str, Any],
    ) -> None:
        """The model planned, and the approval gate decided, on the cached version: a tool that
        stopped being read-only must not run on the old hint."""
        store = _Store()
        listing_tools[:] = [{"name": "create_issue", "inputSchema": {}, "annotations": {"readOnlyHint": True}}]
        await _turn(store)
        listing_tools[:] = [changed]
        session_manager, _, _ = await _turn(store)

        with pytest.raises(MCPToolChangedError, match="changed the tool create_issue since this chat loaded it"):
            await session_manager.call(_server(), "create_issue", {})
        assert client_manager_factory.built[1].calls == []

        third, _ = await MCPSessionManager(make_context(config_service=store)).tools(_server(), "jira")
        assert (third[0].input_schema, third[0].annotations) == (changed["inputSchema"], changed["annotations"])

    async def test_a_changed_description_alone_doesnt_stop_the_call(
        self, client_manager_factory: _FakeClientManagerFactory, listing_tools: list[Any],
    ) -> None:
        store = _Store()
        await _turn(store)
        listing_tools[:] = [{"name": "search", "inputSchema": {}, "description": "Search better."}, _TOOLS[1]]
        session_manager, _, _ = await _turn(store)

        assert await session_manager.call(_server(), "search", {}) == "ok"

    async def test_an_unchanged_fresh_entry_isnt_rewritten(
        self, client_manager_factory: _FakeClientManagerFactory, listing_tools: list[Any],
    ) -> None:
        store = _Store()
        await _turn(store)
        store.writes.clear()
        session_manager, _, _ = await _turn(store)

        await session_manager.call(_server(), "search", {})
        assert store.writes == []

    async def test_three_first_calls_whose_listing_gets_a_401_refresh_once(
        self, client_manager_factory: _FakeClientManagerFactory, listing_tools: list[Any], tokens: _TokenStore,
    ) -> None:
        store = _Store()
        server = _oauth_server()
        await _turn(store, server)
        session_manager, _, _ = await _turn(store, server)
        await session_manager._get_or_open(server)
        client_manager_factory.built[1].list_responses = [MCPHttpStatusError(401)]

        results = await asyncio.wait_for(asyncio.gather(*(
            session_manager.call(server, "search", {"n": n}) for n in range(3)
        )), 5)

        assert results == ["ok", "ok", "ok"]
        assert tokens.endpoint_calls == 1
        assert client_manager_factory.built[1].listings == 2

    async def test_a_listing_that_cant_reach_the_server_fails_the_call_as_never_sent(
        self, client_manager_factory: _FakeClientManagerFactory, listing_tools: list[Any], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = _Store()
        await _turn(store)
        cached = dict(store.values)
        session_manager, _, _ = await _turn(store)

        async def _unreachable(self: _FakeClientManager) -> _FakeClientManager:
            raise MCPRequestNotSentError("Couldn't reach the MCP server: All connection attempts failed")

        monkeypatch.setattr(_FakeClientManager, "open", _unreachable)
        with pytest.raises(MCPRequestNotSentError) as caught:
            await session_manager.call(_server(), "create_issue", {})

        assert not isinstance(caught.value, MCPCallInterruptedError)
        assert store.values == cached

    async def test_a_failed_listing_on_a_live_session_still_sends_the_call(
        self, client_manager_factory: _FakeClientManagerFactory, listing_tools: list[Any],
    ) -> None:
        store = _Store()
        await _turn(store)
        session_manager, _, _ = await _turn(store)
        live = await session_manager._get_or_open(_server())
        live.list_responses = [MCPRequestLostError("reset"), MCPRequestLostError("reset again")]

        assert await session_manager.call(_server(), "search", {}) == "ok"
        assert live.calls == [("search", {})]

    async def test_a_failed_listing_is_tried_again_by_the_next_call(
        self, client_manager_factory: _FakeClientManagerFactory, listing_tools: list[Any],
    ) -> None:
        store = _Store()
        await _turn(store)
        session_manager, _, _ = await _turn(store)
        live = await session_manager._get_or_open(_server())
        live.list_responses = [MCPRequestLostError("reset"), MCPRequestLostError("reset again")]

        await session_manager.call(_server(), "search", {})
        await session_manager.call(_server(), "search", {})
        assert live.listings == 3

    async def test_tools_that_changed_during_the_turn_are_listed_again_next_turn(
        self, client_manager_factory: _FakeClientManagerFactory, listing_tools: list[Any],
    ) -> None:
        store = _Store()
        session_manager, _, _ = await _turn(store)
        client_manager_factory.built[0].tools_changed = True

        await session_manager.aclose_all()

        assert store.pointers() == []

    async def test_a_refreshed_token_keeps_the_record_the_cache_checks(
        self, client_manager_factory: _FakeClientManagerFactory, tokens: _TokenStore,
    ) -> None:
        session_manager = MCPSessionManager(make_context())
        server = _server("oauth", auth={"oauthTokens": {"accessToken": "old-token"}, "connectedAt": 123})
        (await session_manager._get_or_open(server)).responses = [MCPHttpStatusError(401), "ok"]

        await session_manager.call(server, "search", {})

        assert server.auth["connectedAt"] == 123
        assert server.auth["oauthTokens"]["accessToken"] == "fresh-token-1"

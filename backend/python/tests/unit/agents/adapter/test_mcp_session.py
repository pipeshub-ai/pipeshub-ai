"""`MCPSessionManager` (`app/agents/agent_loop/mcp_session.py`) — per-request,
per-instance session reuse, and the on-demand OAuth-refresh-then-retry-once
flow for a call that looks like it failed against an expired token."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.agents.agent_loop import mcp_session as mcp_session_module
from app.agents.agent_loop.mcp_access import ResolvedMCPServer
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.mcp.models import OAuthTokens
from app.agents.mcp.token_refresh import MCPTokenRefreshError
from tests.unit.agents.adapter.conftest import make_context


def _instance(auth_mode: str = "none") -> dict[str, Any]:
    return {
        "_id": "inst-1", "orgId": "org-1", "createdBy": "user-1",
        "name": "JiraMCP", "typeId": "jira_mcp", "transport": "sse", "authMode": auth_mode,
        "createdAt": 0, "updatedAt": 0,
    }


def _server(auth_mode: str = "none", **overrides: Any) -> ResolvedMCPServer:
    defaults: dict[str, Any] = {
        "instance_id": "inst-1", "name": "JiraMCP", "display_name": "Jira MCP",
        "instance": _instance(auth_mode), "auth": {}, "owner_id": "user-1", "attached_tools": None,
    }
    defaults.update(overrides)
    return ResolvedMCPServer(**defaults)


class _FakeClientManager:
    """Records `open()`/`call_tool_in_session()`/`aclose()` calls; `responses`
    is consumed in order (an `Exception` instance is raised instead of
    returned)."""

    def __init__(self, config: Any, env: dict | None = None, headers: dict | None = None) -> None:
        self.config = config
        self.env = env
        self.headers = headers
        self.opened = False
        self.closed = False
        self.calls: list[tuple[str, dict]] = []
        self.responses: list[Any] = ["ok"]

    async def open(self) -> "_FakeClientManager":
        self.opened = True
        return self

    async def call_tool_in_session(self, tool_name: str, arguments: dict) -> Any:
        self.calls.append((tool_name, arguments))
        response = self.responses.pop(0) if self.responses else "ok"
        if isinstance(response, Exception):
            raise response
        return response

    async def aclose(self) -> None:
        self.closed = True


class _FakeClientManagerFactory:
    """Constructs `_FakeClientManager`s and keeps every instance built, so
    tests can assert on the SPECIFIC manager a given `open()` call built
    (e.g. the second one, after a refresh-triggered rebuild)."""

    def __init__(self) -> None:
        self.built: list[_FakeClientManager] = []

    def __call__(self, config: Any, env: dict | None = None, headers: dict | None = None) -> _FakeClientManager:
        manager = _FakeClientManager(config, env=env, headers=headers)
        self.built.append(manager)
        return manager


@pytest.fixture
def client_manager_factory(monkeypatch: pytest.MonkeyPatch) -> _FakeClientManagerFactory:
    factory = _FakeClientManagerFactory()
    monkeypatch.setattr(mcp_session_module, "MCPClientManager", factory)
    return factory


class TestCall:
    async def test_opens_session_once_and_reuses_across_calls(
        self, client_manager_factory: _FakeClientManagerFactory,
    ) -> None:
        context = make_context()
        manager = MCPSessionManager(context)
        server = _server()

        result1 = await manager.call(server, "search", {"q": "a"})
        result2 = await manager.call(server, "search", {"q": "b"})

        assert result1 == "ok"
        assert result2 == "ok"
        assert len(client_manager_factory.built) == 1
        assert client_manager_factory.built[0].calls == [("search", {"q": "a"}), ("search", {"q": "b"})]

    async def test_second_session_manager_sharing_tool_state_reuses_cache(
        self, client_manager_factory: _FakeClientManagerFactory,
    ) -> None:
        """Mirrors `ToolInstanceCreator`'s per-request `_client_cache`
        contract: any `MCPSessionManager` built against the SAME
        `context.tool_state` dict shares the same underlying session cache
        (this is what lets `stream_bridge.py`'s teardown construct a fresh
        `MCPSessionManager` and still find the real sessions)."""
        context = make_context()
        server = _server()
        await MCPSessionManager(context).call(server, "search", {})
        await MCPSessionManager(context).call(server, "search", {})

        assert len(client_manager_factory.built) == 1

    async def test_non_oauth_expiry_like_error_is_not_retried(
        self, client_manager_factory: _FakeClientManagerFactory,
    ) -> None:
        context = make_context()
        manager = MCPSessionManager(context)
        server = _server(auth_mode="api_token")

        opened = await manager._get_or_open(server)  # noqa: SLF001 — pre-seed to control the raised error
        opened.responses = [RuntimeError("401 unauthorized")]

        with pytest.raises(RuntimeError, match="401"):
            await manager.call(server, "search", {})

        assert len(client_manager_factory.built) == 1

    async def test_non_expiry_error_propagates_without_refresh(
        self, client_manager_factory: _FakeClientManagerFactory, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = False

        async def _fake_refresh(*_args: Any, **_kwargs: Any) -> OAuthTokens:
            nonlocal called
            called = True
            raise AssertionError("refresh should not be attempted for a non-expiry error")

        monkeypatch.setattr(mcp_session_module, "refresh_credential_record", _fake_refresh)
        context = make_context()
        server = _server(auth_mode="oauth")

        session_manager = MCPSessionManager(context)
        opened = await session_manager._get_or_open(server)  # noqa: SLF001
        opened.responses = [RuntimeError("some unrelated network error")]

        with pytest.raises(RuntimeError, match="unrelated"):
            await session_manager.call(server, "search", {})
        assert called is False

    async def test_oauth_expiry_refreshes_rebuilds_and_retries_once(
        self, client_manager_factory: _FakeClientManagerFactory, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        refreshed_tokens = OAuthTokens(access_token="fresh-token")

        async def _fake_refresh(instance_id: str, owner_id: str, config_service: Any, **kwargs: Any) -> OAuthTokens:
            assert instance_id == "inst-1"
            assert owner_id == "user-1"
            return refreshed_tokens

        monkeypatch.setattr(mcp_session_module, "refresh_credential_record", _fake_refresh)
        context = make_context()
        server = _server(auth_mode="oauth")
        session_manager = MCPSessionManager(context)

        first_manager = await session_manager._get_or_open(server)  # noqa: SLF001
        first_manager.responses = [RuntimeError("401 Unauthorized: token expired")]

        result = await session_manager.call(server, "search", {"q": "x"})

        assert result == "ok"
        assert len(client_manager_factory.built) == 2
        assert first_manager.closed is True
        assert server.auth["oauthTokens"]["accessToken"] == "fresh-token"
        second_manager = client_manager_factory.built[1]
        assert second_manager.calls == [("search", {"q": "x"})]

    async def test_concurrent_expiry_refreshes_only_once(
        self, client_manager_factory: _FakeClientManagerFactory, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two tool calls racing against the same expired-token instance must not each
        rebuild-and-store their own session — the loser would leak an orphaned session and
        the credential would be refreshed twice. Both `_get_or_open` and
        `_refresh_and_reopen_locked` share the same per-instance lock, so whichever of the
        two the second caller ends up blocked in, it comes out the other side reusing the
        manager the first caller already built rather than building its own."""
        refresh_calls = 0
        refresh_started = asyncio.Event()
        release_refresh = asyncio.Event()

        async def _fake_refresh(instance_id: str, owner_id: str, config_service: Any, **kwargs: Any) -> OAuthTokens:
            nonlocal refresh_calls
            refresh_calls += 1
            refresh_started.set()
            await release_refresh.wait()
            return OAuthTokens(access_token="fresh-token")

        monkeypatch.setattr(mcp_session_module, "refresh_credential_record", _fake_refresh)
        context = make_context()
        server = _server(auth_mode="oauth")
        session_manager = MCPSessionManager(context)

        first_manager = await session_manager._get_or_open(server)  # noqa: SLF001
        # Both concurrent calls see an expired-token failure on the same (only) manager.
        first_manager.responses = [
            RuntimeError("401 Unauthorized: token expired"),
            RuntimeError("401 Unauthorized: token expired"),
        ]

        task_a = asyncio.create_task(session_manager.call(server, "search", {"q": "a"}))
        task_b = asyncio.create_task(session_manager.call(server, "search", {"q": "b"}))

        await refresh_started.wait()
        # Give the second task a chance to reach (and block on) the lock before unblocking
        # the refresh — proves it's actually waiting rather than racing ahead independently.
        await asyncio.sleep(0)
        release_refresh.set()

        result_a, result_b = await asyncio.gather(task_a, task_b)

        assert result_a == "ok"
        assert result_b == "ok"
        assert refresh_calls == 1
        # Exactly one rebuilt session — the second caller reused it instead of building
        # its own third manager.
        assert len(client_manager_factory.built) == 2
        assert first_manager.closed is True

    async def test_refresh_failure_propagates(
        self, client_manager_factory: _FakeClientManagerFactory, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fake_refresh(*_args: Any, **_kwargs: Any) -> OAuthTokens:
            raise MCPTokenRefreshError("no refresh token available")

        monkeypatch.setattr(mcp_session_module, "refresh_credential_record", _fake_refresh)
        context = make_context()
        server = _server(auth_mode="oauth")
        session_manager = MCPSessionManager(context)

        first_manager = await session_manager._get_or_open(server)  # noqa: SLF001
        first_manager.responses = [RuntimeError("401 unauthorized")]

        with pytest.raises(MCPTokenRefreshError):
            await session_manager.call(server, "search", {})


class TestAcloseAll:
    async def test_closes_and_clears_every_cached_manager(
        self, client_manager_factory: _FakeClientManagerFactory,
    ) -> None:
        context = make_context()
        server_a = _server(instance_id="inst-1", instance=_instance())
        server_b = _server(instance_id="inst-2", instance={**_instance(), "_id": "inst-2"})
        session_manager = MCPSessionManager(context)
        await session_manager.call(server_a, "search", {})
        await session_manager.call(server_b, "search", {})

        await session_manager.aclose_all()

        assert all(m.closed for m in client_manager_factory.built)
        assert session_manager._managers == {}  # noqa: SLF001

    async def test_is_a_no_op_when_nothing_was_ever_opened(self) -> None:
        context = make_context()
        session_manager = MCPSessionManager(context)
        await session_manager.aclose_all()  # must not raise

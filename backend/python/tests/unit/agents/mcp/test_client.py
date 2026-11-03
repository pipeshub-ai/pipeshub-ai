"""Unit tests for app.agents.mcp.client, against a stand-in for the SDK's `Client`.

The stand-in writes the operation's wire record the way the URL guard and the response hook
do for real requests, so the error mapping is tested on what the client actually reads.
`test_client_e2e.py` runs the same client against real servers.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import httpx2
import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import (
    CONNECTION_CLOSED,
    INTERNAL_ERROR,
    METHOD_NOT_FOUND,
    REQUEST_TIMEOUT,
)

from app.agents.mcp import client as client_module
from app.agents.mcp import wire
from app.agents.mcp.client import (
    INHERITED_NETWORK_ENV,
    MCPClientManager,
    MCPConnectionError,
    _Transport,
    build_transport,
    http_read_timeout,
)
from app.agents.mcp.errors import (
    MCPConnectionLostError,
    MCPHttpStatusError,
    MCPRequestLostError,
    MCPRequestNotSentError,
    MCPSessionExpiredError,
    MCPUrlBlockedError,
    is_http_unauthorized,
)
from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPTransport

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator


def _config(**overrides: Any) -> MCPServerConfig:  # noqa: ANN401
    defaults: dict[str, Any] = {
        "id": "inst-1",
        "org_id": "org-1",
        "created_by": "user-1",
        "name": "Server",
        "transport": MCPTransport.STDIO,
        "command": "npx",
        "auth_mode": MCPAuthMode.NONE,
        "created_at": 0,
        "updated_at": 0,
    }
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


def _http_config(**overrides: Any) -> MCPServerConfig:  # noqa: ANN401
    return _config(**{"transport": MCPTransport.STREAMABLE_HTTP, "url": "https://mcp.example.com/mcp?api_key=secret", "command": None, **overrides})


async def _answered(status: int, *, session_id: bool = False, challenge: str | None = None) -> None:
    """What the response hook records for one request."""
    headers = {"mcp-session-id": "s-1"} if session_id else {}
    response = SimpleNamespace(status_code=status, request=SimpleNamespace(headers=headers))
    if challenge is not None:
        response.headers = SimpleNamespace(get_list=lambda _name: [challenge])
    await wire.record_response(response)


class _FakeSession:
    def __init__(self, *, modern: bool = False, instructions: str | None = None) -> None:
        self.initialize_result = None if modern else SimpleNamespace(instructions=instructions)
        self.discover_result = SimpleNamespace(instructions=instructions) if modern else None
        self.instructions = instructions
        # Where the SDK keeps whether the connection has ended.
        self._dispatcher = SimpleNamespace(_closed=False)

    async def validate_tool_result(self, _name: str, _result: Any) -> None:  # noqa: ANN401
        return None


class _FakeClient:
    """The SDK `Client` as this module uses it. `script` decides what each step does."""

    def __init__(self, script: "_Script", streams: Any, **kwargs: Any) -> None:  # noqa: ANN401
        self.script = script
        self.streams = streams
        self.kwargs = kwargs
        self.session = _FakeSession(modern=script.modern, instructions=script.instructions)
        self.protocol_version = "2026-07-28" if script.modern else "2025-11-25"
        self._session_open = False
        self.exited = asyncio.Event()

    @property
    def instructions(self) -> str | None:
        return self.session.instructions

    @property
    def _session(self) -> _FakeSession:
        return self.session

    async def __aenter__(self) -> "_FakeClient":
        await self.script.enter(self)
        self._session_open = True
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self._session_open = False
        self.exited.set()
        await self.script.exit(self)

    def _require_session(self) -> None:
        if not self._session_open:
            raise RuntimeError("Client must be used within an async context manager")

    async def list_tools(self, *, cursor: str | None = None) -> Any:  # noqa: ANN401
        self._require_session()
        return await self.script.list_page(self, cursor)

    async def call_tool(
        self, name: str, arguments: dict[str, Any], read_timeout_seconds: float | None = None,
        progress_callback: Any = None,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        self._require_session()
        self.script.calls.append((name, arguments, read_timeout_seconds))
        self.script.progress_callbacks.append(progress_callback)
        return await self.script.call(self, name, arguments)


async def _nothing(_client: _FakeClient) -> None:
    return None


class _Script:
    def __init__(self) -> None:
        self.modern = False
        self.instructions: str | None = None
        self.clients: list[_FakeClient] = []
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []
        self.progress_callbacks: list[Any] = []
        self.enter: Callable[[_FakeClient], Awaitable[None]] = _nothing
        self.exit: Callable[[_FakeClient], Awaitable[None]] = _nothing
        self.pages: list[Any] = [SimpleNamespace(tools=["t1", "t2"], next_cursor=None)]
        self.answer: Callable[[_FakeClient, str, dict[str, Any]], Awaitable[Any]] = self._ok

    async def _ok(self, _client: _FakeClient, name: str, _arguments: dict[str, Any]) -> Any:  # noqa: ANN401
        return {"result": f"{name} ok"}

    async def list_page(self, _client: _FakeClient, cursor: str | None) -> Any:  # noqa: ANN401
        page = self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        if isinstance(page, Exception):
            raise page
        if callable(page):
            return await page(cursor)
        return page

    async def call(self, client: _FakeClient, name: str, arguments: dict[str, Any]) -> Any:  # noqa: ANN401
        return await self.answer(client, name, arguments)

    def make(self, streams: Any, **kwargs: Any) -> _FakeClient:  # noqa: ANN401
        client = _FakeClient(self, streams, **kwargs)
        self.clients.append(client)
        return client


@pytest.fixture
def sdk() -> Iterator[_Script]:
    """The SDK client replaced by `_Script`, the transport by a placeholder that remembers
    where stderr is captured, and the pre-connect URL check skipped."""
    script = _Script()
    script.stderr_paths: list[Path | None] = []  # type: ignore[attr-defined]
    script.transports: list[_Transport] = []  # type: ignore[attr-defined]

    def _build(config: MCPServerConfig, *, env: Any = None, headers: Any = None, stderr_log_file: Path | None = None) -> _Transport:  # noqa: ANN401
        script.stderr_paths.append(stderr_log_file)  # type: ignore[attr-defined]
        transport = _Transport("streams", http_clients=[MagicMock(headers={})])
        script.transports.append(transport)  # type: ignore[attr-defined]
        return transport

    client_module._initialize_only.clear()
    with patch.object(client_module, "Client", side_effect=script.make), \
         patch.object(client_module, "build_transport", side_effect=_build), \
         patch.object(client_module, "check_mcp_url", new=AsyncMock()):
        yield script
    client_module._initialize_only.clear()


class TestBlockedUrlFailsBeforeConnecting:
    async def test_connect_rejects_a_loopback_url_without_building_a_transport(self) -> None:
        config = _config(transport=MCPTransport.STREAMABLE_HTTP, url="http://127.0.0.1:8080/mcp", command=None)
        with patch.object(client_module, "build_transport") as build:
            with pytest.raises(MCPConnectionError, match="not allowed"):
                async with MCPClientManager(config).connect():
                    pass
        build.assert_not_called()

    async def test_open_rejects_a_metadata_url_without_building_a_transport(self) -> None:
        config = _config(transport=MCPTransport.SSE, url="http://169.254.169.254/latest", command=None)
        with patch.object(client_module, "build_transport") as build:
            with pytest.raises(MCPConnectionError, match="not allowed"):
                await MCPClientManager(config).open()
        build.assert_not_called()


@pytest.fixture
def allow_custom_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", "true")


class TestBuildTransport:
    @pytest.fixture(autouse=True)
    def _custom_stdio_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", "true")
        for name in INHERITED_NETWORK_ENV:
            monkeypatch.delenv(name, raising=False)

    def test_a_local_server_gets_its_command_and_env(self) -> None:
        config = _config(command="npx", args=["-y", "server"])
        with patch.object(client_module, "stdio_client") as stdio:
            transport = build_transport(config, env={"KEY": "value"})

        params = stdio.call_args.args[0]
        assert (params.command, params.args, params.env) == ("npx", ["-y", "server"], {"KEY": "value"})
        assert transport.streams is stdio.return_value

    def test_a_local_server_inherits_the_hosts_network_settings_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:3128")
        monkeypatch.setenv("NODE_EXTRA_CA_CERTS", "/etc/ssl/corp-ca.pem")
        monkeypatch.setenv("NPM_CONFIG_REGISTRY", "https://npm.corp.example.com")
        monkeypatch.setenv("DATABASE_PASSWORD", "not-for-servers")
        with patch.object(client_module, "stdio_client") as stdio:
            build_transport(_config(), env={"API_KEY": "k", "HTTPS_PROXY": "http://own-proxy:8080"})

        assert stdio.call_args.args[0].env == {
            "HTTPS_PROXY": "http://own-proxy:8080",
            "NODE_EXTRA_CA_CERTS": "/etc/ssl/corp-ca.pem",
            "NPM_CONFIG_REGISTRY": "https://npm.corp.example.com",
            "API_KEY": "k",
        }

    async def test_stderr_goes_to_a_real_file_the_transport_closes(self, tmp_path: Path) -> None:
        """The subprocess writes to it through its descriptor."""
        log = tmp_path / "stderr.log"
        with patch.object(client_module, "stdio_client") as stdio:
            transport = build_transport(_config(), stderr_log_file=log)

        errlog = stdio.call_args.kwargs["errlog"]
        assert errlog.fileno() >= 0
        assert Path(errlog.name) == log
        await transport.aclose()
        assert errlog.closed

    def test_a_local_server_without_command_raises(self) -> None:
        with pytest.raises(MCPConnectionError, match="no command"):
            build_transport(_config(command=None))

    def test_streamable_http_goes_through_the_guarded_client(self) -> None:
        config = _http_config(url="https://example.com/mcp")
        with patch.object(client_module, "streamable_http_client") as transport_fn, \
             patch.object(client_module, "guarded_mcp_http_client") as guarded:
            transport = build_transport(config, headers={"Authorization": "Bearer x"})

        guarded.assert_called_once_with(
            "https://example.com/mcp", allow_private=True, headers={"Authorization": "Bearer x"}, read_timeout=300.0,
        )
        transport_fn.assert_called_once_with("https://example.com/mcp", http_client=guarded.return_value)
        # The SDK doesn't close a client it was given.
        assert transport.owned_http_client is guarded.return_value
        assert transport.http_clients == [guarded.return_value]

    def test_a_long_call_timeout_lengthens_the_http_read_timeout(self) -> None:
        with patch.object(client_module, "streamable_http_client"), \
             patch.object(client_module, "guarded_mcp_http_client") as guarded:
            build_transport(_http_config(call_timeout_seconds=600))

        assert guarded.call_args.kwargs["read_timeout"] == 630.0

    def test_the_private_network_setting_reaches_the_guard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_ALLOW_PRIVATE_NETWORK_URLS", "false")
        with patch.object(client_module, "streamable_http_client"), \
             patch.object(client_module, "guarded_mcp_http_client") as guarded:
            build_transport(_http_config())

        assert guarded.call_args.kwargs["allow_private"] is False

    def test_sse_goes_through_the_guarded_factory_with_both_timeouts(self) -> None:
        config = _config(transport=MCPTransport.SSE, url="https://example.com/sse", command=None)
        with patch.object(client_module, "sse_client") as sse:
            transport = build_transport(config, headers={"Authorization": "Bearer x"})

        kwargs = sse.call_args.kwargs
        assert sse.call_args.args == ("https://example.com/sse",)
        assert kwargs["headers"] == {"Authorization": "Bearer x"}
        assert (kwargs["timeout"], kwargs["sse_read_timeout"]) == (30.0, 300.0)
        # The clients the SDK has the factory make are the ones a refreshed token goes to.
        made = kwargs["httpx_client_factory"](headers={"Authorization": "Bearer x"}, timeout=httpx2.Timeout(30.0))
        assert transport.http_clients == [made]
        assert transport.owned_http_client is None

    @pytest.mark.parametrize("transport", [MCPTransport.SSE, MCPTransport.STREAMABLE_HTTP])
    def test_http_without_url_raises(self, transport: MCPTransport) -> None:
        with pytest.raises(MCPConnectionError, match="no url"):
            build_transport(_config(transport=transport, url=None, command=None))

    def test_unsupported_transport_raises(self) -> None:
        config = MagicMock()
        config.id = "inst-1"
        config.transport = "carrier_pigeon"
        with pytest.raises(MCPConnectionError, match="Unsupported MCP transport"):
            build_transport(config)


class TestBuildTransportStdioPolicy:
    async def test_build_transport_refuses_legacy_custom_stdio_when_flag_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MCP_ALLOW_CUSTOM_STDIO", raising=False)
        marker = tmp_path / "spawned"
        config = _config(transport=MCPTransport.STDIO, command="touch", args=[str(marker)])
        with pytest.raises(MCPConnectionError, match="MCP_ALLOW_CUSTOM_STDIO"):
            await MCPClientManager(config).list_tools()
        assert not marker.exists()

    @pytest.mark.parametrize("flag", ["true", "false"])
    def test_build_transport_uses_template_command_for_catalog(
        self, flag: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", flag)
        config = _config(transport=MCPTransport.STDIO, type_id="slack", command="touch", args=["/tmp/x"])
        with patch.object(client_module, "stdio_client") as stdio:
            build_transport(config)
        params = stdio.call_args.args[0]
        assert params.command == "npx"
        assert params.args == ["-y", "@modelcontextprotocol/server-slack@2025.4.25"]

    def test_build_transport_refuses_catalog_http_template_flipped_to_stdio(self) -> None:
        config = _config(transport=MCPTransport.STDIO, type_id="github", command="touch", args=["/tmp/x"])
        with patch.object(client_module, "stdio_client") as stdio:
            with pytest.raises(MCPConnectionError, match="not a STDIO server"):
                build_transport(config)
        stdio.assert_not_called()

    @pytest.mark.usefixtures("allow_custom_stdio")
    @pytest.mark.parametrize("env_name", ["NODE_OPTIONS", "LD_PRELOAD", "PATH", "PYTHONPATH"])
    def test_build_transport_refuses_dangerous_env_names(self, env_name: str) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx", args=["-y", "server"])
        with patch.object(client_module, "stdio_client") as stdio:
            with pytest.raises(MCPConnectionError, match=env_name):
                build_transport(config, env={env_name: "x"})
        stdio.assert_not_called()


class TestTheTransportsOwnPieces:
    def test_new_headers_reach_every_client(self) -> None:
        clients = [MagicMock(headers=httpx2.Headers({"Authorization": "Bearer old"})) for _ in range(2)]
        _Transport("streams", http_clients=clients).set_headers({"Authorization": "Bearer new"})
        assert [c.headers["authorization"] for c in clients] == ["Bearer new", "Bearer new"]

    async def test_closing_closes_the_owned_client_even_if_it_fails(self) -> None:
        owned = MagicMock(aclose=AsyncMock(side_effect=RuntimeError("already closed")))
        await _Transport("streams", owned_http_client=owned).aclose()
        owned.aclose.assert_awaited_once()


class TestStderrHelpers:
    def test_new_stderr_capture_path_returns_none_for_non_stdio(self) -> None:
        from app.agents.mcp.client import _new_stderr_capture_path

        assert _new_stderr_capture_path(_http_config()) is None

    def test_cleanup_stderr_capture_path_noop_for_none(self) -> None:
        from app.agents.mcp.client import _cleanup_stderr_capture_path

        _cleanup_stderr_capture_path(None)  # must not raise

    def test_read_stderr_tail_returns_empty_for_none(self) -> None:
        from app.agents.mcp.client import _read_stderr_tail

        assert _read_stderr_tail(None) == ""

    def test_read_stderr_tail_returns_empty_on_oserror(self, tmp_path: Path) -> None:
        from app.agents.mcp.client import _read_stderr_tail

        assert _read_stderr_tail(tmp_path / "does-not-exist.log") == ""


class TestOneShotUse:
    async def test_connect_yields_the_client_and_closes_it(self, sdk: _Script) -> None:
        async with MCPClientManager(_config()).connect() as client:
            assert client is sdk.clients[0]
            assert not client.exited.is_set()
        assert client.exited.is_set()

    async def test_list_tools_returns_every_page(self, sdk: _Script) -> None:
        async def _page(cursor: str | None) -> Any:  # noqa: ANN401
            return SimpleNamespace(tools=[f"after-{cursor}"], next_cursor="2" if cursor is None else None)

        sdk.pages = [_page]
        assert await MCPClientManager(_config()).list_tools() == ["after-None", "after-2"]

    async def test_a_server_that_never_stops_paging_is_cut_off(self, sdk: _Script) -> None:
        sdk.pages = [SimpleNamespace(tools=["t"], next_cursor="again")]
        with patch.object(client_module, "_MAX_TOOL_PAGES", 3):
            assert await MCPClientManager(_config()).list_tools() == ["t", "t", "t"]

    async def test_call_tool_returns_the_protocol_result_and_closes(self, sdk: _Script) -> None:
        assert await MCPClientManager(_config()).call_tool("search", {"q": "x"}) == {"result": "search ok"}
        backstop = client_module.max_call_seconds(_config()) + client_module._OUTER_TIMEOUT_MARGIN_SECONDS
        assert sdk.calls == [("search", {"q": "x"}, backstop)]
        assert sdk.clients[0].exited.is_set()

    async def test_the_session_closes_even_when_the_caller_is_cancelled(self, sdk: _Script) -> None:
        started = asyncio.Event()

        async def _hang(*_args: object) -> Any:  # noqa: ANN401
            started.set()
            await asyncio.sleep(3600)

        sdk.answer = _hang
        call = asyncio.ensure_future(MCPClientManager(_config()).call_tool("search", {}))
        await started.wait()
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

        await asyncio.wait_for(sdk.clients[0].exited.wait(), 5)


class TestAListingCarriesWhatTheCacheKeeps:
    @staticmethod
    def _page(**hints: Any) -> Any:  # noqa: ANN401
        from mcp.types import ListToolsResult, Tool

        return ListToolsResult(tools=[Tool(name="search", input_schema={"type": "object"})], **hints)

    async def test_instructions_come_with_the_tools(self, sdk: _Script) -> None:
        sdk.instructions = "Prefer search."
        listing = await MCPClientManager(_http_config()).fetch_tool_listing()
        assert (listing.tools, listing.instructions) == (["t1", "t2"], "Prefer search.")

    async def test_a_modern_servers_hints_are_read(self, sdk: _Script) -> None:
        sdk.modern = True
        sdk.pages = [self._page(ttl_ms=60000, cache_scope="public")]
        listing = await MCPClientManager(_http_config()).fetch_tool_listing()
        assert (listing.ttl_seconds, listing.public) == (60.0, True)

    async def test_hints_the_server_didnt_send_are_none(self, sdk: _Script) -> None:
        """The protocol types default them to 0 and private, which would mean "don't cache"."""
        sdk.modern = True
        sdk.pages = [self._page()]
        listing = await MCPClientManager(_http_config()).fetch_tool_listing()
        assert (listing.ttl_seconds, listing.public) == (None, False)

    async def test_an_older_sessions_hints_mean_nothing(self, sdk: _Script) -> None:
        sdk.pages = [self._page(ttl_ms=60000, cache_scope="public")]
        listing = await MCPClientManager(_http_config()).fetch_tool_listing()
        assert (listing.ttl_seconds, listing.public) == (None, False)

    async def test_a_listing_cut_at_the_page_limit_says_so(self, sdk: _Script, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp.types import ListToolsResult, Tool

        monkeypatch.setattr(client_module, "_MAX_TOOL_PAGES", 2)
        monkeypatch.setattr(client_module, "_truncation_warned", set())
        pages = [ListToolsResult(tools=[Tool(name=f"t{i}", input_schema={"type": "object"})], next_cursor=str(i + 1)) for i in range(3)]
        sdk.pages = list(pages)
        listing = await MCPClientManager(_http_config()).fetch_tool_listing()
        assert [t.name for t in listing.tools] == ["t0", "t1"]
        assert caplog.text.count("lists more than 2 pages of tools; only the first 2 tools are used") == 1

        sdk.pages = list(pages)
        await MCPClientManager(_http_config()).fetch_tool_listing()
        assert caplog.text.count("lists more than 2 pages") == 1, "once per server, not every turn"

    async def test_the_open_session_gives_the_same(self, sdk: _Script) -> None:
        sdk.modern, sdk.instructions = True, "Prefer search."
        sdk.pages = [self._page(ttl_ms=1500)]
        manager = await _open(sdk)
        listing = await manager.fetch_tool_listing_in_session()
        assert (listing.instructions, listing.ttl_seconds) == ("Prefer search.", 1.5)
        assert [t.name for t in await manager.list_tools_in_session()] == ["search"]


class TestConnecting:
    async def test_the_sdk_client_is_made_for_this_server(self, sdk: _Script) -> None:
        manager = MCPClientManager(_config(call_timeout_seconds=90))
        await manager.open()
        assert sdk.clients[0].streams == "streams"
        # The SDK's own response cache stays off, and PipesHub introduces itself by name.
        assert sdk.clients[0].kwargs == {
            "mode": "legacy", "read_timeout_seconds": 90, "cache": None, "message_handler": manager._on_server_message,
            "client_info": client_module.CLIENT_INFO,
        }
        assert client_module.CLIENT_INFO.name == "PipesHub"

    async def test_a_server_saying_its_tools_changed_is_noted(self, sdk: _Script) -> None:
        from mcp.types import LoggingMessageNotification, ToolListChangedNotification

        manager = MCPClientManager(_config())
        await manager._on_server_message(RuntimeError("a transport error reaches the handler too"))
        await manager._on_server_message(LoggingMessageNotification(params={"level": "info", "data": "x"}))
        assert manager.tools_changed is False

        await manager._on_server_message(ToolListChangedNotification())
        assert manager.tools_changed is True

    async def test_open_is_idempotent(self, sdk: _Script) -> None:
        manager = MCPClientManager(_config())
        assert await manager.open() is await manager.open()
        assert len(sdk.clients) == 1

    async def test_the_session_runs_outside_the_callers_task(self, sdk: _Script) -> None:
        """The SDK's task group must be entered and left in one task, and a transport failure in
        it cancels that task: it can't be the chat turn's."""
        entered_in: list[asyncio.Task[Any] | None] = []

        async def _enter(_client: _FakeClient) -> None:
            entered_in.append(asyncio.current_task())

        sdk.enter = _enter
        manager = MCPClientManager(_config())
        await manager.open()

        assert entered_in[0] is not asyncio.current_task()
        await manager.aclose()

    async def test_instructions_are_kept_trimmed(self, sdk: _Script) -> None:
        sdk.instructions = "  Prefer search over list.  "
        manager = MCPClientManager(_config())
        assert manager.server_instructions is None
        await manager.open()
        assert manager.server_instructions == "Prefer search over list."

    async def test_blank_instructions_are_none(self, sdk: _Script) -> None:
        sdk.instructions = "   "
        manager = MCPClientManager(_config())
        await manager.open()
        assert manager.server_instructions is None

    async def test_an_unexpected_failure_is_a_connection_error(self, sdk: _Script) -> None:
        async def _fail(_client: _FakeClient) -> None:
            raise RuntimeError("boom")

        sdk.enter = _fail
        with pytest.raises(MCPConnectionError, match="boom"):
            await MCPClientManager(_config()).open()

    async def test_a_connection_error_from_inside_is_kept_as_it_is(self, sdk: _Script) -> None:
        reason = OSError("no such file")
        typed = MCPConnectionError("already typed")
        typed.__cause__ = reason

        async def _fail(_client: _FakeClient) -> None:
            raise ExceptionGroup("tg", [typed])

        sdk.enter = _fail
        with pytest.raises(MCPConnectionError) as caught:
            await MCPClientManager(_config()).open()
        assert caught.value is typed
        assert caught.value.__cause__ is reason

    async def test_a_failure_grouped_with_a_cancellation_still_fails_the_open(self, sdk: _Script) -> None:
        async def _fail(_client: _FakeClient) -> None:
            raise BaseExceptionGroup("tg", [RuntimeError("transport died"), asyncio.CancelledError()])

        sdk.enter = _fail
        with pytest.raises(MCPConnectionError, match="transport died"):
            await MCPClientManager(_config()).open()

    async def test_the_local_servers_stderr_is_kept_apart_and_its_file_removed(self, sdk: _Script) -> None:
        async def _write_stderr_then_fail(_client: _FakeClient) -> None:
            sdk.stderr_paths[-1].write_text("exa-mcp-server: missing EXA_API_KEY\n")  # type: ignore[attr-defined]
            raise MCPError(CONNECTION_CLOSED, "Connection closed")

        sdk.enter = _write_stderr_then_fail
        manager = MCPClientManager(_config())
        with pytest.raises(MCPConnectionError) as caught:
            await manager.open()

        # The stderr tail is kept for operators and admins, never in the caller-facing text.
        assert "missing EXA_API_KEY" not in str(caught.value)
        assert caught.value.stderr_tail == "exa-mcp-server: missing EXA_API_KEY"
        assert "missing EXA_API_KEY" in caught.value.detail
        assert not sdk.stderr_paths[-1].exists()  # type: ignore[attr-defined]
        assert manager.is_open is False

    async def test_a_failed_transport_build_removes_the_stderr_file(self) -> None:
        captured: list[Path] = []

        def _fail(_config: MCPServerConfig, **kwargs: Any) -> Any:  # noqa: ANN401
            captured.append(kwargs["stderr_log_file"])
            raise MCPConnectionError("no command configured")

        with patch.object(client_module, "build_transport", side_effect=_fail):
            with pytest.raises(MCPConnectionError, match="no command"):
                await MCPClientManager(_config()).open()

        assert captured and not captured[0].exists()

    async def test_a_hung_connect_times_out_and_is_abandoned(self, sdk: _Script) -> None:
        abandoned = asyncio.Event()

        async def _hang(_client: _FakeClient) -> None:
            try:
                await asyncio.sleep(3600)
            finally:
                abandoned.set()

        sdk.enter = _hang
        with pytest.raises(MCPConnectionError, match=r"Timed out connecting to the MCP server after 0\.1s"):
            await MCPClientManager(_config(connect_timeout_seconds=0.1)).open()
        assert abandoned.is_set()

    async def test_a_timeout_is_a_timeout_even_after_a_refused_version_probe(self, sdk: _Script) -> None:
        async def _probe_refused_then_hang(_client: _FakeClient) -> None:
            await _answered(400)
            await asyncio.sleep(3600)

        sdk.enter = _probe_refused_then_hang
        with pytest.raises(MCPConnectionError, match="Timed out") as caught:
            await MCPClientManager(_http_config(connect_timeout_seconds=0.1)).open()
        assert not isinstance(caught.value, MCPHttpStatusError)

    async def test_a_cancelled_open_abandons_the_connect(self, sdk: _Script) -> None:
        started, abandoned = asyncio.Event(), asyncio.Event()

        async def _hang(_client: _FakeClient) -> None:
            started.set()
            try:
                await asyncio.sleep(3600)
            finally:
                abandoned.set()

        sdk.enter = _hang
        manager = MCPClientManager(_config())
        opening = asyncio.ensure_future(manager.open())
        await started.wait()
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await opening

        assert abandoned.is_set()
        assert manager.is_open is False
        assert not sdk.stderr_paths[-1].exists()  # type: ignore[attr-defined]


class TestWhatAFailedConnectSays:
    async def _open_failing(self, sdk: _Script, record: Callable[[], Awaitable[None]], error: BaseException) -> MCPConnectionError:
        async def _fail(_client: _FakeClient) -> None:
            await record()
            raise error

        sdk.enter = _fail
        with pytest.raises(MCPConnectionError) as caught:
            await MCPClientManager(_http_config()).open()
        return caught.value

    async def test_a_401_is_restored_as_unauthorized(self, sdk: _Script) -> None:
        async def _probe_and_handshake_refused() -> None:
            await _answered(401)
            await _answered(401)

        error = await self._open_failing(sdk, _probe_and_handshake_refused, MCPError(INTERNAL_ERROR, "Server returned an error response"))
        assert isinstance(error, MCPHttpStatusError) and error.status_code == 401
        assert is_http_unauthorized(error)
        assert "secret" not in str(error)
        assert "Server returned an error response" not in str(error)

    async def test_a_403_at_the_handshake_keeps_the_servers_challenge(self, sdk: _Script) -> None:
        error = await self._open_failing(
            sdk, lambda: _answered(403, challenge='Bearer error="insufficient_scope", scope="admin"'),
            MCPError(INTERNAL_ERROR, "Server returned an error response"),
        )
        assert isinstance(error, MCPHttpStatusError) and error.status_code == 403
        assert error.challenge == {"error": "insufficient_scope", "scope": "admin"}

    async def test_a_404_says_the_endpoint_may_not_be_enabled(self, sdk: _Script) -> None:
        error = await self._open_failing(sdk, lambda: _answered(404), MCPError(METHOD_NOT_FOUND, "Not Found"))
        assert isinstance(error, MCPHttpStatusError) and error.status_code == 404
        assert "isn't enabled/available for this account" in str(error)
        assert "https://mcp.example.com/mcp" in str(error)
        assert "api_key" not in str(error)

    async def test_the_servers_own_message_is_kept(self, sdk: _Script) -> None:
        error = await self._open_failing(sdk, lambda: _answered(400), MCPError(-32600, "Bad Request: missing X-Tenant header"))
        assert str(error).endswith(": Bad Request: missing X-Tenant header")

    async def test_a_server_that_couldnt_be_reached_is_not_sent(self, sdk: _Script) -> None:
        async def _unsent() -> None:
            wire.record_unsent(httpx2.ConnectError("All connection attempts failed"))
            await _answered(502)

        error = await self._open_failing(sdk, _unsent, MCPError(INTERNAL_ERROR, "Server returned an error response"))
        assert isinstance(error, MCPRequestNotSentError)
        assert "All connection attempts failed" in str(error)

    async def test_a_blocked_redirect_hop_is_blocked(self, sdk: _Script) -> None:
        blocked = MCPUrlBlockedError("The MCP server redirected to a different host, which is not allowed.")

        async def _blocked() -> None:
            wire.record_unsent(blocked)

        assert await self._open_failing(sdk, _blocked, MCPError(INTERNAL_ERROR, "x")) is blocked

    async def test_the_sdks_own_refusal_of_a_redirect_is_blocked(self, sdk: _Script) -> None:
        error = await self._open_failing(
            sdk, lambda: _answered(307),
            MCPError(-32600, "Redirect to https://elsewhere.example.net/mcp?token=t not followed; use that URL as the endpoint"),
        )
        assert isinstance(error, MCPUrlBlockedError)
        assert "token" not in str(error)

    async def test_an_sse_redirect_is_blocked(self, sdk: _Script) -> None:
        request = httpx2.Request("GET", "https://mcp.example.com/sse")
        redirect = httpx2.HTTPStatusError(
            "302", request=request, response=httpx2.Response(302, headers={"location": "https://x.example.net/"}, request=request),
        )
        error = await self._open_failing(sdk, lambda: _answered(302), ExceptionGroup("tg", [redirect]))
        assert isinstance(error, MCPUrlBlockedError)


class TestTheHandshakeIsRemembered:
    async def test_local_servers_and_sse_use_the_initialize_handshake(self, sdk: _Script) -> None:
        await MCPClientManager(_config()).open()
        await MCPClientManager(_config(transport=MCPTransport.SSE, url="https://mcp.example.com/sse", command=None)).open()
        assert [c.kwargs["mode"] for c in sdk.clients] == ["legacy", "legacy"]

    async def test_an_initialize_only_server_skips_the_probe_next_time(self, sdk: _Script) -> None:
        await MCPClientManager(_http_config()).open()
        await MCPClientManager(_http_config()).open()
        assert [c.kwargs["mode"] for c in sdk.clients] == ["auto", "legacy"]

    async def test_a_modern_server_is_always_probed(self, sdk: _Script) -> None:
        sdk.modern = True
        await MCPClientManager(_http_config()).open()
        await MCPClientManager(_http_config()).open()
        assert [c.kwargs["mode"] for c in sdk.clients] == ["auto", "auto"]

    async def test_the_memory_runs_out(self, sdk: _Script) -> None:
        await MCPClientManager(_http_config()).open()
        later = client_module.time.monotonic() + 3601
        with patch.object(client_module.time, "monotonic", return_value=later):
            await MCPClientManager(_http_config()).open()
        assert [c.kwargs["mode"] for c in sdk.clients] == ["auto", "auto"]

    async def test_a_remembered_handshake_that_fails_is_probed_again_once(self, sdk: _Script) -> None:
        await MCPClientManager(_http_config()).open()

        async def _refuse_initialize(client: _FakeClient) -> None:
            if client.kwargs["mode"] == "legacy":
                raise MCPError(-32022, "Unsupported protocol version")

        sdk.enter = _refuse_initialize
        await MCPClientManager(_http_config()).open()
        assert [c.kwargs["mode"] for c in sdk.clients] == ["auto", "legacy", "auto"]

    async def test_sessions_that_skip_the_probe_dont_stretch_the_memory(self, sdk: _Script) -> None:
        start = client_module.time.monotonic()
        for offset in (0, 3000, 3601):
            with patch.object(client_module.time, "monotonic", return_value=start + offset):
                await MCPClientManager(_http_config()).open()
        assert [c.kwargs["mode"] for c in sdk.clients] == ["auto", "legacy", "auto"]

    @pytest.mark.parametrize("status", [None, 401])
    async def test_a_server_that_is_down_or_refuses_the_credentials_isnt_probed_again(self, sdk: _Script, status: int | None) -> None:
        await MCPClientManager(_http_config()).open()

        async def _fail(_client: _FakeClient) -> None:
            if status is None:
                wire.record_unsent(httpx2.ConnectError("refused"))
                await _answered(502)
            else:
                await _answered(status)
            raise MCPError(INTERNAL_ERROR, "Server returned an error response")

        sdk.enter = _fail
        with pytest.raises(MCPConnectionError):
            await MCPClientManager(_http_config()).open()
        assert len(sdk.clients) == 2


async def _open(sdk: _Script, config: MCPServerConfig | None = None) -> MCPClientManager:
    manager = MCPClientManager(config or _http_config())
    await manager.open()
    return manager


def _answer_with(*steps: Callable[[], Awaitable[None]], error: BaseException) -> Callable[..., Awaitable[Any]]:
    async def _answer(*_args: object) -> Any:  # noqa: ANN401
        for step in steps:
            await step()
        raise error

    return _answer


class TestWhatAFailedCallSays:
    async def test_a_403_keeps_the_servers_challenge(self, sdk: _Script) -> None:
        manager = await _open(sdk)
        sdk.answer = _answer_with(
            lambda: _answered(403, challenge='Bearer error="insufficient_scope", scope="files.write"'),
            error=MCPError(INTERNAL_ERROR, "Server returned an error response"),
        )

        with pytest.raises(MCPHttpStatusError) as caught:
            await manager.call_tool_in_session("upload", {})
        assert caught.value.status_code == 403
        assert caught.value.challenge == {"error": "insufficient_scope", "scope": "files.write"}

    async def test_a_401_is_restored_and_the_session_survives_it(self, sdk: _Script) -> None:
        manager = await _open(sdk)
        sdk.answer = _answer_with(lambda: _answered(401), error=MCPError(INTERNAL_ERROR, "Server returned an error response"))

        with pytest.raises(MCPHttpStatusError) as caught:
            await manager.call_tool_in_session("search", {})
        assert caught.value.status_code == 401
        assert manager.is_open

    async def test_a_404_on_a_session_id_is_an_expired_session(self, sdk: _Script) -> None:
        manager = await _open(sdk)
        sdk.answer = _answer_with(
            lambda: _answered(404, session_id=True), error=MCPError(-32001, "Session not found"),
        )

        with pytest.raises(MCPSessionExpiredError):
            await manager.call_tool_in_session("search", {})
        assert not manager.is_open

    async def test_a_404_without_one_is_just_a_404(self, sdk: _Script) -> None:
        manager = await _open(sdk)
        sdk.answer = _answer_with(lambda: _answered(404), error=MCPError(METHOD_NOT_FOUND, "Not Found"))

        with pytest.raises(MCPHttpStatusError) as caught:
            await manager.call_tool_in_session("search", {})
        assert caught.value.status_code == 404
        assert manager.is_open

    async def test_a_request_that_never_left_keeps_the_session(self, sdk: _Script) -> None:
        manager = await _open(sdk)

        async def _unsent() -> None:
            wire.record_unsent(httpx2.ConnectError("refused"))
            await _answered(502)

        sdk.answer = _answer_with(_unsent, error=MCPError(INTERNAL_ERROR, "Server returned an error response"))
        with pytest.raises(MCPRequestNotSentError):
            await manager.call_tool_in_session("search", {})
        assert manager.is_open

    async def test_a_request_lost_on_the_way_back_may_have_run(self, sdk: _Script) -> None:
        manager = await _open(sdk)

        async def _lost() -> None:
            wire.record_lost(httpx2.RemoteProtocolError("peer closed connection"))

        sdk.answer = _answer_with(_lost, error=MCPError(INTERNAL_ERROR, "Server returned an error response"))
        with pytest.raises(MCPRequestLostError, match="peer closed connection"):
            await manager.call_tool_in_session("create_issue", {})
        assert manager.is_open

    async def test_a_read_timeout_on_the_way_back_is_a_timeout(self, sdk: _Script) -> None:
        manager = await _open(sdk)

        async def _timed_out() -> None:
            wire.record_lost(httpx2.ReadTimeout("slow"))

        sdk.answer = _answer_with(_timed_out, error=MCPError(INTERNAL_ERROR, "Server returned an error response"))
        with pytest.raises(TimeoutError):
            await manager.call_tool_in_session("search", {})

    async def test_the_sdks_own_timeout_is_a_timeout(self, sdk: _Script) -> None:
        manager = await _open(sdk)
        sdk.answer = _answer_with(error=MCPError(REQUEST_TIMEOUT, "Request 'tools/call' timed out"))

        with pytest.raises(TimeoutError):
            await manager.call_tool_in_session("search", {})
        assert manager.is_open

    async def test_a_servers_own_32001_is_not_a_timeout(self, sdk: _Script) -> None:
        manager = await _open(sdk)
        sdk.answer = _answer_with(lambda: _answered(200), error=MCPError(REQUEST_TIMEOUT, "Upstream timed out"))

        with pytest.raises(MCPError, match="Upstream timed out"):
            await manager.call_tool_in_session("search", {})

    async def test_a_closed_connection_ends_the_session(self, sdk: _Script) -> None:
        manager = await _open(sdk, _config())
        sdk.answer = _answer_with(error=MCPError(CONNECTION_CLOSED, "Connection closed"))

        with pytest.raises(MCPConnectionLostError):
            await manager.call_tool_in_session("create_issue", {})
        assert not manager.is_open
        with pytest.raises(MCPRequestNotSentError):
            await manager.call_tool_in_session("search", {})

    async def test_a_response_stream_that_ended_early_is_a_lost_request(self, sdk: _Script) -> None:
        manager = await _open(sdk)
        sdk.answer = _answer_with(lambda: _answered(200), error=MCPError(CONNECTION_CLOSED, "SSE stream ended without a response"))

        with pytest.raises(MCPRequestLostError):
            await manager.call_tool_in_session("create_issue", {})
        assert manager.is_open

    async def test_a_servers_own_32000_is_left_as_it_is(self, sdk: _Script) -> None:
        manager = await _open(sdk)
        sdk.answer = _answer_with(lambda: _answered(200), error=MCPError(CONNECTION_CLOSED, "Jira is down"))

        with pytest.raises(MCPError, match="Jira is down") as caught:
            await manager.call_tool_in_session("search", {})
        assert manager.is_open
        assert caught.value.__cause__ is None

    async def test_any_failed_request_ends_an_sse_session(self, sdk: _Script) -> None:
        """A failed SSE POST ends that transport's writer without a word."""
        manager = await _open(sdk, _config(transport=MCPTransport.SSE, url="https://mcp.example.com/sse", command=None))
        sdk.answer = _answer_with(lambda: _answered(500), error=MCPError(REQUEST_TIMEOUT, "Request 'tools/call' timed out"))

        with pytest.raises(MCPHttpStatusError):
            await manager.call_tool_in_session("search", {})
        assert not manager.is_open

    async def test_an_sse_call_that_times_out_after_a_failed_post_ends_the_session_too(self, sdk: _Script) -> None:
        """Our clock ends the call before the SDK notices the refused POST."""
        manager = await _open(sdk, _config(
            transport=MCPTransport.SSE, url="https://mcp.example.com/sse", command=None, call_timeout_seconds=0.05,
        ))

        async def _refused_then_silent(*_args: object) -> Any:  # noqa: ANN401
            await _answered(500)
            await asyncio.sleep(3600)

        sdk.answer = _refused_then_silent
        with pytest.raises(TimeoutError):
            await manager.call_tool_in_session("search", {})
        assert not manager.is_open

    async def test_a_refused_redirect_is_blocked(self, sdk: _Script) -> None:
        manager = await _open(sdk)
        sdk.answer = _answer_with(error=MCPError(-32600, "Redirect to https://other.example.net/ not followed"))

        with pytest.raises(MCPUrlBlockedError):
            await manager.call_tool_in_session("search", {})

    async def test_a_call_after_the_session_ended_under_it_was_never_sent(self, sdk: _Script) -> None:
        manager = await _open(sdk)
        session = manager._session
        assert session is not None
        await session.runner.stop()

        with pytest.raises(MCPRequestNotSentError):
            await manager.call_tool_in_session("search", {})

    @pytest.mark.parametrize("then", ["unsent", 401, 404])
    async def test_a_failure_after_the_server_took_the_call_means_it_may_have_run(self, sdk: _Script, then: str | int) -> None:
        """A resumable server drops a long call's stream on purpose; the SDK resumes it with a GET
        inside the call. If that GET can't get through, the call itself still went through."""
        manager = await _open(sdk)

        async def _resumption_fails() -> None:
            await _answered(200, session_id=True)
            if then == "unsent":
                wire.record_unsent(httpx2.ConnectError("refused"))
                await _answered(502)
            else:
                await _answered(int(then), session_id=True)

        sdk.answer = _answer_with(
            _resumption_fails, error=MCPError(CONNECTION_CLOSED, "SSE stream ended and reconnection attempts were exhausted"),
        )
        with pytest.raises(MCPRequestLostError) as caught:
            await manager.call_tool_in_session("create_issue", {})
        assert not is_http_unauthorized(caught.value)
        assert manager.is_open

    async def test_reconnection_attempts_running_out_is_a_lost_request(self, sdk: _Script) -> None:
        manager = await _open(sdk)
        sdk.answer = _answer_with(
            lambda: _answered(200), error=MCPError(CONNECTION_CLOSED, "SSE stream ended and reconnection attempts were exhausted"),
        )
        with pytest.raises(MCPRequestLostError):
            await manager.call_tool_in_session("create_issue", {})

    async def test_a_servers_own_error_at_400_keeps_its_message(self, sdk: _Script) -> None:
        """2.x servers send invalid params as HTTP 400 with the JSON-RPC error in the body."""
        manager = await _open(sdk)
        sdk.answer = _answer_with(lambda: _answered(400), error=MCPError(-32602, "Invalid params: 'q' is required"))

        with pytest.raises(MCPError, match="'q' is required") as caught:
            await manager.call_tool_in_session("search", {})
        assert not isinstance(caught.value, MCPHttpStatusError)
        assert manager.is_open

    async def test_method_not_found_on_a_session_is_not_an_expired_session(self, sdk: _Script) -> None:
        manager = await _open(sdk)
        sdk.answer = _answer_with(
            lambda: _answered(404, session_id=True), error=MCPError(METHOD_NOT_FOUND, "Method not found: tools/call"),
        )
        with pytest.raises(MCPError, match="Method not found"):
            await manager.call_tool_in_session("search", {})
        assert manager.is_open

    async def test_a_401_with_a_body_is_still_a_401(self, sdk: _Script) -> None:
        manager = await _open(sdk)
        sdk.answer = _answer_with(lambda: _answered(401), error=MCPError(-32001, "Unauthorized: token expired"))

        with pytest.raises(MCPHttpStatusError) as caught:
            await manager.call_tool_in_session("search", {})
        assert caught.value.status_code == 401

    async def test_a_listing_refused_on_a_later_page_is_still_a_401(self, sdk: _Script) -> None:
        """Listing changes nothing, so a failure after an earlier page was taken stays retryable."""

        async def _page(cursor: str | None) -> Any:  # noqa: ANN401
            if cursor is None:
                await _answered(200)
                return SimpleNamespace(tools=["t1"], next_cursor="2")
            await _answered(401)
            raise MCPError(INTERNAL_ERROR, "Server returned an error response")

        sdk.pages = [_page]
        manager = await _open(sdk)
        with pytest.raises(MCPHttpStatusError) as caught:
            await manager.list_tools_in_session()
        assert caught.value.status_code == 401

    async def test_another_runtime_error_is_left_as_it_is(self, sdk: _Script) -> None:
        manager = await _open(sdk)
        sdk.answer = _answer_with(error=RuntimeError("input required rounds exceeded"))

        with pytest.raises(RuntimeError, match="rounds exceeded"):
            await manager.call_tool_in_session("search", {})
        assert manager.is_open

    async def test_each_call_reads_only_its_own_requests(self, sdk: _Script) -> None:
        """Another call's 401 is that call's: the SDK sends each request in its caller's context."""
        manager = await _open(sdk)
        first_answered = asyncio.Event()

        async def _answer(_client: _FakeClient, name: str, _arguments: dict[str, Any]) -> Any:  # noqa: ANN401
            if name == "refused":
                await _answered(401)
                first_answered.set()
                raise MCPError(INTERNAL_ERROR, "Server returned an error response")
            await first_answered.wait()
            await _answered(200)
            raise MCPError(-32602, "bad arguments")

        sdk.answer = _answer
        refused, other = await asyncio.gather(
            manager.call_tool_in_session("refused", {}), manager.call_tool_in_session("other", {}), return_exceptions=True,
        )
        assert isinstance(refused, MCPHttpStatusError)
        assert isinstance(other, MCPError) and not isinstance(other, MCPHttpStatusError)


class TestCallTimeouts:
    async def test_the_sdks_own_timeout_only_backs_ours_up(self, sdk: _Script, monkeypatch: pytest.MonkeyPatch) -> None:
        # Past the limit for one call, so ours, which progress restarts, always ends it first.
        monkeypatch.delenv(client_module.MAX_CALL_SECONDS_ENV, raising=False)
        manager = await _open(sdk, _config(call_timeout_seconds=120))
        await manager.call_tool_in_session("search", {})
        assert sdk.calls == [("search", {}, 1800 + client_module._OUTER_TIMEOUT_MARGIN_SECONDS)]

    async def test_our_own_clock_ends_a_call_the_sdk_doesnt(self, sdk: _Script) -> None:
        async def _hang(*_args: object) -> Any:  # noqa: ANN401
            await asyncio.sleep(3600)

        sdk.answer = _hang
        manager = await _open(sdk, _config(call_timeout_seconds=0.05))
        with patch.object(client_module, "_OUTER_TIMEOUT_MARGIN_SECONDS", 0.05):
            with pytest.raises(TimeoutError, match="didn't finish"):
                await manager.call_tool_in_session("search", {})

    @pytest.mark.parametrize(("env", "call_timeout", "limit"), [
        (None, 60, 1800.0), ("600", 60, 600.0), ("30", 60, 60.0), ("soon", 60, 1800.0), ("0", 0.5, 1.0),
    ])
    def test_the_limit_for_one_call(
        self, monkeypatch: pytest.MonkeyPatch, env: str | None, call_timeout: float, limit: float,
    ) -> None:
        if env is None:
            monkeypatch.delenv(client_module.MAX_CALL_SECONDS_ENV, raising=False)
        else:
            monkeypatch.setenv(client_module.MAX_CALL_SECONDS_ENV, env)
        assert client_module.max_call_seconds(_config(call_timeout_seconds=call_timeout)) == limit

    async def test_a_listing_that_hangs_times_out(self, sdk: _Script) -> None:
        async def _hang(_cursor: str | None) -> Any:  # noqa: ANN401
            await asyncio.sleep(3600)

        sdk.pages = [_hang]
        manager = await _open(sdk, _config())
        with patch.object(client_module, "LIST_TOOLS_TIMEOUT_SECONDS", 0.05):
            with pytest.raises(TimeoutError, match="didn't list its tools"):
                await manager.list_tools_in_session()


class TestProgressKeepsACallAlive:
    """The call timeout bounds each wait for the answer or for progress (MCP spec: progress may
    restart it), up to the limit for one call."""

    @staticmethod
    def _reporting(sdk: _Script, every: float, *, then_answer_after: float | None = None, message: str = "") -> None:
        async def _answer(_client: _FakeClient, name: str, _arguments: dict[str, Any]) -> Any:  # noqa: ANN401
            report = sdk.progress_callbacks[-1]
            loop = asyncio.get_running_loop()
            started = loop.time()
            step = 0
            while then_answer_after is None or loop.time() - started < then_answer_after:
                step += 1
                await report(float(step), 10.0, message or None)
                await asyncio.sleep(every)
            return {"result": f"{name} ok"}

        sdk.answer = _answer

    async def test_progress_carries_a_call_past_its_timeout(self, sdk: _Script) -> None:
        self._reporting(sdk, every=0.05, then_answer_after=0.6)
        manager = await _open(sdk, _config(call_timeout_seconds=0.25))

        assert await manager.call_tool_in_session("export", {}) == {"result": "export ok"}

    async def test_without_progress_it_still_ends_at_the_call_timeout(self, sdk: _Script) -> None:
        async def _hang(*_args: object) -> Any:  # noqa: ANN401
            await asyncio.sleep(3600)

        sdk.answer = _hang
        manager = await _open(sdk, _config(call_timeout_seconds=0.1))
        started = asyncio.get_running_loop().time()

        with pytest.raises(TimeoutError, match="didn't finish within") as caught:
            await manager.call_tool_in_session("export", {})
        assert not isinstance(caught.value, client_module.MCPCallTooLongError)
        assert asyncio.get_running_loop().time() - started < 2

    async def test_the_limit_for_one_call_holds_against_endless_progress(
        self, sdk: _Script, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(client_module.MAX_CALL_SECONDS_ENV, raising=False)
        monkeypatch.setattr(client_module, "DEFAULT_MAX_CALL_SECONDS", 0.4)
        self._reporting(sdk, every=0.05)
        manager = await _open(sdk, _config(call_timeout_seconds=0.15))
        started = asyncio.get_running_loop().time()

        with pytest.raises(client_module.MCPCallTooLongError, match="the limit for one call") as caught:
            await manager.call_tool_in_session("export", {})
        assert caught.value.limit_seconds == 0.4
        assert 0.35 < asyncio.get_running_loop().time() - started < 2

    async def test_whoever_asked_hears_each_report(self, sdk: _Script) -> None:
        self._reporting(sdk, every=0.01, then_answer_after=0.03, message="page 1")
        manager = await _open(sdk, _config())
        heard: list[tuple[float, float | None, str | None]] = []

        async def _hear(progress: float, total: float | None, message: str | None) -> None:
            heard.append((progress, total, message))

        await manager.call_tool_in_session("export", {}, on_progress=_hear)

        assert heard and heard[0] == (1.0, 10.0, "page 1")

    async def test_a_listener_that_fails_doesnt_fail_the_call(self, sdk: _Script) -> None:
        self._reporting(sdk, every=0.01, then_answer_after=0.03)
        manager = await _open(sdk, _config())

        async def _broken(*_args: object) -> None:
            raise RuntimeError("the chat went away")

        assert await manager.call_tool_in_session("export", {}, on_progress=_broken) == {"result": "export ok"}

    async def test_progress_after_the_call_ended_is_ignored(self, sdk: _Script) -> None:
        # The SDK runs each report in a task of its own, so one can arrive after the answer.
        manager = await _open(sdk, _config())
        heard: list[float] = []

        async def _hear(progress: float, *_rest: object) -> None:
            heard.append(progress)

        await manager.call_tool_in_session("export", {}, on_progress=_hear)
        await sdk.progress_callbacks[-1](5.0, None, None)

        assert heard == []

    async def test_progress_after_the_call_timed_out_is_ignored(self, sdk: _Script) -> None:
        async def _hang(*_args: object) -> Any:  # noqa: ANN401
            await asyncio.sleep(3600)

        sdk.answer = _hang
        manager = await _open(sdk, _config(call_timeout_seconds=0.05))
        with pytest.raises(TimeoutError):
            await manager.call_tool_in_session("export", {})

        await sdk.progress_callbacks[-1](5.0, None, None)


class TestTheOpenSession:
    async def test_calls_need_an_open_session(self) -> None:
        with pytest.raises(MCPConnectionError, match="is not open"):
            await MCPClientManager(_config()).call_tool_in_session("search", {})

    async def test_is_open_follows_the_session(self, sdk: _Script) -> None:
        manager = MCPClientManager(_config())
        assert manager.is_open is False
        await manager.open()
        assert manager.is_open is True
        await manager.aclose()
        assert manager.is_open is False

    async def test_a_connection_that_ended_between_calls_leaves_the_session_unusable(self, sdk: _Script) -> None:
        """A local server that exited, an SSE stream that ended: the next call can't be sent, so it
        is reported as never sent and the turn reconnects."""
        manager = await _open(sdk, _config())
        sdk.clients[0].session._dispatcher._closed = True

        assert manager.is_open is False
        with pytest.raises(MCPRequestNotSentError):
            await manager.call_tool_in_session("search", {})
        assert sdk.calls == []

    async def test_new_headers_go_to_the_open_sessions_clients_and_later_ones(self, sdk: _Script) -> None:
        manager = await _open(sdk)
        manager.update_headers({"Authorization": "Bearer fresh"})

        assert manager.headers["Authorization"] == "Bearer fresh"
        assert sdk.transports[0].http_clients[0].headers == {"Authorization": "Bearer fresh"}  # type: ignore[attr-defined]

    async def test_a_failed_calls_stderr_is_logged_but_kept_out_of_the_error(
        self, sdk: _Script, caplog: pytest.LogCaptureFixture,
    ) -> None:
        manager = await _open(sdk, _config())
        sdk.stderr_paths[-1].write_text("exa-mcp-server crashed: SECRET_TOKEN=abc\n")  # type: ignore[attr-defined]
        sdk.answer = _answer_with(error=MCPError(-32603, "boom"))

        with pytest.raises(MCPError) as caught:
            await manager.call_tool_in_session("search", {})

        assert "SECRET_TOKEN" not in str(caught.value)
        assert "SECRET_TOKEN=abc" in caplog.text
        await manager.aclose()

    async def test_aclose_closes_the_session_and_removes_the_stderr_file(self, sdk: _Script) -> None:
        manager = await _open(sdk, _config())
        stderr_path = sdk.stderr_paths[-1]  # type: ignore[attr-defined]

        await manager.aclose()

        assert sdk.clients[0].exited.is_set()
        assert stderr_path is not None and not stderr_path.exists()
        await manager.aclose()  # a no-op the second time

    async def test_aclose_is_a_noop_when_never_opened(self) -> None:
        await MCPClientManager(_config()).aclose()

    async def test_aclose_swallows_close_errors(self, sdk: _Script) -> None:
        async def _fail(_client: _FakeClient) -> None:
            raise RuntimeError("already dead")

        sdk.exit = _fail
        manager = await _open(sdk, _config())
        await manager.aclose()
        assert manager.is_open is False

    async def test_a_close_that_hangs_is_cut_short(self, sdk: _Script) -> None:
        async def _hang(_client: _FakeClient) -> None:
            await asyncio.sleep(3600)

        sdk.exit = _hang
        manager = await _open(sdk, _config())
        with patch.object(client_module, "_CLOSE_TIMEOUT_SECONDS", 0.05):
            await asyncio.wait_for(manager.aclose(), 5)


class TestDescribeError:
    @pytest.mark.parametrize("lib", [httpx, httpx2])
    def test_an_http_status_error_hides_query_secrets(self, lib: Any) -> None:  # noqa: ANN401
        from app.agents.mcp.client import _describe_error

        request = lib.Request("POST", "https://mcp.example.com/mcp?api_key=secret")
        error = lib.HTTPStatusError("x", request=request, response=lib.Response(401, request=request))

        assert _describe_error(error) == "HTTP 401 Unauthorized from https://mcp.example.com/mcp"

    def test_urls_in_any_text_are_redacted(self) -> None:
        from app.agents.mcp.client import _describe_error

        assert _describe_error(RuntimeError("GET https://h.example.com/x?token=t failed")) == "GET https://h.example.com/x failed"

    def test_exception_group_uses_its_first_error(self) -> None:
        from app.agents.mcp.client import _describe_error

        assert _describe_error(ExceptionGroup("tasks", [ValueError("real reason")])) == "real reason"

    def test_empty_message_falls_back_to_the_type_name(self) -> None:
        from app.agents.mcp.client import _describe_error

        assert _describe_error(TimeoutError()) == "TimeoutError"


class TestConnectionErrorDetail:
    def test_detail_without_stderr_is_the_message(self) -> None:
        assert MCPConnectionError("nope").detail == "nope"

    def test_str_never_contains_stderr(self) -> None:
        error = MCPConnectionError("nope", stderr_tail="PASSWORD=x")
        assert str(error) == "nope"
        assert error.detail == "nope | subprocess stderr: PASSWORD=x"


class TestWireFailures:
    @pytest.mark.parametrize("record,kind", [
        (wire.WireRecord(unsent=httpx2.ConnectError("x"), statuses=[502]), MCPRequestNotSentError),
        (wire.WireRecord(lost=httpx2.ReadError("x"), statuses=[502]), MCPRequestLostError),
        (wire.WireRecord(statuses=[404], sent_session_id=True), MCPSessionExpiredError),
        (wire.WireRecord(statuses=[404]), MCPHttpStatusError),
        (wire.WireRecord(statuses=[200, 503]), MCPHttpStatusError),
    ])
    def test_what_the_requests_say(self, record: wire.WireRecord, kind: type) -> None:
        from app.agents.mcp.client import _wire_failure

        assert type(_wire_failure(record, what="the call")) is kind

    @pytest.mark.parametrize("record", [wire.WireRecord(), wire.WireRecord(statuses=[200]), wire.WireRecord(statuses=[400, 200])])
    def test_nothing_when_they_say_nothing(self, record: wire.WireRecord) -> None:
        from app.agents.mcp.client import _wire_failure

        assert _wire_failure(record, what="the call") is None


class TestResultsThatMissTheirSchemaAreKept:
    """The SDK's own check runs after the tool did; a mismatch must not throw the result away."""

    def _session(self) -> Any:  # noqa: ANN401
        from mcp.client.session import ClientSession

        session = ClientSession(dispatcher=MagicMock())
        session._tool_output_schemas = {
            "search": {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
        }
        return session

    async def test_the_installed_sdk_still_rejects_a_mismatch_by_default(self) -> None:
        from mcp.types import CallToolResult

        with pytest.raises(RuntimeError):
            await self._session().validate_tool_result("search", CallToolResult(content=[], structured_content={"b": 1}))

    async def test_a_mismatch_is_logged_and_the_result_kept(self, caplog: pytest.LogCaptureFixture) -> None:
        from mcp.types import CallToolResult

        from app.agents.mcp.client import _keep_results_that_miss_their_schema

        session = self._session()
        _keep_results_that_miss_their_schema(SimpleNamespace(session=session))  # type: ignore[arg-type]

        await session.validate_tool_result("search", CallToolResult(content=[], structured_content={"b": 1}))

        assert "declared schema" in caplog.text

    async def test_a_failing_validation_listing_does_not_fail_the_call(self) -> None:
        from mcp.types import CallToolResult

        from app.agents.mcp.client import _keep_results_that_miss_their_schema

        session = self._session()
        # Not listed yet: the SDK lists tools first.
        session._tool_output_schemas = {}
        session.list_tools = AsyncMock(side_effect=MCPError(CONNECTION_CLOSED, "Connection closed"))
        _keep_results_that_miss_their_schema(SimpleNamespace(session=session))  # type: ignore[arg-type]

        await session.validate_tool_result("search", CallToolResult(content=[], structured_content={"b": 1}))

    async def test_open_installs_it(self, sdk: _Script) -> None:
        manager = await _open(sdk, _config())
        assert manager._session is not None
        assert manager._session.client.session.validate_tool_result.__name__ == "_validate_leniently"


class TestHttpReadTimeout:
    """The read timeout outlasts the call timeout, so the call's own limit is what ends it."""

    @pytest.mark.parametrize(("call_timeout", "read_timeout"), [(None, 300.0), (60, 300.0), (280, 310.0), (600, 630.0)])
    def test_never_shorter_than_the_call_or_the_sdk_default(self, call_timeout: float | None, read_timeout: float) -> None:
        assert http_read_timeout(_http_config(call_timeout_seconds=call_timeout)) == read_timeout

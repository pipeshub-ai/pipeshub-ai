"""Unit tests for app.agents.mcp.client."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.mcp.client import (
    MCPClientManager,
    MCPConnectionError,
    build_transport,
)
from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPTransport


def _config(**overrides) -> MCPServerConfig:
    defaults = dict(
        id="inst-1",
        org_id="org-1",
        created_by="user-1",
        name="Server",
        transport=MCPTransport.STDIO,
        auth_mode=MCPAuthMode.NONE,
        created_at=0,
        updated_at=0,
    )
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


class TestSanitizeUrlForDiagnostics:
    def test_strips_userinfo_query_and_fragment(self) -> None:
        from app.agents.mcp.client import _sanitize_url_for_diagnostics

        raw = "https://user:pass@mcp.example.com:8443/v1/mcp?access_token=secret#frag"
        assert _sanitize_url_for_diagnostics(raw) == "https://mcp.example.com:8443/v1/mcp"

    def test_preserves_clean_url(self) -> None:
        from app.agents.mcp.client import _sanitize_url_for_diagnostics

        assert (
            _sanitize_url_for_diagnostics("https://gitlab.com/api/v4/mcp")
            == "https://gitlab.com/api/v4/mcp"
        )

    def test_ipv6_host_keeps_brackets_drops_userinfo(self) -> None:
        from app.agents.mcp.client import _sanitize_url_for_diagnostics

        raw = "https://token@[2001:db8::1]/mcp?key=abc"
        assert _sanitize_url_for_diagnostics(raw) == "https://[2001:db8::1]/mcp"


class TestLastHttpResponseRedaction:
    @pytest.mark.asyncio
    async def test_on_response_redacts_sensitive_url_components(self) -> None:
        """Regression: query tokens / userinfo must never be stored for diagnostics —
        `_annotate_with_last_http_response` later embeds `recorder.url` in logged
        `MCPConnectionError` messages."""
        from app.agents.mcp.client import _LastHttpResponse, _annotate_with_last_http_response

        recorder = _LastHttpResponse()
        request = MagicMock()
        request.url = "https://user:pass@mcp.example.com/v1/mcp?access_token=secret#frag"
        response = MagicMock()
        response.status_code = 404
        response.request = request

        await recorder._on_response(response)

        assert recorder.url == "https://mcp.example.com/v1/mcp"
        message = _annotate_with_last_http_response("Session terminated", recorder)
        assert "https://mcp.example.com/v1/mcp" in message
        assert "secret" not in message
        assert "user:pass" not in message
        assert "access_token" not in message


class TestBuildTransport:
    def test_stdio_builds_stdio_transport(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx", args=["-y", "server"])
        with patch("app.agents.mcp.client.StdioTransport") as mock_cls:
            build_transport(config, env={"KEY": "value"})
            mock_cls.assert_called_once_with(
                command="npx", args=["-y", "server"], env={"KEY": "value"}, log_file=None, keep_alive=None,
            )

    def test_stdio_forwards_stderr_log_file_and_keep_alive(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx", args=["-y", "server"])
        with patch("app.agents.mcp.client.StdioTransport") as mock_cls:
            build_transport(config, stderr_log_file=Path("/tmp/mcp-stderr.log"), keep_alive=False)
            mock_cls.assert_called_once_with(
                command="npx", args=["-y", "server"], env={}, log_file=Path("/tmp/mcp-stderr.log"), keep_alive=False,
            )

    def test_stdio_without_command_raises(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command=None)
        with pytest.raises(MCPConnectionError, match="no command"):
            build_transport(config)

    def test_sse_builds_sse_transport(self) -> None:
        config = _config(transport=MCPTransport.SSE, url="https://example.com/sse")
        with patch("app.agents.mcp.client.SSETransport") as mock_cls:
            build_transport(config, headers={"Authorization": "Bearer x"})
            mock_cls.assert_called_once_with(
                url="https://example.com/sse", headers={"Authorization": "Bearer x"}, httpx_client_factory=None,
            )

    def test_sse_without_url_raises(self) -> None:
        config = _config(transport=MCPTransport.SSE, url=None)
        with pytest.raises(MCPConnectionError, match="no url"):
            build_transport(config)

    def test_streamable_http_builds_transport(self) -> None:
        config = _config(transport=MCPTransport.STREAMABLE_HTTP, url="https://example.com/mcp")
        with patch("app.agents.mcp.client.StreamableHttpTransport") as mock_cls:
            build_transport(config)
            mock_cls.assert_called_once_with(url="https://example.com/mcp", headers={}, httpx_client_factory=None)

    def test_streamable_http_wires_up_response_recorder_factory(self) -> None:
        from app.agents.mcp.client import _LastHttpResponse

        config = _config(transport=MCPTransport.STREAMABLE_HTTP, url="https://example.com/mcp")
        recorder = _LastHttpResponse()
        with patch("app.agents.mcp.client.StreamableHttpTransport") as mock_cls:
            build_transport(config, http_response_recorder=recorder)
            mock_cls.assert_called_once_with(
                url="https://example.com/mcp", headers={}, httpx_client_factory=recorder.httpx_client_factory,
            )

    def test_streamable_http_without_url_raises(self) -> None:
        config = _config(transport=MCPTransport.STREAMABLE_HTTP, url=None)
        with pytest.raises(MCPConnectionError, match="no url"):
            build_transport(config)

    def test_unsupported_transport_raises(self) -> None:
        config = MagicMock()
        config.id = "inst-1"
        config.transport = "carrier_pigeon"
        with pytest.raises(MCPConnectionError, match="Unsupported MCP transport"):
            build_transport(config)


class TestAnnotateWithLastHttpResponse:
    """`_annotate_with_last_http_response` turns the MCP SDK's opaque
    `"Session terminated"` error into an actionable message when we captured the
    real HTTP status behind it (see `_LastHttpResponse`'s docstring)."""

    def test_no_recorder_returns_message_unchanged(self) -> None:
        from app.agents.mcp.client import _annotate_with_last_http_response

        assert _annotate_with_last_http_response("Session terminated", None) == "Session terminated"

    def test_recorder_without_captured_status_returns_message_unchanged(self) -> None:
        from app.agents.mcp.client import _LastHttpResponse, _annotate_with_last_http_response

        assert _annotate_with_last_http_response("Session terminated", _LastHttpResponse()) == "Session terminated"

    def test_unrelated_error_message_is_left_unchanged(self) -> None:
        from app.agents.mcp.client import _LastHttpResponse, _annotate_with_last_http_response

        recorder = _LastHttpResponse()
        recorder.status_code = 404
        recorder.url = "https://gitlab.com/api/v4/mcp"
        assert _annotate_with_last_http_response("boom", recorder) == "boom"

    def test_404_produces_actionable_hint(self) -> None:
        from app.agents.mcp.client import _LastHttpResponse, _annotate_with_last_http_response

        recorder = _LastHttpResponse()
        recorder.status_code = 404
        recorder.url = "https://gitlab.com/api/v4/mcp"
        message = _annotate_with_last_http_response("Session terminated", recorder)
        assert "404 Not Found" in message
        assert "https://gitlab.com/api/v4/mcp" in message
        assert "isn't enabled/available for this account" in message

    def test_other_status_code_appends_generic_context(self) -> None:
        from app.agents.mcp.client import _LastHttpResponse, _annotate_with_last_http_response

        recorder = _LastHttpResponse()
        recorder.status_code = 503
        recorder.url = "https://example.com/mcp"
        message = _annotate_with_last_http_response("Session terminated", recorder)
        assert "503" in message
        assert "https://example.com/mcp" in message


class TestStderrHelpers:
    def test_new_stderr_capture_path_returns_none_for_non_stdio(self) -> None:
        from app.agents.mcp.client import _new_stderr_capture_path

        assert _new_stderr_capture_path(_config(transport=MCPTransport.SSE, url="https://example.com/sse")) is None

    def test_cleanup_stderr_capture_path_noop_for_none(self) -> None:
        from app.agents.mcp.client import _cleanup_stderr_capture_path

        _cleanup_stderr_capture_path(None)  # must not raise

    def test_read_stderr_tail_returns_empty_for_none(self) -> None:
        from app.agents.mcp.client import _read_stderr_tail

        assert _read_stderr_tail(None) == ""

    def test_read_stderr_tail_returns_empty_on_oserror(self, tmp_path: Path) -> None:
        from app.agents.mcp.client import _read_stderr_tail

        missing = tmp_path / "does-not-exist.log"
        assert _read_stderr_tail(missing) == ""


class TestMCPClientManagerConnect:
    @pytest.mark.asyncio
    async def test_connect_yields_client_and_closes(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            async with manager.connect() as client:
                assert client is mock_client
            mock_client.__aenter__.assert_awaited_once()
            mock_client.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connect_wraps_unexpected_errors(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(side_effect=RuntimeError("boom"))
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            with pytest.raises(MCPConnectionError, match="boom"):
                async with manager.connect():
                    pass

    @pytest.mark.asyncio
    async def test_connect_builds_transport_with_keep_alive_false(self) -> None:
        """One-shot `connect()` must not leave the STDIO subprocess running — see
        `build_transport`'s docstring for why `keep_alive=True` (fastmcp's default)
        would orphan it here."""
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()) as mock_build, \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            async with manager.connect():
                pass
            assert mock_build.call_args.kwargs["keep_alive"] is False
            assert mock_build.call_args.kwargs["stderr_log_file"] is not None

    @pytest.mark.asyncio
    async def test_connect_annotates_error_with_captured_stderr(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aexit__ = AsyncMock(return_value=False)

        captured_path: dict[str, Path] = {}

        def _fake_build_transport(*_args, stderr_log_file: Path, **_kwargs) -> MagicMock:
            captured_path["path"] = stderr_log_file
            return MagicMock()

        async def _write_stderr_then_raise() -> None:
            captured_path["path"].write_text("exa-mcp-server: missing EXA_API_KEY\n")
            raise RuntimeError("boom")

        with patch("app.agents.mcp.client.build_transport", side_effect=_fake_build_transport), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            mock_client.__aenter__ = AsyncMock(side_effect=_write_stderr_then_raise)
            manager = MCPClientManager(config)
            with pytest.raises(MCPConnectionError, match="missing EXA_API_KEY"):
                async with manager.connect():
                    pass

    @pytest.mark.asyncio
    async def test_connect_enriches_session_terminated_with_captured_404(self) -> None:
        """A streamable-http/SSE instance that 404s on the initial handshake gets
        the SDK's generic "Session terminated" replaced with an actionable hint —
        see `_LastHttpResponse`'s docstring for why the raw status is otherwise lost."""
        config = _config(transport=MCPTransport.STREAMABLE_HTTP, url="https://gitlab.com/api/v4/mcp")
        mock_client = MagicMock()
        mock_client.__aexit__ = AsyncMock(return_value=False)

        captured_recorder = {}

        def _fake_build_transport(*_args, http_response_recorder, **_kwargs) -> MagicMock:
            captured_recorder["recorder"] = http_response_recorder
            return MagicMock()

        async def _fail_with_session_terminated() -> None:
            captured_recorder["recorder"].status_code = 404
            captured_recorder["recorder"].url = "https://gitlab.com/api/v4/mcp"
            raise RuntimeError("Session terminated")

        mock_client.__aenter__ = AsyncMock(side_effect=_fail_with_session_terminated)

        with patch("app.agents.mcp.client.build_transport", side_effect=_fake_build_transport), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            with pytest.raises(MCPConnectionError, match="isn't enabled/available for this account"):
                async with manager.connect():
                    pass

    @pytest.mark.asyncio
    async def test_connect_propagates_mcp_connection_error_unwrapped(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command=None)
        manager = MCPClientManager(config)
        with pytest.raises(MCPConnectionError, match="no command"):
            async with manager.connect():
                pass

    @pytest.mark.asyncio
    async def test_connect_reraises_mcp_connection_error_from_client_session(self) -> None:
        """MCPConnectionError raised inside the client session must not be re-wrapped."""
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(side_effect=MCPConnectionError("already typed"))
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            with pytest.raises(MCPConnectionError, match="already typed") as exc_info:
                async with manager.connect():
                    pass
            assert exc_info.value.args == ("already typed",)
            assert exc_info.value.__cause__ is None

    @pytest.mark.asyncio
    async def test_list_tools_delegates_to_client(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.list_tools = AsyncMock(return_value=["tool1", "tool2"])

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            tools = await manager.list_tools()
            assert tools == ["tool1", "tool2"]

    @pytest.mark.asyncio
    async def test_call_tool_delegates_to_client(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.call_tool = AsyncMock(return_value={"result": "ok"})

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            result = await manager.call_tool("search", {"q": "test"})
            assert result == {"result": "ok"}
            mock_client.call_tool.assert_awaited_once_with("search", {"q": "test"})

    @pytest.mark.asyncio
    async def test_call_tool_times_out_on_unresponsive_server(self) -> None:
        """A hung remote MCP server must not block the caller forever — see
        `DEFAULT_CALL_TIMEOUT_SECONDS`'s docstring."""
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def _hangs_forever(*_args, **_kwargs) -> None:
            await asyncio.sleep(3600)

        mock_client.call_tool = AsyncMock(side_effect=_hangs_forever)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client), \
             patch("app.agents.mcp.client.DEFAULT_CALL_TIMEOUT_SECONDS", 0.01):
            manager = MCPClientManager(config)
            # connect()'s generic except wraps the TimeoutError as MCPConnectionError.
            with pytest.raises(MCPConnectionError):
                await manager.call_tool("search", {"q": "test"})


class TestMCPClientManagerSession:
    """The long-lived `open()` / `call_tool_in_session()` / `aclose()` trio used by
    the agent-loop runtime (`MCPSessionManager`) to reuse one connection across every
    tool call an instance receives within a chat turn."""

    @pytest.mark.asyncio
    async def test_open_builds_transport_with_keep_alive_true(self) -> None:
        """Unlike one-shot `connect()`, a session must survive between calls."""
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()) as mock_build, \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            await manager.open()
            assert mock_build.call_args.kwargs["keep_alive"] is True
            assert mock_build.call_args.kwargs["stderr_log_file"] is not None

    @pytest.mark.asyncio
    async def test_open_is_idempotent(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()) as mock_build, \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            first = await manager.open()
            second = await manager.open()
            assert first is second is mock_client
            mock_build.assert_called_once()

    @pytest.mark.asyncio
    async def test_open_annotates_error_with_captured_stderr_and_cleans_up(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.close = AsyncMock()

        captured_path: dict[str, Path] = {}

        def _fake_build_transport(*_args, stderr_log_file: Path, **_kwargs) -> MagicMock:
            captured_path["path"] = stderr_log_file
            return MagicMock()

        async def _write_stderr_then_raise() -> None:
            captured_path["path"].write_text("exa-mcp-server: missing EXA_API_KEY\n")
            raise RuntimeError("boom")

        with patch("app.agents.mcp.client.build_transport", side_effect=_fake_build_transport), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            mock_client.__aenter__ = AsyncMock(side_effect=_write_stderr_then_raise)
            manager = MCPClientManager(config)
            with pytest.raises(MCPConnectionError, match="missing EXA_API_KEY"):
                await manager.open()

        assert not captured_path["path"].exists()
        # Failed `__aenter__` with keep_alive=True must force-close so a half-started
        # STDIO transport can't leave an orphaned subprocess behind.
        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_open_cleans_stderr_when_build_transport_fails(self) -> None:
        """`stderr_path` is allocated before `build_transport` — a raise there must
        still unlink the temp capture file."""
        config = _config(transport=MCPTransport.STDIO, command="npx")
        captured_path: dict[str, Path] = {}

        def _fail_build(*_args, stderr_log_file: Path, **_kwargs) -> MagicMock:
            captured_path["path"] = stderr_log_file
            raise MCPConnectionError("no command configured")

        with patch("app.agents.mcp.client.build_transport", side_effect=_fail_build):
            manager = MCPClientManager(config)
            with pytest.raises(MCPConnectionError, match="no command"):
                await manager.open()

        assert "path" in captured_path
        assert not captured_path["path"].exists()
        assert manager._session_client is None

    @pytest.mark.asyncio
    async def test_open_closes_client_on_aenter_timeout(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.close = AsyncMock()

        async def _hangs_forever() -> None:
            await asyncio.sleep(3600)

        mock_client.__aenter__ = AsyncMock(side_effect=_hangs_forever)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client), \
             patch("app.agents.mcp.client.DEFAULT_CONNECT_TIMEOUT_SECONDS", 0.01):
            manager = MCPClientManager(config)
            with pytest.raises(MCPConnectionError):
                await manager.open()

        mock_client.close.assert_awaited_once()
        assert manager._session_client is None

    @pytest.mark.asyncio
    async def test_call_tool_in_session_raises_when_not_open(self) -> None:
        manager = MCPClientManager(_config())
        with pytest.raises(MCPConnectionError, match="is not open"):
            await manager.call_tool_in_session("search", {"q": "test"})

    @pytest.mark.asyncio
    async def test_open_times_out_on_unresponsive_server(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()

        async def _hangs_forever() -> None:
            await asyncio.sleep(3600)

        mock_client.__aenter__ = AsyncMock(side_effect=_hangs_forever)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client), \
             patch("app.agents.mcp.client.DEFAULT_CONNECT_TIMEOUT_SECONDS", 0.01):
            manager = MCPClientManager(config)
            with pytest.raises(MCPConnectionError):
                await manager.open()

    @pytest.mark.asyncio
    async def test_call_tool_in_session_delegates_to_open_client(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.call_tool = AsyncMock(return_value={"result": "ok"})

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            await manager.open()
            result = await manager.call_tool_in_session("search", {"q": "test"})
            assert result == {"result": "ok"}
            mock_client.call_tool.assert_awaited_once_with("search", {"q": "test"})

    @pytest.mark.asyncio
    async def test_call_tool_in_session_times_out_on_unresponsive_server(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)

        async def _hangs_forever(*_args, **_kwargs) -> None:
            await asyncio.sleep(3600)

        mock_client.call_tool = AsyncMock(side_effect=_hangs_forever)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client), \
             patch("app.agents.mcp.client.DEFAULT_CALL_TIMEOUT_SECONDS", 0.01):
            manager = MCPClientManager(config)
            await manager.open()
            with pytest.raises((asyncio.TimeoutError, TimeoutError)):
                await manager.call_tool_in_session("search", {"q": "test"})

    @pytest.mark.asyncio
    async def test_call_tool_in_session_annotates_error_with_captured_stderr(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.call_tool = AsyncMock(side_effect=RuntimeError("Connection closed"))

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            await manager.open()
            manager._session_stderr_path.write_text("exa-mcp-server crashed: rate limited\n")
            with pytest.raises(RuntimeError, match="rate limited"):
                await manager.call_tool_in_session("search", {"q": "test"})
            await manager.aclose()

    @pytest.mark.asyncio
    async def test_call_tool_in_session_preserves_original_when_exception_type_rejects_message(self) -> None:
        """Exception subtypes that don't accept a single message arg must still propagate."""

        class TwoArgError(Exception):
            def __init__(self, code: int, detail: str) -> None:
                super().__init__(detail)
                self.code = code
                self.detail = detail

        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.call_tool = AsyncMock(side_effect=TwoArgError(42, "Connection closed"))

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            await manager.open()
            manager._session_stderr_path.write_text("subprocess died\n")
            with pytest.raises(TwoArgError) as exc_info:
                await manager.call_tool_in_session("search", {"q": "test"})
            assert exc_info.value.code == 42
            assert exc_info.value.detail == "Connection closed"
            await manager.aclose()

    @pytest.mark.asyncio
    async def test_aclose_calls_client_close_not_aexit(self) -> None:
        """`__aexit__()` alone would leave a `keep_alive=True` STDIO subprocess running
        forever — see `aclose()`'s docstring."""
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.close = AsyncMock(return_value=None)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            await manager.open()
            stderr_path = manager._session_stderr_path
            await manager.aclose()
            mock_client.close.assert_awaited_once()
            mock_client.__aexit__.assert_not_awaited()
            assert manager._session_client is None
            assert stderr_path is not None and not stderr_path.exists()

    @pytest.mark.asyncio
    async def test_aclose_is_a_noop_when_never_opened(self) -> None:
        manager = MCPClientManager(_config())
        await manager.aclose()  # must not raise

    @pytest.mark.asyncio
    async def test_aclose_swallows_close_errors(self) -> None:
        config = _config(transport=MCPTransport.STDIO, command="npx")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.close = AsyncMock(side_effect=RuntimeError("already dead"))

        with patch("app.agents.mcp.client.build_transport", return_value=MagicMock()), \
             patch("app.agents.mcp.client.Client", return_value=mock_client):
            manager = MCPClientManager(config)
            await manager.open()
            await manager.aclose()  # must not raise
            assert manager._session_client is None

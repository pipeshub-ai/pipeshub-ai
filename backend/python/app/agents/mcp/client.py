"""fastmcp `Client` wrapper — builds the right transport for an MCP instance and
exposes a thin async interface for tool discovery.
"""
import asyncio
import logging
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

import httpx
from fastmcp import Client
from fastmcp.client.transports import (
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
)
from mcp.shared._httpx_utils import create_mcp_http_client

from app.agents.mcp.models import MCPServerConfig, MCPTransport

logger = logging.getLogger(__name__)

# Bounds `open()`'s session handshake (`client.__aenter__()`).
DEFAULT_CONNECT_TIMEOUT_SECONDS = 15.0
# Bounds `call_tool()`/`call_tool_in_session()` — separate from the connect timeout above
# since a tool invocation (the remote server actually doing work) can legitimately take much
# longer than establishing the connection itself. Applied at this layer so every caller
# (Phase 2 agent-loop tool execution via `MCPSessionManager.call()`, and any future one-shot
# `call_tool()` caller) inherits it without having to remember to wrap the call themselves —
# previously neither method had any timeout, so an unresponsive remote MCP server could hang
# a tool call (and the whole agent-loop turn awaiting it) forever.
DEFAULT_CALL_TIMEOUT_SECONDS = 60.0

# fastmcp/mcp swallow a STDIO subprocess's stderr (defaults to the parent process's own
# sys.stderr) and surface only a generic "Connection closed" on failure. That hides the
# one thing an admin actually needs to fix a broken instance — e.g. `npx: command not
# found`, an npm registry timeout, or the target server's own "missing API key" message.
# Capturing it to a temp file lets `MCPClientManager` fold the real reason into the
# raised `MCPConnectionError`.
_STDERR_TAIL_MAX_CHARS = 4000


class MCPConnectionError(Exception):
    """Raised when an MCP server cannot be reached or its transport config is invalid."""


def _sanitize_url_for_diagnostics(url: str) -> str:
    """Scheme + host[:port] + path only — strip userinfo, query, and fragment so
    credentials never land in logs or `MCPConnectionError` messages."""
    try:
        parsed = urlparse(url)
    except Exception:
        return "<unparseable-url>"
    if not parsed.scheme or not parsed.netloc:
        return "<redacted-url>"
    # Drop userinfo (`user:pass@`) while keeping host/port, including bracketed IPv6.
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urlunparse((parsed.scheme, netloc, parsed.path or "", "", "", ""))


class _LastHttpResponse:
    """Records the most recent HTTP status/URL seen on an HTTP transport's httpx
    client, purely for diagnostics.

    The MCP SDK's streamable-HTTP transport swallows a 404 on the very first
    (initialize) request into a generic `"Session terminated"` JSON-RPC error with
    no trace of the real status code (`mcp.client.streamable_http
    .StreamableHTTPTransport._handle_post_request`), which makes "this endpoint
    doesn't exist/isn't enabled for this account" indistinguishable from an actual
    mid-session disconnect. `_annotate_with_last_http_response` uses this to tell
    the two apart.
    """

    def __init__(self) -> None:
        self.status_code: Optional[int] = None
        self.url: Optional[str] = None

    async def _on_response(self, response: httpx.Response) -> None:
        self.status_code = response.status_code
        self.url = _sanitize_url_for_diagnostics(str(response.request.url))

    def httpx_client_factory(
        self,
        headers: Optional[dict[str, str]] = None,
        timeout: Optional[httpx.Timeout] = None,
        auth: Optional[httpx.Auth] = None,
        **kwargs: Any,
    ) -> httpx.AsyncClient:
        client = create_mcp_http_client(headers=headers, timeout=timeout, auth=auth)
        client.event_hooks["response"].append(self._on_response)
        return client


def _annotate_with_last_http_response(message: str, recorder: Optional[_LastHttpResponse]) -> str:
    """Turns the SDK's opaque `"Session terminated"` error into something actionable
    when we captured the real HTTP status behind it — a 404 there almost always means
    the remote MCP endpoint isn't enabled/available for this account (wrong URL,
    feature/beta flag off, or an entitlement not met) rather than a genuinely dropped
    session.
    """
    if recorder is None or recorder.status_code is None or "session terminated" not in message.lower():
        return message
    if recorder.status_code == 404:
        return (
            f"{message} — the server responded 404 Not Found at {recorder.url}. This "
            "usually means the MCP endpoint isn't enabled/available for this account "
            "(wrong URL, a feature/beta flag that needs turning on, or a plan/entitlement "
            "requirement not met) rather than a dropped session — check the connector's "
            "prerequisites and the configured server URL, then retry."
        )
    return f"{message} (last HTTP response before failure: {recorder.status_code} from {recorder.url})"


def build_transport(
    config: MCPServerConfig,
    env: Optional[dict[str, str]] = None,
    headers: Optional[dict[str, str]] = None,
    stderr_log_file: Optional[Path] = None,
    *,
    keep_alive: Optional[bool] = None,
    http_response_recorder: Optional[_LastHttpResponse] = None,
):
    """Build the fastmcp transport object for `config`, given resolved env/headers.

    `keep_alive` (STDIO only) controls whether the spawned subprocess is left running
    after this transport's session ends. Callers that build a fresh transport per call
    (`MCPClientManager.connect()`) must pass `keep_alive=False` — fastmcp's default
    (`True`) is meant for a transport instance that is reused across many sessions, and
    on a throwaway, one-shot transport it instead orphans the `npx`/`node` subprocess
    since nothing else will ever call `disconnect()` on it.
    """
    if config.transport == MCPTransport.STDIO:
        if not config.command:
            raise MCPConnectionError(f"MCP instance {config.id} is STDIO but has no command configured")
        return StdioTransport(
            command=config.command,
            args=list(config.args or []),
            env=dict(env or {}),
            log_file=stderr_log_file,
            keep_alive=keep_alive,
        )

    if config.transport == MCPTransport.SSE:
        if not config.url:
            raise MCPConnectionError(f"MCP instance {config.id} is SSE but has no url configured")
        factory = http_response_recorder.httpx_client_factory if http_response_recorder else None
        return SSETransport(url=config.url, headers=dict(headers or {}), httpx_client_factory=factory)

    if config.transport == MCPTransport.STREAMABLE_HTTP:
        if not config.url:
            raise MCPConnectionError(f"MCP instance {config.id} is streamable_http but has no url configured")
        factory = http_response_recorder.httpx_client_factory if http_response_recorder else None
        return StreamableHttpTransport(url=config.url, headers=dict(headers or {}), httpx_client_factory=factory)

    raise MCPConnectionError(f"Unsupported MCP transport: {config.transport}")


def _new_stderr_capture_path(config: MCPServerConfig) -> Optional[Path]:
    """A temp-file path to capture a STDIO subprocess's stderr into, or None for
    non-STDIO transports (nothing to capture). Caller owns cleanup —
    `_cleanup_stderr_capture_path`."""
    if config.transport != MCPTransport.STDIO:
        return None
    fd, name = tempfile.mkstemp(prefix=f"mcp-stderr-{config.id}-", suffix=".log")
    os.close(fd)
    return Path(name)


def _cleanup_stderr_capture_path(path: Optional[Path]) -> None:
    if path is None:
        return
    # Best-effort — e.g. the subprocess's log file handle is still open on Windows.
    # A stray temp file is harmless; losing the real error isn't.
    with suppress(OSError):
        path.unlink(missing_ok=True)


@asynccontextmanager
async def _capture_stdio_stderr(config: MCPServerConfig) -> AsyncIterator[Optional[Path]]:
    """Scoped wrapper around `_new_stderr_capture_path` for callers (`connect()`) whose
    transport is guaranteed torn down by the time the `with` block exits."""
    path = _new_stderr_capture_path(config)
    try:
        yield path
    finally:
        _cleanup_stderr_capture_path(path)


def _read_stderr_tail(path: Optional[Path]) -> str:
    """Best-effort read of the captured subprocess stderr, truncated to the last
    `_STDERR_TAIL_MAX_CHARS` characters (the useful part of a crash is at the end)."""
    if path is None:
        return ""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return ""
    return text[-_STDERR_TAIL_MAX_CHARS:].strip()


def _annotate_with_stderr(message: str, stderr_tail: str) -> str:
    if not stderr_tail:
        return message
    return f"{message} | subprocess stderr: {stderr_tail}"


class MCPClientManager:
    """Opens a short-lived fastmcp `Client` for a single instance (connect, use, disconnect).

    Phase 1 only needs discovery (`list_tools`); `call_tool` is exposed for Phase 2's
    agent-loop tool adapter to reuse without re-deriving the transport/auth wiring.
    """

    def __init__(
        self,
        config: MCPServerConfig,
        env: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.config = config
        self.env = env or {}
        self.headers = headers or {}
        self._session_client: Optional[Client] = None
        self._session_stderr_path: Optional[Path] = None

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[Client]:
        async with _capture_stdio_stderr(self.config) as stderr_path:
            response_recorder = (
                _LastHttpResponse() if self.config.transport in (MCPTransport.STREAMABLE_HTTP, MCPTransport.SSE) else None
            )
            transport = build_transport(
                self.config,
                env=self.env,
                headers=self.headers,
                stderr_log_file=stderr_path,
                keep_alive=False,
                http_response_recorder=response_recorder,
            )
            client = Client(transport)
            try:
                async with client:
                    yield client
            except MCPConnectionError:
                raise
            except Exception as e:
                message = _annotate_with_stderr(str(e), _read_stderr_tail(stderr_path))
                message = _annotate_with_last_http_response(message, response_recorder)
                logger.error(f"Failed to connect to MCP server {self.config.id} ({self.config.name}): {message}")
                raise MCPConnectionError(message) from e

    async def list_tools(self) -> list[Any]:
        async with self.connect() as client:
            return await client.list_tools()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        async with self.connect() as client:
            return await asyncio.wait_for(
                client.call_tool(tool_name, arguments), timeout=DEFAULT_CALL_TIMEOUT_SECONDS,
            )

    # ------------------------------------------------------------------
    # Long-lived session mode — Phase 2's agent-loop runtime
    # (`app/agents/agent_loop/mcp_session.py::MCPSessionManager`) reuses ONE
    # connection across every tool call an instance receives within a
    # request instead of `connect()`'s per-call connect/disconnect above.
    # Deliberately a separate, explicit pair of methods (not a flag on
    # `connect()`) so Phase 1's one-shot discovery callers are unaffected.
    # ------------------------------------------------------------------

    async def open(self) -> Client:
        """Opens a session for reuse across `call_tool_in_session()` calls.

        Idempotent: a no-op returning the already-open client if called
        again. Callers MUST `aclose()` when done with this instance.
        """
        if self._session_client is not None:
            return self._session_client
        stderr_path = _new_stderr_capture_path(self.config)
        response_recorder = (
            _LastHttpResponse() if self.config.transport in (MCPTransport.STREAMABLE_HTTP, MCPTransport.SSE) else None
        )
        client: Optional[Client] = None
        try:
            transport = build_transport(
                self.config,
                env=self.env,
                headers=self.headers,
                stderr_log_file=stderr_path,
                keep_alive=True,
                http_response_recorder=response_recorder,
            )
            client = Client(transport)
            await asyncio.wait_for(client.__aenter__(), timeout=DEFAULT_CONNECT_TIMEOUT_SECONDS)
        except Exception as e:
            # Read stderr before tearing anything down — the capture file is unlinked
            # below, and `keep_alive=True` means a half-started STDIO transport can
            # leave an orphaned subprocess unless we force-close (same reason
            # `aclose()` calls `client.close()` rather than `__aexit__()`).
            stderr_tail = _read_stderr_tail(stderr_path)
            if client is not None:
                with suppress(Exception):
                    await client.close()
            _cleanup_stderr_capture_path(stderr_path)
            if isinstance(e, MCPConnectionError):
                raise
            message = _annotate_with_stderr(str(e), stderr_tail)
            message = _annotate_with_last_http_response(message, response_recorder)
            logger.error(f"Failed to open MCP session for {self.config.id} ({self.config.name}): {message}")
            raise MCPConnectionError(message) from e
        self._session_client = client
        self._session_stderr_path = stderr_path
        return client

    async def call_tool_in_session(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Calls a tool on the session `open()` established.

        Raises MCPConnectionError if no session is open.
        """
        if self._session_client is None:
            raise MCPConnectionError(f"MCP session for {self.config.id} ({self.config.name}) is not open")
        try:
            return await asyncio.wait_for(
                self._session_client.call_tool(tool_name, arguments), timeout=DEFAULT_CALL_TIMEOUT_SECONDS,
            )
        except Exception as e:
            stderr_tail = _read_stderr_tail(self._session_stderr_path)
            if not stderr_tail:
                raise
            try:
                raise type(e)(_annotate_with_stderr(str(e), stderr_tail)) from e
            except TypeError:
                # Exception subtype doesn't accept a single message arg — don't let the
                # annotation attempt itself mask the original error.
                raise e from None

    async def aclose(self) -> None:
        """Closes the session opened by `open()`. A no-op if never opened.

        Uses `client.close()`, not `client.__aexit__()` — with `keep_alive=True`
        (needed so the subprocess survives *between* `call_tool_in_session()` calls),
        `__aexit__()` alone leaves the STDIO subprocess running forever, since fastmcp's
        transport-level `keep_alive` deliberately skips `disconnect()` there. `close()`
        forces the transport closed regardless of `keep_alive`, which is exactly what a
        request-scoped session needs once the request itself is done with it.
        """
        client, self._session_client = self._session_client, None
        stderr_path, self._session_stderr_path = self._session_stderr_path, None
        if client is None:
            return
        try:
            await client.close()
        except Exception as e:
            logger.debug(f"Error closing MCP session for {self.config.id} ({self.config.name}): {e}")
        finally:
            _cleanup_stderr_capture_path(stderr_path)
